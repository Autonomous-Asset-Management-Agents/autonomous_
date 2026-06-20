"""HAR-RV forward-volatility model — real non-LLM risk signal.

Guards the contract the Guardian sizing relies on: a forecast for healthy input,
a safe no-op (None / scaler 1.0) for degenerate input, and the risk-parity
direction (volatile -> size down, calm -> size up).
"""

import numpy as np
import pandas as pd

from core.ml.vol_model import forecast_forward_vol, vol_regime, vol_size_scaler


def _bars(daily_vol, n=400, seed=0):
    rng = np.random.RandomState(seed)
    rets = rng.normal(0, daily_vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(
        {
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
        }
    )


def test_forecast_in_plausible_range():
    fv = forecast_forward_vol(_bars(0.02))
    assert fv is not None
    assert 0.005 < fv < 0.06  # ~2%/day input -> forecast in a sane band


def test_forecast_tracks_volatility_level():
    calm = forecast_forward_vol(_bars(0.008, seed=1))
    wild = forecast_forward_vol(_bars(0.045, seed=2))
    assert calm is not None and wild is not None
    assert wild > calm  # higher-vol series -> higher forecast


def test_degenerate_input_is_safe_noop():
    assert forecast_forward_vol(pd.DataFrame({"close": [1, 2, 3]})) is None
    assert forecast_forward_vol(None) is None
    assert vol_size_scaler(pd.DataFrame({"close": [1, 2, 3]})) == 1.0  # no-op


def test_size_scaler_is_risk_parity_and_clamped():
    calm = vol_size_scaler(_bars(0.006, seed=3))  # low vol -> size UP
    wild = vol_size_scaler(_bars(0.05, seed=4))  # high vol -> size DOWN
    assert calm > wild
    assert 0.5 <= wild <= 1.5 and 0.5 <= calm <= 1.5


def test_regime_classifies():
    r = vol_regime(_bars(0.02))
    assert r in {"low", "normal", "high"}
