import time
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import MagicMock, patch

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
