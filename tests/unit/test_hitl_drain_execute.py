# tests/unit/test_hitl_drain_execute.py
# ii-5a (PR-0a-ii, GAP2): the human-approval EXECUTOR (EU AI Act Art. 14).
#
# `execute_approved_order(payload)` takes a drained HitlQueue payload and executes it through
# the SAME pipeline as an autonomous order, but bypassing the HITL gate (N1: routing it back
# through _process_signal_event would re-queue it). It resolves the tenant by user_id, builds
# a synthetic SignalEvent from the payload (N8), delegates to _execute_tenant_order with
# source="human_approved" (which skips ONLY the daily-cap gate + the autonomous-budget
# increment, ii-3/§4.5), and audits the outcome on the Art-14 hash chain
# (approved / iron_dome_rejected / rejected). Dormant until the ii-5b drain wires it.
#
# Decision-2 Option B (N11): on an OSS engine with no OAuth tenant, it HOLDs + warns + audits
# "rejected" — it never reuses the inline global path.
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.events import SignalEvent  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_executor(compliance=None):
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = MagicMock()
    executor.compliance_guardian = compliance
    executor.live_universe = []
    return executor


def _payload(**kw):
    p = {
        "approval_id": "appr-1",
        "user_id": "u1",
        "symbol": "AAPL",
        "action": "BUY",
        "qty": 3.0,
        "price": 100.0,
        "conviction": 0.7,
        "target_weight": 0.05,
    }
    p.update(kw)
    return p


def _audit_patches():
    """Patch hitl_gate's audit + policy helpers; return the log_execution_event AsyncMock."""
    audit = AsyncMock()
    return (
        audit,
        patch("core.hitl_gate.log_execution_event", audit),
        patch("core.hitl_gate.policy_snapshot", return_value={}),
        patch("core.hitl_gate.policy_hash", return_value="pol-hash"),
    )


def _last_branch(audit):
    return audit.await_args.args[0].branch


# ── execute_approved_order: resolution + delegation + audit ──────────────────────


def test_approved_order_resolves_tenant_and_delegates_human_approved():
    executor = _make_executor()
    tenant = {"user_id": "u1", "client": MagicMock(), "equity": 1000.0}
    executor.get_active_tenant_clients = AsyncMock(return_value=[tenant])
    executor._execute_tenant_order = AsyncMock(return_value=True)  # submitted

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload()))

    assert result is True
    executor._execute_tenant_order.assert_awaited_once()
    args, kwargs = executor._execute_tenant_order.await_args
    assert kwargs.get("source") == "human_approved"
    assert args[0]["user_id"] == "u1"  # the matching tenant, not a fan-out
    assert _last_branch(audit) == "approved"


def test_approved_order_builds_synthetic_signal_event_from_payload():
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(
            executor.execute_approved_order(
                _payload(qty=3.0, price=100.0, conviction=0.7)
            )
        )

    event = executor._execute_tenant_order.await_args.args[1]
    assert isinstance(event, SignalEvent)
    assert event.action == "BUY"
    assert event.symbol == "AAPL"
    assert event.suggested_quantity == 3.0
    assert event.is_simulation is False
    ctx = event.decision_context
    assert ctx.current_price == 100.0
    assert ctx.conviction_score == 0.7
    # human approval overrides the AI gates
    assert ctx.risk_approved is True
    assert ctx.portfolio_approved is True
    assert ctx.intelligence_approved is True
    # broker idempotency key derived deterministically from the approval_id (DD F3)
    assert ctx.client_order_id == "hitl-appr-1"


def test_approved_order_client_order_id_is_deterministic_from_approval_id():
    # DD F3: the broker client_order_id must be a stable function of the approval_id so a
    # re-submission of the SAME approved order is deduped by the broker (never executes twice).
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(_payload(approval_id="appr-XYZ")))
        cid1 = executor._execute_tenant_order.await_args.args[
            1
        ].decision_context.client_order_id
        _run(executor.execute_approved_order(_payload(approval_id="appr-XYZ")))
        cid2 = executor._execute_tenant_order.await_args.args[
            1
        ].decision_context.client_order_id

    assert cid1 == cid2 == "hitl-appr-XYZ"  # same approval → same broker id → deduped


def test_approved_order_without_approval_id_keeps_default_uuid_client_order_id():
    # When the payload carries no approval_id the deterministic stamp is skipped and the
    # DecisionContext's default uuid4 client_order_id stands — no crash, never an empty id.
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)
    p = _payload()
    p.pop("approval_id")

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(p))

    cid = executor._execute_tenant_order.await_args.args[
        1
    ].decision_context.client_order_id
    assert cid and not cid.startswith(
        "hitl-"
    )  # default uuid4, not the deterministic stamp


def test_oss_no_tenant_holds_and_audits_rejected():
    # Decision-2 Option B: no OAuth tenant ⇒ HOLD, never execute, audit "rejected".
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(return_value=[])
    executor._execute_tenant_order = AsyncMock()

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload()))

    assert result is False
    executor._execute_tenant_order.assert_not_awaited()
    assert _last_branch(audit) == "rejected"


def test_unknown_user_id_holds_and_audits_rejected():
    # tenants exist but none match payload["user_id"] ⇒ do NOT fan out to others; HOLD.
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "someone-else", "client": MagicMock(), "equity": 1.0}]
    )
    executor._execute_tenant_order = AsyncMock()

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload(user_id="u1")))

    assert result is False
    executor._execute_tenant_order.assert_not_awaited()
    assert _last_branch(audit) == "rejected"


def test_iron_dome_rejection_is_audited_not_silently_dropped():
    # P3: if _execute_tenant_order does not submit (e.g. check_order/max_order_value blocks),
    # audit "iron_dome_rejected" — never silently drop a human-approved order.
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=False)  # not submitted

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload()))

    assert result is False
    assert _last_branch(audit) == "iron_dome_rejected"


# ── _execute_tenant_order(source=): cap-skip + budget-skip for human approvals ───


def _make_signal(action="BUY", symbol="AAPL", qty=1.0):
    ctx = MagicMock()
    ctx.current_price = 150.0
    ctx.conviction_score = 0.8
    ctx.atr_14d = 1.0
    ctx.vix_level = 20.0
    ctx.lstm_prediction = 0.0
    ctx.client_order_id = "coid"
    ctx.alpaca_order_id = None
    event = MagicMock(spec=SignalEvent)
    event.action = action
    event.symbol = symbol
    event.suggested_quantity = qty
    event.decision_context = ctx
    event.is_simulation = False
    return event


def _buy_tenant():
    client = MagicMock()
    acct = MagicMock()
    acct.cash = 100000.0
    client.get_account.return_value = acct
    client.submit_order.side_effect = Exception("stop-after-increment")
    return {"user_id": "u1", "client": client, "equity": 100000.0}


def _drive_buy_to_increment(executor, tenant, event, source_kwarg, rm_size=1.0):
    """Drive _execute_tenant_order through a BUY to the check_trade/increment region, then
    abort cleanly at submit_order (so the broker polling loop is never exercised)."""
    rm = MagicMock()
    rm.calculate_position_size.return_value = rm_size
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    pm = MagicMock()
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = (True, "OK", None)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)
    redis = MagicMock(
        publish=AsyncMock(),
        lock=MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        ),
    )
    with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
        "core.engine.order_executor.restore_pm_state_from_redis", AsyncMock()
    ):
        mock_redis.get_redis = AsyncMock(return_value=redis)
        _run(executor._execute_tenant_order(tenant, event, **source_kwarg))


def test_human_approved_skips_cap_and_budget_increment():
    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    guardian.daily_trades = 0
    executor = _make_executor(compliance=guardian)
    _drive_buy_to_increment(
        executor, _buy_tenant(), _make_signal("BUY"), {"source": "human_approved"}
    )
    # check_trade was told the order is human-approved
    assert guardian.check_trade.call_args.kwargs.get("source") == "human_approved"
    # and the autonomous daily-trade budget was NOT consumed. #1849: the budget is
    # now consumed via the atomic record_trade() increment, so it must NOT be called.
    guardian.record_trade.assert_not_called()
    assert guardian.daily_trades == 0


def test_autonomous_default_increments_budget():
    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    guardian.daily_trades = 0
    executor = _make_executor(compliance=guardian)
    _drive_buy_to_increment(
        executor, _buy_tenant(), _make_signal("BUY"), {}
    )  # default source
    # #1849: the autonomous path consumes one budget unit via the atomic,
    # lock-guarded record_trade() (replacing the racy ``daily_trades += 1``).
    guardian.record_trade.assert_called_once()


def test_submitted_but_unfilled_order_reports_as_submitted():
    # An order that reaches the broker but is cancelled for non-fill DID reach the market —
    # _execute_tenant_order returns True so the drain audits it "approved", not iron_dome.
    from alpaca.trading.enums import OrderStatus

    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    guardian.daily_trades = 0
    executor = _make_executor(compliance=guardian)

    client = MagicMock()
    acct = MagicMock()
    acct.cash = 100000.0
    client.get_account.return_value = acct
    submitted_order = MagicMock()
    submitted_order.id = "ord-1"
    client.submit_order.return_value = submitted_order  # submit SUCCEEDS
    cancelled = MagicMock()
    cancelled.status = OrderStatus.CANCELED  # polling sees non-fill → cancel path
    client.get_order_by_id.return_value = cancelled
    tenant = {"user_id": "u1", "client": client, "equity": 100000.0}

    rm = MagicMock()
    rm.calculate_position_size.return_value = 1.0
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    pm = MagicMock()
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = (True, "OK", None)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)
    redis = MagicMock(
        publish=AsyncMock(),
        lock=MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        ),
    )
    with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
        "core.engine.order_executor.restore_pm_state_from_redis", AsyncMock()
    ):
        mock_redis.get_redis = AsyncMock(return_value=redis)
        result = _run(
            executor._execute_tenant_order(
                tenant, _make_signal("BUY"), source="human_approved"
            )
        )

    assert result is True  # reached the broker ⇒ "submitted" (audited "approved")


# ── ii-5c: execution-path hardening (audit-before, ADR-016 ceiling, malformed floor) ──


def test_approved_audited_before_execution():
    # BLOCKER: the "approved" record must hit the immutable chain BEFORE the order can reach the
    # broker — capital must never move without a prior Art-14 record.
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    manager = MagicMock()
    manager.attach_mock(audit, "audit")
    manager.attach_mock(executor._execute_tenant_order, "execute")
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(_payload()))

    names = [c[0] for c in manager.mock_calls]
    assert names.index("audit") < names.index("execute")  # audit fired first
    assert audit.await_args_list[0].args[0].branch == "approved"


def test_human_approved_order_capped_at_approved_quantity():
    # ADR-016 (EU AI Act Art. 14 + MiFID II RTS 6): the engine executes AT MOST the approved
    # quantity — RiskManager may only REDUCE it, never size ABOVE what the human authorised.
    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    guardian.daily_trades = 0
    executor = _make_executor(compliance=guardian)
    tenant = _buy_tenant()  # submit_order raises → aborts after the request is built
    _drive_buy_to_increment(
        executor,
        tenant,
        _make_signal("BUY", qty=3.0),  # human approved 3 shares
        {"source": "human_approved"},
        rm_size=100.0,  # RiskManager WANTS 100 — must be ignored above the ceiling
    )
    req = tenant["client"].submit_order.call_args.args[0]
    assert req.qty == 3.0  # capped to the approved 3, NOT the RM's 100


def test_autonomous_order_is_not_capped():
    # The ceiling applies ONLY to human-approved orders; autonomous sizing is unchanged.
    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    guardian.daily_trades = 0
    executor = _make_executor(compliance=guardian)
    tenant = _buy_tenant()
    _drive_buy_to_increment(
        executor,
        tenant,
        _make_signal("BUY", qty=3.0),
        {},
        rm_size=100.0,  # default source
    )
    req = tenant["client"].submit_order.call_args.args[0]
    assert req.qty == 100.0  # autonomous path uses the RM size, uncapped


def test_malformed_payload_zero_price_rejected_not_executed():
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload(price=0.0)))

    assert result is False
    executor._execute_tenant_order.assert_not_awaited()  # never reaches the broker path
    evt = audit.await_args.args[0]
    assert evt.branch == "rejected"
    assert evt.reason == "malformed_payload"


def test_malformed_payload_buy_zero_qty_rejected():
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u1", "client": MagicMock(), "equity": 1000.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        result = _run(executor.execute_approved_order(_payload(action="BUY", qty=0.0)))

    assert result is False
    executor._execute_tenant_order.assert_not_awaited()
    assert audit.await_args.args[0].reason == "malformed_payload"


# ── ii-5c: the ceiling on the SELL side (cap to approved; never oversell; close-all) ──


def _sell_tenant(held_qty=10.0):
    client = MagicMock()
    client.get_open_position.return_value = MagicMock(qty=held_qty)
    client.submit_order.side_effect = Exception("stop-after-request")
    return {"user_id": "u1", "client": client, "equity": 100000.0}


def _drive_sell(executor, tenant, event, source_kwarg):
    """Drive _execute_tenant_order through a SELL to the request build, abort at submit_order."""
    executor._get_tenant_risk_manager = MagicMock(return_value=MagicMock())
    pm = MagicMock()
    pm.can_sell_position.return_value = (True, "")
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)
    redis = MagicMock(
        publish=AsyncMock(),
        lock=MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        ),
    )
    with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
        "core.engine.order_executor.restore_pm_state_from_redis", AsyncMock()
    ):
        mock_redis.get_redis = AsyncMock(return_value=redis)
        _run(executor._execute_tenant_order(tenant, event, **source_kwarg))


def _guardian_ok():
    g = MagicMock()
    g.check_order.return_value = True
    g.check_trade.return_value = True
    g.daily_trades = 0
    return g


def test_sell_ceiling_caps_to_approved_quantity():
    # human approved selling 2 of a 10-share position ⇒ execute exactly 2, never the full 10.
    executor = _make_executor(compliance=_guardian_ok())
    tenant = _sell_tenant(held_qty=10.0)
    _drive_sell(
        executor, tenant, _make_signal("SELL", qty=2.0), {"source": "human_approved"}
    )
    assert tenant["client"].submit_order.call_args.args[0].qty == 2.0


def test_sell_ceiling_cannot_oversell_beyond_held():
    # approved 100 but only 10 held ⇒ min(held=10, approved=100) = 10; never oversell.
    executor = _make_executor(compliance=_guardian_ok())
    tenant = _sell_tenant(held_qty=10.0)
    _drive_sell(
        executor, tenant, _make_signal("SELL", qty=100.0), {"source": "human_approved"}
    )
    assert tenant["client"].submit_order.call_args.args[0].qty == 10.0


def test_sell_close_position_zero_qty_sells_full_position():
    # a "close position" approval carries qty 0 ⇒ ceiling skipped ⇒ sell the full held position.
    executor = _make_executor(compliance=_guardian_ok())
    tenant = _sell_tenant(held_qty=10.0)
    _drive_sell(
        executor, tenant, _make_signal("SELL", qty=0.0), {"source": "human_approved"}
    )
    assert tenant["client"].submit_order.call_args.args[0].qty == 10.0
