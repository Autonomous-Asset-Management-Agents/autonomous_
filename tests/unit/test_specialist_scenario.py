# tests/unit/test_specialist_scenario.py
# Rich-synthesis card (Direction A) — honest, non-directional HAR-RV vol band.
#
# Gherkin (plan):
#   Szenario: Vol-Band aus dem HAR-RV-Forecast
#     Angenommen der Forward-Vol-Forecast liefert daily_vol
#     Wenn vol_scenario läuft
#     Dann ist sigma = daily_vol * sqrt(20) * 100, base = 0.0, low = -sigma, high = +sigma
#     Und scenario_basis = "realized_vol_har"
#   Szenario: Kein Forecast -> keine Zeile
#     Angenommen zu wenig Historie (<180 Bars) -> forecast None
#     Dann gibt vol_scenario None (die Karte lässt die Szenario-Zeile weg)
#
# HARD INVARIANT: the band is a SPREAD, never a direction — base is exactly 0.0
# (no drift claim), because no directional edge survived FDR (TFT 0/488).

from __future__ import annotations

import math
from unittest.mock import patch

import allure
import numpy as np
import pandas as pd

from core.specialist import scenario


def _bars(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({"close": close})


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Scenario Band")
class TestVolScenario:
    def test_band_is_symmetric_zero_based_sigma(self):
        with patch.object(scenario, "forecast_forward_vol", return_value=0.02):
            out = scenario.vol_scenario(_bars(200))
        assert out is not None
        expected_sigma = 0.02 * math.sqrt(20) * 100.0
        assert out["vol_band_sigma_pct"] == expected_sigma
        assert out["vol_band_high_pct"] == expected_sigma
        assert out["vol_band_low_pct"] == -expected_sigma
        # base is EXACTLY 0.0 — a spread, never a directional claim.
        assert out["vol_band_base_pct"] == 0.0
        assert out["scenario_basis"] == "realized_vol_har"

    def test_no_forecast_returns_none(self):
        with patch.object(scenario, "forecast_forward_vol", return_value=None):
            assert scenario.vol_scenario(_bars(200)) is None

    def test_nonpositive_forecast_returns_none(self):
        with patch.object(scenario, "forecast_forward_vol", return_value=0.0):
            assert scenario.vol_scenario(_bars(200)) is None

    def test_too_little_history_returns_none_real_path(self):
        # Real HAR-RV path: <180 bars -> forecast None -> band None.
        assert scenario.vol_scenario(_bars(120)) is None

    def test_real_path_structure_when_available(self):
        out = scenario.vol_scenario(_bars(400, seed=3))
        # HAR-RV may or may not fit on random data, but when it does the shape
        # must be the honest symmetric spread.
        if out is not None:
            assert out["vol_band_base_pct"] == 0.0
            assert out["vol_band_low_pct"] == -out["vol_band_sigma_pct"]
            assert out["vol_band_high_pct"] == out["vol_band_sigma_pct"]
            assert out["vol_band_sigma_pct"] > 0
