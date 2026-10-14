# flake8: noqa
# tests/unit/test_flow_jump_capacity_3068.py
# rc3 live finding (28.08.-family): a SECOND deposit of almost the same size as the first
# (5,786 vs 5,793) let TWO flows claim the SAME equity jump — the chart showed one stacked
# "+$11,578" marker, the week tile +1,654.70% (the first jump re-read as market return once
# the carry-guard neutralised the over-stacked flow) and the today tile -94.27%
# (11,929.70 - 11,579 = 350 over a 6,143 base). A jump of +5,792 can only BE ~5,792 of
# settlement: each jump's capacity must be consumed as flows attach.

from __future__ import annotations

import pytest

from core.engine.perf_metrics import align_cashflows_to_points

# Andre's real curve on 2026-08-27/28 (second deposit settled -> its own jump exists).
DATES = ["2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"]
EQUITY = [350.48, 349.94, 6142.36, 6143.38, 11929.70]
FLOWS = {"2026-08-24": 5793.00, "2026-08-26": 5786.35}


def test_two_similar_deposits_land_on_their_own_jumps():
    out = align_cashflows_to_points(DATES, EQUITY, FLOWS)
    by = dict(zip(DATES, out))
    assert by["2026-08-25"] == pytest.approx(5793.00)  # first deposit -> first jump
    assert by["2026-08-27"] == pytest.approx(5786.35)  # second deposit -> its own jump
    # never both on one point (the "+$11,578" stacked marker)
    assert max(abs(v) for v in out) < 6000


def test_second_deposit_waits_while_its_jump_is_not_in_the_curve_yet():
    """The settlement-transition state (the -5,784 / +1,654% display): the second
    deposit's activity row exists but its equity jump does NOT. The first jump's
    capacity is consumed by the first flow -> the second flow is DROPPED (it is not
    in the curve's equity yet), never stacked onto the foreign jump."""
    dates = ["2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"]
    equity = [350.48, 349.94, 6142.36, 6143.38, 6143.68]  # second jump NOT settled
    out = align_cashflows_to_points(dates, equity, FLOWS)
    assert sum(out) == pytest.approx(5793.00)  # only the settled deposit counts
    assert dict(zip(dates, out))["2026-08-25"] == pytest.approx(5793.00)


def test_two_deposits_settling_in_one_combined_jump_still_both_attach():
    """Legitimate opposite case: both flows settle into the SAME day -> the jump is the
    SUM (+11,579) and has capacity for both."""
    dates = ["2026-08-25", "2026-08-26"]
    equity = [350.0, 11929.0]
    out = align_cashflows_to_points(dates, equity, FLOWS)
    assert out == [0.0, pytest.approx(5793.00 + 5786.35)]
