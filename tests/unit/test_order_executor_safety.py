from unittest.mock import MagicMock, patch

import allure
import pytest

from core.cloud_logger import DecisionContext
from core.events import SignalEvent


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_order_executor_warns_on_zero_price(caplog):
    """Verify that _execute_tenant_order returns early and logs a warning if price is 0.0.

    Uses synchronous inspection: we patch context.current_price = 0.0 and confirm
    the function exits before submit_order is called.
    """
    import asyncio

    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = MagicMock()
    executor.compliance_guardian = None
    executor.live_universe = []
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()

    # Build a tenant dict as expected by _execute_tenant_order
    client_mock = MagicMock()
    tenant = {"user_id": "user1", "client": client_mock, "equity": 10000.0}

    # context with price = 0.0 → should trigger early return + warning
    context = DecisionContext(symbol="CPRT", action="BUY", conviction_score=0.653)
    # DecisionContext.current_price defaults to 0.0 unless explicitly provided
    event = SignalEvent(symbol="CPRT", action="BUY", decision_context=context)

    import logging

    with caplog.at_level(logging.WARNING, logger="root"):
        asyncio.run(executor._execute_tenant_order(tenant, event))

    # Should have returned early — no submit_order call
    client_mock.submit_order.assert_not_called()

    # Warning should mention missing price
    assert any(
        "Missing current_price" in record.message or "Skipping" in record.message
        for record in caplog.records
    ), f"Expected price warning in logs, got: {[r.message for r in caplog.records]}"
