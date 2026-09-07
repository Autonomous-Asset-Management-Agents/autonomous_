import time
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# Load all scenarios from the feature file
scenarios("../golden_path.feature")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _suppress_cloud_logger():
    with patch("core.compliance.get_cloud_logger") as mock_gl:
        mock_gl.return_value = MagicMock()
        yield


@pytest.fixture
def guardian():
    from core.compliance import ComplianceGuardian

    g = ComplianceGuardian()
    g._recent_trades = []
    g.daily_trades = 0
    return g


@pytest.fixture
def risk_manager():
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        with patch("core.risk_manager.AILearnedRules") as MockRules:
            MockRules.return_value.get_rules.return_value = []
            from core.risk_manager import RiskManager

            mock_client = MagicMock()
            mock_client.get_all_positions.return_value = []

            rm = RiskManager(
                client=mock_client,
                total_capital=100_000.0,
                risk_per_trade_percent=0.02,
                daily_drawdown_limit_percent=0.175,
            )
            rm.ai_rules_singleton = MockRules.return_value
            return rm


@pytest.fixture
def test_context():
    """A dictionary to pass state between steps."""
    return {}


def _make_order(symbol="AAPL", side="buy", qty=10, price=150.0):
    return {
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "strategy_id": "golden_path_test",
        "timestamp": time.time(),
    }


def _calc_position_size(rm, vix=15.0, price=150.0, cash=50_000.0):
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        return rm.calculate_position_size(
            stop_loss_atr_multiplier=2.0,
            atr=1.5,
            confidence="medium",
            market_data={"vix": vix},
            current_price=price,
            account_cash=cash,
            conviction_score=0.7,
        )


# ---------------------------------------------------------------------------
# Step Definitions: STORY-01 & 03
# ---------------------------------------------------------------------------


@given(parsers.parse("the system has {capital:d} capital and VIX is {vix:d}"))
def system_state(risk_manager, test_context, capital, vix):
    risk_manager.total_capital = float(capital)
    test_context["vix"] = float(vix)


@given(parsers.parse('a BUY order for "{symbol}" at {price:f} was just approved'))
def buy_order_just_approved(guardian, symbol, price):
    order = _make_order(symbol=symbol, side="buy", price=price, qty=5)
    assert guardian.check_order(order) is True


@when(
    parsers.parse('a BUY order for {qty:d} shares of "{symbol}" at {price:f} is placed')
)
def place_buy_order(guardian, test_context, qty, symbol, price):
    order = _make_order(symbol=symbol, side="buy", price=price, qty=qty)
    test_context["order"] = order
    test_context["approval"] = guardian.check_order(order)


@when(parsers.parse('a SELL order for "{symbol}" at {price:f} is placed immediately'))
def place_sell_order_immediately(guardian, test_context, symbol, price):
    order = _make_order(symbol=symbol, side="sell", price=price, qty=5)
    test_context["approval"] = guardian.check_order(order)


@then("the order is approved by compliance")
def check_approval(test_context):
    assert test_context["approval"] is True


@then("the order is rejected by compliance")
def check_rejection(test_context):
    assert test_context["approval"] is False


@then("the RiskManager allocates a position size greater than 0")
def check_position_size(risk_manager, test_context):
    vix = test_context.get("vix", 15.0)
    order = test_context.get("order", {})
    price = order.get("price", 150.0)
    size = _calc_position_size(risk_manager, vix=vix, price=price)
    assert size > 0


@then("the daily trade count is not increased")
def check_trade_count(guardian):
    assert (
        guardian.daily_trades == 0
    )  # guardian does not increment internally during check_order


# ---------------------------------------------------------------------------
# Step Definitions: STORY-02 (Stop-Loss) & SmartExit
# ---------------------------------------------------------------------------


@given(parsers.parse('a position in "{symbol}" was entered at {entry_price:f}'))
def position_entered(test_context, symbol, entry_price):
    test_context["symbol"] = symbol
    test_context["entry_price"] = entry_price


@when(
    parsers.parse(
        'the price of "{symbol}" rises to {new_price:f} after {hours:d} hours'
    )
)
@when(
    parsers.parse(
        'the price of "{symbol}" drops to {new_price:f} after {hours:d} hours'
    )
)
def price_changes(test_context, symbol, new_price, hours):
    from core.smart_exit import should_sell_smart

    entry_price = test_context.get("entry_price", 150.0)  # default if not set
    # Using new_price as high_water_mark for simplicity
    result = should_sell_smart(
        symbol=symbol,
        entry_price=entry_price,
        current_price=new_price,
        high_water_mark=max(entry_price, new_price),
        hours_held=float(hours),
        in_top_n=True,
        lstm_rank=3,
        smart_take_profit=False,
    )
    test_context["exit_result"] = result


@then(
    parsers.parse(
        'the SmartExit module triggers a "{action}" action with reason "{reason}"'
    )
)
def check_smart_exit(test_context, action, reason):
    result = test_context["exit_result"]
    assert result.action == action, f"Expected {action}, got {result.action}"
    assert (
        reason.lower() in result.reason.lower()
    ), f"Expected reason to contain {reason}, got {result.reason}"


# ---------------------------------------------------------------------------
# Step Definitions: STORY-04 (Iron Dome)
# ---------------------------------------------------------------------------


@given(
    parsers.parse(
        "the system has a peak daily equity of {peak:f} and a limit of {limit:f}"
    )
)
def set_equity_limits(risk_manager, peak, limit):
    risk_manager.peak_daily_equity = peak
    risk_manager.daily_drawdown_limit = limit
    risk_manager.portfolio_stop_loss_pct = 0.0


@when(parsers.parse("the account equity drops to {equity:f}"))
def drop_equity(risk_manager, equity):
    with patch("core.kill_switch.kill_switch"):
        risk_manager.update_account_equity(equity)


@then("the RiskManager halts trading")
def check_halt(risk_manager):
    assert risk_manager.trading_halted is True


@then(parsers.parse("new orders receive a position size of {size:f}"))
def check_zero_position(risk_manager, size):
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = True
        actual_size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=2.0,
            atr=1.5,
            confidence="high",
            market_data={"vix": 15},
            current_price=150.0,
            account_cash=50_000.0,
            conviction_score=0.9,
        )
    assert actual_size == size


@when(parsers.parse("the daily limit is reset to {equity:f}"))
def reset_limit(risk_manager, equity):
    with patch("core.kill_switch.kill_switch"):
        risk_manager.reset_daily_limit(equity)


@then("trading is resumed")
def check_resume(risk_manager):
    assert risk_manager.trading_halted is False


@given("the holding time is only 1 minute")
def holding_time_1_minute(test_context):
    test_context["hours_held"] = 1.0 / 60.0


@when(parsers.parse('a SELL signal is generated for "{symbol}"'))
def sell_signal_generated(test_context, risk_manager, symbol):
    from datetime import datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    import numpy as np
    import pandas as pd

    from core.portfolio_manager import PortfolioManager
    from core.strategies.rl_execution import RLExecutionMixin

    mock_client = MagicMock()
    # Mock position to have qty > 0 so that it is processed as "in_position"
    pos = MagicMock()
    pos.qty = "10.0"
    pos.avg_entry_price = "150.0"
    mock_client.get_open_position.return_value = pos

    class ConcreteExecution(RLExecutionMixin):
        def __init__(self):
            self.client = mock_client
            self.risk_manager = risk_manager
            self.symbols = [symbol]
            self.strategy_name = "RLAgent"
            self._current_vix = 20.0
            self._vix_regime = "normal"
            self.high_water_marks = {symbol: 150.0}
            self._entry_time = {}
            self._lstm_states = {}
            self.portfolio_manager = PortfolioManager(
                client=mock_client, total_capital=100000.0
            )
            self.portfolio_manager._min_hold_hours = 0.5
            self.portfolio_manager._consecutive_sell_threshold = 8
            # Record trade so that can_sell_position sees it in history
            self.portfolio_manager.record_trade(symbol, "buy")
            # Overwrite the buy timestamp to be 1 minute ago
            self.portfolio_manager._trade_history[symbol] = [
                datetime.now() - timedelta(minutes=1)
            ]
            self._pending_orders = {}
            self._last_order_time = {}
            self.trade_intelligence = None
            self.compliance_guardian = None
            self._rl_model_version = "rl_agent_v3_dsr"
            self.thought_callback = None
            self.last_thought_time = {}

        def log_thought(self, msg):
            pass

        async def _submit_order_safe(
            self, sym, qty, side, expected_cost=0.0, current_price=None
        ):
            test_context["submitted"] = True
            return True

        async def _get_current_state(self, sym, current_date, market_data):
            df = pd.DataFrame(
                [{"atr_14d": 2.5, "rsi_14": 45.0, "macd": 0.5, "adx_14": 28.0}]
            )
            return None, df, 0.5

        def _calculate_conviction_score(self, features, pred, market_data):
            return 0.6

        def _generate_thought(self, *args, **kwargs):
            pass

        def _update_vix_from_market_data(self, market_data):
            pass

    test_context["execution"] = ConcreteExecution()
    # Mock evaluate_signal to return SELL
    test_context["execution"]._evaluate_signal = AsyncMock(
        return_value={"signal": "SELL", "raw_rl_action": 2, "rl_action": 2}
    )
    test_context["execution"]._check_smart_exit = MagicMock(
        return_value={"triggered": False, "signal": "HOLD"}
    )

    test_context["submitted"] = False

    # Run loop
    import asyncio

    loop = asyncio.get_event_loop()
    # We call run_for_symbol_impl
    event = loop.run_until_complete(
        test_context["execution"]._run_for_symbol_impl(
            symbol=symbol,
            ohlc_data={
                "open": 150.0,
                "high": 150.0,
                "low": 150.0,
                "close": 150.0,
                "volume": 1000,
            },
            market_data={"vix": 15.0, "regime": "normal"},
            current_time=datetime.now(),
        )
    )
    test_context["event"] = event


@then("the order is rejected by anti-churn")
def check_rejected_by_anti_churn(test_context):
    assert test_context["submitted"] is False
    assert test_context["event"].action == "HOLD"
