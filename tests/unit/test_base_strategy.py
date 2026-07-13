# tests/unit/test_base_strategy.py
# Epic 1.7 / PR-B — TDD Green-Phase
# Tests für BaseStrategy und _submit_order_safe (core/strategies/base.py)

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Simulation-Client (MagicMock → hasattr('simulation_data')=True → is_simulation=True)."""
    client = MagicMock()
    client.submit_order = MagicMock()
    client.get_account.return_value = MagicMock(
        buying_power=10000.0,
        daytrading_buying_power=1000.0,
        cash=8000.0,
        equity=10000.0,
        pattern_day_trader=False,
    )
    client.list_orders.return_value = []
    client.get_clock.return_value = MagicMock(is_open=True)
    return client


@pytest.fixture
def live_client():
    """Live-Client: spec= → hasattr('simulation_data')=False → is_simulation=False."""
    client = MagicMock(spec=["get_clock", "get_account", "list_orders", "submit_order"])
    client.get_clock.return_value = MagicMock(is_open=True)
    client.get_account.return_value = MagicMock(
        buying_power=10000.0,
        daytrading_buying_power=1000.0,
        cash=8000.0,
        equity=10000.0,
        pattern_day_trader=False,
    )
    client.list_orders.return_value = []
    return client


def _make_concrete_strategy(client, symbols=None, total_capital=10000.0):
    from core.strategies.base import BaseStrategy

    class ConcreteStrategy(BaseStrategy):
        async def run_for_symbol(self, *args, **kwargs):
            pass

        async def evaluate_for_symbol(self, *args, **kwargs):
            return {}

    rm = MagicMock()
    rm.evaluate_new_trade.return_value = (True, "OK", {})
    strategy = ConcreteStrategy(
        client=client,
        symbols=symbols or ["AAPL", "MSFT"],
        running_event=None,
        total_capital=total_capital,
        risk_manager=rm,
        data_provider=MagicMock(),
    )
    strategy._last_gtc_buy_submit_time = 0.0
    strategy._pending_orders = {}
    strategy._last_order_time = {}
    strategy.compliance_guardian = None
    return strategy


@pytest.fixture
def base_strategy(mock_client):
    """Simulation mode (keine spec → MagicMock) — für Simulation-Tests."""
    return _make_concrete_strategy(mock_client)


@pytest.fixture
def live_strategy(live_client):
    """Live mode (spec=) — für Live-Mode-Guards Tests."""
    return _make_concrete_strategy(live_client)


# ---------------------------------------------------------------------------
# 1. BaseStrategy Grundstruktur
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestBaseStrategyInit:
    def test_init_sets_symbols(self, base_strategy):
        assert base_strategy.symbols == ["AAPL", "MSFT"]

    def test_init_creates_ai_rules(self, base_strategy):
        assert base_strategy.ai_rules is not None

    def test_strategy_name_is_class_name(self, base_strategy):
        assert base_strategy.strategy_name == "ConcreteStrategy"

    def test_log_thought_uses_callback(self, mock_client):
        messages = []
        strategy = _make_concrete_strategy(mock_client)
        strategy.thought_callback = messages.append
        strategy.log_thought("Test thought")
        assert messages == ["Test thought"]

    def test_get_trade_context_returns_dict(self, base_strategy):
        market_data = {"vix": 20.0, "regime": "Normal", "latest_news_sentiment": 0.0}
        ctx = base_strategy._get_trade_context("AAPL", {"rsi": 50}, market_data)
        assert ctx["strategy"] == "ConcreteStrategy"
        assert ctx["regime"] == "Normal"
        assert ctx["vix"] == 20.0


# ---------------------------------------------------------------------------
# 2. _submit_order_safe — Compliance Guardian Block (Live Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafeCompliance:
    @pytest.mark.anyio
    async def test_blocks_when_compliance_fails_check_order(self, live_strategy):
        guardian = MagicMock()
        guardian.check_order.return_value = False
        guardian.check_trade.return_value = True
        live_strategy.compliance_guardian = guardian

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False
        guardian.check_order.assert_called_once()
        guardian.check_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_blocks_when_compliance_fails_check_trade(self, live_strategy):
        guardian = MagicMock()
        guardian.check_order.return_value = True
        guardian.check_trade.return_value = False
        live_strategy.compliance_guardian = guardian

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False
        guardian.check_order.assert_called_once()
        guardian.check_trade.assert_called_once()


# ---------------------------------------------------------------------------
# 3. _submit_order_safe — Market Closed (Live Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafeMarketClosed:
    @pytest.mark.anyio
    async def test_skips_when_market_closed(self, live_strategy, live_client):
        live_client.get_clock.return_value = MagicMock(is_open=False)

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False
        live_client.submit_order.assert_not_called()

    @pytest.mark.anyio
    async def test_bypass_market_hours_allows_order_when_closed(
        self, live_strategy, live_client
    ):
        """
        Given: market is closed (is_open=False) AND BYPASS_MARKET_HOURS=True
        When:  _submit_order_safe is called for an off-hours paper trade
        Then:  the market-closed gate is skipped — control flows to the next
               check (deduplication / buying power). Order may still be
               rejected downstream by Alpaca, but THIS gate must not block.
        """
        live_client.get_clock.return_value = MagicMock(is_open=False)
        # Use AsyncMock so inspect.iscoroutinefunction returns True → avoids
        # run_in_executor (which has asyncio.get_event_loop() issues in anyio tests).
        live_client.submit_order = AsyncMock(return_value=None)

        # Patch the lazily-imported flag. _submit_order_safe does
        # `from config import BYPASS_MARKET_HOURS` inside the function, so
        # patching the module attribute is the right hook.
        with patch("config.BYPASS_MARKET_HOURS", True):
            result = await live_strategy._submit_order_safe(
                "AAPL", 1.0, "buy", expected_cost=150.0
            )

        # Direct evidence the market-closed gate was skipped: control flowed
        # all the way to broker submission. (Compare:
        # test_skips_when_market_closed asserts submit_order is NOT called.)
        live_client.submit_order.assert_called()
        assert result is True


# ---------------------------------------------------------------------------
# 4. _submit_order_safe — Order Deduplication (Live Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafeDeduplication:
    @pytest.mark.anyio
    async def test_skips_when_pending_order_exists(self, live_strategy, live_client):
        existing_order = MagicMock()
        existing_order.symbol = "AAPL"
        existing_order.side = "buy"
        live_client.list_orders.return_value = [existing_order]

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False


# ---------------------------------------------------------------------------
# 5. _submit_order_safe — Buying Power Check (Live Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafeBuyingPower:
    @pytest.mark.anyio
    async def test_blocks_when_insufficient_cash(self, live_strategy, live_client):
        live_client.get_account.return_value = MagicMock(
            buying_power=100.0,
            daytrading_buying_power=0.0,
            cash=100.0,
            equity=100.0,
            pattern_day_trader=False,
        )
        live_client.list_orders.return_value = []

        result = await live_strategy._submit_order_safe(
            "AAPL", 100.0, "buy", expected_cost=10000.0
        )
        assert result is False

    @pytest.mark.anyio
    async def test_blocks_when_cash_is_zero(self, live_strategy, live_client):
        live_client.get_account.return_value = MagicMock(
            buying_power=0.0,
            daytrading_buying_power=0.0,
            cash=0.0,
            equity=5000.0,
            pattern_day_trader=False,
        )
        live_client.list_orders.return_value = []

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False


# ---------------------------------------------------------------------------
# 6. _submit_order_safe — Successful Submission (Simulation Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafeSimulation:
    @pytest.mark.anyio
    async def test_submits_successfully_in_simulation(self):
        sim_client = MagicMock(spec=["submit_order", "simulation_data"])
        sim_client.simulation_data = {}
        sim_client.submit_order = MagicMock()
        strategy = _make_concrete_strategy(sim_client, symbols=["AAPL"])
        strategy._last_gtc_buy_submit_time = 0.0

        result = await strategy._submit_order_safe("AAPL", 2.0, "buy")
        assert result is True
        sim_client.submit_order.assert_called_once()

    @pytest.mark.anyio
    async def test_async_submit_called_in_async_simulation(self):
        sim_client = MagicMock(spec=["submit_order", "simulation_data"])
        sim_client.simulation_data = {}
        sim_client.submit_order = AsyncMock(return_value=None)
        strategy = _make_concrete_strategy(sim_client, symbols=["AAPL"])
        strategy._last_gtc_buy_submit_time = 0.0

        result = await strategy._submit_order_safe("AAPL", 2.0, "buy")
        assert result is True
        sim_client.submit_order.assert_called_once()


# ---------------------------------------------------------------------------
# 7. _submit_order_safe — PDT GTC Handling (Live Mode)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSubmitOrderSafePDT:
    @pytest.mark.anyio
    async def test_uses_gtc_when_pdt_and_dt_power_exhausted(
        self, live_strategy, live_client
    ):
        """PDT account mit 0 day trading power → Slot reserviert, Skip."""
        live_client.get_account.return_value = MagicMock(
            buying_power=5000.0,
            daytrading_buying_power=0.0,
            cash=5000.0,
            equity=10000.0,
            pattern_day_trader=True,
        )
        live_client.list_orders.return_value = []
        live_strategy._last_gtc_buy_submit_time = 0.0

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False
        assert (
            live_strategy._last_gtc_buy_submit_time > 0
        ), "GTC cooldown timestamp should be set"

    @pytest.mark.anyio
    async def test_skips_second_gtc_buy_within_cooldown(
        self, live_strategy, live_client
    ):
        """Zweiter GTC BUY innerhalb 90s wird übersprungen."""
        live_client.get_account.return_value = MagicMock(
            buying_power=5000.0,
            daytrading_buying_power=0.0,
            cash=5000.0,
            equity=10000.0,
            pattern_day_trader=True,
        )
        live_client.list_orders.return_value = []
        live_strategy._last_gtc_buy_submit_time = time.time()

        result = await live_strategy._submit_order_safe(
            "AAPL", 1.0, "buy", expected_cost=150.0
        )
        assert result is False
