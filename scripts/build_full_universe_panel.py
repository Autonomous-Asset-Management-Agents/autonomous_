#!/usr/bin/env python
"""Reproducible offline full-universe panel builder — #1907 (MLR-7) Inc 2/3, plan §5.4.

WHAT: materialises the cross-sectional bar panel that feeds the #2388 retrain
targets, from (a) the PIT-capable membership table (open intervals —
scripts/rebuild_sp500_membership.py) and (b) daily bars pulled through the
existing HistoricalDataProvider (Databento-first "historical" routing). Writes:

  <out>/full_universe_panel.csv   long format: date,symbol,open,high,low,close,volume
                                  — only symbol-days INSIDE the symbol's recorded
                                  membership window (no look-ahead entrants,
                                  no post-removal ghosts)
  <out>/panel_manifest.json       reproducibility manifest: membership source hash,
                                  coverage window, adjust policy, content hashes.
                                  NO wall-clock fields — two runs on identical
                                  inputs are byte-identical (tests R11/R12).

This is an OFFLINE artifact: it is NOT the live-only lstm_panel_store (which holds
no history by design) and nothing on the live decision path imports this script.

RUNBOOK:
  cd ai_trading_bot
  python scripts/rebuild_sp500_membership.py --fetch --out data/sp500_historical_membership.csv \
      --legacy-csv data/sp500_historical_membership.csv --manifest data/sp500_membership_manifest.json
  python scripts/build_full_universe_panel.py --start 2019-01-02 --end 2026-07-18 \
      --out-dir data/full_universe_panel
  # gate check (same judgement the #2388 retrain pre-stage must make):
  python scripts/build_full_universe_panel.py --check-gate data/full_universe_panel/panel_manifest.json

FAIL-CLOSED: a removals-only membership table (the pre-Inc-2 state) is REFUSED —
building a "full universe" panel on a table that cannot say who was in the index
would silently reintroduce the exact bias this epic removes.

PRICE ADJUSTMENT: bars come from the provider's historical routing as-is; the
policy is recorded in the manifest verbatim. Pinning splits/dividends adjustment
per source is the open V-4 lane question (plan §12) — recorded, not resolved here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

# Standard scripts/ idiom (mirrors run_training.py): make ai_trading_bot importable
# when executed as `python scripts/build_full_universe_panel.py`.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logger = logging.getLogger(__name__)

PANEL_FILENAME = "full_universe_panel.csv"
MANIFEST_FILENAME = "panel_manifest.json"
PRICE_ADJUSTMENT_POLICY = (
    "HistoricalDataProvider.get_data(use_case='historical') — Databento-first, "
    "Alpaca/Polygon fallback; source-default adjustment (V-4 pending, #1907 §12)"
)

BarsFn = Callable[[str, date, date], pd.DataFrame]


def _load_intervals(membership_csv: Path) -> Dict[str, List[Tuple[str, str]]]:
    """symbol -> [(start, end)] with '' for open/unknown. Refuses removals-only
    tables (fail-closed)."""
    import csv as _csv

    intervals: Dict[str, List[Tuple[str, str]]] = {}
    any_open = False
    with open(membership_csv, newline="", encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue
            start = (row.get("start_date") or "").strip()
            end = (row.get("end_date") or "").strip()
            any_open = any_open or not end
            intervals.setdefault(symbol, []).append((start, end))

    if not intervals or not any_open:
        raise ValueError(
            f"membership table {membership_csv} is removals-only or empty — not "
            "PIT-capable; run scripts/rebuild_sp500_membership.py first (#1907)"
        )
    return intervals


def _member_on(intervals: List[Tuple[str, str]], day: str) -> bool:
    return any(
        (not start or start <= day) and (not end or end >= day)
        for start, end in intervals
    )


def _default_bars_fn() -> BarsFn:
    """Production fetch through the existing provider (historical routing)."""
    from core.data_provider import HistoricalDataProvider

    # #1907 Inc-2 Fix: NIE api=None erzwingen — das umgeht _default_data_client()
    # (R10-Seam) und lässt den Builder ohne Credential-WARNING instant-leer laufen
    # (553 Symbole in 133 ms, 0 Zeilen). Der Sentinel-Default resolved Alpaca aus
    # der Config; ohne Keys degradiert er LAUT (WARNING) zu Polygon.
    provider = HistoricalDataProvider()

    def fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        end_dt = datetime.combine(end, datetime.min.time())
        days = (end - start).days + 10
        bars = provider.get_data(
            symbol, end_date=end_dt, days=days, use_case="historical"
        )
        return bars if bars is not None else pd.DataFrame()

    return fetch


def build_panel(
    membership_csv: Path,
    start: date,
    end: date,
    out_dir: Path,
    bars_fn: Optional[BarsFn] = None,
) -> Dict:
    """Build the panel deterministically; returns the manifest dict."""
    membership_csv = Path(membership_csv)
    out_dir = Path(out_dir)
    intervals = _load_intervals(membership_csv)
    bars_fn = bars_fn or _default_bars_fn()

    start_iso, end_iso = start.isoformat(), end.isoformat()
    frames: List[pd.DataFrame] = []
    skipped: List[str] = []
    for symbol in sorted(intervals):
        # Symbols whose recorded membership never touches the window are not
        # fetched at all.
        if not any(
            (not s or s <= end_iso) and (not e or e >= start_iso)
            for s, e in intervals[symbol]
        ):
            continue
        bars = bars_fn(symbol, start, end)
        if bars is None or bars.empty:
            skipped.append(symbol)
            continue
        frame = bars.copy()
        frame.index = pd.to_datetime(frame.index)
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None)
        frame = frame.loc[
            (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        ]
        if frame.empty:
            skipped.append(symbol)
            continue
        frame = frame[["open", "high", "low", "close", "volume"]].copy()
        frame.insert(0, "date", frame.index.strftime("%Y-%m-%d"))
        frame.insert(1, "symbol", symbol)
        # THE PIT FILTER: keep only days inside the symbol's membership window.
        member_mask = frame["date"].map(
            lambda d, iv=intervals[symbol]: _member_on(iv, d)
        )
        frame = frame.loc[member_mask]
        if not frame.empty:
            frames.append(frame)

    if skipped:
        # §5.6 — a data gap is never silent: these names were members in the
        # window but delivered no bars (delisted feeds etc.).
        logger.warning(
            "build_full_universe_panel: no bars for %d member symbol(s) in "
            "%s..%s (first 20: %s) — panel coverage gap.",
            len(skipped),
            start_iso,
            end_iso,
            ", ".join(skipped[:20]),
        )

    if frames:
        panel = pd.concat(frames, ignore_index=True)
        panel = panel.sort_values(["date", "symbol"], kind="mergesort").reset_index(
            drop=True
        )
    else:
        panel = pd.DataFrame(
            columns=["date", "symbol", "open", "high", "low", "close", "volume"]
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = out_dir / PANEL_FILENAME
    csv_text = panel.to_csv(index=False, lineterminator="\n", float_format="%.6f")
    panel_path.write_text(csv_text, encoding="utf-8", newline="")
    panel_bytes = panel_path.read_bytes()

    manifest = {
        "artifact": "full_universe_panel",
        "generator": "scripts/build_full_universe_panel.py (#1907 Inc 2/3)",
        "membership_csv": membership_csv.name,
        # License obligation (CC BY-SA 4.0): the membership table is derived from
        # Wikipedia content — the panel carries the attribution forward.
        "membership_source_license": (
            "membership table derived from Wikipedia, 'List of S&P 500 "
            "companies' (Wikipedia contributors), CC BY-SA 4.0 — see "
            "data/sp500_membership_manifest.json for full attribution"
        ),
        "membership_sha256": hashlib.sha256(membership_csv.read_bytes()).hexdigest(),
        "price_adjustment": PRICE_ADJUSTMENT_POLICY,
        "coverage_start": start_iso,
        "coverage_end": end_iso,
        "symbols": int(panel["symbol"].nunique()) if len(panel) else 0,
        "rows": int(len(panel)),
        "symbols_without_bars": sorted(skipped),
        "panel_sha256": hashlib.sha256(panel_bytes).hexdigest(),
    }
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    logger.info(
        "wrote %s: %d rows, %d symbols, window %s..%s",
        panel_path,
        manifest["rows"],
        manifest["symbols"],
        start_iso,
        end_iso,
    )
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check-gate",
        type=Path,
        metavar="MANIFEST",
        help="only run the stale/coverage gate against an existing manifest",
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument(
        "--membership-csv",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data"
        / "sp500_historical_membership.csv",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="training window end for the gate's membership-coverage judgement",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.check_gate:
        from core.panel_stale_gate import panel_gate_check

        allowed, reason = panel_gate_check(args.check_gate, as_of=args.as_of)
        print(("ALLOWED: " if allowed else "BLOCKED: ") + reason)
        return 0 if allowed else 1

    if not (args.start and args.end and args.out_dir):
        parser.error("--start, --end and --out-dir are required to build")
    if args.end < args.start:
        parser.error("--end must be on/after --start")
    if args.end > date.today() + timedelta(days=1):
        parser.error("--end lies in the future — a panel cannot cover it")

    build_panel(
        membership_csv=args.membership_csv,
        start=args.start,
        end=args.end,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
