# tests/unit/test_lstm_blocked_sell.py
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.events import SignalEvent
from core.strategies.lstm_strategy import LSTMDynamicStrategy


def _make_strategy():
    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()
    strategy.scaler_x = MagicMock()
    strategy.torch = MagicMock()
    strategy.np = MagicMock()
    strategy.pd = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close", "volume", "rsi_14"]
    strategy._initialized = True
    strategy.device = "cpu"
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.risk_manager = MagicMock()
    strategy.high_water_marks = {}
    strategy._entry_time = {}
    strategy._lstm_rank_cache = [("AAPL", 1)]
    strategy.thought_callback = None
    strategy._bought_this_window = set()
    return strategy


@pytest.mark.anyio
async def test_run_for_symbol_sell_allowed():
    """Verify that an allowed SELL signal yields a SELL event and pops

    state.
    """
    strategy = _make_strategy()
    symbol = "AAPL"
    current_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
    market_data = {"vix": 20.0, "regime": "normal"}
    ohlc_data = {"close": 150.0}

    # Setup open position
    pos = MagicMock()
    pos.qty = 10.0
    pos.avg_entry_price = 140.0
    strategy.client.get_open_position = MagicMock(return_value=pos)

    # Populate tracking state
    strategy.high_water_marks[symbol] = 145.0
    strategy._entry_time[symbol] = current_time

    # Mock should_sell_smart to return SELL
    decision = MagicMock()
    decision.action = "SELL"
    decision.reason = "Trailing stop triggered"

    # Mock evaluate_new_trade to approve the SELL
    rm_mock = MagicMock(return_value=(True, "OK", {}))
    strategy.risk_manager.evaluate_new_trade = rm_mock

    p_sell = patch(
        "core.strategies.lstm_strategy.should_sell_smart",
        return_value=decision,
    )
    p_top_n = patch("core.strategies.lstm_strategy.LSTM_DYNAMIC_TOP_N", 10)
    p_reset = patch.object(strategy, "_ensure_bought_window_reset")

    with p_sell, p_top_n, p_reset:
        event = await strategy.run_for_symbol(
            symbol, ohlc_data, market_data, current_time
        )

    assert isinstance(event, SignalEvent)
    assert event.action == "SELL"
    assert event.decision_context.risk_approved is True
    assert event.decision_context.action == "SELL"
    assert event.decision_context.triggered_by_stop is True
    assert event.decision_context.stop_type == "Trailing stop triggered"

    # State must be popped
    assert symbol not in strategy.high_water_marks
    assert symbol not in strategy._entry_time


@pytest.mark.anyio
async def test_run_for_symbol_sell_blocked():
    """Verify that a blocked SELL signal yields a HOLD event and preserves

    tracking state.
    """
    strategy = _make_strategy()
    symbol = "AAPL"
    current_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
    market_data = {"vix": 20.0, "regime": "normal"}
    ohlc_data = {"close": 150.0}

    # Setup open position
    pos = MagicMock()
    pos.qty = 10.0
    pos.avg_entry_price = 140.0
    strategy.client.get_open_position = MagicMock(return_value=pos)

    # Populate tracking state
    strategy.high_water_marks[symbol] = 145.0
    strategy._entry_time[symbol] = current_time

    # Mock should_sell_smart to return SELL
    decision = MagicMock()
    decision.action = "SELL"
    decision.reason = "Trailing stop triggered"

    # Mock evaluate_new_trade to block the SELL
    rm_mock = MagicMock(return_value=(False, "Blocked by Risk Manager", {}))
    strategy.risk_manager.evaluate_new_trade = rm_mock

    p_sell = patch(
        "core.strategies.lstm_strategy.should_sell_smart",
        return_value=decision,
    )
    p_top_n = patch("core.strategies.lstm_strategy.LSTM_DYNAMIC_TOP_N", 10)
    p_reset = patch.object(strategy, "_ensure_bought_window_reset")

    with p_sell, p_top_n, p_reset:
        event = await strategy.run_for_symbol(
            symbol, ohlc_data, market_data, current_time
        )

    assert isinstance(event, SignalEvent)
    assert event.action == "HOLD"
    assert event.decision_context.risk_approved is False
    assert event.decision_context.risk_reason == "Blocked by Risk Manager"
    assert event.decision_context.action == "HOLD"
    assert event.decision_context.triggered_by_stop is True
    assert event.decision_context.stop_type == "Trailing stop triggered"

    # State must be preserved
    assert (
        strategy.high_water_marks[symbol] == 150.0
    )  # updated by high_water_marks max check
    assert strategy._entry_time[symbol] == current_time
