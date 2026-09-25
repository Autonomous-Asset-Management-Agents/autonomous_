# tests/unit/test_order_executor_size_zero.py
# #2069 Gap-Fix — the PRIMARY risk block (calculated size <= 0) must be RECORDED.
#
# The size<=0 guard in _execute_tenant_order published a trade_rejected event to
# Redis and returned — it never called _rec_outcome, so BLOCKED_RISK never
# reached the outcome store (and therefore never the #2069 telemetry
# choke-point). R8: the path records ("blocked:risk"). R9: the guard's
# behaviour is unchanged (early return — no submit, no compliance check).
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
    return {"user_id": "u1", "client": client, "equity": 100000.0}


def _drive_buy_with_size(executor, tenant, event, rm_size):
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
        _run(executor._execute_tenant_order(tenant, event))


def test_size_zero_records_blocked_risk():
    # R8 — the size<=0 guard must record the outcome so the #2069 telemetry
    # choke-point (record_execution_outcome) sees the most frequent block class.
    guardian = MagicMock()
    executor = _make_executor(compliance=guardian)
    tenant = _buy_tenant()
    with patch("core.engine.order_executor._rec_outcome") as rec:
        _drive_buy_with_size(executor, tenant, _make_signal("BUY"), rm_size=0.0)
    rec.assert_called_once()
    args = rec.call_args.args
    assert args[0] == "AAPL"
    assert args[1] == "blocked:risk"


def test_size_zero_behaviour_unchanged_no_submit_no_compliance():
    # R9 — pure observation: the guard still early-returns BEFORE compliance
    # and BEFORE any broker submit.
    guardian = MagicMock()
    executor = _make_executor(compliance=guardian)
    tenant = _buy_tenant()
    _drive_buy_with_size(executor, tenant, _make_signal("BUY"), rm_size=0.0)
    tenant["client"].submit_order.assert_not_called()
    guardian.check_order.assert_not_called()
    guardian.check_trade.assert_not_called()
