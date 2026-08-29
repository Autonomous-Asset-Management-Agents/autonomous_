# tests/unit/test_order_executor_vix_sizing.py
"""#2958 — regression pin: the context's vix_level is what sizing actually sees.

The executor side was always wired (getattr(context, "vix_level", 20.0) ->
market_data={"vix": ...} -> ADR-R08 ladder in risk_manager). The defect sat one
layer up: the runner never filled the context, so this path fed the frozen 20.0
into every sizing call. This test pins the executor half of the contract so a
future refactor cannot silently disconnect context -> market_data again.

Harness mirrors test_order_executor_size_zero.py (drive _execute_tenant_order
with a mocked risk manager; rm_size=0.0 early-returns right AFTER the sizing
call we assert on — no compliance/submit mocking needed).
"""
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


def _make_executor():
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = MagicMock()
    executor.compliance_guardian = MagicMock()
    executor.live_universe = []
    return executor


def _make_signal(vix_level):
    ctx = MagicMock()
    ctx.current_price = 150.0
    ctx.conviction_score = 0.8
    ctx.atr_14d = 1.0
    ctx.vix_level = vix_level
    ctx.lstm_prediction = 0.0
    ctx.client_order_id = "coid"
    ctx.alpaca_order_id = None
    event = MagicMock(spec=SignalEvent)
    event.action = "BUY"
    event.symbol = "AAPL"
    event.suggested_quantity = 1.0
    event.decision_context = ctx
    event.is_simulation = False
    return event


def _buy_tenant():
    client = MagicMock()
    acct = MagicMock()
    acct.cash = 100000.0
    client.get_account.return_value = acct
    return {"user_id": "u1", "client": client, "equity": 100000.0}


def _drive(executor, tenant, event):
    rm = MagicMock()
    rm.calculate_position_size.return_value = 0.0  # early-return after sizing
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
        _run(executor._execute_tenant_order(tenant, event))
    return rm


def test_context_vix_reaches_risk_manager_market_data():
    executor = _make_executor()
    rm = _drive(executor, _buy_tenant(), _make_signal(vix_level=45.0))
    rm.calculate_position_size.assert_called_once()
    market_data = rm.calculate_position_size.call_args.kwargs["market_data"]
    assert market_data == {
        "vix": 45.0
    }, "the context's vix_level must reach the ADR-R08 ladder unchanged"


def test_low_vix_context_reaches_risk_manager_market_data():
    executor = _make_executor()
    rm = _drive(executor, _buy_tenant(), _make_signal(vix_level=13.19))
    market_data = rm.calculate_position_size.call_args.kwargs["market_data"]
    assert market_data == {"vix": 13.19}
