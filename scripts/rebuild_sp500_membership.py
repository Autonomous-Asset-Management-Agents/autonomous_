#!/usr/bin/env python
"""Rebuild data/sp500_historical_membership.csv from FREE sources — #1907 Inc 2.

WHAT THIS PRODUCES (plan §5.2, Option C): an OPEN-INTERVAL membership table
(``symbol,start_date,end_date`` — ``end_date`` empty = still a member) that is
structurally PIT-capable in the sense of ``has_point_in_time_membership`` (#1907
Inc 1): it can answer "who was in the S&P 500 on date D" inside its coverage,
which the shipped 22-row removals-only table never could.

SOURCE (free, no paid APIs): the Wikipedia page "List of S&P 500 companies" —
  * the CONSTITUENTS table (Symbol / ... / Date added) yields one open-ended row
    per current member, and
  * the SELECTED CHANGES table (Effective Date / Added / Removed) is replayed
    chronologically into closed membership intervals for departed names.
Parsing is stdlib-only (html.parser) so the tool runs in any environment.

RUNBOOK (deterministic, offline-friendly):
  1. Fetch the page (either lets the tool do it, or curl it yourself for audit):
       python scripts/rebuild_sp500_membership.py --fetch --out data/sp500_historical_membership.csv
     or, reproducibly from a saved snapshot:
       curl -o /tmp/sp500_wiki.html "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
       python scripts/rebuild_sp500_membership.py --html /tmp/sp500_wiki.html \
           --out data/sp500_historical_membership.csv \
           --legacy-csv data/sp500_historical_membership.csv \
           --manifest data/sp500_membership_manifest.json \
           --retrieved-at 2026-07-22
  2. Commit CSV + manifest together — the manifest pins source hash and coverage.
  Two runs on the same HTML snapshot produce byte-identical CSV and manifest.

HONESTY / KNOWN LIMITS (documented, not hidden — plan §12):
  * Coverage is bounded by Wikipedia's changes table (dense from the late 1990s,
    sparse before); intervals starting before coverage carry an EMPTY start_date,
    which the reader treats as "member since before coverage" (over-inclusive for
    early dates — the survivorship-SAFE direction: it keeps losers in, it never
    invents future winners).
  * ``end_date`` is the change's EFFECTIVE date as published (±1 day precision;
    the reader counts the end date itself as a membership day).
  * Tickers added per the changes table but absent from the current constituents
    (renames such as FLT→CPAY) cannot honestly be emitted as open rows — they are
    DROPPED and listed in the manifest under ``unresolved_open_adds``.
  * Ticker reuse across decades is not disambiguated (best-effort).
The legacy 22-row table is MIGRATED, not discarded (--legacy-csv): rows for
symbols the reconstruction does not know survive verbatim, and a legacy row may
donate its start_date to a reconstructed unknown-start interval with a matching
end (±30 days).

Airlock: lives in scripts/, imported by tests only — never by the finance runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Attribution (license obligation, not decoration): the source page's text content
# is written by Wikipedia contributors and licensed CC BY-SA 4.0. The membership
# CSV + manifest are DERIVED from that content, so they are redistributed under
# the same license with attribution — recorded verbatim in every manifest.
SOURCE_LICENSE = (
    "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/) — source "
    "content by Wikipedia contributors, 'List of S&P 500 companies', Wikipedia, "
    "The Free Encyclopedia. The derived membership table and this manifest are "
    "share-alike: redistributed under CC BY-SA 4.0 with this attribution."
)

# A membership row: (symbol, start_date, end_date) — ISO strings, "" = open/unknown.
Row = Tuple[str, str, str]

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")


class _TableParser(HTMLParser):
    """Minimal, dependency-free extraction of every <table> as rows of cell text."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._stack: List[List[List[str]]] = []
        self._row: Optional[List[str]] = None
        self._cell_depth = 0
        self._cell: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._stack:
            self._cell_depth += 1
            self._cell = []
        elif tag == "br" and self._cell_depth:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
        elif tag == "tr" and self._stack and self._row is not None:
            self._stack[-1].append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell_depth:
            self._cell_depth -= 1
            if self._row is not None:
                self._row.append("".join(self._cell).strip())

    def handle_data(self, data: str) -> None:
        if self._cell_depth:
            self._cell.append(data)


def extract_tables(html_text: str) -> Dict[str, List[List[str]]]:
    """Locate the constituents and changes tables by their headers.

    Returns ``{"constituents": rows, "changes": rows}``; raises ValueError when
    either table cannot be identified (fail-closed — a silently missing table
    would rebuild an empty membership).
    """
    parser = _TableParser()
    parser.feed(html_text)

    constituents: Optional[List[List[str]]] = None
    changes: Optional[List[List[str]]] = None
    for table in parser.tables:
        if not table:
            continue
        header = [c.lower() for c in table[0]]
        if (
            constituents is None
            and "symbol" in header
            and any("date added" in h for h in header)
        ):
            constituents = table
        elif changes is None and any("effective date" in h for h in header):
            changes = table

    if constituents is None or changes is None:
        raise ValueError(
            "could not identify the constituents and/or changes table — the page "
            "layout may have changed; refusing to rebuild from a partial parse"
        )
    return {"constituents": constituents, "changes": changes}


def _norm_ticker(cell: str) -> str:
    """Uppercase, strip footnote refs, normalise '.'->'-' (get_sp500_symbols_live
    dialect)."""
    ticker = _FOOTNOTE_RE.sub("", cell).strip().upper()
    return ticker.replace(".", "-")


def _parse_change_date(cell: str) -> Optional[str]:
    """'June 22, 2026' -> '2026-06-22'; None when unparseable (header rows)."""
    text = _FOOTNOTE_RE.sub("", cell).strip()
    try:
        return datetime.strptime(text, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _parse_added_date(cell: str) -> str:
    """First ISO date in the constituents 'Date added' cell, '' when absent."""
    match = _ISO_DATE_RE.search(cell or "")
    return match.group(0) if match else ""


def reconstruct_membership(
    tables: Dict[str, List[List[str]]],
) -> Tuple[List[Row], Dict]:
    """Replay the changes chronologically and anchor open rows on the current
    constituents. Deterministic: same tables in, same rows out."""
    constituents = tables["constituents"]
    changes = tables["changes"]

    header = [c.lower() for c in constituents[0]]
    sym_idx = next(i for i, h in enumerate(header) if "symbol" in h)
    added_idx = next(i for i, h in enumerate(header) if "date added" in h)

    current: Dict[str, str] = {}  # ticker -> date_added ('' when unknown)
    for row in constituents[1:]:
        if len(row) <= max(sym_idx, added_idx):
            continue
        ticker = _norm_ticker(row[sym_idx])
        if ticker:
            current[ticker] = _parse_added_date(row[added_idx])

    # Change events, chronologically ascending (the page lists newest first).
    events: List[Tuple[str, str, str]] = []  # (date, added_ticker, removed_ticker)
    for row in changes:
        if len(row) < 6:
            continue  # header / subheader rows
        event_date = _parse_change_date(row[0])
        if event_date is None:
            continue
        events.append((event_date, _norm_ticker(row[1]), _norm_ticker(row[3])))
    events.sort(key=lambda e: e[0])

    rows: List[Row] = []
    open_since: Dict[str, str] = {}
    stats = {
        "events": len(events),
        "adds": 0,
        "removals": 0,
        "closed_intervals": 0,
        "unknown_start_removals": 0,
        "readd_while_open": 0,
        "unresolved_open_adds": [],
        "changes_per_year": {},
    }
    for event_date, added, removed in events:
        year = event_date[:4]
        stats["changes_per_year"][year] = stats["changes_per_year"].get(year, 0) + 1
        if removed:
            stats["removals"] += 1
            start = open_since.pop(removed, "")
            if not start:
                stats["unknown_start_removals"] += 1
            rows.append((removed, start, event_date))
            stats["closed_intervals"] += 1
        if added:
            stats["adds"] += 1
            if added in open_since:
                # Data quirk (double add without a removal in between) — keep the
                # earlier start, count it. §5.6: a data limitation is never silent.
                stats["readd_while_open"] += 1
                logger.warning(
                    "rebuild_sp500_membership: %s added on %s while already open "
                    "since %s — keeping the earlier start.",
                    added,
                    event_date,
                    open_since[added],
                )
            else:
                open_since[added] = event_date

    for ticker in sorted(current):
        # Prefer the replayed event start when the ticker's CURRENT stint began
        # inside coverage (a re-join after an earlier recorded exit): using the
        # constituents' original "Date added" there would bridge the gap and
        # falsely claim membership between exit and re-entry.
        start = open_since.pop(ticker, "") or current[ticker]
        rows.append((ticker, start, ""))

    # Added per changes, never removed, NOT a current constituent (renames such
    # as FLT->CPAY): an open row would falsely claim current membership — drop,
    # list, and let the manifest carry the gap honestly.
    for ticker in sorted(open_since):
        stats["unresolved_open_adds"].append(ticker)
        logger.warning(
            "rebuild_sp500_membership: %s was added per the changes table "
            "(%s) but is not a current constituent (rename/relist?) — dropped, "
            "recorded as coverage gap.",
            ticker,
            open_since[ticker],
        )

    rows.sort()
    return rows, stats


def merge_legacy_rows(rows: List[Row], legacy_csv: Path) -> Tuple[List[Row], Dict]:
    """Migrate the pre-#1907 removals-only table instead of discarding it.

    * Legacy rows for symbols the reconstruction does not know at all -> kept
      verbatim (they carry removals Wikipedia's replay may not cover).
    * A legacy row may donate its start_date to a reconstructed interval of the
      same symbol that lacks one, when both agree on the end (±30 days).
    * Everything else is superseded by the reconstruction.
    """
    import csv as _csv

    stats = {"legacy_kept": 0, "legacy_superseded": 0, "legacy_start_adopted": 0}
    known = {r[0] for r in rows}
    merged = list(rows)

    with open(legacy_csv, newline="", encoding="utf-8") as fh:
        for legacy in _csv.DictReader(fh):
            symbol = _norm_ticker(legacy.get("symbol") or "")
            l_start = (legacy.get("start_date") or "").strip()
            l_end = (legacy.get("end_date") or "").strip()
            if not symbol:
                continue
            if symbol not in known:
                merged.append((symbol, l_start, l_end))
                stats["legacy_kept"] += 1
                continue
            adopted = False
            if l_start and l_end:
                for i, (r_sym, r_start, r_end) in enumerate(merged):
                    if (
                        r_sym == symbol
                        and not r_start
                        and r_end
                        and abs(
                            (
                                datetime.fromisoformat(r_end)
                                - datetime.fromisoformat(l_end)
                            ).days
                        )
                        <= 30
                    ):
                        merged[i] = (r_sym, l_start, r_end)
                        stats["legacy_start_adopted"] += 1
                        adopted = True
                        break
            if not adopted:
                stats["legacy_superseded"] += 1

    merged.sort()
    return merged, stats


def rows_to_csv_text(rows: List[Row]) -> str:
    """Deterministic CSV text: sorted rows, LF-terminated, no trailing spaces."""
    lines = ["symbol,start_date,end_date"]
    lines.extend(f"{s},{start},{end}" for s, start, end in sorted(rows))
    return "\n".join(lines) + "\n"


def build_manifest(
    rows: List[Row],
    stats: Dict,
    legacy_stats: Optional[Dict],
    source: str,
    source_sha256: str,
    retrieved_at: str,
) -> Dict:
    open_rows = [r for r in rows if not r[2]]
    dated_ends = [r[2] for r in rows if r[2]]
    dated_starts = [r[1] for r in rows if r[1]]
    return {
        "artifact": "sp500_historical_membership",
        "generator": "scripts/rebuild_sp500_membership.py (#1907 Inc 2)",
        "source": source,
        "source_license": SOURCE_LICENSE,
        "source_sha256": source_sha256,
        "retrieved_at": retrieved_at,
        "method": (
            "Wikipedia constituents table -> open-ended rows (current members); "
            "'Selected changes' table replayed chronologically -> closed "
            "intervals. end_date = published effective date (±1 day). Empty "
            "start_date = joined before coverage (reader treats as member since "
            "before coverage — survivorship-safe over-inclusion)."
        ),
        "rows_total": len(rows),
        "rows_open_ended": len(open_rows),
        "rows_closed": len(rows) - len(open_rows),
        "earliest_dated_start": min(dated_starts) if dated_starts else "",
        "earliest_dated_end": min(dated_ends) if dated_ends else "",
        "coverage_end": max(dated_ends) if dated_ends else "",
        "reconstruction_stats": stats,
        "legacy_migration_stats": legacy_stats or {},
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--html", type=Path, help="parse a saved page snapshot")
    src.add_argument(
        "--fetch", action="store_true", help=f"fetch {WIKI_URL} (free source)"
    )
    parser.add_argument("--out", type=Path, required=True, help="membership CSV out")
    parser.add_argument(
        "--legacy-csv", type=Path, help="existing removals-only CSV to migrate"
    )
    parser.add_argument("--manifest", type=Path, help="provenance manifest out")
    parser.add_argument(
        "--retrieved-at",
        default="",
        help="ISO date the source was retrieved (recorded verbatim in the manifest)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.fetch:
        import urllib.request

        request = urllib.request.Request(
            WIKI_URL, headers={"User-Agent": "AAAgents-MLR7-membership-rebuild/1.0"}
        )
        # nosec B310 — WIKI_URL is a hardcoded https constant (no user-controlled
        # scheme); offline runs use --html and never open a socket.
        with urllib.request.urlopen(request, timeout=60) as resp:  # nosec B310
            html_bytes = resp.read()
        source = WIKI_URL
    else:
        html_bytes = args.html.read_bytes()
        source = f"{WIKI_URL} (snapshot: {args.html.name})"

    source_sha256 = hashlib.sha256(html_bytes).hexdigest()
    tables = extract_tables(html_bytes.decode("utf-8", errors="replace"))
    rows, stats = reconstruct_membership(tables)

    legacy_stats = None
    if args.legacy_csv and args.legacy_csv.exists():
        rows, legacy_stats = merge_legacy_rows(rows, args.legacy_csv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rows_to_csv_text(rows), encoding="utf-8", newline="")

    manifest = build_manifest(
        rows, stats, legacy_stats, source, source_sha256, args.retrieved_at
    )
    if args.manifest:
        args.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )

    logger.info(
        "wrote %s: %d rows (%d open-ended, %d closed), coverage_end=%s",
        args.out,
        manifest["rows_total"],
        manifest["rows_open_ended"],
        manifest["rows_closed"],
        manifest["coverage_end"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
