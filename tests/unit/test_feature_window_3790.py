"""#3790 — Merkmalsfenster: MACD, MA200 und der Baz-Trend brauchen eingeschwungene Historie.

Vorher holte der Feature-Knoten 30 Kalendertage (≈ 21 Handelstage): EMA(26) nie eingeschwungen,
``price_ma200`` NaN. Der DrawdownGuard bleibt auf 30 Handelstagen (eigener Abruf, eigener Test).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.vc1]

from core.orchestration import graph  # noqa: E402
from core.round_table import agents  # noqa: E402
from core.round_table.features import (  # noqa: E402
    TREND_BAZ_MIN_BARS,
    compute_technical_features,
)


def _frame(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 80.0 * np.exp(np.cumsum(0.0004 + 0.018 * rng.standard_normal(n)))
    idx = pd.bdate_range("2021-01-04", periods=n)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1e6,
        },
        index=idx,
    )


def _handelstage(kalendertage: int) -> int:
    return int(kalendertage * 252 / 365)


def test_fenster_reicht_fuer_baz_trend():
    assert _handelstage(graph._FEATURE_WINDOW_DAYS) >= TREND_BAZ_MIN_BARS


def test_macd_eingeschwungen_gegen_volle_historie():
    full = _frame(1200)
    tail = full.iloc[-_handelstage(graph._FEATURE_WINDOW_DAYS) :]
    a = compute_technical_features(full)["macd_hist"].iloc[-1]
    b = compute_technical_features(tail)["macd_hist"].iloc[-1]
    assert b == pytest.approx(a, rel=1e-6, abs=1e-9)


def test_price_ma200_endlich():
    tail = _frame(1200).iloc[-_handelstage(graph._FEATURE_WINDOW_DAYS) :]
    assert np.isfinite(compute_technical_features(tail)["price_ma200"].iloc[-1])


def test_trend_baz_endlich_im_fenster():
    tail = _frame(1200).iloc[-_handelstage(graph._FEATURE_WINDOW_DAYS) :]
    assert np.isfinite(compute_technical_features(tail)["trend_baz"].iloc[-1])


def test_drawdown_guard_bleibt_30_handelstage():
    assert agents.DRAWDOWN_WINDOW_TRADING_DAYS == 30
