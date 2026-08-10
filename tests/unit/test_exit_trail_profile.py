# tests/unit/test_exit_trail_profile.py
# #2681 (P2) — intelligent_exit trailing tiers: flag-gated mid-term profile.
#
# Live evidence 2026-08-05 (paper, 0.3.14): HPE topped up 14:00:49 @ 53.57, FULL
# exit 14:30:26 @ 52.96 ("TRAILING STOP: Drawdown 1.6% vom High"), re-bought
# 15:01:28 @ 53.21 — buy-high/sell-low inside 60 minutes. Root cause is the
# HARDCODED tier table `TRAIL_PROFIT_TIERS` (intelligent_exit.py:39): at +2..5%
# profit it trails at 1.5% after just 1.0h — noise for a single stock, and
# structurally incompatible with the proven "selection + mid-term hold" pattern
# (#1957/#2401). Neither #2678 (SMART_EXIT_* rotation gates) nor #2404
# (EXIT_POLICY_TRAILING_ENABLED) touch this subsystem.
#
# Fix (#2681 Option A): `EXIT_TRAIL_PROFILE` ("legacy" default | "midterm"),
# resolved at CALL time by `_active_trail_tiers()` — module constants are frozen
# at import, so an env value would otherwise never arrive. Unknown value ->
# legacy + WARNING (§5.6). Loss side (LOSS_TIER_*, HARD_STOP_LOSS_PCT) untouched.
#
# Test plan (issue §TDD): T1 legacy default identity · T2 legacy score regression
# battery · T3 the real HPE case no longer exits under midterm · T4 midterm still
# protects a genuine collapse · T5 unknown profile fails safe + WARNING ·
# T6 loss path unchanged · T7 config parity (BORA).

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/

# The 05.08. HPE case, from decisions/activities: bought 53.57, high 53.82-ish,
# sold 52.96 => ~+2.4% vs the older lots' entry, 1.6% off the position high,
# roughly half an hour after the top-up.
HPE_PNL_PCT = 2.4
HPE_DRAWDOWN_PCT = 1.6
HPE_HOURS_HELD = 0.5


def _profile(monkeypatch, value):
    """Pin EXIT_TRAIL_PROFILE on the module-level config seam intelligent_exit
    reads (`_config` -> `getattr(config, name, default)`)."""
    import config

    monkeypatch.setattr(config, "EXIT_TRAIL_PROFILE", value, raising=False)


# ---------------------------------------------------------------------------
# T1 — legacy is the default and is the identity
# ---------------------------------------------------------------------------


def test_t1_default_profile_is_legacy_identity():
    from core.intelligent_exit import TRAIL_PROFIT_TIERS, _active_trail_tiers

    assert _active_trail_tiers() == TRAIL_PROFIT_TIERS


def test_t1b_explicit_legacy_is_identity(monkeypatch):
    from core.intelligent_exit import TRAIL_PROFIT_TIERS, _active_trail_tiers

    _profile(monkeypatch, "legacy")
    assert _active_trail_tiers() == TRAIL_PROFIT_TIERS


# ---------------------------------------------------------------------------
# T2 — legacy behaviour stays byte-identical (pinned score battery)
# ---------------------------------------------------------------------------

# (pnl_pct, drawdown_pct, hours_held) -> fires? under TODAY's table
LEGACY_BATTERY = [
    (HPE_PNL_PCT, HPE_DRAWDOWN_PCT, HPE_HOURS_HELD, False),  # 0.5h < 1.0h min-hold
    (HPE_PNL_PCT, HPE_DRAWDOWN_PCT, 1.5, True),  # the real 05.08. exit
    (2.2, 2.2, 2.0, True),  # the PCG case
    (3.0, 1.0, 5.0, False),  # dip below the 1.5% trail
    (12.0, 3.0, 10.0, False),  # tier 3: 4.0% trail not reached
    (12.0, 4.5, 10.0, True),  # tier 3 breached
    (-1.0, 9.0, 50.0, False),  # loss => not the trailing lever's business
]


@pytest.mark.parametrize("pnl,dd,held,fires", LEGACY_BATTERY)
def test_t2_legacy_scores_unchanged(monkeypatch, pnl, dd, held, fires):
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, "legacy")
    score = _calculate_trailing_stop_score(pnl, dd, held)
    assert (
        score >= 85
    ) is fires, f"legacy regression at {(pnl, dd, held)}: score={score}"


# ---------------------------------------------------------------------------
# T3 — the real HPE case must NOT exit under midterm
# ---------------------------------------------------------------------------


def test_t3_midterm_does_not_exit_the_hpe_case(monkeypatch):
    """05.08. HPE: +2.4% profit, 1.6% off the high, half an hour held.
    legacy exits (churn); midterm must hold."""
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, "legacy")
    legacy_score = _calculate_trailing_stop_score(HPE_PNL_PCT, HPE_DRAWDOWN_PCT, 1.5)
    assert legacy_score >= 85, "precondition: legacy must exit this case"

    _profile(monkeypatch, "midterm")
    midterm_score = _calculate_trailing_stop_score(
        HPE_PNL_PCT, HPE_DRAWDOWN_PCT, HPE_HOURS_HELD
    )
    assert midterm_score == 0.0, "midterm must not exit on 1.6% noise after 0.5h"


def test_t3b_midterm_holds_through_a_normal_daily_dip(monkeypatch):
    """A held winner (+8%) that dips 4% after 30h keeps running under midterm
    (legacy would exit at the 2.5% tier)."""
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, "legacy")
    assert _calculate_trailing_stop_score(8.0, 4.0, 30.0) >= 85

    _profile(monkeypatch, "midterm")
    assert _calculate_trailing_stop_score(8.0, 4.0, 30.0) == 0.0


# ---------------------------------------------------------------------------
# T4 — midterm still protects a genuine collapse
# ---------------------------------------------------------------------------


def test_t4_midterm_still_fires_on_a_real_collapse(monkeypatch):
    from core.intelligent_exit import _calculate_trailing_stop_score

    _profile(monkeypatch, "midterm")
    # +3% profit, 6.5% below the position high, held 80h -> past the 6%/72h tier
    assert _calculate_trailing_stop_score(3.0, 6.5, 80.0) >= 85


def test_t4b_midterm_gives_big_winners_more_room(monkeypatch):
    from core.intelligent_exit import get_dynamic_trailing_stop_pct

    _profile(monkeypatch, "legacy")
    assert get_dynamic_trailing_stop_pct(40.0) == pytest.approx(8.0)

    _profile(monkeypatch, "midterm")
    assert get_dynamic_trailing_stop_pct(40.0) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# T5 — unknown profile fails safe to legacy + WARNING
# ---------------------------------------------------------------------------


def test_t5_unknown_profile_falls_back_to_legacy_with_warning(monkeypatch, caplog):
    from core.intelligent_exit import TRAIL_PROFIT_TIERS, _active_trail_tiers

    _profile(monkeypatch, "wat")
    with caplog.at_level(logging.WARNING):
        tiers = _active_trail_tiers()

    assert tiers == TRAIL_PROFIT_TIERS
    assert any(
        rec.levelno >= logging.WARNING and "EXIT_TRAIL_PROFILE" in rec.getMessage()
        for rec in caplog.records
    ), "an unknown profile must be visible at WARNING (§5.6)"


# ---------------------------------------------------------------------------
# T6 — the loss side is untouched by the profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["legacy", "midterm"])
def test_t6_loss_pressure_identical_across_profiles(monkeypatch, profile):
    from core.intelligent_exit import HARD_STOP_LOSS_PCT, _calculate_loss_pressure

    _profile(monkeypatch, profile)
    # a hard-stop-level loss must score the same regardless of the trail profile
    assert _calculate_loss_pressure(HARD_STOP_LOSS_PCT - 1.0, 30.0) >= 85


# ---------------------------------------------------------------------------
# T7 — config parity (BORA)
# ---------------------------------------------------------------------------


def test_t7_enterprise_config_defines_profile_default_legacy():
    import config

    cfg = config.get_config()
    assert hasattr(
        cfg, "EXIT_TRAIL_PROFILE"
    ), "config.py must define EXIT_TRAIL_PROFILE"
    assert cfg.EXIT_TRAIL_PROFILE == "legacy", "default must be legacy (byte-identical)"


def test_t7b_oss_config_defines_profile_default_legacy():
    spec = importlib.util.spec_from_file_location(
        "config_oss_trail_profile_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(
        module, "EXIT_TRAIL_PROFILE"
    ), "config.oss.py must define EXIT_TRAIL_PROFILE (BORA parity)"
    assert module.EXIT_TRAIL_PROFILE == "legacy"
