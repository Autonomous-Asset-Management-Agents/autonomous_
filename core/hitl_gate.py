"""core/hitl_gate.py — HITL threshold gate (EU AI Act Art. 14), PR-0a-ii-4b.

The single order-path chokepoint of the HITL autonomy policy. For one real-money BUY/SELL
it decides whether the order may execute autonomously or must be routed to a human — and
records the immutable Art-14 audit evidence for that decision.

**DORMANT by default.** The order executor only calls the gate when ``HITL_ENABLED`` is
True (config default False, see ADR-015). With HITL off the gate is never reached, so the
order path is byte-identical to before this change — no behaviour change ships dormant.

Three modes, derived from two value limits (full spec: ``docs/6_runbooks/HITL_AUTONOMY_POLICY.md``):

* **all-manual** — both limits ``0`` ⇒ every order exceeds the per-trade limit ⇒ queued.
* **threshold** *(primary)* — an order within both ``HITL_MAX_VALUE_PER_TRADE`` and the
  running per-NY-day ``HITL_MAX_VALUE_PER_DAY`` executes autonomously; over either ⇒ queued.
* **unlimited** — ``HITL_AUTONOMOUS_UNLIMITED`` ⇒ fully autonomous (Mode C).

A risk-reducing SELL can be exempted from the gate by the
``HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS`` switch (so a stop-out is never blocked behind a
human). **Fail-closed**: any error — a gate fault, or an over-limit order the queue could
not store — HOLDs the order; the gate never lets an order execute on an error path.

Side-effects (queue push via ``HitlQueue``, autonomous-notional booking via
``HitlDayNotional``, audit via the Round-Table hash chain) are performed here. The trading
loop drains approved orders and executes them in PR-0a-ii-5.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytz

from config import get_config
from core.hitl_day_notional import HitlDayNotional
from core.hitl_queue import HitlQueue
from core.round_table.senate_log import (
    HITLExecutionEvent,
    HITLPolicyEvent,
    LiveEnableEvent,
    LocalJSONAuditLogger,
    ManualCancelEvent,
)

logger = logging.getLogger(__name__)

_NY_TZ = pytz.timezone("America/New_York")
_fallback_audit_logger: Optional[LocalJSONAuditLogger] = None
# Guards the lazy construction of the fallback logger so it is built exactly once across
# threads — its hash chain must have a single writer (see _resolve_audit_logger).
_fallback_audit_lock = threading.Lock()


def _ny_date() -> str:
    """Today's NY trading date as ``YYYY-MM-DD`` — the key of the per-day notional counter."""
    return datetime.now(_NY_TZ).date().isoformat()


def _now() -> str:
    """UTC timestamp for an audit event."""
    return datetime.now(timezone.utc).isoformat()


def policy_snapshot(cfg: Any = None) -> dict:
    """The six HITL policy values as a plain dict — the audited policy state.

    Shared with the policy-change endpoint (PR-0a-ii-6), which records old/new snapshots on
    a ``HITLPolicyEvent``; here it stamps every execution event with the active policy.
    """
    cfg = cfg or get_config()
    return {
        "HITL_ENABLED": bool(cfg.HITL_ENABLED),
        "HITL_MAX_VALUE_PER_TRADE": float(cfg.HITL_MAX_VALUE_PER_TRADE),
        "HITL_MAX_VALUE_PER_DAY": float(cfg.HITL_MAX_VALUE_PER_DAY),
        "HITL_AUTONOMOUS_UNLIMITED": bool(cfg.HITL_AUTONOMOUS_UNLIMITED),
        "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS": bool(
            cfg.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS
        ),
        "HITL_EXPIRY_SECONDS": int(cfg.HITL_EXPIRY_SECONDS),
    }


def policy_hash(snapshot: dict) -> str:
    """Stable SHA-256 of a policy snapshot (sorted keys) — the ``policy_hash`` audit field."""
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _resolve_audit_logger():
    """The process-wide Art-14 audit logger.

    Reuse the Round-Table logger when it is configured, so HITL events land on the SAME
    SHA-256 hash chain as Senate sessions (one tamper-evident chain — a second writer to the
    shared daily file would split the chain). Before the round table is configured, or on the
    enterprise ``SenateProtocol`` path, fall back to a dedicated singleton
    ``LocalJSONAuditLogger`` so Art-14 evidence is still recorded locally (the enterprise
    Cloud-SQL HITL sink lands in PR-0a-ii-7).
    """
    try:
        from core.round_table import runner

        existing = runner.get_audit_logger()
        if existing is not None:
            return existing
    except Exception as exc:  # defensive: round-table import / boot order
        logger.warning("[HITL] round-table audit logger unavailable: %s", exc)

    # Fallback: only before boot_engine wires _senate (or on the enterprise SenateProtocol
    # path). It must never run concurrently with a live runner LocalJSONAuditLogger — both
    # default to the same daily file with per-instance hash state, which would split the chain
    # (boot order guarantees this). Double-checked locking yields exactly ONE process-wide
    # fallback instance even under thread contention, so its hash chain stays single-writer.
    global _fallback_audit_logger
    if _fallback_audit_logger is None:
        with _fallback_audit_lock:
            if _fallback_audit_logger is None:
                _fallback_audit_logger = LocalJSONAuditLogger()
    return _fallback_audit_logger


async def _audit(event: HITLExecutionEvent) -> None:
    """Record an execution-decision event on the tamper-evident chain (best-effort).

    Audit failure must never break the order path, so exceptions are logged, never raised —
    but note the decision itself (HOLD vs execute) is already made by the caller; a missing
    audit line is a logged evidence gap, not a control bypass.
    """
    try:
        await _resolve_audit_logger().log_hitl_event(event)
    except Exception as exc:  # pragma: no cover - audit must not crash the trading loop
        logger.warning("[HITL] audit log failed (%s): %s", event.branch, exc)


async def log_execution_event(event: HITLExecutionEvent) -> None:
    """Public entry point to record a HITL execution-decision event on the Art-14 hash chain.

    Used by the human-approval drain (PR-0a-ii-5 ``execute_approved_order``) so its audits land
    on the SAME tamper-evident chain as the autonomous gate's. Best-effort (never raises).
    """
    await _audit(event)


async def log_policy_event(
    old_policy: dict, new_policy: dict, actor: str = "api", *, strict: bool = False
) -> None:
    """Record a HITL policy-change on the Art-14 hash chain (PR-0a-ii-6 POST /api/hitl/policy).

    Written BEFORE the running policy is mutated, so the immutable trail always shows the
    old→new transition an operator made (and by whom). Same single tamper-evident chain as the
    execution audits.

    With ``strict=True`` (the policy API) a failed write **re-raises**, so the caller can refuse
    the mutation: a policy change is operator-initiated, low-frequency and off the hot path, so
    it must never proceed *unaudited*. Default (``strict=False``) stays best-effort, like the
    execution audit, for any future fire-and-forget caller (e.g. a boot snapshot).
    """
    try:
        await _resolve_audit_logger().log_hitl_event(
            HITLPolicyEvent(
                timestamp=_now(),
                actor=actor,
                old_policy=old_policy,
                new_policy=new_policy,
            )
        )
    except Exception as exc:
        logger.warning("[HITL] policy-event audit failed: %s", exc)
        if strict:
            raise


async def log_live_enablement_event(
    *,
    action: str,
    acknowledgment: str,
    nonce: str,
    actor: str = "operator",
    switch_id: Optional[str] = None,
    strict: bool = True,
) -> None:
    """Record a deliberate live-trading enablement/revocation on the Art-14 hash chain (LIVE-1 T4).

    **audit-before-enable**: written BEFORE the desktop shell is allowed to boot the engine live
    (the shell only flips ``SHADOW_MODE`` off once ``verifyAuditChain`` confirms an un-revoked
    ``enable`` record — T1). With ``strict=True`` (the ``/api/live/enable`` default) a failed WORM
    write **re-raises** so the API refuses to report success: capital may never go live on an
    unaudited decision. ``action`` ∈ {enable, disable}.

    ``switch_id`` (#2277 INC-1) is the UI→API→boot→revoke correlation id. It defaults to the
    ``nonce`` so the WORM record is never ``null`` (D1: no null-noise) and a direct enable/disable
    self-correlates; a compensating ``disable`` passes the *enable's* switch_id to join the degrade
    back to its arming.
    """
    try:
        await _resolve_audit_logger().log_hitl_event(
            LiveEnableEvent(
                timestamp=_now(),
                actor=actor,
                action=action,
                acknowledgment=acknowledgment,
                nonce=nonce,
                switch_id=switch_id or nonce,
            )
        )
    except Exception as exc:
        logger.warning("[HITL] live-enablement audit failed (%s): %s", action, exc)
        if strict:
            raise


async def collect_live_enablement_nonces() -> set[str]:
    """Return every nonce already recorded as a ``live_enablement`` event on the WORM chain.

    Enforces the ``LiveEnableRequest.nonce`` "replay-distinct" invariant (LSR R7, #2255 /
    audit #18): the caller (``/api/live/enable`` and ``/api/live/disable``) rejects a nonce in
    this set with **409 BEFORE** appending a duplicate WORM record. The scan resolves its
    directory from the SAME logger that writes the chain (``_resolve_audit_logger()._log_dir``),
    so read and write can never diverge, and reads the local audit JSONL **once** off the hot
    path (operator-initiated, low-frequency ⇒ ``asyncio.to_thread``).

    **Fail-open** on any IO/JSON error: a missing or partially-written log must never block a
    legitimate first enablement — an unreadable chain yields the empty set, so only a *provably*
    already-present nonce is ever rejected (the rejection itself is fail-closed at the call site).
    """
    log_dir = getattr(_resolve_audit_logger(), "_log_dir", None)
    if log_dir is None:
        log_dir = Path(os.getenv("SENATE_LOG_DIR", "oss_audit_logs"))

    def _scan() -> set[str]:
        seen: set[str] = set()
        try:
            for f in sorted(Path(log_dir).glob("audit_log_*.jsonl")):
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue  # skip a torn / partial trailing line, keep scanning
                    if rec.get("event_type") == "live_enablement":
                        nonce = rec.get("nonce")
                        if isinstance(nonce, str):
                            seen.add(nonce)
        except (
            OSError
        ) as exc:  # fail-open: an unreadable chain must not block a first enable
            logger.warning("[HITL] nonce-scan read failed (fail-open): %s", exc)
        return seen

    return await asyncio.to_thread(_scan)


async def log_manual_cancel_event(
    *, order_id: str, actor: str = "operator", strict: bool = False
) -> None:
    """Record an operator's manual HITL cancel of a working order on the Art-14 hash chain (#2137).

    A hand-initiated cancel is an EU AI Act Art. 14 human-oversight intervention and must leave an
    immutable trail on the SAME tamper-evident chain as the other audit events. Default
    ``strict=False`` (best-effort, never raises): the cancel is a risk-*reducing* safety action and
    must never be blocked by an audit-sink hiccup — a failed WORM write is a logged evidence gap,
    not a control bypass (mirrors the execution-audit posture).
    """
    try:
        await _resolve_audit_logger().log_hitl_event(
            ManualCancelEvent(timestamp=_now(), actor=actor, order_id=order_id)
        )
    except Exception as exc:
        logger.warning("[HITL] manual-cancel audit failed (%s): %s", order_id, exc)
        if strict:
            raise


async def should_hold(
    event: Any, user_id: str = "global", calculated_qty: Optional[float] = None
) -> bool:
    """Return True to HOLD the order (queued / blocked), False to execute it autonomously.

    Only called when ``HITL_ENABLED``. Performs the queue / day-notional / audit side-effects
    for the chosen branch. **Fail-closed**: on any unexpected error, HOLD — an Art-14 gate
    must never resolve a fault by executing capital autonomously.

    ``calculated_qty`` (#2704) is the quantity the EXECUTOR resolved, passed by the two call
    sites that now sit AFTER position sizing. Board signals carry no quantity by construction
    (``round_table/runner.py`` — the board decides *whether*, the executor *how much*), so
    before the gate moved it read the 0.0 default from the event, valued every order at 0 and
    routed all of them to ``unknown_value``: 13 live orders queued with ``Qty 0`` on 2026-08-06,
    none of them approvable, and the ADR-016 approval ceiling silently skipped because it tests
    ``_approved_qty > 0``.

    Passed EXPLICITLY rather than by mutating ``event.suggested_quantity``: that field is read
    downstream (including by the ADR-016 ceiling), so mutating it would change behaviour outside
    this seam and the audit could no longer distinguish a board-supplied quantity from a
    sizer-derived one. Optional with a ``None`` default, so every pre-existing caller and its
    tests stay byte-identical.
    """
    try:
        return await _decide(event, user_id, calculated_qty)
    except Exception as exc:
        logger.error(
            "[HITL] gate error → fail-closed HOLD for %s: %s",
            getattr(event, "symbol", "?"),
            exc,
        )
        return True


async def _decide(
    event: Any, user_id: str, calculated_qty: Optional[float] = None
) -> bool:
    cfg = get_config()
    phash = policy_hash(policy_snapshot(cfg))
    context = event.decision_context
    symbol = event.symbol
    action = event.action
    price = float(getattr(context, "current_price", 0.0) or 0.0)
    # Keep the SIGNED held quantity: only a LONG (position_qty > 0) being SOLD is risk-reducing.
    # abs() would make a short look long and wrongly exempt a SELL that *increases* a short
    # position — a risk-INCREASING trade slipping past the Art-14 human gate.
    signed_position_qty = float(getattr(context, "position_qty", 0.0) or 0.0)

    # Resolve order quantity, most authoritative source first (#2704):
    #   1. the quantity the EXECUTOR calculated (passed by the post-sizing call sites) — the only
    #      source that knows what will actually be submitted;
    #   2. the event's own suggested_quantity (a strategy path that sizes up front);
    #   3. for a "close position" SELL, the held position size — so a position-closing sell with
    #      no explicit quantity is still measured against the limits.
    # A non-positive `calculated_qty` is NOT authoritative: a sizer that produced nothing must not
    # be mistaken for an order genuinely worth 0.
    qty = 0.0
    if calculated_qty is not None:
        try:
            qty = abs(float(calculated_qty))
        except (TypeError, ValueError):
            qty = 0.0
    if qty <= 0:
        qty = abs(float(getattr(event, "suggested_quantity", 0.0) or 0.0))
    if qty <= 0 and action == "SELL":
        qty = abs(signed_position_qty)
    order_value = qty * price
    reduces_position = action == "SELL" and signed_position_qty > 0

    # Mode C — fully autonomous: execute, record the active policy, do not track notional.
    if cfg.HITL_AUTONOMOUS_UNLIMITED:
        await _audit(
            HITLExecutionEvent(
                timestamp=_now(),
                symbol=symbol,
                action=action,
                branch="under_limit",
                policy_hash=phash,
                order_value=order_value,
                reason="autonomous_unlimited",
            )
        )
        return False

    # Risk-reducing SELL exemption (switch): a stop-out is never blocked behind a human.
    if reduces_position and cfg.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS:
        await _audit(
            HITLExecutionEvent(
                timestamp=_now(),
                symbol=symbol,
                action=action,
                branch="risk_off_exempt",
                policy_hash=phash,
                order_value=order_value,
                reason="risk_reducing_sell",
            )
        )
        return False

    # Already awaiting approval for this (user, symbol): HOLD without re-queueing (C4) — the
    # original queue event was already audited, so we do not flood the chain every cycle.
    if await HitlQueue.has_pending(symbol, user_id):
        logger.info("[HITL] %s already pending approval → HOLD", symbol)
        return True

    # Cannot value the order — an unknown-size SELL (no suggested qty, no known long position)
    # or a missing price ⇒ order_value <= 0. Route to a human rather than execute on a guess;
    # in particular this stops a 0-priced order from silently escaping all-manual mode (both
    # limits 0), where ``0 > 0`` would otherwise read as "under limit". Conservative / fail-safe.
    if order_value <= 0:
        breached = (
            "unknown_sell_size" if (action == "SELL" and qty <= 0) else "unknown_value"
        )
        await _queue_and_audit(
            event, user_id, qty, price, order_value, phash, breached=breached
        )
        return True

    # Threshold test: over the per-trade limit, or the per-day autonomous budget. The
    # current()-then-add() pair is not atomic, but _process_signal_event is awaited serially in
    # the trading loop (no gather), so within one instance two under-limit orders cannot
    # interleave the gap and overshoot the day budget; add() itself is atomic (incrbyfloat). A
    # future multi-instance deployment sharing one Redis must revisit this (see PR-0a-ii-7).
    day_notional = await HitlDayNotional.current(_ny_date())
    over_trade = order_value > float(cfg.HITL_MAX_VALUE_PER_TRADE)
    over_day = (day_notional + order_value) > float(cfg.HITL_MAX_VALUE_PER_DAY)
    if over_trade or over_day:
        breached = "per_trade" if over_trade else "per_day"
        await _queue_and_audit(
            event, user_id, qty, price, order_value, phash, breached=breached
        )
        return True

    # Under both limits — execute autonomously and book the notional against today's budget.
    # NOTE (DD 2026-06-15, INTENTIONAL — do not "fix" without a compliance decision): order_value
    # is the pre-risk ESTIMATE (|qty| * price). The executor may later shrink or reject the order,
    # so this can over-count vs. capital actually deployed. That is the deliberately CONSERVATIVE
    # direction for an Art-14 control: it routes to human approval slightly EARLIER (more
    # oversight), never later. "Correcting" it to the executed value would reduce human oversight
    # — a compliance trade-off owned by the operator, not a bug to silently fix.
    new_total = await HitlDayNotional.add(_ny_date(), order_value)
    await _audit(
        HITLExecutionEvent(
            timestamp=_now(),
            symbol=symbol,
            action=action,
            branch="under_limit",
            policy_hash=phash,
            order_value=order_value,
            day_notional_after=new_total,
        )
    )
    return False


async def _queue_and_audit(
    event: Any,
    user_id: str,
    qty: float,
    price: float,
    order_value: float,
    phash: str,
    *,
    breached: str,
) -> None:
    """Queue an over-limit order for human approval and audit it on the ``queued`` branch.

    Fail-closed: if the queue is unavailable (``push`` returns None — e.g. Redis configured
    but down), the order is still HELD by the caller; we record it queued with a
    ``queue_unavailable_fail_closed`` reason so the evidence shows the order did not execute.
    """
    context = event.decision_context
    approval_id = None
    try:
        approval_id = await HitlQueue.push(
            user_id=user_id,
            symbol=event.symbol,
            action=event.action,
            qty=qty,
            price=price,
            conviction=float(getattr(context, "conviction_score", 0.0) or 0.0),
            target_weight=float(getattr(context, "target_weight", 0.0) or 0.0),
        )
    except Exception as exc:
        logger.error(
            "[HITL] queue push failed → fail-closed HOLD for %s: %s", event.symbol, exc
        )
    await _audit(
        HITLExecutionEvent(
            timestamp=_now(),
            symbol=event.symbol,
            action=event.action,
            branch="queued",
            policy_hash=phash,
            order_value=order_value,
            approval_id=approval_id,
            threshold_breached=breached,
            reason=None if approval_id else "queue_unavailable_fail_closed",
        )
    )


# ── PR-1 WORM Fail-Closed Boot Guard ─────────────────────────────────────────


def assert_worm_writable() -> None:
    """Fail-Closed pre-flight: verify the WORM audit dir is writable BEFORE live boot.

    Uses an active temp-file probe (NOT ``os.access(..., os.W_OK)``) to guarantee
    correctness on Windows NTFS ACLs — ``os.access`` only checks the legacy DOS
    Read-Only attribute and silently returns ``True`` on ACL-denied directories.

    Called from ``assert_live_trading_config()`` in ``live_trading_guard.py`` AFTER
    the ``PAPER_TRADING`` early-return but BEFORE the entitlement gate, so the WORM
    dir is guaranteed writable for both the normal live path AND the entitlement
    downgrade branch (which writes a compensating ``disable`` record).

    A raise here is caught by ``__main__.py``'s fail-safe wrapper:
      - Desktop → degrade to PAPER (engine stays alive, positions stay managed)
      - Cloud   → clean ``exit(0)`` (no restart loop)
    """
    log_dir = getattr(_resolve_audit_logger(), "_log_dir", None)
    if log_dir is None:
        log_dir = Path(os.getenv("SENATE_LOG_DIR", "oss_audit_logs"))
    worm_dir = Path(log_dir)

    # Step 1: ensure the directory exists (auto-create on first boot).
    try:
        worm_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"[HITL] BLOCKED: WORM audit dir {worm_dir} cannot be created: {exc}. "
            "Live trading requires a writable audit directory for MiFID II compliance."
        ) from exc

    # Step 2: active write probe — definitively proves writability on all platforms.
    # UUID suffix prevents race conditions when multiple workers (Uvicorn/Gunicorn)
    # execute this check concurrently at boot (static name → WinError 32 / ENOENT).
    import uuid

    probe = worm_dir / f".worm_write_probe_{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"probe")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        # Defensive cleanup — if write_bytes succeeded but unlink raised, remove it.
        probe.unlink(missing_ok=True)
        raise RuntimeError(
            f"[HITL] BLOCKED: WORM audit dir {worm_dir} is not writable: {exc}. "
            "Live trading requires a writable audit directory for MiFID II compliance. "
            "Fix: check filesystem permissions on the directory."
        ) from exc

    logger.info("[HITL] WORM audit dir %s is writable — pre-flight OK.", worm_dir)
