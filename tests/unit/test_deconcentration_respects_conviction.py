# tests/unit/test_deconcentration_respects_conviction.py
"""Churn root-cause fix — the de-concentration TRIM must not fight the conviction SIZER.

The dynamic sizer (risk_manager.calculate_position_size) intentionally targets 5–25% per name by
conviction. The trim previously de-concentrated EVERY name back to the flat 1/max_positions (=10%)
weight, so a conviction winner built to ~20% was trimmed to 10% and re-bought next cycle →
buy-high/sell-low churn (two contradictory target-weight authorities). With
DECONCENTRATION_TRIM_RESPECTS_CONVICTION (default True) the trim de-concentrates ONLY genuine
over-concentration past MAX_POSITION_PERCENT (25%), leaving the sizer's band intact and never
emitting INCREASE.
"""

from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import PortfolioManager


def _pm_with(weights_pct, respects, capital=100_000.0):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm._trim_respects_conviction = respects
    pm._max_position_pct = 0.25
    pm._drift_threshold_pct = 3.0
    pm.max_positions = 10
    pm._position_scores = {
        sym: MagicMock(market_value=capital * (pct / 100.0), total_score=70.0)
        for sym, pct in weights_pct.items()
    }
    pm.refresh_positions = lambda: None  # keep our hand-set positions
    pm._can_trade_symbol = (
        lambda s: True
    )  # isolate the target-weight logic from cooldowns
    return pm


def test_conviction_weight_below_cap_is_not_trimmed():
    # A 20% conviction position is inside the sizer's 5–25% band → must NOT be trimmed.
    assert _pm_with({"BNY": 20.0}, respects=True).get_rebalance_recommendations() == []


def test_genuine_over_concentration_is_trimmed_to_cap():
    # A 30% position is truly over-concentrated (> 25% cap + 3% drift) → REDUCE toward 25%, not 10%.
    recs = _pm_with({"BNY": 30.0}, respects=True).get_rebalance_recommendations()
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "BNY" and r["action"] == "REDUCE"
    assert r["target_pct"] == pytest.approx(25.0)
    assert r["adjustment_value"] == pytest.approx(
        -5000.0
    )  # 30% → 25% = -$5k, not -$20k


def test_no_increase_recs_in_conviction_mode():
    # A 4% low-conviction position must NOT be bought up to 10% (kills the latent INCREASE loop).
    assert _pm_with({"BNY": 4.0}, respects=True).get_rebalance_recommendations() == []


def test_legacy_flat_target_preserved_when_flag_off():
    # Rollback: flag off → old flat-1/N behaviour (a 20% name IS trimmed toward 10%).
    recs = _pm_with({"BNY": 20.0}, respects=False).get_rebalance_recommendations()
    assert len(recs) == 1 and recs[0]["action"] == "REDUCE"
    assert recs[0]["target_pct"] == pytest.approx(10.0)
    assert recs[0]["adjustment_value"] == pytest.approx(-10000.0)  # 20% → 10% = -$10k


def test_config_default_respects_conviction_both_editions():
    import importlib.util
    from pathlib import Path

    import config as ent

    assert ent.get_config().DECONCENTRATION_TRIM_RESPECTS_CONVICTION is True
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "config_oss_decon", str(root / "config.oss.py")
    )
    oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oss)
    assert oss.DECONCENTRATION_TRIM_RESPECTS_CONVICTION is True
