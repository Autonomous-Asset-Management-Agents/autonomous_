# tests/unit/test_conviction_ewma_deadband.py
"""Conviction smoothing (EWMA) + top-up dead-band — churn fix follow-up.

The conviction that drives dynamic sizing re-rates every cycle from the raw round-table score, so a
held name's target weight jitters → repeated micro top-ups (buy-high/sell-low churn). Two guards:
  * EWMA smooths conviction per symbol (smoothed = alpha·raw + (1-alpha)·prev).
  * a top-up DEAD-BAND blocks re-buying a held name already within POSITION_TOPUP_DEAD_BAND_PCT of its
    (smoothed) conviction target weight.
"""

from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import OpportunityScore, PortfolioManager, PositionScore


def _pm(capital=100_000.0):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm.refresh_positions = lambda: None
    pm._last_refresh_ok = True
    pm._within_order_cooldown = lambda s: False
    pm._can_trade_symbol_when_room = lambda s: True
    return pm


def _opp(symbol, conf, score=80.0):
    return OpportunityScore(
        symbol=symbol, current_price=100.0, model_confidence=conf, total_score=score
    )


# ---- EWMA ----
def test_ewma_first_observation_returns_raw():
    assert _pm()._blend_conviction("X", 0.7) == pytest.approx(0.7)


def test_ewma_blends_with_prior():
    pm = _pm()
    pm._conviction_ewma_alpha = 0.3
    pm._conviction_ewma["X"] = 0.8
    assert pm._blend_conviction("X", 0.4) == pytest.approx(
        0.3 * 0.4 + 0.7 * 0.8
    )  # 0.68


def test_ewma_disabled_returns_raw():
    pm = _pm()
    pm._conviction_ewma_enabled = False
    pm._conviction_ewma["X"] = 0.8
    assert pm._blend_conviction("X", 0.4) == pytest.approx(0.4)


def test_update_conviction_advances_ewma_and_stores_smoothed():
    pm = _pm()
    pm._conviction_ewma_alpha = 0.3
    pm._conviction_ewma["X"] = 0.8
    ps = PositionScore(
        symbol="X",
        qty=10,
        avg_entry=100.0,
        current_price=100.0,
        market_value=10_000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    pm._position_scores["X"] = ps
    pm.update_position_conviction("X", 0.4)
    assert pm._conviction_ewma["X"] == pytest.approx(0.68)  # 0.3*0.4 + 0.7*0.8
    assert ps.conviction_score == pytest.approx(68.0)


def test_clear_conviction_resets_state():
    pm = _pm()
    pm._conviction_ewma["X"] = 0.8
    pm.clear_conviction("X")
    assert "X" not in pm._conviction_ewma


def test_full_exit_clears_conviction_ewma():
    # refresh_positions prunes a vanished name → its EWMA state must be dropped too (re-entry = raw).
    pm = PortfolioManager(client=MagicMock(), total_capital=100_000.0)
    pm._position_scores["X"] = MagicMock(symbol="X")
    pm._conviction_ewma["X"] = 0.8
    pm.client.get_all_positions.return_value = []  # X no longer held
    pm.refresh_positions()
    assert "X" not in pm._position_scores
    assert "X" not in pm._conviction_ewma


# ---- Dead-band (should_open_new_position, room path) ----
def test_deadband_blocks_topup_near_target():
    pm = _pm()
    pm._position_scores["X"] = MagicMock(market_value=18_000.0)  # held at 18%
    ok, reason, _ = pm.should_open_new_position(
        _opp("X", 0.6)
    )  # conv 0.6 -> target 20%
    assert ok is False and "dead-band" in reason  # gap 2% < 5% band


def test_deadband_allows_topup_far_below_target():
    pm = _pm()
    pm._position_scores["X"] = MagicMock(market_value=8_000.0)  # held at 8%
    ok, _, _ = pm.should_open_new_position(_opp("X", 0.6))  # target 20%, gap 12% > 5%
    assert ok is True


def test_deadband_skips_new_name():
    ok, _, _ = _pm().should_open_new_position(_opp("X", 0.6))  # not held → no dead-band
    assert ok is True


def test_deadband_disabled_allows_topup():
    pm = _pm()
    pm._topup_dead_band_pct = 0.0
    pm._position_scores["X"] = MagicMock(market_value=18_000.0)
    ok, _, _ = pm.should_open_new_position(_opp("X", 0.6))
    assert ok is True


@pytest.mark.mutates_global_state
def test_config_defaults_both_editions():
    import importlib.util
    from pathlib import Path

    import config as ent

    c = ent.get_config()
    assert c.CONVICTION_EWMA_ENABLED is True
    assert c.CONVICTION_EWMA_ALPHA == pytest.approx(0.3)
    assert c.POSITION_TOPUP_DEAD_BAND_PCT == pytest.approx(5.0)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "config_oss_ewma", str(root / "config.oss.py")
    )
    oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oss)
    assert oss.CONVICTION_EWMA_ENABLED is True
    assert oss.CONVICTION_EWMA_ALPHA == pytest.approx(0.3)
    assert oss.POSITION_TOPUP_DEAD_BAND_PCT == pytest.approx(5.0)
