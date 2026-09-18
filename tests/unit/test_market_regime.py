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


def test_failure_result_uses_short_ttl_and_self_heals(monkeypatch):
    """A failed regime fetch (value=None) must NOT be cached for the day: within
    the 5-min failure TTL a live-VIX recovery is retried instead of returning the
    stale failure (2026-07-23: transient VIX outage kept VIXAwareRiskAgent
    EXCLUDED until restart)."""
    from datetime import datetime, timedelta
    from unittest.mock import MagicMock

    import core.market_regime as mr

    dp = MagicMock()
    dp.get_data.return_value = None  # VIX fetch fails
    model = mr.MarketRegimeModel(dp)
    monkeypatch.setattr(model, "_calculate_spy_volatility", lambda *a, **k: None)

    now = datetime.now()
    r1 = model.get_market_regime(now)
    assert r1["value"] is None  # failure cached
    key = now.strftime("%Y-%m-%d")
    # age the cached failure past the 5-min TTL but well within the day TTL
    data, _ = model.vix_cache[key]
    model.vix_cache[key] = (data, now - timedelta(minutes=6))
    # VIX now recovers
    import pandas as pd

    dp.get_data.return_value = pd.DataFrame(
        {"close": [18.0]}, index=pd.to_datetime(["2026-07-23"])
    )
    r2 = model.get_market_regime(now)
    assert r2["value"] is not None  # self-healed, not the stale failure


def test_successful_regime_keeps_day_ttl(monkeypatch):
    """A genuine live-VIX regime is byte-identical to before: cached for the full
    day, so an immediate second call does not re-fetch."""
    from datetime import datetime
    from unittest.mock import MagicMock

    import pandas as pd

    import core.market_regime as mr

    dp = MagicMock()
    dp.get_data.return_value = pd.DataFrame(
        {"close": [18.0]}, index=pd.to_datetime(["2026-07-23"])
    )
    model = mr.MarketRegimeModel(dp)
    now = datetime.now()
    model.get_market_regime(now)
    calls = dp.get_data.call_count
    model.get_market_regime(now)  # within day TTL → served from cache
    assert dp.get_data.call_count == calls


# ---------------------------------------------------------------------------
# #2958 — the SPY-fallback must be honest: label + WARNING (§5.6)
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestSpyFallbackHonesty:
    def test_spy_fallback_labels_spy_vol(self, regime_model):
        """A realized-vol reading of 18 (the NORMAL range) came back labeled
        "VIX" — the <10 heuristic never fires for real-world values (~12-30).
        The label must say what the number IS."""
        with patch.object(regime_model, "_calculate_spy_volatility", return_value=18.0):
            result = regime_model.get_market_regime(datetime(2026, 8, 19))
        assert result["value"] == pytest.approx(18.0)
        assert result["indicator"] == "SPY_VOL"

    def test_real_vix_keeps_vix_label(self, regime_model, mock_data_provider):
        vix_df = pd.DataFrame(
            {"close": [22.0, 25.0]},
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )
        mock_data_provider.get_data.return_value = vix_df
        result = regime_model.get_market_regime(datetime(2026, 8, 19))
        assert result["value"] == pytest.approx(25.0)
        assert result["indicator"] == "VIX"

    def test_spy_fallback_warns_not_debugs(self, regime_model, caplog):
        """CLAUDE.md §5.6: fallback substitution logs at WARNING, never DEBUG.
        The ^VIX->SPY substitution was invisible at default log level."""
        import logging as _logging

        with patch.object(
            regime_model, "_calculate_spy_volatility", return_value=18.0
        ), caplog.at_level(_logging.WARNING):
            regime_model.get_market_regime(datetime(2026, 8, 20))
        assert any(
            r.levelno == _logging.WARNING and "SPY" in r.getMessage()
            for r in caplog.records
        ), "the VIX->SPY substitution must be visible at WARNING"
