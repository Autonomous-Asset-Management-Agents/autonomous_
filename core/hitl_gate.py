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

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
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
    strict: bool = True,
) -> None:
    """Record a deliberate live-trading enablement/revocation on the Art-14 hash chain (LIVE-1 T4).

    **audit-before-enable**: written BEFORE the desktop shell is allowed to boot the engine live
    (the shell only flips ``SHADOW_MODE`` off once ``verifyAuditChain`` confirms an un-revoked
    ``enable`` record — T1). With ``strict=True`` (the ``/api/live/enable`` default) a failed WORM
    write **re-raises** so the API refuses to report success: capital may never go live on an
    unaudited decision. ``action`` ∈ {enable, disable}.
    """
    try:
        await _resolve_audit_logger().log_hitl_event(
            LiveEnableEvent(
                timestamp=_now(),
                actor=actor,
                action=action,
                acknowledgment=acknowledgment,
                nonce=nonce,
            )
        )
    except Exception as exc:
        logger.warning("[HITL] live-enablement audit failed (%s): %s", action, exc)
        if strict:
            raise


async def should_hold(event: Any, user_id: str = "global") -> bool:
    """Return True to HOLD the order (queued / blocked), False to execute it autonomously.

    Only called when ``HITL_ENABLED``. Performs the queue / day-notional / audit side-effects
    for the chosen branch. **Fail-closed**: on any unexpected error, HOLD — an Art-14 gate
    must never resolve a fault by executing capital autonomously.
    """
    try:
        return await _decide(event, user_id)
    except Exception as exc:
        logger.error(
            "[HITL] gate error → fail-closed HOLD for %s: %s",
            getattr(event, "symbol", "?"),
            exc,
        )
        return True


async def _decide(event: Any, user_id: str) -> bool:
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

    # Resolve order quantity. A "close position" SELL carries suggested_quantity 0; value it
    # via the held position size (magnitude) so a position-closing sell is still measured
    # against the limits.
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
