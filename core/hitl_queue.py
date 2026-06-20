"""core/hitl_queue.py — HITL approval queue (EU AI Act Art. 14), PR-0a-ii-1 storage layer.

A Redis-backed store that holds real-money orders awaiting human approval. This module is
**dumb storage only**: deciding *which* orders need approval (the threshold policy) and the
tamper-evident audit mirror live in the caller (PR-0a-ii-4 / -5 / -2), not here. The queue is
**UNWIRED** until those PRs route the order path through it — default-dormant, no behaviour
change.

Backend = ``RedisClient.get_redis()`` → real Redis (cloud), the in-memory ``LocalStateClient``
(desktop), or ``None`` when Redis is configured-but-unavailable (every method then no-ops).

Keys (all carry a px TTL so they self-expire — an unreviewed order auto-rejects on timeout):
  ``hitl:pending:{approval_id}``           JSON payload, TTL = ``HITL_EXPIRY_SECONDS``
  ``hitl:pending_idx:{user_id}:{symbol}``  → approval_id, **same TTL** (N6: expires together,
                                             so ``has_pending`` can never return stale-True and
                                             permanently block a symbol)
  ``hitl:approved:{approval_id}``          JSON payload, 10-min window for the loop to drain it
  ``hitl:inflight:{approval_id}``          set while the loop EXECUTES a claimed approval;
                                             cleared on a definitive outcome. A survivor of a
                                             mid-execution crash is surfaced (not silently lost)
                                             by ``recover_orphaned_inflight``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import get_config
from core.redis_client import RedisClient

logger = logging.getLogger(__name__)

_PENDING_PREFIX = "hitl:pending:"
_IDX_PREFIX = "hitl:pending_idx:"
_APPROVED_PREFIX = "hitl:approved:"
_INFLIGHT_PREFIX = "hitl:inflight:"
# 10-minute window for the trading loop to pick up an approved order before it lapses.
_APPROVED_WINDOW_SECONDS = 600
# How long a claimed-but-unacked approval (a mid-execution crash survivor) lingers as a
# recovery marker. Long enough for an operator to act on the loud orphan alert before it
# self-cleans; the Art-14 ``approved`` audit record is on the tamper-evident chain regardless.
_INFLIGHT_TTL_SECONDS = 86_400  # 24h


def _idx_key(user_id: str, symbol: str) -> str:
    return f"{_IDX_PREFIX}{user_id}:{symbol}"


class HitlQueue:
    """Redis-backed HITL approval queue. All methods are static + async."""

    @staticmethod
    async def push(
        *,
        user_id: str,
        symbol: str,
        action: str,
        qty: float,
        price: float,
        conviction: float,
        target_weight: float,
        event_json: str = "",
    ) -> Optional[str]:
        """Queue an order for human approval; return its approval_id (None without Redis).

        Dedups on (user_id, symbol) (G): an existing pending entry for the same symbol is
        superseded, so the queue never holds two live approvals — or a contradictory
        BUY+SELL — for one symbol while a human deliberates.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return None

        expiry_seconds = int(get_config().HITL_EXPIRY_SECONDS)
        px = expiry_seconds * 1000
        idx_key = _idx_key(user_id, symbol)

        # G — supersede any existing pending entry for (user_id, symbol).
        existing = await redis.get(idx_key)
        if existing:
            await redis.delete(_PENDING_PREFIX + existing, idx_key)

        approval_id = str(uuid.uuid4())
        payload = {
            "approval_id": approval_id,
            "user_id": user_id,
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": price,
            "conviction": conviction,
            "target_weight": target_weight,
            "event_json": event_json,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(_PENDING_PREFIX + approval_id, json.dumps(payload), px=px)
        # N6 — the index carries the SAME TTL so it can never outlive the pending key.
        await redis.set(idx_key, approval_id, px=px)
        logger.info(
            "[HITL] queued %s %s qty=%.4f price=%.2f (id=%s ttl=%ds)",
            action,
            symbol,
            qty,
            price,
            approval_id,
            expiry_seconds,
        )
        return approval_id

    @staticmethod
    async def has_pending(symbol: str, user_id: str) -> bool:
        """True iff a live pending approval exists for (user_id, symbol).

        O(1) secondary-index lookup (no scan), with a belt-and-suspenders check that the
        pending blob itself still exists — so an index that somehow outlived its key never
        reports a phantom pending.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return False
        approval_id = await redis.get(_idx_key(user_id, symbol))
        if not approval_id:
            return False
        return (await redis.get(_PENDING_PREFIX + approval_id)) is not None

    @staticmethod
    async def get_pending() -> List[Dict[str, Any]]:
        """Every currently-pending item (expired keys have already self-removed).

        Uses KEYS over the small pending set (orders awaiting human approval number in the
        handful) — acceptable here; not a hot path.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return []
        items: List[Dict[str, Any]] = []
        for key in await redis.keys(_PENDING_PREFIX + "*"):
            blob = await redis.get(key)
            if not blob:
                continue
            try:
                items.append(json.loads(blob))
            except (ValueError, TypeError):
                logger.warning("[HITL] skipping unparseable pending blob at %s", key)
        return items

    @staticmethod
    async def approve(approval_id: str) -> Optional[Dict[str, Any]]:
        """Approve a pending order: move pending → approved (10-min pickup window) and
        return the payload so the caller can execute it. None if gone/expired."""
        redis = await RedisClient.get_redis()
        if redis is None:
            return None
        blob = await redis.get(_PENDING_PREFIX + approval_id)
        if not blob:
            return None
        payload = json.loads(blob)
        payload["approved_at"] = datetime.now(timezone.utc).isoformat()
        await redis.set(
            _APPROVED_PREFIX + approval_id,
            json.dumps(payload),
            px=_APPROVED_WINDOW_SECONDS * 1000,
        )
        await redis.delete(
            _PENDING_PREFIX + approval_id,
            _idx_key(payload.get("user_id", ""), payload.get("symbol", "")),
        )
        logger.info(
            "[HITL] APPROVED %s %s (id=%s)",
            payload.get("action"),
            payload.get("symbol"),
            approval_id,
        )
        return payload

    @staticmethod
    async def reject(approval_id: str, reason: str = "") -> bool:
        """Reject a pending order; delete the pending entry and its index. True if removed."""
        redis = await RedisClient.get_redis()
        if redis is None:
            return False
        keys = [_PENDING_PREFIX + approval_id]
        blob = await redis.get(_PENDING_PREFIX + approval_id)
        if blob:
            try:
                p = json.loads(blob)
                keys.append(_idx_key(p.get("user_id", ""), p.get("symbol", "")))
            except (ValueError, TypeError):
                pass
        deleted = await redis.delete(*keys)
        if deleted:
            logger.info("[HITL] REJECTED id=%s reason=%r", approval_id, reason)
        return bool(deleted)

    @staticmethod
    async def claim_approved() -> List[Dict[str, Any]]:
        """Claim every approved order for execution: move ``approved`` → ``inflight`` and
        return the payloads (called by the trading loop each cycle).

        Crash-safe replacement for the old delete-on-read drain: each approved key is first
        copied to a ``hitl:inflight:{id}`` marker and only then removed, so an order is never
        lost in the gap between being drained and being executed. The caller MUST
        ``ack_inflight`` once the order reaches a definitive outcome; a mid-execution crash
        leaves the marker for ``recover_orphaned_inflight``. ``[]`` without Redis.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return []
        results: List[Dict[str, Any]] = []
        for key in await redis.keys(_APPROVED_PREFIX + "*"):
            blob = await redis.get(key)
            if not blob:
                await redis.delete(key)
                continue
            try:
                payload = json.loads(blob)
            except (ValueError, TypeError):
                logger.warning("[HITL] skipping unparseable approved blob at %s", key)
                await redis.delete(key)
                continue
            aid = payload.get("approval_id") or key[len(_APPROVED_PREFIX) :]
            # Pin the resolved id back onto the returned payload so the caller's ack_inflight
            # targets exactly this marker even for a (malformed) blob whose approval_id was
            # missing/empty — otherwise the ack key and the inflight key could diverge and the
            # marker would linger until its TTL.
            payload["approval_id"] = aid
            # Inflight marker FIRST, then drop the approved key — so a crash in between can at
            # worst leave both (re-claimed next cycle, executed once) but never neither (which
            # would be a silent loss of a human-approved order).
            await redis.set(
                _INFLIGHT_PREFIX + aid, blob, px=_INFLIGHT_TTL_SECONDS * 1000
            )
            await redis.delete(key)
            results.append(payload)
        return results

    @staticmethod
    async def ack_inflight(approval_id: str) -> bool:
        """Clear the inflight marker once a claimed approval reached a definitive outcome
        (submitted, or terminally refused by Iron Dome). No-op without Redis / if already gone.
        """
        redis = await RedisClient.get_redis()
        if redis is None or not approval_id:
            return False
        return bool(await redis.delete(_INFLIGHT_PREFIX + approval_id))

    @staticmethod
    async def recover_orphaned_inflight() -> List[Dict[str, Any]]:
        """Surface approvals claimed for execution but never acked — i.e. the engine
        crashed/redeployed mid-execution.

        These are **logged loudly and returned, but NOT auto-re-executed**: without
        broker-side idempotency a blind re-run could place the order twice. An operator must
        verify at the broker whether the order filled before re-approving. The alert repeats
        each cycle (intentionally) until the marker is cleared or its 24h TTL lapses. ``[]``
        without Redis / when none.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return []
        orphans: List[Dict[str, Any]] = []
        for key in await redis.keys(_INFLIGHT_PREFIX + "*"):
            blob = await redis.get(key)
            if not blob:
                continue
            try:
                orphans.append(json.loads(blob))
            except (ValueError, TypeError):
                logger.warning("[HITL] skipping unparseable inflight blob at %s", key)
        for o in orphans:
            logger.error(
                "[HITL] ORPHANED in-flight approval id=%s %s %s — the engine restarted while "
                "executing it. VERIFY at the broker whether it filled BEFORE re-approving "
                "(not auto-re-executed, to avoid a double order).",
                o.get("approval_id"),
                o.get("action"),
                o.get("symbol"),
            )
        return orphans
