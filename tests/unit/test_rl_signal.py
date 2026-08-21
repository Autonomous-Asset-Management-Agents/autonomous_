# tests/unit/test_rl_signal.py
# Epic 1.7 / PR-B — TDD Red-Phase
# Tests für die Signal-Methoden aus _run_for_symbol_impl, die nach
# core/strategies/rl_signal.py extrahiert werden.

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_open_position.return_value = None
    client.get_account.return_value = MagicMock(
        buying_power=10000.0,
        daytrading_buying_power=0.0,
        cash=10000.0,
        equity=10000.0,
        pattern_day_trader=False,
    )
    return client


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm.evaluate_new_trade.return_value = (
        True,
        "OK",
        {"sl_multiplier": 3.0, "size_scaler": 1.0},
    )
    rm.calculate_position_size.return_value = 5.0
    return rm


@pytest.fixture
def market_data():
    return {
        "vix": 18.5,
        "regime": "Normal",
        "latest_news_sentiment": 0.1,
        "regime_info": {"value": 18.5},
    }


@pytest.fixture
def features_series():
    """Minimale Feature-Series für Tests."""
    return pd.Series(
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
            "volume": 1_000_000,
            "volume_sma_20d": 900_000,
            "returns": 0.01,
        }
    )


# ---------------------------------------------------------------------------
# 1. _calculate_conviction_score
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestCalculateConvictionScore:
    """Conviction Score: 0.0–1.0 basierend auf LSTM-Pred, RSI, ADX, MACD, VIX."""

    def _make_strategy(self, mock_client, mock_risk_manager):
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin.client = mock_client
        mixin.risk_manager = mock_risk_manager
        mixin.symbols = ["AAPL"]
        return mixin

    def test_high_conviction_oversold_strong_trend(
        self, mock_client, mock_risk_manager, features_series, market_data
    ):
        strategy = self._make_strategy(mock_client, mock_risk_manager)
        feat = features_series.copy()
        feat["rsi_14"] = 28.0  # Oversold
        feat["adx_14"] = 42.0  # Very strong trend
        feat["macd"] = 0.8
        feat["macd_signal"] = 0.2
        score = strategy._calculate_conviction_score(feat, 1.0, market_data)
        assert score >= 0.7, f"Expected high conviction, got {score}"

    def test_low_conviction_no_data(self, mock_client, mock_risk_manager, market_data):
        strategy = self._make_strategy(mock_client, mock_risk_manager)
        score = strategy._calculate_conviction_score(None, 0.0, market_data)
        assert score == 0.2, "Default conviction when features=None should be 0.2"

    def test_conviction_clamped_to_one(
        self, mock_client, mock_risk_manager, features_series, market_data
    ):
        strategy = self._make_strategy(mock_client, mock_risk_manager)
        feat = features_series.copy()
        feat["rsi_14"] = 20.0
        feat["adx_14"] = 50.0
        feat["macd"] = 1.5
        feat["macd_signal"] = 0.1
        score = strategy._calculate_conviction_score(feat, 2.0, market_data)
        assert 0.0 <= score <= 1.0, f"Score must be clamped to [0,1], got {score}"

    def test_conviction_overbought_reduces_score(
        self, mock_client, mock_risk_manager, features_series, market_data
    ):
        strategy = self._make_strategy(mock_client, mock_risk_manager)
        feat_low = features_series.copy()
        feat_low["rsi_14"] = 28.0  # Oversold
        feat_high = features_series.copy()
        feat_high["rsi_14"] = 78.0  # Overbought
        score_low = strategy._calculate_conviction_score(feat_low, 0.8, market_data)
        score_high = strategy._calculate_conviction_score(feat_high, 0.8, market_data)
        assert (
            score_low > score_high
        ), "Oversold should yield higher conviction than overbought for BUY"

    def test_conviction_high_vix_no_bonus(
        self, mock_client, mock_risk_manager, features_series
    ):
        strategy = self._make_strategy(mock_client, mock_risk_manager)
        market_low_vix = {"vix": 12.0}
        market_high_vix = {"vix": 40.0}
        score_low = strategy._calculate_conviction_score(
            features_series, 0.8, market_low_vix
        )
        score_high = strategy._calculate_conviction_score(
            features_series, 0.8, market_high_vix
        )
        assert score_low > score_high, "Low VIX should yield higher conviction"


# ---------------------------------------------------------------------------
# 2. _get_vix_adaptive_thresholds
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestVixAdaptiveThresholds:
    """VIX-Regime-abhängige Handelsschwellenwerte."""

    def _make_strategy(self, vix: float):
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin._current_vix = vix
        mixin._vix_regime = "normal"
        return mixin

    def test_low_regime_thresholds(self):
        strategy = self._make_strategy(12.0)
        thresholds = strategy._get_vix_adaptive_thresholds()
        assert strategy._vix_regime == "low"
        assert thresholds["buy_votes_required"] == 2
        assert thresholds["lstm_buy_threshold"] < 0.5  # Aggressiver bei niedrigem VIX

    def test_normal_regime_thresholds(self):
        strategy = self._make_strategy(20.0)
        thresholds = strategy._get_vix_adaptive_thresholds()
        assert strategy._vix_regime == "normal"
        assert thresholds["buy_votes_required"] == 2

    def test_elevated_regime_requires_more_confirmations(self):
        strategy = self._make_strategy(28.0)
        thresholds = strategy._get_vix_adaptive_thresholds()
        assert strategy._vix_regime == "elevated"
        assert thresholds["buy_votes_required"] == 3  # Mehr Bestätigung bei hohem VIX

    def test_crisis_regime_very_defensive(self):
        strategy = self._make_strategy(42.0)
        thresholds = strategy._get_vix_adaptive_thresholds()
        assert strategy._vix_regime == "crisis"
        assert thresholds["buy_votes_required"] == 4
        assert thresholds["lstm_buy_threshold"] >= 1.0  # LSTM muss sehr sicher sein


# ---------------------------------------------------------------------------
# 3. _update_vix_from_market_data
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestUpdateVixFromMarketData:
    def _make_strategy(self):
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin._current_vix = 20.0
        mixin._vix_regime = "normal"
        return mixin

    def test_updates_from_vix_key(self):
        strategy = self._make_strategy()
        strategy._update_vix_from_market_data({"vix": 35.5})
        assert strategy._current_vix == 35.5

    def test_updates_from_regime_info(self):
        strategy = self._make_strategy()
        strategy._update_vix_from_market_data({"regime_info": {"value": 28.0}})
        assert strategy._current_vix == 28.0

    def test_ignores_invalid_vix(self):
        strategy = self._make_strategy()
        original = strategy._current_vix
        strategy._update_vix_from_market_data({"vix": -5.0})
        assert strategy._current_vix == original, "Negative VIX should be ignored"

    def test_ignores_none_vix(self):
        strategy = self._make_strategy()
        original = strategy._current_vix
        strategy._update_vix_from_market_data({"vix": None})
        assert strategy._current_vix == original


# ---------------------------------------------------------------------------
# 4. _stabilize_signal
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestStabilizeSignal:
    """RL + LSTM Signal-Stabilisierung."""

    def _make_strategy(self):
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        return mixin

    def test_rl_buy_not_in_position_lstm_neutral(self):
        strategy = self._make_strategy()
        # RL=BUY, not in position, LSTM neutral (pred=0.1) → BUY
        result = strategy._stabilize_signal(
            "AAPL", raw_action=1, pred=0.1, in_position=False
        )
        # RL BUY + LSTM not bearish → should pass through or be filtered
        assert result in (0, 1)  # 0 wenn LSTM nicht bullish genug, 1 wenn RL dominiert

    def test_rl_buy_already_in_position_returns_hold(self):
        strategy = self._make_strategy()
        result = strategy._stabilize_signal(
            "AAPL", raw_action=1, pred=0.8, in_position=True
        )
        assert result == 0, "BUY when already in position should return HOLD"

    def test_rl_sell_not_in_position_returns_hold(self):
        strategy = self._make_strategy()
        result = strategy._stabilize_signal(
            "AAPL", raw_action=2, pred=-0.8, in_position=False
        )
        assert result == 0, "SELL when not in position should return HOLD"

    def test_strong_lstm_buy_overrides_rl_hold(self):
        strategy = self._make_strategy()
        # RL=HOLD (0), LSTM very bullish (pred=0.7) → should override to BUY
        result = strategy._stabilize_signal(
            "AAPL", raw_action=0, pred=0.7, in_position=False
        )
        assert result == 1, "Strong LSTM BUY should override RL HOLD"

    def test_strong_lstm_sell_overrides_rl_hold(self):
        strategy = self._make_strategy()
        # RL=HOLD (0), LSTM very bearish → should override to SELL
        result = strategy._stabilize_signal(
            "AAPL", raw_action=0, pred=-0.7, in_position=True
        )
        assert result == 2, "Strong LSTM SELL should override RL HOLD"

    def test_rl_buy_lstm_bearish_returns_hold(self):
        strategy = self._make_strategy()
        # RL=BUY but LSTM is bearish → HOLD (conflict)
        result = strategy._stabilize_signal(
            "AAPL", raw_action=1, pred=-0.5, in_position=False
        )
        assert result == 0, "RL BUY conflicting with bearish LSTM should be HOLD"


# ---------------------------------------------------------------------------
# 5. _normalize_state
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestNormalizeState:
    def _make_strategy_with_vec_normalize(self, vec_normalize=None):
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin.vec_normalize = vec_normalize
        return mixin

    def test_returns_raw_when_no_vec_normalize(self):
        strategy = self._make_strategy_with_vec_normalize(None)
        raw = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = strategy._normalize_state(raw)
        np.testing.assert_array_equal(result, raw)

    def test_normalizes_with_vec_normalize(self):
        mock_vn = MagicMock()
        mock_vn.obs_rms.mean = np.array([1.0, 1.0, 1.0])
        mock_vn.obs_rms.var = np.array([1.0, 1.0, 1.0])
        strategy = self._make_strategy_with_vec_normalize(mock_vn)
        raw = np.array([2.0, 2.0, 2.0], dtype=np.float32)
        result = strategy._normalize_state(raw)
        # Normalized = clip((raw - mean) / sqrt(var + 1e-8), -10, 10)
        expected_val = (2.0 - 1.0) / np.sqrt(1.0 + 1e-8)
        assert abs(result[0] - expected_val) < 1e-4

    def test_output_clipped_to_minus10_plus10(self):
        mock_vn = MagicMock()
        mock_vn.obs_rms.mean = np.array([0.0])
        mock_vn.obs_rms.var = np.array([0.0001])  # Very small var → large output
        strategy = self._make_strategy_with_vec_normalize(mock_vn)
        raw = np.array([1000.0], dtype=np.float32)
        result = strategy._normalize_state(raw)
        assert result[0] <= 10.0, "Output should be clipped at +10"
