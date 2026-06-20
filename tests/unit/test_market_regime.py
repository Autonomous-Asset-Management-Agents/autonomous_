# tests/unit/test_market_regime.py
# TDD-First: Tests written BEFORE core/market_regime.py exists.
# Defines the contract of the MarketRegimeModel extracted from ai_components.py.
# Epic 1.7 / PR-A

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import pandas as pd
import pytest

# RED: will fail until core/market_regime.py is created
from core.market_regime import MarketRegimeModel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_data_provider():
    dp = MagicMock()
    dp.get_data.return_value = pd.DataFrame()
    return dp


@pytest.fixture()
def regime_model(mock_data_provider):
    return MarketRegimeModel(mock_data_provider)


# ---------------------------------------------------------------------------
# _regime_from_value — pure function, no external dependencies
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestRegimeFromValue:
    def test_none_returns_ranging_default(self, regime_model):
        result = regime_model._regime_from_value(None)
        assert result["regime"] == "Ranging"
        assert result["confidence"] == pytest.approx(0.1)
        assert result["value"] is None

    def test_low_vix_returns_low_volatility(self, regime_model):
        result = regime_model._regime_from_value(10.0)
        assert result["regime"] == "Low Volatility"
        assert result["confidence"] > 0.5
        assert result["value"] == pytest.approx(10.0)

    def test_normal_vix_returns_ranging(self, regime_model):
        result = regime_model._regime_from_value(20.0)
        assert result["regime"] == "Ranging"

    def test_elevated_vix_returns_trending(self, regime_model):
        result = regime_model._regime_from_value(30.0)
        assert result["regime"] == "Trending"

    def test_crisis_vix_returns_high_volatility(self, regime_model):
        result = regime_model._regime_from_value(40.0)
        assert result["regime"] == "High Volatility"
        assert result["confidence"] >= 0.7

    def test_nan_returns_ranging_default(self, regime_model):
        import math

        result = regime_model._regime_from_value(float("nan"))
        assert result["regime"] == "Ranging"

    def test_result_has_all_required_keys(self, regime_model):
        result = regime_model._regime_from_value(20.0)
        for key in ("regime", "confidence", "indicator", "value"):
            assert key in result


# ---------------------------------------------------------------------------
# get_market_regime — uses cache and data_provider
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestGetMarketRegime:
    def test_returns_dict_with_regime_key(self, regime_model, mock_data_provider):
        # Simulate no VIX data → falls back to default
        mock_data_provider.get_data.return_value = pd.DataFrame()
        result = regime_model.get_market_regime(datetime(2024, 1, 15))
        assert "regime" in result

    def test_uses_vix_data_when_available(self, regime_model, mock_data_provider):
        vix_df = pd.DataFrame(
            {"close": [25.0, 26.0, 24.0]},
            index=pd.date_range("2024-01-13", periods=3),
        )
        mock_data_provider.get_data.return_value = vix_df
        result = regime_model.get_market_regime(datetime(2024, 1, 15))
        assert result["regime"] in ("Ranging", "Trending")  # 25 = boundary
        assert result["value"] is not None

    def test_caches_result_for_same_date(self, regime_model, mock_data_provider):
        mock_data_provider.get_data.return_value = pd.DataFrame()
        date = datetime(2024, 2, 1)
        regime_model.get_market_regime(date)
        regime_model.get_market_regime(date)
        # First call makes 2 get_data calls (VIX + SPY fallback), second call uses cache → 2 total
        assert mock_data_provider.get_data.call_count == 2

    def test_sim_client_bypasses_cache(self, regime_model):
        sim_client = MagicMock()
        sim_client.get_bars.return_value = pd.DataFrame()
        date = datetime(2024, 2, 1)
        regime_model.get_market_regime(date, sim_client=sim_client)
        regime_model.get_market_regime(date, sim_client=sim_client)
        # sim_client should be called every time (no cache)
        assert sim_client.get_bars.call_count >= 2

    def test_exception_returns_error_fallback(self, regime_model, mock_data_provider):
        mock_data_provider.get_data.side_effect = Exception("network error")
        result = regime_model.get_market_regime(datetime(2024, 3, 1))
        assert "regime" in result
        assert result["indicator"] == "Error"
