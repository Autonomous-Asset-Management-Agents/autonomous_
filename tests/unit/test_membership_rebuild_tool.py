# tests/unit/test_membership_rebuild_tool.py
# #1907 (MLR-7) Increment 2 — free-source PIT reconstruction of the S&P 500
# membership table (plan §5.2 / Option C).
#
# The tool (scripts/rebuild_sp500_membership.py) reconstructs open-interval
# membership from the FREE Wikipedia "List of S&P 500 companies" page — the
# constituents table ("Date added" column) gives current members their open rows,
# and the "Selected changes" table (Effective Date / Added / Removed) is replayed
# chronologically into closed intervals. No paid APIs, stdlib HTML parsing only.
#
# These tests run OFFLINE against a small wiki-shaped HTML fixture; the network
# fetch (--fetch) is a documented runbook step that produces the same parse input.

from __future__ import annotations

import pytest

from scripts.rebuild_sp500_membership import (
    extract_tables,
    merge_legacy_rows,
    reconstruct_membership,
    rows_to_csv_text,
)

# Wiki-shaped fixture: same header structure as the real page (2026-07 snapshot):
#  * constituents: 8 columns, "Symbol" + "Date added" among them
#  * changes: header row [Effective Date, Added, Removed, Reason] + subheader
#    [Ticker, Security, Ticker, Security], then 6-cell data rows.
FIXTURE_HTML = """
<html><body>
<table class="wikitable" id="constituents">
<tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>IT</td><td>Hardware</td><td>Cupertino</td>
<td>1982-11-30</td><td>0000320193</td><td>1977</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi</td><td>Omaha</td>
<td>2010-02-16</td><td>0001067983</td><td>1839</td></tr>
<tr><td>MRVL</td><td>Marvell Technology</td><td>IT</td><td>Semis</td><td>Santa Clara</td>
<td>2026-06-22</td><td>0001835632</td><td>1995</td></tr>
<tr><td>NODATE</td><td>No Date Corp</td><td>IT</td><td>Misc</td><td>Nowhere</td>
<td></td><td>0000000001</td><td>1900</td></tr>
</tbody>
</table>
<p>Selected changes below.</p>
<table class="wikitable" id="changes">
<tbody>
<tr><th>Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th><th>Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>June 22, 2026</td><td>MRVL</td><td>Marvell Technology</td><td>POOL</td>
<td>Pool Corporation</td><td>Market capitalization change.<sup>[6]</sup></td></tr>
<tr><td>February 2, 2024</td><td>GHOST</td><td>Ghost Adds Inc</td><td></td>
<td></td><td>Added but later renamed off-list.<sup>[7]</sup></td></tr>
<tr><td>March 15, 2023</td><td></td><td></td><td>SIVB</td>
<td>SVB Financial</td><td>Bank failure.<sup>[8]</sup></td></tr>
<tr><td>January 5, 2021</td><td></td><td></td><td>XYZ</td>
<td>XYZ Corp</td><td>Left again.<sup>[9]</sup></td></tr>
<tr><td>September 1, 2020</td><td>XYZ</td><td>XYZ Corp</td><td></td>
<td></td><td>Joined briefly.<sup>[10]</sup></td></tr>
</tbody>
</table>
</body></html>
"""


@pytest.fixture
def recon():
    tables = extract_tables(FIXTURE_HTML)
    return reconstruct_membership(tables)


def _rows_for(rows, symbol):
    return [r for r in rows if r[0] == symbol]


class TestReconstruction:
    def test_current_members_get_open_rows_with_their_date_added(self, recon):
        rows, _stats = recon
        (aapl,) = _rows_for(rows, "AAPL")
        assert aapl == ("AAPL", "1982-11-30", "")

    def test_dot_tickers_are_normalised_to_dash(self, recon):
        """get_sp500_symbols_live() normalises '.'->'-'; the membership table must
        speak the same dialect or the set-diff can never match."""
        rows, _stats = recon
        (brk,) = _rows_for(rows, "BRK-B")
        assert brk == ("BRK-B", "2010-02-16", "")
        assert not _rows_for(rows, "BRK.B")

    def test_removed_name_gets_a_closed_interval(self, recon):
        rows, _stats = recon
        (sivb,) = _rows_for(rows, "SIVB")
        assert sivb == ("SIVB", "", "2023-03-15")  # start pre-coverage -> empty

    def test_add_then_remove_builds_a_dated_closed_interval(self, recon):
        rows, _stats = recon
        (xyz,) = _rows_for(rows, "XYZ")
        assert xyz == ("XYZ", "2020-09-01", "2021-01-05")

    def test_change_add_matching_current_member_uses_event_date(self, recon):
        rows, _stats = recon
        (mrvl,) = _rows_for(rows, "MRVL")
        assert mrvl == ("MRVL", "2026-06-22", "")

    def test_member_without_any_date_stays_open_with_empty_start(self, recon):
        rows, _stats = recon
        (nodate,) = _rows_for(rows, "NODATE")
        assert nodate == ("NODATE", "", "")

    def test_unresolved_open_add_is_dropped_and_counted(self, recon):
        """GHOST was added per the changes table but is not a current constituent
        (ticker rename etc.). Emitting it open would falsely claim current
        membership — it is dropped and listed in the stats (honest coverage gap)."""
        rows, stats = recon
        assert not _rows_for(rows, "GHOST")
        assert "GHOST" in stats["unresolved_open_adds"]

    def test_pool_removal_is_closed_even_without_start(self, recon):
        rows, _stats = recon
        (pool,) = _rows_for(rows, "POOL")
        assert pool == ("POOL", "", "2026-06-22")


class TestDeterminism:
    def test_two_reconstructions_are_byte_identical(self):
        rows_a, _ = reconstruct_membership(extract_tables(FIXTURE_HTML))
        rows_b, _ = reconstruct_membership(extract_tables(FIXTURE_HTML))
        assert rows_to_csv_text(rows_a) == rows_to_csv_text(rows_b)

    def test_csv_text_is_sorted_and_lf_terminated(self, recon):
        rows, _ = recon
        text = rows_to_csv_text(rows)
        lines = text.split("\n")
        assert lines[0] == "symbol,start_date,end_date"
        body = [ln for ln in lines[1:] if ln]
        assert body == sorted(body)
        assert "\r" not in text


class TestLegacyMigration:
    """The shipped 22-row removals-only CSV is MIGRATED, not discarded: rows for
    symbols the reconstruction does not know survive verbatim; rows the
    reconstruction supersedes may still donate their start_date to an
    unknown-start interval (the legacy table knew starts the changes replay
    cannot see)."""

    LEGACY = (
        "symbol,start_date,end_date\n"
        "SIVB,2018-03-12,2023-03-13\n"
        "MON,1984-01-01,2018-06-01\n"
    )

    def test_unknown_legacy_symbol_is_kept(self, recon, tmp_path):
        rows, _ = recon
        legacy = tmp_path / "legacy.csv"
        legacy.write_text(self.LEGACY, encoding="utf-8")
        merged, stats = merge_legacy_rows(rows, legacy)
        (mon,) = _rows_for(merged, "MON")
        assert mon == ("MON", "1984-01-01", "2018-06-01")
        assert stats["legacy_kept"] == 1

    def test_superseded_legacy_row_donates_its_start_date(self, recon, tmp_path):
        """Reconstructed SIVB has no start (pre-coverage); the legacy row knew
        2018-03-12 and its end (2023-03-13) is within days of the reconstructed
        end (2023-03-15) — adopt the legacy start, keep the reconstructed end."""
        rows, _ = recon
        legacy = tmp_path / "legacy.csv"
        legacy.write_text(self.LEGACY, encoding="utf-8")
        merged, stats = merge_legacy_rows(rows, legacy)
        (sivb,) = _rows_for(merged, "SIVB")
        assert sivb == ("SIVB", "2018-03-12", "2023-03-15")
        assert stats["legacy_start_adopted"] == 1


class TestOutputFeedsTheClaimAuthority:
    """End-to-end pin: the tool's output must be PIT-capable in the eyes of
    has_point_in_time_membership (#1907 Inc 1) — open-ended rows present, claim
    True within coverage, False past it."""

    def test_output_is_pit_capable(self, recon, tmp_path, monkeypatch):
        import datetime as dt

        import core.data_provider as dp

        rows, _ = recon
        out = tmp_path / "membership.csv"
        out.write_text(rows_to_csv_text(rows), encoding="utf-8")
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(out))
        assert dp.HistoricalDataProvider.has_point_in_time_membership() is True
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2023, 6, 1)
            )
            is True
        )
        assert (
            dp.HistoricalDataProvider.has_point_in_time_membership(
                as_of=dt.date(2099, 1, 1)
            )
            is False
        )
