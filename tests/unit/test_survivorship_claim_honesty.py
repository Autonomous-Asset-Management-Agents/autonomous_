# tests/unit/test_survivorship_claim_honesty.py
# The Console labels a backtest "survivorship-adjusted" — this suite keeps that label
# honest.
#
# HISTORY:
#   Pre-#1907 the shipped data/sp500_historical_membership.csv held 22 data rows, ALL
#   carrying an end_date — removals-only, i.e. it listed only names that LEFT the index,
#   never who was IN it (coverage 2018-06-01 .. 2024-12-23), while
#   `has_point_in_time_membership()` was `os.path.exists(CSV)` — EXISTENCE, not
#   adequacy. The result fed `survivorship_adjusted(mode, has_membership)`, landed in
#   the simulation output as `"survivorship_adjusted": True`
#   (core/engine/simulation_runner.py), and the Console SUPPRESSED its
#   survivorship-limitation flag: a stale, partial correction presented as complete.
#
# #1907 Inc 1 (MLR-7, RC-1/RC-3) fixed THE CLAIM with a STRUCTURAL gate: a table where
# EVERY row carries an end_date is removals-only and can never support the claim, for
# ANY as_of (including the no-arg path). Only a table with open-ended rows (current
# members present, end_date empty) is structurally PIT-capable; coverage judgement via
# as_of applies on top of that. Those semantics are pinned below on fixture tables.
#
# #1907 Inc 2 fixed THE DATA: scripts/rebuild_sp500_membership.py reconstructs
# open-interval membership from the free Wikipedia constituents + changes tables
# (~500 open rows, dated coverage into 2026), so the shipped file is now PIT-capable
# and the claim can honestly be True inside its coverage — see
# TestTheShippedFileIsNowPitCapable. Under-claiming past coverage remains the posture.

from __future__ import annotations

import csv
import datetime as dt
import inspect
import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import core.data_provider as dp


@pytest.fixture
def csv_path():
    p = Path(dp.SP500_MEMBERSHIP_CSV)
    if not p.exists():
        pytest.skip("membership CSV absent — nothing to claim about")
    return p


class TestTheShippedFileIsNowPitCapable:
    """#1907 Increment 2 replaced the 22-row removals-only table with a free-source
    reconstruction (Wikipedia constituents + changes replay,
    scripts/rebuild_sp500_membership.py). These pin the NEW facts about the shipped
    file, so nobody has to re-derive them: open-ended rows for current members exist,
    and dated coverage extends into 2026."""

    def test_the_table_carries_open_ended_rows(self, csv_path):
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        open_ended = [r for r in rows if not (r.get("end_date") or "").strip()]
        assert len(open_ended) >= 400, (
            "the shipped table must carry an open-ended row per current S&P 500 "
            f"member (~500); got {len(open_ended)} — a removals-only regression "
            "would resurrect the #1907 RC-1 defect"
        )

    def test_dated_coverage_extends_into_2026(self, csv_path):
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        latest = max(
            ((r.get("end_date") or "").strip() for r in rows if r.get("end_date"))
        )
        assert latest >= "2026-01-01", (
            "dated coverage regressed below 2026 — the reconstruction is stale; "
            "re-run scripts/rebuild_sp500_membership.py (see its runbook docstring)"
        )

    def test_the_claim_is_now_true_within_coverage(self, csv_path):
        """The point of Increment 2: with a PIT-capable table the claim can honestly
        be True again — inside the covered window."""
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2024, 6, 1)
            )
            is True
        )

    def test_a_period_past_coverage_is_still_not_claimable(self, csv_path):
        """Coverage honesty survives the upgrade: an as_of past the latest dated row
        is still refused (under-claiming stays the default posture)."""
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2099, 1, 1)
            )
            is False
        )


class TestTheClaimTracksTheEvidence:
    """The Inc-1 semantics, pinned on fixture tables (the shipped file is no longer
    removals-only, but the refusal logic must survive for anyone who ships such a
    table again): the boolean must go False when the table cannot support the claim —
    structurally (removals-only, #1907 RC-1) or by coverage (as_of past the table's
    end)."""

    REMOVALS_ONLY = (
        "symbol,start_date,end_date\n"
        "SIVB,2018-03-12,2023-03-13\n"
        "FLT,2010-04-01,2024-12-23\n"
    )

    @pytest.fixture
    def removals_only_csv(self, monkeypatch, tmp_path):
        p = tmp_path / "removals_only.csv"
        p.write_text(self.REMOVALS_ONLY, encoding="utf-8")
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(p))
        return p

    def test_within_coverage_is_not_claimable_on_removals_only_table(
        self, removals_only_csv
    ):
        """#1907 RC-1/RC-3: a removals-only table (zero open-ended rows) cannot say
        who was IN the index even for an as_of INSIDE its dated coverage — the
        overlay is 'today's list ∪ a few returnees', which still contains
        look-ahead entrants. Claiming survivorship-adjusted here is a false label."""
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2024, 6, 1)
            )
            is False
        )

    def test_no_as_of_is_not_claimable_on_removals_only_table(self, removals_only_csv):
        """#1907 RC-3: THE over-claim path. `has_point_in_time_membership()` (no
        as_of) used to return True on bare file existence; the simulation runner
        called exactly that, shipped `"survivorship_adjusted": True`, and the Console
        suppressed its limitation flag. The old docstring said flipping this is 'a
        decision, not a side effect' — #1907 (plan-approved) IS that decision."""
        assert dp.HistoricalDataProvider.has_point_in_time_membership() is False


class TestAPitCapableTableCanCarryTheClaim:
    """The structural gate must not block the Increment-2 table: once the CSV carries
    open-ended rows (current members with an empty end_date) it can answer 'who was in
    the index on date D', and the as_of coverage judgement applies on top."""

    CAPABLE = (
        "symbol,start_date,end_date\n"
        "AAPL,1982-11-30,\n"
        "MSFT,1994-06-01,\n"
        "SIVB,2018-03-12,2023-03-13\n"
    )

    @pytest.fixture
    def capable_csv(self, monkeypatch, tmp_path):
        p = tmp_path / "capable.csv"
        p.write_text(self.CAPABLE, encoding="utf-8")
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(p))
        return p

    def test_no_as_of_keeps_the_old_answer_for_a_capable_table(self, capable_csv):
        """Back-compat survives — but only for a table that is structurally able to
        support the claim in the first place."""
        assert dp.HistoricalDataProvider.has_point_in_time_membership() is True

    def test_within_coverage_is_claimable(self, capable_csv):
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2022, 6, 1)
            )
            is True
        )

    def test_past_coverage_is_still_not_claimable(self, capable_csv):
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2026, 5, 1)
            )
            is False
        )


class TestSimulationRunnerPassesItsWindow:
    """#1907 (plan §5.3 / V-2): the simulation runner is THE claim caller that shipped
    the over-claim. It must judge the claim against its own backtest window, not against
    bare file existence."""

    def test_runner_passes_as_of_to_the_claim(self):
        import core.engine.simulation_runner as sim_runner

        src = inspect.getsource(sim_runner)
        assert re.search(r"has_point_in_time_membership\(\s*as_of\s*=", src), (
            "simulation_runner must pass its backtest window (as_of=end_date) to "
            "has_point_in_time_membership — the bare no-arg call is the #1907 "
            "over-claim path"
        )
        assert not re.search(r"has_point_in_time_membership\(\s*\)", src), (
            "the bare has_point_in_time_membership() call must be gone from "
            "simulation_runner"
        )


class TestAdditiveOnlyOverlayIsFlaggedLoudly:
    """#1907 RC-2 back-compat path: on a REMOVALS-ONLY table get_sp500_symbols_at_date
    still starts from TODAY's list and only ever adds — such a table has no start rows
    to key a discard on. That residual look-ahead must be logged at WARNING
    (CODING_POLICY §5.6 — a known data limitation is never silent). The healed set-diff
    path on PIT-capable tables is pinned by tests/unit/test_pit_universe_no_lookahead.py.
    """

    def test_overlay_application_warns_about_lookahead(
        self, monkeypatch, tmp_path, caplog
    ):
        p = tmp_path / "membership.csv"
        p.write_text(
            "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(p))

        provider = dp.HistoricalDataProvider(api=None)
        with patch.object(provider, "get_sp500_symbols", return_value=["AAPL", "MSFT"]):
            with caplog.at_level(logging.WARNING, logger=""):
                result = provider.get_sp500_symbols_at_date(dt.datetime(2022, 1, 1))

        assert "SIVB" in result  # overlay behaviour itself is unchanged
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("look-ahead" in m for m in warnings), (
            "applying the additive-only overlay must WARN about the residual "
            f"look-ahead bias; got warnings: {warnings!r}"
        )


class TestAMissingFileIsStillFalse:
    def test_absent_csv_is_not_claimable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(tmp_path / "nope.csv"))
        assert dp.HistoricalDataProvider.has_point_in_time_membership() is False
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2024, 6, 1)
            )
            is False
        )

    def test_an_unreadable_csv_is_not_claimable(self, monkeypatch, tmp_path):
        """Fail-CLOSED: garbage must not read as 'adjusted'. Over-claiming on a broken
        file is worse than admitting we cannot tell."""
        bad = tmp_path / "bad.csv"
        bad.write_text("this is not a membership table", encoding="utf-8")
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(bad))
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2024, 6, 1)
            )
            is False
        )

    def test_a_header_only_table_is_not_claimable(self, monkeypatch, tmp_path):
        """#1907: an empty table knows nobody's membership — fail-CLOSED on both
        paths (previously the no-arg path answered True on bare existence)."""
        empty = tmp_path / "empty.csv"
        empty.write_text("symbol,start_date,end_date\n", encoding="utf-8")
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(empty))
        assert dp.HistoricalDataProvider.has_point_in_time_membership() is False
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2024, 6, 1)
            )
            is False
        )


class TestSurvivorshipAdjustedStillGuardsItsOtherAxis:
    """Unchanged behaviour: the full-market universe can never be survivorship-adjusted,
    membership table or not."""

    def test_full_market_is_never_adjusted(self):
        assert dp.survivorship_adjusted("full", True) is False

    def test_sp500_without_membership_is_not_adjusted(self):
        assert dp.survivorship_adjusted("sp500", False) is False

    def test_sp500_with_membership_is_adjusted(self):
        assert dp.survivorship_adjusted("sp500", True) is True
