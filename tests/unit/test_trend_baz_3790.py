"""#3790 — Mehrhorizont-Trend nach Baz et al. (2015) als Merkmal ``trend_baz`` und TrendAgent-Eingang.

Plan: docs/3790-macd-trend-rebuild/implementation_plan.md (Option A). Konstruktion je Horizont (S, L) in
{(8,24), (16,48), (32,96)}: MACD = EWMA_S − EWMA_L (alpha = 1/S), q = MACD / Std(Preis, 63), y = q / Std(q, 252),
φ(y) = y·exp(−y²/4)/0,89; ``trend_baz`` = Mittel der drei φ. Preisniveau-unabhängig, gesättigt.
"""

from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.vc1]

from core.round_table.agenten import trend  # noqa: E402
from core.round_table.agents import TrendAgent  # noqa: E402
from core.round_table.features import (  # noqa: E402
    BAZ_HORIZONS,
    TREND_BAZ_MIN_BARS,
    baz_phi,
    compute_technical_features,
    trend_baz_series,
)


def _frame(close: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=len(close))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(len(close), 1e6),
        },
        index=idx,
    )


def _walk(n: int, seed: int = 7, drift: float = 0.0005) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 50.0 * np.exp(np.cumsum(drift + 0.02 * rng.standard_normal(n)))


class TestTrendBazMerkmal:
    def test_preisniveau_unabhaengig(self):
        c = _walk(700)
        a = compute_technical_features(_frame(c))["trend_baz"].iloc[-1]
        b = compute_technical_features(_frame(c * 30.0))["trend_baz"].iloc[-1]
        assert math.isfinite(a)
        assert a == pytest.approx(b, abs=1e-9)

    def test_saettigung_phi(self):
        peak = baz_phi(math.sqrt(2.0))
        assert baz_phi(4.0) < peak
        assert baz_phi(-4.0) > -peak
        assert baz_phi(0.0) == 0.0
        assert abs(peak) < 1.0

    def test_mittel_ueber_drei_horizonte(self):
        c = pd.Series(_walk(700))
        parts = []
        for s, l in BAZ_HORIZONS:
            m = (
                c.ewm(alpha=1.0 / s, adjust=False).mean()
                - c.ewm(alpha=1.0 / l, adjust=False).mean()
            )
            q = m / c.rolling(63).std()
            y = q / q.rolling(252).std()
            parts.append(baz_phi(float(y.iloc[-1])))
        assert trend_baz_series(c).iloc[-1] == pytest.approx(
            float(np.mean(parts)), abs=1e-12
        )

    def test_zu_kurze_historie_nan(self):
        c = pd.Series(_walk(TREND_BAZ_MIN_BARS - 50))
        assert math.isnan(trend_baz_series(c).iloc[-1])

    def test_aufwaertstrend_positiv_abwaertstrend_negativ(self):
        up = compute_technical_features(_frame(_walk(700, drift=0.003)))[
            "trend_baz"
        ].iloc[-1]
        down = compute_technical_features(_frame(_walk(700, drift=-0.003)))[
            "trend_baz"
        ].iloc[-1]
        assert up > 0 > down


def _vote(features):
    return asyncio.run(TrendAgent().vote({"symbol": "X", "features": features}))


class TestTrendAgentBaz:
    def test_liest_trend_baz_nicht_macd_hist(self, monkeypatch):
        monkeypatch.setattr(trend, "_trend_agent_enabled", lambda: True)
        v = _vote({"macd_hist": 50.0, "trend_baz": -0.5})
        assert v.score is not None and v.score < 0.5
        assert "trend" in v.reasoning.lower()

    def test_enthaltung_bei_kurzer_historie(self, monkeypatch):
        monkeypatch.setattr(trend, "_trend_agent_enabled", lambda: True)
        v = _vote({"macd_hist": 1.0})
        assert v.weight == 0.0 and v.score is None
        assert "history too short" in v.reasoning

    def test_score_in_0_1_und_monoton(self, monkeypatch):
        monkeypatch.setattr(trend, "_trend_agent_enabled", lambda: True)
        xs = [-0.96, -0.5, -0.1, 0.0, 0.1, 0.5, 0.96]
        scores = [_vote({"trend_baz": x}).score for x in xs]
        assert all(0.0 < s < 1.0 for s in scores)
        assert all(a < b for a, b in zip(scores, scores[1:]))
        assert _vote({"trend_baz": 0.0}).score == pytest.approx(0.5)
