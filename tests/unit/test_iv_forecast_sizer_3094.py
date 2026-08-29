"""#3094 (a) — IV aus dem Konsens-Mittel in den Sizer.

Zwei pure Bausteine getestet:
  * die VRP-entzerrte annualisiert→täglich-Umrechnung der IV (vol_model)
  * die flag-abhängige Ausschlussmenge des Konsens-Mittels (consensus)
"""

import math

import pytest

from core.ml.vol_model import debiased_daily_vol_from_iv
from core.round_table.consensus import NON_DIRECTIONAL_AGENTS, consensus_exclusions

SQRT252 = math.sqrt(252.0)


def test_debias_converts_annual_to_daily_and_removes_vrp():
    # IV 0.30 p.a., default de-bias factor 0.80 -> daily forecast
    got = debiased_daily_vol_from_iv(0.30, vrp_factor=0.80)
    assert got == pytest.approx(round(0.30 / SQRT252 * 0.80, 6), abs=1e-9)


def test_debias_factor_one_is_raw_option_a_arm():
    got = debiased_daily_vol_from_iv(0.30, vrp_factor=1.0)
    assert got == pytest.approx(round(0.30 / SQRT252, 6), abs=1e-9)


def test_debias_invalid_inputs_return_none():
    for bad in (None, 0.0, -0.1, float("inf"), float("nan"), True, "0.3"):
        assert debiased_daily_vol_from_iv(bad) is None


def test_debias_default_factor_is_below_one():
    # a raw IV (factor 1.0) must size DOWN more than the de-biased default
    raw = debiased_daily_vol_from_iv(0.40, vrp_factor=1.0)
    deb = debiased_daily_vol_from_iv(
        0.40
    )  # default factor < 1 -> smaller vol -> larger scaler
    assert deb < raw


def test_consensus_exclusions_flag_off_is_byte_identical():
    assert consensus_exclusions(False) == NON_DIRECTIONAL_AGENTS


def test_consensus_exclusions_flag_on_adds_vixaware():
    excl = consensus_exclusions(True)
    assert "VIXAwareRiskAgent" in excl
    for a in NON_DIRECTIONAL_AGENTS:
        assert a in excl


def test_consensus_exclusions_flag_on_only_adds_vixaware():
    assert set(consensus_exclusions(True)) == set(NON_DIRECTIONAL_AGENTS) | {
        "VIXAwareRiskAgent"
    }
