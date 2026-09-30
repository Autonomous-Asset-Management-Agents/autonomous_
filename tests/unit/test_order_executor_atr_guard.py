# tests/unit/test_order_executor_atr_guard.py
# #2389 (Epic RTR #1958) — TDD Red→Green: atr<=0 sizing guard (Inc 1, always active).
#
# Forensics over 8017 live decisions: the neutral default snapshot carries
# atr_14d=0.0, so at the FIRST sizing call-site (_execute_tenant_order) the
# `getattr(context, "atr_14d", curr * 0.05)` default never fired — the attribute
# EXISTS as 0.0 — and RiskManager.calculate_position_size returned 0 shares
# (silent no-trade at the `size <= 0` return). The SECOND call-site (the global
# fallback path in _process_signal_event) already guards `atr <= 0 → curr * 0.05`.
# Inc 1 brings the first call-site to parity with that audited convention.
#
# Gherkin (plan #2389 §7, Scenario "Sizing-Unblock"):
#   Given a BUY with context.atr_14d = 0.0 and current_price = 300
#   When  order_executor sizes the position (tenant path)
#   Then  atr is set to curr*0.05 (=15.0) — parity with the global fallback path
#   And   calculate_position_size yields > 0 shares (no silent return)

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events import SignalEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(atr_14d: float, current_price: float = 300.0):
    """Real-attribute context (SimpleNamespace, not MagicMock): the bug under test
    is that `atr_14d` EXISTS as 0.0 — a MagicMock auto-attribute would mask it."""
    return SimpleNamespace(
        atr_14d=atr_14d,
        current_price=current_price,
        vix_level=20.0,
        conviction_score=0.8,
        lstm_prediction=0.7,
        rl_stabilized_action=1,
        risk_approved=True,
        portfolio_approved=True,
        intelligence_approved=True,
        client_order_id="test-uuid",
        alpaca_order_id=None,
        action_executed=False,
    )


def _make_signal(atr_14d: float, current_price: float = 300.0, qty: float = 0.0):
    event = MagicMock(spec=SignalEvent)
    event.action = "BUY"
    event.symbol = "AAPL"
    event.suggested_quantity = qty
    event.decision_context = _make_context(atr_14d, current_price)
    event.is_simulation = False
    return event


def _make_executor():
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = MagicMock()
    executor.compliance_guardian = None
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()
    executor.cloud_logger.log_decision = MagicMock()
    executor.live_universe = []
    return executor


def _redis_mock():
    redis_mock = MagicMock()
    redis_mock.publish = AsyncMock()
    redis_mock.lock = MagicMock(
        return_value=MagicMock(
            acquire=AsyncMock(return_value=True),
            release=AsyncMock(return_value=True),
        )
    )
    return redis_mock


def _filled_client():
    """Broker client mock whose order fills immediately — otherwise the EXC-1
    lifecycle polling loop spins for up to 120s against a never-FILLED MagicMock."""
    from alpaca.trading.enums import OrderStatus

    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="order-2389")
    client.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)
    return client


async def _run_tenant_path(
    atr_14d: float, current_price: float = 300.0, real_sizer_contract: bool = False
):
    """Drive _execute_tenant_order to the BUY sizing branch; return (rm, client).

    real_sizer_contract=True mirrors RiskManager.calculate_position_size's own
    guard (`atr is None or atr <= 0 → 0.0 shares`) so the silent `size <= 0`
    return is reachable exactly like with the real sizer."""
    executor = _make_executor()
    rm = MagicMock()
    if real_sizer_contract:
        rm.calculate_position_size.side_effect = lambda **kw: (
            0.0 if (kw.get("atr") is None or kw["atr"] <= 0) else 5.0
        )
    else:
        rm.calculate_position_size.return_value = 5.0
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    pm = MagicMock()
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = (True, "OK", None)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

    client = _filled_client()
    tenant = {"user_id": "user-1", "client": client, "equity": 10000.0}
    event = _make_signal(atr_14d, current_price)

    with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
        "core.engine.order_executor.config.SHADOW_MODE", False, create=True
    ):
        mock_redis.get_redis = AsyncMock(return_value=_redis_mock())
        await executor._execute_tenant_order(tenant, event)
    return rm, client


async def _run_global_path(atr_14d: float, current_price: float = 300.0):
    """Drive the single-tenant fallback in _process_signal_event (the call-site
    that ALREADY guards atr<=0) to the live_risk_manager sizing; return the rm."""
    executor = _make_executor()
    executor.api = _filled_client()
    lrm = MagicMock()
    lrm.calculate_position_size.return_value = 5.0
    executor.live_risk_manager = lrm
    executor._market_closed_blocks_order = AsyncMock(return_value=False)

    event = _make_signal(atr_14d, current_price, qty=0.0)

    with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
        "core.engine.order_executor.config.SHADOW_MODE", False, create=True
    ), patch.object(
        executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
    ):
        mock_redis.get_redis = AsyncMock(return_value=_redis_mock())
        await executor._process_signal_event(event)
    return lrm


def _atr_passed_to(rm) -> float:
    assert rm.calculate_position_size.called, "sizer was never called"
    return rm.calculate_position_size.call_args.kwargs["atr"]


# ---------------------------------------------------------------------------
# R7: atr=0 → sizing uses curr*0.05, yields >0 shares (no silent return)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r7_atr_zero_falls_back_to_five_percent_of_price():
    """Given context.atr_14d = 0.0 (the neutral default snapshot) and curr = 300,
    Then the tenant sizing call receives atr = 15.0 — NOT 0.0."""
    rm, _ = await _run_tenant_path(atr_14d=0.0, current_price=300.0)
    assert _atr_passed_to(rm) == pytest.approx(15.0)


@pytest.mark.anyio
async def test_r7_atr_zero_no_silent_zero_size_return():
    """With the guard, the (contract-faithful) sizer yields > 0 shares and the
    order is submitted — no silent `size <= 0` return. Without the guard the
    sizer receives atr=0.0 → 0 shares → the BUY dies silently before the broker."""
    rm, client = await _run_tenant_path(
        atr_14d=0.0, current_price=300.0, real_sizer_contract=True
    )
    client.submit_order.assert_called()


@pytest.mark.anyio
async def test_r7_atr_negative_also_guarded():
    """A (corrupt) negative ATR is guarded exactly like 0.0."""
    rm, _ = await _run_tenant_path(atr_14d=-1.0, current_price=300.0)
    assert _atr_passed_to(rm) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# R8: atr>0 → unchanged (real ATR is passed through untouched)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r8_real_atr_passed_through_unchanged():
    rm, _ = await _run_tenant_path(atr_14d=2.5, current_price=300.0)
    assert _atr_passed_to(rm) == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# R9: parity — the tenant call-site behaves exactly like the global fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r9_tenant_and_global_call_sites_are_at_parity():
    """Same context (atr_14d=0.0, curr=300) through BOTH sizing call-sites →
    both must hand the SAME guarded atr (curr*0.05) to their RiskManager."""
    tenant_rm, _ = await _run_tenant_path(atr_14d=0.0, current_price=300.0)
    global_rm = await _run_global_path(atr_14d=0.0, current_price=300.0)
    assert _atr_passed_to(tenant_rm) == pytest.approx(_atr_passed_to(global_rm))
    assert _atr_passed_to(global_rm) == pytest.approx(15.0)


@pytest.mark.anyio
async def test_r9_parity_holds_for_real_atr_too():
    tenant_rm, _ = await _run_tenant_path(atr_14d=3.7, current_price=300.0)
    global_rm = await _run_global_path(atr_14d=3.7, current_price=300.0)
    assert _atr_passed_to(tenant_rm) == pytest.approx(3.7)
    assert _atr_passed_to(global_rm) == pytest.approx(3.7)
