"""SIM-1 T2 (#1485): survivorship honesty.

The simulation already wires point-in-time S&P 500 membership (simulation.py uses
``get_sp500_symbols_at_date``, ML-1 Phase 5). What was missing is an HONEST signal the Console can
surface: was point-in-time membership actually applied, or did the backtest fall back to the
(survivorship-biased) current index? ``has_point_in_time_membership`` is that testable predicate.
"""

import os
from unittest.mock import patch

import core.data_provider as dp_mod
from core.data_provider import HistoricalDataProvider


def test_membership_claim_granted_for_shipped_pit_capable_csv():
    # #1907 Inc 2 (MLR-7): the committed data/sp500_historical_membership.csv is now a
    # free-source PIT reconstruction with open-ended rows for current members — it CAN
    # reconstruct per-day membership, so the no-arg claim is honestly True again
    # (coverage judgement still applies when as_of is passed; see
    # tests/unit/test_survivorship_claim_honesty.py).
    assert HistoricalDataProvider.has_point_in_time_membership() is True


def test_membership_claim_denied_for_removals_only_csv(tmp_path):
    # #1907 Inc 1 (MLR-7 RC-1/RC-3), pinned on a fixture: a removals-only table (every
    # row carries an end_date) cannot reconstruct per-day membership — bare file
    # existence must never read as "survivorship-adjusted"; the Console shows its
    # limitation flag instead of suppressing it.
    p = tmp_path / "removals_only.csv"
    p.write_text(
        "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\n", encoding="utf-8"
    )
    with patch.object(dp_mod, "SP500_MEMBERSHIP_CSV", str(p)):
        assert HistoricalDataProvider.has_point_in_time_membership() is False


def test_membership_unavailable_when_csv_absent():
    with patch.object(
        dp_mod, "SP500_MEMBERSHIP_CSV", os.path.join("no", "such", "file.csv")
    ):
        assert HistoricalDataProvider.has_point_in_time_membership() is False


def test_survivorship_adjusted_only_for_sp500_with_csv():
    # The flag the result carries: point-in-time is meaningful only for the S&P 500 universe,
    # and only when the membership CSV is present.
    from core.data_provider import survivorship_adjusted

    assert survivorship_adjusted("sp500", has_membership=True) is True
    assert (
        survivorship_adjusted("sp500", has_membership=False) is False
    )  # → honest UI flag
    assert survivorship_adjusted("full_market", has_membership=True) is False
