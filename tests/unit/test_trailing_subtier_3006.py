# tests/unit/test_trailing_subtier_3006.py
# #3006 (P1) — close the sub-+2% trailing-stop fall-through.
#
# #2681 gave `midterm` its wide tiers (>= +2% -> 6-15% / 72-120h) but left the PRE-LOOP
# default untouched: a winner whose gain is BELOW the lowest tier (0..+2%) still gets the
# hardcoded 1.5% / 1.0h trailing (intelligent_exit.py:348-349). Measured (live, 24.07-21.08):
# every trailing exit fired at 1.5-2.5% drawdown on near-flat positions the round table still
# rated BUY -> ~15% of paper turnover / ~3.4% of live turnover as round-trip churn.
#
# Fix (plan #3007, Option A): the sub-lowest-tier band follows the active profile.
#   legacy  -> (1.5%, 1.0h)  == today, byte-identical rollback.
#   midterm -> NO trailing exit below +2% (a +0.5% gain is not a gain to protect).
# Keyed off the existing EXIT_TRAIL_PROFILE; no new flag; loss side untouched.

from __future__ import annotations

import pytest


def _profile(monkeypatch, value):
    """Pin EXIT_TRAIL_PROFILE on the config seam intelligent_exit reads at call time."""
    import config

    monkeypatch.setattr(config, "EXIT_TRAIL_PROFILE", value, raising=False)


# A near-flat winner: +0.8% now, peaked +1.6% -> 1.6% off the high, held 2h (> the 1h
# legacy min-hold). This is exactly the IFF/PM/MU shape from the live blotter.
SUB2_PNL, SUB2_DD, SUB2_HELD = 0.8, 1.6, 2.0


# ---------------------------------------------------------------------------
# Red: under midterm a sub-+2% winner must NOT be trailing-exited
# ---------------------------------------------------------------------------


def test_midterm_sub2pct_winner_is_not_trailing_exited(monkeypatch):
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, "midterm")
    score = _calculate_trailing_stop_score(SUB2_PNL, SUB2_DD, SUB2_HELD)
    assert (
        score == 0.0
    ), f"midterm must not exit a sub-+2% winner on noise; score={score}"


def test_midterm_dynamic_pct_is_zero_below_2pct(monkeypatch):
    from core.intelligent_exit import get_dynamic_trailing_stop_pct

    _profile(monkeypatch, "midterm")
    assert get_dynamic_trailing_stop_pct(SUB2_PNL) == 0.0


# ---------------------------------------------------------------------------
# Rollback safety: legacy sub-+2% is byte-identical to today
# ---------------------------------------------------------------------------


def test_legacy_sub2pct_is_byte_identical(monkeypatch):
    from core.intelligent_exit import (
        _calculate_trailing_stop_score,
        get_dynamic_trailing_stop_pct,
    )

    _profile(monkeypatch, "legacy")
    # today: pre-loop default 1.5% / 1.0h -> fires at 1.6% drawdown after 2h
    assert _calculate_trailing_stop_score(SUB2_PNL, SUB2_DD, SUB2_HELD) >= 85
    assert get_dynamic_trailing_stop_pct(SUB2_PNL) == 1.5


# ---------------------------------------------------------------------------
# Guards: the governed tiers (>= +2%) and the loss side are untouched
# ---------------------------------------------------------------------------


def test_at_or_above_2pct_still_uses_the_tier(monkeypatch):
    from core.intelligent_exit import get_dynamic_trailing_stop_pct

    _profile(monkeypatch, "midterm")
    assert (
        get_dynamic_trailing_stop_pct(3.0) == 6.0
    )  # midterm +2-5% tier, not the sub-tier
    _profile(monkeypatch, "legacy")
    assert get_dynamic_trailing_stop_pct(3.0) == 1.5  # legacy +2-5% tier


def test_unknown_profile_subtier_falls_back_to_legacy(monkeypatch):
    from core.intelligent_exit import get_dynamic_trailing_stop_pct

    _profile(monkeypatch, "does-not-exist")
    # _active_trail_tiers already WARNs on unknown; the sub-tier fails safe to legacy 1.5%.
    assert get_dynamic_trailing_stop_pct(SUB2_PNL) == 1.5


@pytest.mark.parametrize("prof", ["legacy", "midterm"])
def test_losers_are_never_the_trailing_levers_business(monkeypatch, prof):
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, prof)
    assert _calculate_trailing_stop_score(-1.0, 9.0, 50.0) == 0.0
