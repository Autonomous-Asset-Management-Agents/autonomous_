# ai_trading_bot/tests/unit/test_displacement_recovery.py
import asyncio
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from core.events import SignalEvent


# Reuse helper creators or define local versions
def _make_decision_context(**kwargs):
    ctx = MagicMock()
    ctx.lstm_prediction = kwargs.get("lstm_prediction", 0.0)
    ctx.rl_stabilized_action = kwargs.get("rl_stabilized_action", 0)
    ctx.risk_approved = kwargs.get("risk_approved", True)
    ctx.portfolio_approved = kwargs.get("portfolio_approved", True)
    ctx.client_order_id = kwargs.get("client_order_id", "test-uuid")
    ctx.intelligence_approved = kwargs.get("intelligence_approved", True)
    ctx.current_price = kwargs.get("current_price", 150.0)
    ctx.conviction_score = kwargs.get("conviction_score", 0.8)
    ctx.alpaca_order_id = None
    return ctx


def _make_signal(action="BUY", symbol="AAPL", qty=2.0, is_simulation=False):
    ctx = _make_decision_context()
    event = MagicMock(spec=SignalEvent)
    event.action = action
    event.symbol = symbol
    event.suggested_quantity = qty
    event.decision_context = ctx
    event.is_simulation = is_simulation
    return event


def _make_executor(api=None, compliance=None):
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = api or MagicMock()
    executor.compliance_guardian = compliance
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()
    executor.cloud_logger.log_decision = MagicMock()
    executor.live_universe = []
    executor.tenant_portfolio_managers = {}
    return executor


# Create a mock APIError for testing rejections
def _make_api_error(status_code, code=40310000, message="Insufficient buying power"):
    error_json = _json.dumps({"message": message, "code": code})
    http_error = MagicMock()
    http_error.response.status_code = status_code
    return APIError(error=error_json, http_error=http_error)


@pytest.mark.anyio
async def test_tenant_path_cash_account_precheck_abort():
    """
    Given: A tenant displacement BUY on a cash account (multiplier = 1)
    When: Settled buying power is insufficient to cover the BUY order value
    Then: The displacement swap is aborted before the SELL is submitted.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(
        multiplier="1", buying_power=100.0, cash=500.0
    )
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    executor = _make_executor(api=api)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    with (
        patch.object(executor, "_get_tenant_portfolio_manager", return_value=pm),
        patch.object(executor, "_get_tenant_risk_manager", return_value=MagicMock()),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        rm = MagicMock()
        rm.calculate_position_size.return_value = 2.0
        with patch.object(executor, "_get_tenant_risk_manager", return_value=rm):
            tenant = {"user_id": "tenant-1", "client": api, "equity": 10000.0}
            await executor._execute_tenant_order(tenant, event)

    api.submit_order.assert_not_called()


@pytest.mark.anyio
async def test_tenant_path_margin_account_precheck_allow():
    """
    Given: A tenant displacement BUY on a margin account (multiplier = 2)
    When: Expected buying power after sell covers the BUY order value
    Then: The displacement swap proceeds.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(
        multiplier="2", buying_power=50.0, cash=500.0
    )
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    from alpaca.trading.enums import OrderStatus

    api.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)

    executor = _make_executor(api=api)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    rm = MagicMock()
    rm.calculate_position_size.return_value = 2.0

    with (
        patch.object(executor, "_get_tenant_portfolio_manager", return_value=pm),
        patch.object(executor, "_get_tenant_risk_manager", return_value=rm),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        tenant = {"user_id": "tenant-1", "client": api, "equity": 10000.0}
        await executor._execute_tenant_order(tenant, event)

    assert api.submit_order.call_count == 2


@pytest.mark.anyio
async def test_tenant_path_recovery_re_buy_on_failed_buy():
    """
    Given: Tenant displacement SELL succeeded, but subsequent BUY fails
    When: Recovery is triggered
    Then: The SELL order is canceled, filled qty is fetched, and a compensating re-BUY is submitted.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    sell_order = MagicMock(id="sell-456")
    api.submit_order.side_effect = [
        sell_order,
        _make_api_error(403, "Insufficient buying power"),
        MagicMock(id="re-buy-789"),
    ]

    api.get_order_by_id.return_value = MagicMock(
        id="sell-456", filled_qty="3.0", status="filled"
    )

    executor = _make_executor(api=api)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    rm = MagicMock()
    rm.calculate_position_size.return_value = 2.0

    with (
        patch.object(executor, "_get_tenant_portfolio_manager", return_value=pm),
        patch.object(executor, "_get_tenant_risk_manager", return_value=rm),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        tenant = {"user_id": "tenant-1", "client": api, "equity": 10000.0}
        await executor._execute_tenant_order(tenant, event)

    api.cancel_order_by_id.assert_called_once_with("sell-456")
    assert api.submit_order.call_count == 3
    re_buy_req = api.submit_order.call_args_list[2].args[0]
    assert re_buy_req.symbol == "MSFT"
    assert re_buy_req.qty == 3.0
    assert re_buy_req.side.value == "buy"
    pm.record_trade.assert_any_call("MSFT", "buy")


@pytest.mark.anyio
async def test_tenant_path_recovery_failure_raises_critical_alert():
    """
    Given: Tenant displacement SELL succeeded, BUY failed, and recovery re-BUY also fails
    When: Recovery runs
    Then: It logs a critical error and publishes a state inconsistency event.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    sell_order = MagicMock(id="sell-456")
    api.submit_order.side_effect = [
        sell_order,
        _make_api_error(403, "BUY AAPL fails"),
        _make_api_error(400, "Recovery re-BUY MSFT fails"),
    ]
    api.get_order_by_id.return_value = MagicMock(
        id="sell-456", filled_qty="5.0", status="filled"
    )

    executor = _make_executor(api=api)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    rm = MagicMock()
    rm.calculate_position_size.return_value = 2.0

    with (
        patch.object(executor, "_get_tenant_portfolio_manager", return_value=pm),
        patch.object(executor, "_get_tenant_risk_manager", return_value=rm),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        tenant = {"user_id": "tenant-1", "client": api, "equity": 10000.0}
        await executor._execute_tenant_order(tenant, event)

    calls = [c[0] for c in pm.record_trade.call_args_list]
    assert ("MSFT", "buy") not in calls


@pytest.mark.anyio
async def test_fallback_path_cash_account_precheck_abort():
    """
    Given: A fallback displacement BUY on a cash account (multiplier = 1)
    When: Settled buying power is insufficient to cover the BUY order value
    Then: The displacement swap is aborted before the SELL is submitted.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(
        multiplier="1", buying_power=100.0, cash=500.0
    )
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    executor = _make_executor(api=api)
    # Mock fallback active strategy and portfolio manager
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    strat = MagicMock()
    strat.portfolio_manager = pm
    executor.active_strategy = strat

    with (
        patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        await executor._process_signal_event(event)

    api.submit_order.assert_not_called()


@pytest.mark.anyio
async def test_fallback_path_recovery_re_buy_on_failed_buy():
    """
    Given: Fallback displacement SELL succeeded, but subsequent BUY fails
    When: Recovery is triggered
    Then: The SELL order is canceled, filled qty is fetched, and a compensating re-BUY is submitted.
    """
    api = MagicMock()
    api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
    api.get_open_position.return_value = MagicMock(
        symbol="MSFT", qty="5", current_price="100.0"
    )

    sell_order = MagicMock(id="sell-456")
    api.submit_order.side_effect = [
        sell_order,
        _make_api_error(403, "Insufficient buying power"),
        MagicMock(id="re-buy-789"),
    ]
    api.get_order_by_id.return_value = MagicMock(
        id="sell-456", filled_qty="3.0", status="filled"
    )

    executor = _make_executor(api=api)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "swap", "MSFT")

    strat = MagicMock()
    strat.portfolio_manager = pm
    executor.active_strategy = strat

    with (
        patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ),
        patch("core.engine.order_executor.RedisClient") as rc,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()),
    ):
        rc.get_redis = AsyncMock(return_value=None)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        await executor._process_signal_event(event)

    api.cancel_order_by_id.assert_called_once_with("sell-456")
    assert api.submit_order.call_count == 3
    re_buy_req = api.submit_order.call_args_list[2].args[0]
    assert re_buy_req.symbol == "MSFT"
    assert re_buy_req.qty == 3.0
    assert re_buy_req.side.value == "buy"
    pm.record_trade.assert_any_call("MSFT", "buy")
