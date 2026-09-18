# tests/unit/test_clean_weight_trim_3284.py
"""#3284 (Epic #3086) — the de-concentration TRIM must follow the clean-weight target.

CLEAN_WEIGHT_SIZING routes the trim's target authority so the rebalance-target does
not contradict the clean-weight buy-target:
  "off" (default) -> unchanged: conviction/cap-breach trim (byte-identical).
  "a"   (strict 1/N) -> symmetric flat-1/N trim (the existing Legacy target path):
        overweight REDUCED and underweight INCREASED toward 1/N.
  "b"   (1/N x vol) INTERIM -> keep the conviction/cap-breach trim (only trims genuine
        over-concentration past MAX; never fights the per-symbol vol-tilt). A vol-aware
        symmetric arm-b trim needs per-symbol forecast_vol plumbed into the rebalance
        path (PositionScore has none) — separate follow-up.
"""

from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import PortfolioManager


def _pm(weights_pct, capital=100_000.0):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm._trim_respects_conviction = True  # production default
    pm._max_position_pct = 0.25
    pm._drift_threshold_pct = 3.0
    pm.max_positions = 10  # flat 1/N target = 10%
    pm._position_scores = {
        sym: MagicMock(market_value=capital * (pct / 100.0), total_score=70.0)
        for sym, pct in weights_pct.items()
    }
    pm.refresh_positions = lambda: None
    pm._can_trade_symbol = lambda s: True
    return pm


def _set(monkeypatch, mode):
    import config

    monkeypatch.setattr(config, "CLEAN_WEIGHT_SIZING", mode, raising=False)


class TestOffByteIdentical:
    def test_off_below_cap_not_trimmed(self, monkeypatch):
        _set(monkeypatch, "off")
        # 20% is inside the conviction band, below the 25% cap → no trim (today's behaviour).
        assert _pm({"AAA": 20.0}).get_rebalance_recommendations() == []

    def test_off_no_increase(self, monkeypatch):
        _set(monkeypatch, "off")
        # 4% underweight is NOT bought up in conviction mode.
        assert _pm({"AAA": 4.0}).get_rebalance_recommendations() == []


class TestArmASymmetricOneOverN:
    def test_a_overweight_reduced_to_flat_1n(self, monkeypatch):
        _set(monkeypatch, "a")
        recs = _pm({"AAA": 20.0}).get_rebalance_recommendations()
        assert len(recs) == 1
        assert recs[0]["symbol"] == "AAA" and recs[0]["action"] == "REDUCE"
        assert recs[0]["target_pct"] == pytest.approx(10.0)  # flat 1/N

    def test_a_underweight_increased_to_flat_1n(self, monkeypatch):
        _set(monkeypatch, "a")
        recs = _pm({"AAA": 4.0}).get_rebalance_recommendations()
        assert len(recs) == 1
        assert recs[0]["symbol"] == "AAA" and recs[0]["action"] == "INCREASE"
        assert recs[0]["target_pct"] == pytest.approx(10.0)

    def test_a_within_deadband_not_trimmed(self, monkeypatch):
        _set(monkeypatch, "a")
        # 11% vs 10% target, drift 1% < 3% dead-band → no trim.
        assert _pm({"AAA": 11.0}).get_rebalance_recommendations() == []


class TestArmBInterimUnchanged:
    def test_b_leaves_vol_tilt_band_intact(self, monkeypatch):
        _set(monkeypatch, "b")
        # A 20% name (below the 25% cap) is NOT pulled back to flat 1/N — the interim
        # arm-b trim only caps genuine over-concentration, never fights the vol-tilt.
        assert _pm({"AAA": 20.0}).get_rebalance_recommendations() == []

    def test_b_still_caps_genuine_over_concentration(self, monkeypatch):
        _set(monkeypatch, "b")
        recs = _pm({"AAA": 30.0}).get_rebalance_recommendations()
        assert len(recs) == 1
        assert recs[0]["action"] == "REDUCE"
        assert recs[0]["target_pct"] == pytest.approx(25.0)  # toward MAX, not 1/N
