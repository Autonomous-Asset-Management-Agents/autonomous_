"""TFT feature builder (fusion S1-E1, core/ml brick — dormant).

`FeatureBuilder.build()` turns raw OHLCV bars into the fixed `FEATURE_COLUMNS`
contract the per-symbol TFT consumes. Pure numpy/pandas, no heavy deps. Lands
DORMANT: nothing on main imports it yet (the specialist feature-pipeline wiring
is a later epic), so it is purely additive.
"""

import numpy as np
import pandas as pd

from core.ml.feature_builder import FEATURE_COLUMNS, MIN_ROWS, FeatureBuilder

# core (non-static, non-calendar-index) features must be finite after NaN-drop
_CORE = [
    c
    for c in FEATURE_COLUMNS
    if c
    not in (
        "time_idx",
        "sector_encoded",
        "market_cap_bucket",
        "earnings_in_next_5d",
        "ex_div_in_next_5d",
    )
]


def _synthetic_bars(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    volume = rng.integers(1_000_000, 5_000_000, n)
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_insufficient_rows_returns_empty_frame_with_columns():
    df = FeatureBuilder().build(_synthetic_bars(30), symbol="AAPL")
    assert list(df.columns) == FEATURE_COLUMNS
    assert df.empty


def test_builds_full_tft_feature_frame():
    df = FeatureBuilder().build(
        _synthetic_bars(320),
        symbol="AAPL",
        sector="Technology",
        market_cap_bucket="large",
    )
    assert list(df.columns) == FEATURE_COLUMNS
    assert len(df) >= MIN_ROWS
    # every core feature is finite (no NaN / inf left after the dropna)
    assert np.isfinite(df[_CORE].to_numpy(dtype="float64")).all()
    # time_idx is re-based to 0 after the NaN drop
    assert int(df["time_idx"].iloc[0]) == 0


def test_build_is_deterministic():
    bars = _synthetic_bars(320)
    a = FeatureBuilder().build(bars, symbol="AAPL")
    b = FeatureBuilder().build(bars, symbol="AAPL")
    pd.testing.assert_frame_equal(a, b)
