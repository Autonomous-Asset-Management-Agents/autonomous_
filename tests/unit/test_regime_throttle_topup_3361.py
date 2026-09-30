"""#3361 — the regime throttle must lower the END size of a position, not only the
size of each tranche.

Positions are built in tranches (per-order cash slot), and the sizer does not subtract
what is already held: the only authority that stops the build is the top-up dead-band
(`_topup_dead_band_reason`, target − held < band). Throttling the ORDER alone therefore
only slows the build — the book keeps topping up, in halves, until the UNthrottled
target is reached. So the dead-band has to see the throttled target as well.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.portfolio_manager import OpportunityScore, PortfolioManager, PositionScore


@pytest.fixture(autouse=True)
def _conviction_mode(monkeypatch):
    """#3619: this module pins the CONVICTION top-up target. In the clean-weight default
    ("b") the dead-band now uses the sizer's 1/N x vol target (measured overshoot fix), so
    the conviction path is selected explicitly here."""
    import config as _c

    monkeypatch.setattr(_c, "CLEAN_WEIGHT_SIZING", "off", raising=False)
    monkeypatch.setattr(_c.get_config(), "CLEAN_WEIGHT_SIZING", "off", raising=False)


CAPITAL = 100_000.0


def _pm():
    pm = PortfolioManager(client=MagicMock(), total_capital=CAPITAL)
    pm.refresh_positions = lambda: None
    pm._last_refresh_ok = True
    pm._conviction_ewma_enabled = False
    pm._topup_dead_band_pct = 5.0
    return pm


def _hold(pm, pct):
    value = CAPITAL * pct / 100.0
    pm._position_scores["X"] = PositionScore(
        symbol="X",
        qty=value / 100.0,
        avg_entry=100.0,
        current_price=100.0,
        market_value=value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _opp(conf=1.0):
    return OpportunityScore(
        symbol="X", current_price=100.0, model_confidence=conf, total_score=80.0
    )


def _build_until_blocked(pm, tranche_pct, factor):
    """Replay the cycle loop: buy a (throttled) tranche while the dead-band lets us."""
    held = 0.0
    for _ in range(50):
        if held > 0:
            _hold(pm, held)
            if pm._topup_dead_band_reason(_opp()) is not None:
                break
        held += tranche_pct * factor
    return held


def test_unthrottled_target_is_the_baseline():
    pm = _pm()
    target = pm._conviction_target_pct(1.0)
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=1.0):
        end = _build_until_blocked(pm, tranche_pct=5.0, factor=1.0)
    assert target - 5.0 < end <= target + 5.0


def test_risk_off_lowers_the_end_size_not_only_the_tranche():
    pm = _pm()
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=1.0):
        calm = _build_until_blocked(pm, tranche_pct=5.0, factor=1.0)
    pm = _pm()
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=0.5):
        stressed = _build_until_blocked(pm, tranche_pct=5.0, factor=0.5)
    # halved tranches AND a halved target: the position ends at about half, instead of
    # creeping up to the calm size in twice as many steps
    assert stressed <= calm * 0.5 + 5.0
    assert stressed < calm - 5.0


def test_held_name_at_the_throttled_target_is_not_topped_up():
    pm = _pm()
    target = pm._conviction_target_pct(1.0)
    _hold(pm, target * 0.5)
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=1.0):
        assert pm._topup_dead_band_reason(_opp()) is None  # calm: top-up allowed
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=0.5):
        reason = pm._topup_dead_band_reason(_opp())
    assert reason is not None and "regime" in reason


def test_a_new_name_is_never_blocked_by_the_throttle():
    pm = _pm()
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=0.5):
        assert pm._topup_dead_band_reason(_opp()) is None


def test_a_broken_signal_leaves_the_dead_band_as_it_was():
    pm = _pm()
    _hold(pm, pm._conviction_target_pct(1.0) * 0.5)
    with patch(
        "core.engine.regime_signal.active_throttle_factor",
        side_effect=RuntimeError("boom"),
    ):
        assert pm._topup_dead_band_reason(_opp()) is None


# --- the shared factor -------------------------------------------------------


def _cfg(enabled):
    return SimpleNamespace(
        REGIME_THROTTLE_ENABLED=enabled,
        REGIME_RISKOFF_PERCENTILE=75,
        REGIME_THROTTLE_SIZE_FACTOR=0.5,
    )


def test_factor_is_one_while_dark_and_never_reads_the_signal():
    from core.engine import regime_signal as rs

    with patch("config.get_config", return_value=_cfg(False)), patch.object(
        rs, "regime_reading"
    ) as reading:
        assert rs.active_throttle_factor() == 1.0
        reading.assert_not_called()


def test_factor_follows_the_reading_when_armed_and_fails_open():
    from core.engine import regime_signal as rs

    with patch("config.get_config", return_value=_cfg(True)):
        with patch.object(rs, "regime_reading", return_value={"factor": 0.5}):
            assert rs.active_throttle_factor() == 0.5
        with patch.object(rs, "regime_reading", side_effect=RuntimeError("boom")):
            assert rs.active_throttle_factor() == 1.0


def test_order_path_and_dead_band_share_one_factor():
    import inspect

    import core.engine.order_executor as oe
    import core.portfolio_manager as pm_mod

    assert "active_throttle_reading(" in inspect.getsource(oe._regime_throttled_size)
    pm_cls = pm_mod.PortfolioManager
    assert "_regime_target_factor()" in inspect.getsource(
        pm_cls._topup_dead_band_reason
    )
    assert "active_throttle_factor(" in inspect.getsource(pm_cls._regime_target_factor)
