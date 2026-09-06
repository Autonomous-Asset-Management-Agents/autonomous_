# tests/unit/test_hitl_gate.py
# ii-4b (PR-0a-ii, GAP2): the HITL threshold gate decision matrix (EU AI Act Art. 14).
#
# should_hold(event, user_id) is the single order-path chokepoint that decides whether a
# real-money BUY/SELL may execute autonomously (return False) or must be HELD / queued for
# human approval (return True). It is only ever called when HITL_ENABLED, so with HITL off
# the gate is never reached and behaviour is byte-identical (dormant).
#
# These tests pin the decision matrix in isolation: the queue, day-notional counter and
# audit logger are all patched so we assert the *decision* and which side-effects fire per
# branch — not the storage backends (those are covered by ii-1 / ii-4a / ii-2).
from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))


def _run(coro):
    return asyncio.run(coro)


def _cfg(
    *,
    per_trade=10_000.0,
    per_day=50_000.0,
    unlimited=False,
    risk_off_exempt=False,
    enabled=True,
    expiry=900,
):
    return SimpleNamespace(
        HITL_ENABLED=enabled,
        HITL_MAX_VALUE_PER_TRADE=per_trade,
        HITL_MAX_VALUE_PER_DAY=per_day,
        HITL_AUTONOMOUS_UNLIMITED=unlimited,
        HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS=risk_off_exempt,
        HITL_EXPIRY_SECONDS=expiry,
    )


def _event(
    *,
    symbol="AAPL",
    action="BUY",
    suggested_quantity=10.0,
    current_price=100.0,
    position_qty=0.0,
    conviction_score=0.7,
    target_weight=0.05,
):
    ctx = SimpleNamespace(
        current_price=current_price,
        position_qty=position_qty,
        conviction_score=conviction_score,
        target_weight=target_weight,
    )
    return SimpleNamespace(
        symbol=symbol,
        action=action,
        suggested_quantity=suggested_quantity,
        decision_context=ctx,
    )


@contextmanager
def _gate_env(
    cfg,
    *,
    day_notional=0.0,
    has_pending=False,
    push_returns="approval-1",
):
    """Patch the gate's collaborators; yield the captured audit logger + the queue mock."""
    audit = SimpleNamespace(log_hitl_event=AsyncMock())
    push = AsyncMock(return_value=push_returns)
    with patch("core.hitl_gate.get_config", return_value=cfg), patch(
        "core.hitl_gate._resolve_audit_logger", return_value=audit
    ), patch(
        "core.hitl_gate.HitlQueue.has_pending",
        AsyncMock(return_value=has_pending),
    ), patch(
        "core.hitl_gate.HitlQueue.push", push
    ), patch(
        "core.hitl_gate.HitlDayNotional.current",
        AsyncMock(return_value=day_notional),
    ), patch(
        "core.hitl_gate.HitlDayNotional.add",
        AsyncMock(return_value=day_notional),
    ) as add:
        yield SimpleNamespace(audit=audit, push=push, add=add)


def _last_event(env):
    """The HITLExecutionEvent passed to the most recent log_hitl_event call."""
    assert env.audit.log_hitl_event.await_count >= 1
    return env.audit.log_hitl_event.await_args.args[0]


# ── threshold mode (the primary mode) ──────────────────────────────────────────


def test_under_both_limits_executes_and_books_notional():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, per_day=50_000.0)
    ev = _event(suggested_quantity=10.0, current_price=100.0)  # value 1_000
    with _gate_env(cfg, day_notional=0.0) as env:
        assert _run(should_hold(ev, "global")) is False  # execute
        env.add.assert_awaited_once()  # booked the notional
        env.push.assert_not_awaited()  # not queued
        evt = _last_event(env)
        assert evt.branch == "under_limit"
        assert evt.day_notional_after is not None


def test_over_per_trade_queues():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, per_day=50_000.0)
    ev = _event(suggested_quantity=200.0, current_price=100.0)  # value 20_000
    with _gate_env(cfg, day_notional=0.0) as env:
        assert _run(should_hold(ev, "global")) is True  # HOLD
        env.push.assert_awaited_once()
        env.add.assert_not_awaited()  # autonomous budget untouched when queued
        evt = _last_event(env)
        assert evt.branch == "queued"
        assert evt.threshold_breached == "per_trade"


def test_over_per_day_queues():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, per_day=50_000.0)
    ev = _event(
        suggested_quantity=30.0, current_price=100.0
    )  # value 3_000, under per-trade
    with _gate_env(cfg, day_notional=48_000.0) as env:  # 48k + 3k = 51k > 50k
        assert _run(should_hold(ev, "global")) is True
        env.push.assert_awaited_once()
        evt = _last_event(env)
        assert evt.branch == "queued"
        assert evt.threshold_breached == "per_day"


def test_all_manual_mode_queues_everything():
    # both limits 0 ⇒ every order is over the per-trade limit ⇒ always queued (Mode A).
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=0.0, per_day=0.0)
    ev = _event(suggested_quantity=1.0, current_price=100.0)  # value 100
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is True
        env.push.assert_awaited_once()
        assert _last_event(env).branch == "queued"


# ── Mode C: fully autonomous ────────────────────────────────────────────────────


def test_unlimited_mode_executes_without_queue_or_booking():
    from core.hitl_gate import should_hold

    cfg = _cfg(unlimited=True)
    ev = _event(suggested_quantity=10_000.0, current_price=100.0)  # value 1_000_000
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is False
        env.push.assert_not_awaited()
        env.add.assert_not_awaited()
        assert _last_event(env).branch == "under_limit"


# ── risk-reducing SELL exemption (a switch) ─────────────────────────────────────


def test_risk_reducing_sell_exempt_when_switch_on():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, risk_off_exempt=True)
    ev = _event(
        action="SELL",
        suggested_quantity=1_000.0,
        current_price=100.0,
        position_qty=1_000.0,
    )  # value 100_000 ≫ limit, but risk-reducing
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is False  # execute
        env.push.assert_not_awaited()
        assert _last_event(env).branch == "risk_off_exempt"


def test_risk_reducing_sell_queued_when_switch_off():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, risk_off_exempt=False)
    ev = _event(
        action="SELL",
        suggested_quantity=1_000.0,
        current_price=100.0,
        position_qty=1_000.0,
    )
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is True
        env.push.assert_awaited_once()
        assert _last_event(env).branch == "queued"


def test_short_increasing_sell_not_exempted_as_risk_reducing():
    # A SELL against a SHORT position (position_qty < 0) INCREASES risk; it must NOT be
    # treated as risk-reducing even with the exemption switch on — abs() must not hide the sign.
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, risk_off_exempt=True)
    ev = _event(
        action="SELL",
        suggested_quantity=1_000.0,
        current_price=100.0,
        position_qty=-1_000.0,  # short
    )  # value 100_000 ≫ limit, risk-INCREASING
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is True  # HOLD, not exempt
        env.push.assert_awaited_once()
        assert _last_event(env).branch == "queued"


# ── SELL quantity resolution ────────────────────────────────────────────────────


def test_sell_qty_resolved_from_position_when_suggested_zero():
    # a "close position" SELL carries suggested_quantity 0; the gate values it via position_qty.
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0, per_day=50_000.0)
    ev = _event(
        action="SELL", suggested_quantity=0.0, current_price=100.0, position_qty=5.0
    )
    with _gate_env(cfg, day_notional=0.0) as env:
        assert _run(should_hold(ev, "global")) is False  # value 500 < limit ⇒ execute
        env.add.assert_awaited_once()
        booked = env.add.await_args.args[1]  # add(ny_date, amount)
        assert booked == 500.0  # 5 * 100, resolved from position_qty


def test_unknown_size_sell_queued_fail_safe():
    # SELL with neither suggested_quantity nor position_qty ⇒ cannot value ⇒ queue (conservative).
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0)
    ev = _event(
        action="SELL", suggested_quantity=0.0, current_price=100.0, position_qty=0.0
    )
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is True
        env.push.assert_awaited_once()
        evt = _last_event(env)
        assert evt.branch == "queued"
        assert evt.threshold_breached == "unknown_sell_size"


def test_zero_value_order_queued_not_executed():
    # A BUY with no price ⇒ order_value 0. In all-manual mode (limits 0) `0 > 0` is False, so a
    # naive threshold test would EXECUTE it — escaping the gate. It must be queued instead.
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=0.0, per_day=0.0)  # all-manual
    ev = _event(action="BUY", suggested_quantity=10.0, current_price=0.0)  # value 0
    with _gate_env(cfg) as env:
        assert _run(should_hold(ev, "global")) is True
        env.add.assert_not_awaited()  # nothing booked / executed
        env.push.assert_awaited_once()
        evt = _last_event(env)
        assert evt.branch == "queued"
        assert evt.threshold_breached == "unknown_value"


# ── idempotency: do not re-queue a symbol already awaiting approval (C4) ─────────


def test_already_pending_holds_without_requeue():
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=0.0)  # would otherwise queue
    ev = _event(suggested_quantity=200.0, current_price=100.0)
    with _gate_env(cfg, has_pending=True) as env:
        assert _run(should_hold(ev, "global")) is True
        env.push.assert_not_awaited()  # no duplicate queue entry


# ── fail-closed behaviour ───────────────────────────────────────────────────────


def test_queue_push_failure_fails_closed_to_hold():
    # over-limit order, but the queue is unavailable (redis down ⇒ push returns None):
    # the order must still be HELD, never executed — fail-closed.
    from core.hitl_gate import should_hold

    cfg = _cfg(per_trade=10_000.0)
    ev = _event(suggested_quantity=200.0, current_price=100.0)  # value 20_000
    with _gate_env(cfg, push_returns=None) as env:
        assert _run(should_hold(ev, "global")) is True
        evt = _last_event(env)
        assert evt.branch == "queued"
        assert evt.reason == "queue_unavailable_fail_closed"


def test_gate_exception_fails_closed_to_hold():
    from core.hitl_gate import should_hold

    ev = _event()
    with patch("core.hitl_gate.get_config", side_effect=RuntimeError("boom")):
        assert _run(should_hold(ev, "global")) is True  # any error ⇒ HOLD


# ── policy hash (stamped on every execution event; reused by ii-6) ───────────────


def test_policy_hash_stable_and_sensitive():
    from core.hitl_gate import policy_hash, policy_snapshot

    base = _cfg(per_trade=10_000.0)
    changed = _cfg(per_trade=20_000.0)
    assert policy_hash(policy_snapshot(base)) == policy_hash(policy_snapshot(base))
    assert policy_hash(policy_snapshot(base)) != policy_hash(policy_snapshot(changed))
