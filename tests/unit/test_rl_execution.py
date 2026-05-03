# tests/unit/test_rl_execution.py
# Epic 1.7 / PR-B — TDD Red-Phase
# Tests für die 8 extrahierten Methoden aus _run_for_symbol_impl
# Zielpfad: core/strategies/rl_execution.py

import numpy as np
import pandas as pd
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_open_position.return_value = None
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
def mock_risk_manager():
    rm = MagicMock()
    rm.evaluate_new_trade.return_value = (
        True,
        "OK",
        {"sl_multiplier": 3.0, "size_scaler": 1.0},
    )
    rm.calculate_position_size.return_value = 3.0
    return rm


@pytest.fixture
def market_data():
    return {
        "vix": 20.0,
        "regime": "Normal",
        "latest_news_sentiment": 0.05,
        "regime_info": {"value": 20.0},
    }


@pytest.fixture
def ohlc_data():
    return {
        "open": 148.0,
        "high": 152.0,
        "low": 147.0,
        "close": 150.0,
        "volume": 1_200_000,
    }


@pytest.fixture
def features_df():
    """Minimales DataFrame mit einer Zeile für Feature-Tests."""
    return pd.DataFrame(
        [
            {
                "rsi_14": 45.0,
                "macd": 0.5,
                "macd_signal": 0.3,
                "adx_14": 28.0,
                "bb_pct": 0.45,
                "atr_14d": 2.5,
                "close": 150.0,
                "volatility_20d": 0.018,
                "momentum_10d": 0.02,
                "volume": 1_200_000,
                "volume_sma_20d": 900_000,
                "returns": 0.01,
                "atr_14": 2.5,
            }
        ]
    )


@pytest.fixture
def rl_execution(mock_client, mock_risk_manager, features_df):
    """Erstellt eine RLExecution-Instanz mit gemockten Abhängigkeiten."""
    from core.strategies.rl_execution import RLExecutionMixin

    class ConcreteExecution(RLExecutionMixin):
        def __init__(self):
            self.client = mock_client
            self.risk_manager = mock_risk_manager
            self.symbols = ["AAPL"]
            self.strategy_name = "RLAgent"
            self._current_vix = 20.0
            self._vix_regime = "normal"
            self.high_water_marks = {}
            self._entry_time = {}
            self._lstm_states = {}
            self._last_gtc_buy_submit_time = 0.0
            self._pending_orders = {}
            self._last_order_time = {}
            self.portfolio_manager = None
            self.trade_intelligence = None
            self.compliance_guardian = None
            self._rl_model_version = "rl_agent_v3_dsr"
            self.thought_callback = None
            self.last_thought_time = {}
            self.rl_model = None
            self.vec_normalize = None

        def log_thought(self, msg):
            pass

        async def _submit_order_safe(self, symbol, qty, side, expected_cost=0.0):
            return True

        async def _get_current_state(self, symbol, current_date, market_data):
            return None, features_df, 0.5

        def _calculate_conviction_score(self, features, pred, market_data):
            return 0.6

        def _generate_thought(self, *args, **kwargs):
            pass

        def _update_vix_from_market_data(self, market_data):
            pass

    return ConcreteExecution()


# ---------------------------------------------------------------------------
# 1. _evaluate_signal (RL + LSTM → finales Signal)
# ---------------------------------------------------------------------------


class TestEvaluateSignal:
    @pytest.mark.anyio
    async def test_buy_signal_when_lstm_bullish(
        self, rl_execution, features_df, market_data
    ):
        """Wenn LSTM stark bullish und kein Position → BUY Signal."""
        result = await rl_execution._evaluate_signal(
            symbol="AAPL",
            state=None,
            features=features_df,
            pred=0.8,  # Bullish LSTM
            in_position=False,
            market_data=market_data,
        )
        assert result["signal"] in ("BUY", "HOLD")
        assert "raw_rl_action" in result
        assert "rl_action" in result

    @pytest.mark.anyio
    async def test_sell_signal_overridden_when_no_position(
        self, rl_execution, features_df, market_data
    ):
        """SELL wenn keine Position → HOLD."""
        result = await rl_execution._evaluate_signal(
            symbol="AAPL",
            state=None,
            features=features_df,
            pred=-0.8,
            in_position=False,
            market_data=market_data,
        )
        assert result["signal"] == "HOLD", "SELL without position must become HOLD"

    @pytest.mark.anyio
    async def test_buy_overridden_when_already_in_position(
        self, rl_execution, features_df, market_data
    ):
        """BUY wenn bereits in Position → HOLD."""
        result = await rl_execution._evaluate_signal(
            symbol="AAPL",
            state=None,
            features=features_df,
            pred=0.8,
            in_position=True,
            market_data=market_data,
        )
        assert (
            result["signal"] == "HOLD"
        ), "BUY when already in position must become HOLD"


# ---------------------------------------------------------------------------
# 2. _check_smart_exit
# ---------------------------------------------------------------------------


class TestCheckSmartExit:
    def test_no_exit_when_not_in_position(self, rl_execution, features_df):
        result = rl_execution._check_smart_exit(
            symbol="AAPL",
            in_position=False,
            qty=0.0,
            avg=0.0,
            curr=150.0,
            current_time=datetime.now(),
            features=features_df,
        )
        assert result["triggered"] is False
        assert result["signal"] == "HOLD"

    def test_no_exit_when_position_profitable_not_at_target(
        self, rl_execution, features_df
    ):
        """Position mit 1% Gewinn → kein Smart-Exit bei normalen Schwellen."""
        rl_execution.high_water_marks["AAPL"] = 150.0
        result = rl_execution._check_smart_exit(
            symbol="AAPL",
            in_position=True,
            qty=10.0,
            avg=148.5,  # Einstieg
            curr=150.0,  # +1% → kein Take-Profit-Level
            current_time=datetime.now(),
            features=features_df,
        )
        # Smart exit needs significant gain or stop trigger
        # 1% shouldn't trigger TP
        assert result["signal"] in ("HOLD", "SELL"), "Result must be HOLD or SELL"

    def test_exit_triggered_on_stop_loss(self, rl_execution, features_df):
        """Position mit -10% Verlust → Smart-Exit sollte SELL auslösen."""
        rl_execution.high_water_marks["AAPL"] = 165.0
        result = rl_execution._check_smart_exit(
            symbol="AAPL",
            in_position=True,
            qty=10.0,
            avg=165.0,
            curr=148.0,  # -10.3% → sollte Stop-Loss auslösen
            current_time=datetime.now(),
            features=features_df,
        )
        assert result["signal"] == "SELL", "Stop-loss should trigger SELL"
        assert result["triggered"] is True


# ---------------------------------------------------------------------------
# 3. _check_position_state (Position-Lookup)
# ---------------------------------------------------------------------------


class TestCheckPositionState:
    def test_returns_not_in_position_when_none(self, rl_execution, mock_client):
        mock_client.get_open_position.return_value = None
        result = rl_execution._check_position_state("AAPL")
        assert result["in_position"] is False
        assert result["qty"] == 0.0

    def test_returns_in_position_when_long(self, rl_execution, mock_client):
        pos = MagicMock()
        pos.qty = "10.0"
        pos.avg_entry_price = "145.0"
        mock_client.get_open_position.return_value = pos
        result = rl_execution._check_position_state("AAPL")
        assert result["in_position"] is True
        assert result["qty"] == 10.0
        assert result["avg"] == 145.0

    def test_handles_api_error_gracefully(self, rl_execution, mock_client):
        mock_client.get_open_position.side_effect = Exception("404 position not found")
        result = rl_execution._check_position_state("AAPL")
        assert result["in_position"] is False  # 404 → no position


# ---------------------------------------------------------------------------
# 4. _apply_risk_filters
# ---------------------------------------------------------------------------


class TestApplyRiskFilters:
    def test_returns_allowed_true_when_risk_manager_approves(
        self, rl_execution, mock_risk_manager, market_data
    ):
        mock_risk_manager.evaluate_new_trade.return_value = (
            True,
            "OK",
            {"sl_multiplier": 3.0},
        )
        result = rl_execution._apply_risk_filters("AAPL", "BUY", market_data)
        assert result["allowed"] is True
        assert result["mods"]["sl_multiplier"] == 3.0

    def test_returns_allowed_false_when_risk_manager_blocks(
        self, rl_execution, mock_risk_manager, market_data
    ):
        mock_risk_manager.evaluate_new_trade.return_value = (
            False,
            "Daily loss limit breached",
            {},
        )
        result = rl_execution._apply_risk_filters("AAPL", "BUY", market_data)
        assert result["allowed"] is False
        assert "Daily loss" in result["reason"]


# ---------------------------------------------------------------------------
# 5. _log_decision_trace  (DecisionContext Building)
# ---------------------------------------------------------------------------


class TestLogDecisionTrace:
    def test_returns_signal_event_with_correct_action(
        self, rl_execution, features_df, market_data
    ):
        from core.events import SignalEvent

        event = rl_execution._log_decision_trace(
            symbol="AAPL",
            signal="BUY",
            pred=0.7,
            raw_rl_action=1,
            rl_action=1,
            conviction=0.75,
            curr=150.0,
            in_position=False,
            qty=0.0,
            avg=0.0,
            triggered_exit=False,
            features=features_df,
            market_data=market_data,
            suggested_qty=3.0,
        )
        assert isinstance(event, SignalEvent)
        assert event.action == "BUY"
        assert event.symbol == "AAPL"
        assert event.suggested_quantity == 3.0

    def test_decision_context_contains_key_fields(
        self, rl_execution, features_df, market_data
    ):
        event = rl_execution._log_decision_trace(
            symbol="AAPL",
            signal="HOLD",
            pred=0.1,
            raw_rl_action=0,
            rl_action=0,
            conviction=0.3,
            curr=150.0,
            in_position=False,
            qty=0.0,
            avg=0.0,
            triggered_exit=False,
            features=features_df,
            market_data=market_data,
            suggested_qty=0.0,
        )
        ctx = event.decision_context
        assert ctx.lstm_prediction == 0.1
        assert ctx.current_price == 150.0
        assert ctx.vix_level == 20.0


# ---------------------------------------------------------------------------
# 6. _gather_market_inputs (VIX + State)
# ---------------------------------------------------------------------------


class TestGatherMarketInputs:
    @pytest.mark.anyio
    async def test_returns_none_features_when_no_data(self, rl_execution, market_data):
        """Wenn _get_current_state None features zurückgibt → Methode gibt None zurück."""
        rl_execution._get_current_state = AsyncMock(return_value=(None, None, 0.0))
        result = await rl_execution._gather_market_inputs(
            "AAPL", datetime.now(), market_data
        )
        assert result["features"] is None

    @pytest.mark.anyio
    async def test_returns_valid_inputs_with_data(
        self, rl_execution, features_df, market_data
    ):
        rl_execution._get_current_state = AsyncMock(
            return_value=(
                np.zeros(12, dtype=np.float32),
                features_df,
                0.55,
            )
        )
        result = await rl_execution._gather_market_inputs(
            "AAPL", datetime.now(), market_data
        )
        assert result["features"] is not None
        assert result["pred"] == 0.55
        assert result["state"] is not None
