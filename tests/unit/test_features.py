# tests/unit/test_features.py
# Epic 4.4 — Feature Engineering Layer
#
# Gherkin:
#   Given: A DataFrame with synthetic OHLCV data (250 rows)
#   When:  compute_technical_features() / compute_spy_features() is called
#   Then:  Returns DataFrame with expected feature columns, no import errors

from __future__ import annotations

import allure
import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n: int = 260) -> pd.DataFrame:
    """Generate deterministic synthetic OHLCV data."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 1.0)  # avoid negative prices
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = low + rng.uniform(0, 1, n) * (high - low)
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestFeaturesImport:
    def test_module_importable(self):
        from core.round_table.features import (  # noqa: F401
            AGENT_FEATURE_SETS,
            AGENT_KEYS,
            SPECIALIST_FEATURES,
            compute_specialist_features_from_report,
            compute_spy_features,
            compute_technical_features,
        )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestComputeTechnicalFeatures:
    def test_returns_dataframe(self):
        from core.round_table.features import compute_technical_features

        df = _make_ohlcv()
        result = compute_technical_features(df)
        assert isinstance(result, pd.DataFrame)

    def test_same_index_as_input(self):
        from core.round_table.features import compute_technical_features

        df = _make_ohlcv()
        result = compute_technical_features(df)
        assert list(result.index) == list(df.index)

    def test_expected_columns_present(self):
        from core.round_table.features import compute_technical_features

        df = _make_ohlcv()
        result = compute_technical_features(df)
        expected = [
            "rsi_14",
            "price_ma50",
            "price_ma200",
            "vol_ratio_5_20",
            "macd_hist",
            "atr_pct",
            "bb_position",
            "momentum_5d",
            "momentum_20d",
            "dist_52w_high",
            "dist_52w_low",
            "consec_up",
            "vol_anomaly",
            "range_expansion",
            "return_1d",
            "return_10d",
            "vol_regime",
            "realized_vol_20d",
            "obv_trend",
            "stochastic_k",
            "price_accel",
            "mean_rev_10d",
            "vol_price_div",
        ]
        for col in expected:
            assert col in result.columns, f"Missing feature column: {col}"

    def test_last_row_extractable_as_dict(self):
        from core.round_table.features import compute_technical_features

        df = _make_ohlcv()
        result = compute_technical_features(df)
        last_row = result.iloc[-1].to_dict()
        assert isinstance(last_row, dict)
        assert "rsi_14" in last_row

    def test_rsi_in_valid_range(self):
        from core.round_table.features import compute_technical_features

        df = _make_ohlcv()
        result = compute_technical_features(df)
        rsi = result["rsi_14"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all(), "RSI must be in [0, 100]"


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestComputeSpyFeatures:
    def test_returns_dataframe(self):
        from core.round_table.features import compute_spy_features

        spy_df = _make_ohlcv()
        result = compute_spy_features(spy_df)
        assert isinstance(result, pd.DataFrame)

    def test_expected_columns_present(self):
        from core.round_table.features import compute_spy_features

        spy_df = _make_ohlcv()
        result = compute_spy_features(spy_df)
        expected = [
            "spy_ma50",
            "spy_ma200",
            "spy_rsi",
            "spy_momentum_5d",
            "spy_momentum_20d",
            "spy_vol_ratio",
            "spy_vol_regime",
            "spy_return_10d",
            "spy_bb_position",
        ]
        for col in expected:
            assert col in result.columns, f"Missing SPY feature: {col}"


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestComputeSpecialistFeaturesFromReport:
    def test_returns_dict(self):
        from core.round_table.features import compute_specialist_features_from_report

        class FakeReport:
            insider_trades = [{"date": "2026-01-01", "shares": 1000}]
            material_events = []
            activist_stakes = []
            wiki_spike = True
            google_trend_score = 75

        result = compute_specialist_features_from_report(FakeReport())
        assert isinstance(result, dict)

    def test_expected_keys_present(self):
        from core.round_table.features import (
            SPECIALIST_FEATURES,
            compute_specialist_features_from_report,
        )

        class EmptyReport:
            pass

        result = compute_specialist_features_from_report(EmptyReport())
        for key in SPECIALIST_FEATURES:
            assert key in result, f"Missing specialist feature key: {key}"

    def test_all_values_are_float(self):
        from core.round_table.features import compute_specialist_features_from_report

        class EmptyReport:
            pass

        result = compute_specialist_features_from_report(EmptyReport())
        for k, v in result.items():
            assert isinstance(v, float), f"{k} should be float, got {type(v)}"

    def test_wiki_spike_flag_true(self):
        from core.round_table.features import compute_specialist_features_from_report

        class Report:
            insider_trades = []
            material_events = []
            activist_stakes = []
            wiki_spike = True
            google_trend_score = 0

        result = compute_specialist_features_from_report(Report())
        assert result["wiki_views_zscore"] == 2.5
        assert result["wiki_spike_ratio"] == 2.5

    def test_no_wiki_spike(self):
        from core.round_table.features import compute_specialist_features_from_report

        class Report:
            insider_trades = []
            material_events = []
            activist_stakes = []
            wiki_spike = False
            google_trend_score = 0

        result = compute_specialist_features_from_report(Report())
        assert result["wiki_views_zscore"] == 0.0
        assert result["wiki_spike_ratio"] == 1.0


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAgentFeatureSets:
    def test_all_eight_agents_present(self):
        from core.round_table.features import AGENT_FEATURE_SETS, AGENT_KEYS

        expected_agents = {
            "regime",
            "momentum",
            "drawdown",
            "squeeze",
            "catalyst",
            "specialist",
            "contrary",
            "construction",
        }
        assert set(AGENT_KEYS) == expected_agents
        assert set(AGENT_FEATURE_SETS.keys()) == expected_agents

    def test_each_agent_has_features(self):
        from core.round_table.features import AGENT_FEATURE_SETS

        for agent, features in AGENT_FEATURE_SETS.items():
            assert len(features) >= 5, f"Agent {agent} has too few features"
            assert all(isinstance(f, str) for f in features)

    def test_no_duplicate_features_per_agent(self):
        from core.round_table.features import AGENT_FEATURE_SETS

        for agent, features in AGENT_FEATURE_SETS.items():
            assert len(features) == len(
                set(features)
            ), f"Agent {agent} has duplicate feature names"

    def test_regime_uses_spy_features(self):
        from core.round_table.features import AGENT_FEATURE_SETS

        regime_features = AGENT_FEATURE_SETS["regime"]
        spy_features = [f for f in regime_features if f.startswith("spy_")]
        assert len(spy_features) >= 3, "Regime agent should use SPY features"

    def test_specialist_uses_alt_data_features(self):
        from core.round_table.features import AGENT_FEATURE_SETS, SPECIALIST_FEATURES

        specialist_features = set(AGENT_FEATURE_SETS["specialist"])
        for feat in SPECIALIST_FEATURES:
            assert (
                feat in specialist_features
            ), f"Specialist agent missing alt-data feature: {feat}"
