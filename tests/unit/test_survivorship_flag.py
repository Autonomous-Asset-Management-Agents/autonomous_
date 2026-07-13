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


def test_membership_available_when_csv_is_shipped():
    # data/sp500_historical_membership.csv is committed in the repo.
    assert HistoricalDataProvider.has_point_in_time_membership() is True


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
