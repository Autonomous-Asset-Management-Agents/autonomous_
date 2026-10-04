# tests/unit/test_vol_size_scaler_from_forecast.py
"""#1953 TRD-2 Inc 1 — scalar risk-parity helper (T1-T5).

`vol_size_scaler_from_forecast` is the SCALAR twin of the bars-based
`vol_size_scaler` (core/ml/vol_model.py): it takes the ALREADY-COMPUTED
forward-vol forecast (plumbed through the DecisionContext) instead of
re-fetching bars at the sizing call-site. Contract:

  scaler = clip(target_daily_vol / forecast_vol, lo, hi)
  missing/invalid forecast -> 1.0 (fail-safe no-op, never over-size)

The bars variant must DELEGATE to the scalar variant so the two can never
drift apart (T5 regression).
"""

import numpy as np
import pandas as pd
import pytest

from core.ml import vol_model
from core.ml.vol_model import vol_size_scaler, vol_size_scaler_from_forecast


class TestScalarRiskParityCore:
    def test_t1_forecast_equal_target_is_neutral(self):
        assert vol_size_scaler_from_forecast(0.015, target_daily_vol=0.015) == 1.0

    def test_t2_calm_forecast_clamps_at_hi(self):
        # 0.015 / 0.005 = 3.0 -> clamped to hi=1.5 (size UP, bounded)
        assert vol_size_scaler_from_forecast(0.005) == 1.5

    def test_t3_wild_forecast_clamps_at_lo(self):
        # 0.015 / 0.06 = 0.25 -> clamped to lo=0.5 (size DOWN, bounded)
        assert vol_size_scaler_from_forecast(0.06) == 0.5

    def test_unclamped_midrange_is_exact_ratio(self):
        # 0.015 / 0.02 = 0.75 — inside [0.5, 1.5], no clamp
        assert vol_size_scaler_from_forecast(0.02) == pytest.approx(0.75)

    def test_custom_target_and_bounds_are_respected(self):
        assert vol_size_scaler_from_forecast(
            0.01, target_daily_vol=0.02, lo=0.8, hi=1.2
        ) == pytest.approx(1.2)


class TestFailSafeNoOp:
    @pytest.mark.parametrize(
        "bad", [None, 0.0, -0.01, float("nan"), float("inf"), "0.02"]
    )
    def test_t4_missing_or_invalid_forecast_is_noop(self, bad):
        assert vol_size_scaler_from_forecast(bad) == 1.0


class TestBarsVariantDelegates:
    def test_t5_bars_variant_delegates_to_scalar_variant(self, monkeypatch):
        """Regression: vol_size_scaler(bars) == vol_size_scaler_from_forecast(fv)
        for the SAME forecast — single source of truth for the risk-parity math."""
        monkeypatch.setattr(vol_model, "forecast_forward_vol", lambda bars: 0.005)
        bars = pd.DataFrame({"close": np.linspace(100, 110, 400)})
        assert vol_size_scaler(bars) == vol_size_scaler_from_forecast(0.005) == 1.5

    def test_t5b_bars_variant_none_forecast_still_noop(self, monkeypatch):
        monkeypatch.setattr(vol_model, "forecast_forward_vol", lambda bars: None)
        bars = pd.DataFrame({"close": np.linspace(100, 110, 400)})
        assert vol_size_scaler(bars) == 1.0
