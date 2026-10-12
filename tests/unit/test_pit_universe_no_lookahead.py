# tests/unit/test_pit_universe_no_lookahead.py
# #1907 (MLR-7 RC-2) Increment 2 — the look-ahead defect, healed on a PIT-capable table.
#
# THE DEFECT (pre-Inc-2): get_sp500_symbols_at_date started from TODAY's index list and
# only ever ADDED historical members back — it never removed today's names that were not
# yet in the index on query_date. A 2019 backtest universe therefore contained PLTR/COIN/
# HOOD — future winners — which is look-ahead bias built into the universe itself, the
# exact mirror image of the survivorship bias the overlay claims to heal.
#
# THE FIX (this increment): once the membership table is PIT-capable (it carries
# open-ended rows, i.e. current members with an empty end_date), the overlay becomes a
# real set-diff: names whose recorded membership does not cover query_date are DISCARDED,
# names whose membership covers it are included, and only names entirely unknown to the
# table are kept best-effort (loudly, at WARNING — CODING_POLICY §5.6).
#
# BACK-COMPAT: a removals-only table (every row carries an end_date) cannot key a
# discard on anything — for those the old additive-only behaviour is preserved and the
# residual look-ahead stays flagged at WARNING (pinned by
# tests/unit/test_survivorship_claim_honesty.py::TestAdditiveOnlyOverlayIsFlaggedLoudly).

from __future__ import annotations

import datetime as dt
import logging
from unittest.mock import patch

import pytest

import core.data_provider as dp

# A PIT-capable table: open-ended rows for current members (empty end_date), a
# post-2019 entrant with a real start_date (PLTR), and two real ex-members.
PIT_CAPABLE = (
    "symbol,start_date,end_date\n"
    "AAPL,1982-11-30,\n"
    "MSFT,1994-06-01,\n"
    "PLTR,2021-09-20,\n"
    "SIVB,2018-03-12,2023-03-13\n"
    "TWTR,2013-11-07,2022-10-27\n"
)


@pytest.fixture
def pit_csv(monkeypatch, tmp_path):
    p = tmp_path / "membership.csv"
    p.write_text(PIT_CAPABLE, encoding="utf-8")
    monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(p))
    return p


@pytest.fixture
def provider():
    return dp.HistoricalDataProvider(api=None)


def _universe(provider, when, today=("AAPL", "MSFT", "PLTR")):
    with patch.object(provider, "get_sp500_symbols", return_value=list(today)):
        return provider.get_sp500_symbols_at_date(when)


class TestFutureEntrantsAreDiscarded:
    """R1 — the Gherkin scenario from the plan: a 2019 universe must not contain a
    name that entered the index in 2021."""

    def test_pltr_is_not_in_a_2019_universe(self, pit_csv, provider):
        result = _universe(provider, dt.datetime(2019, 6, 1))
        assert "PLTR" not in result, (
            "PLTR (start_date 2021-09-20) in a 2019-06-01 universe is look-ahead "
            "bias — the set-diff must discard post-dated entrants"
        )

    def test_a_true_2019_member_that_later_left_is_included(self, pit_csv, provider):
        """R2 — the survivorship side must keep working: TWTR was a member in 2019
        and left 2022-10-27."""
        result = _universe(provider, dt.datetime(2019, 6, 1))
        assert "TWTR" in result
        assert "SIVB" in result  # member 2018-03-12 .. 2023-03-13

    def test_pltr_is_included_once_its_membership_started(self, pit_csv, provider):
        result = _universe(provider, dt.datetime(2022, 6, 1))
        assert "PLTR" in result

    def test_departed_members_are_gone_after_their_end_date(self, pit_csv, provider):
        result = _universe(provider, dt.datetime(2023, 6, 1))
        assert "SIVB" not in result
        assert "TWTR" not in result


class TestOpenEndedMembersAreIncluded:
    """R3 — open-ended rows (current members) resolve as members for any date
    on/after their start."""

    def test_open_ended_member_is_included(self, pit_csv, provider):
        result = _universe(provider, dt.datetime(2019, 6, 1))
        assert "AAPL" in result
        assert "MSFT" in result

    def test_open_ended_member_is_excluded_before_its_start(self, pit_csv, provider):
        result = _universe(provider, dt.datetime(1990, 1, 1))
        assert "MSFT" not in result  # start 1994-06-01
        assert "AAPL" in result  # start 1982-11-30


class TestUnknownNamesAreKeptLoudly:
    """A name in today's list that the table does not know at all cannot be judged —
    it is kept (best-effort), and that residual limitation is logged at WARNING
    (§5.6), never silently."""

    def test_unknown_symbol_is_kept_and_warned_about(self, pit_csv, provider, caplog):
        with caplog.at_level(logging.WARNING, logger=""):
            result = _universe(
                provider,
                dt.datetime(2019, 6, 1),
                today=("AAPL", "MSFT", "PLTR", "ZZZT"),
            )
        assert "ZZZT" in result
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("ZZZT" in m or "unknown" in m.lower() for m in warnings), (
            "keeping a table-unknown name is a best-effort residual and must be "
            f"WARNED about; got: {warnings!r}"
        )


class TestFallbackWhenCsvMissing:
    """R4 — unchanged: no CSV means no overlay; fall back to the current list and
    say so at WARNING."""

    def test_missing_csv_falls_back_to_current_list(
        self, monkeypatch, tmp_path, provider, caplog
    ):
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(tmp_path / "nope.csv"))
        with caplog.at_level(logging.WARNING, logger=""):
            result = _universe(provider, dt.datetime(2019, 6, 1))
        assert sorted(result) == ["AAPL", "MSFT", "PLTR"]
        assert any(r.levelno == logging.WARNING for r in caplog.records)


class TestRemovalsOnlyTableKeepsAdditiveBackCompat:
    """A removals-only table has nothing to key a discard on — the pre-Inc-2
    additive behaviour (plus its look-ahead WARNING) must survive unchanged."""

    def test_no_discard_on_removals_only_table(
        self, monkeypatch, tmp_path, provider, caplog
    ):
        p = tmp_path / "removals_only.csv"
        p.write_text(
            "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(p))
        with caplog.at_level(logging.WARNING, logger=""):
            result = _universe(provider, dt.datetime(2019, 6, 1))
        # PLTR stays (additive-only — nothing to discard on), SIVB is re-added.
        assert "PLTR" in result
        assert "SIVB" in result
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("look-ahead" in m for m in warnings)
