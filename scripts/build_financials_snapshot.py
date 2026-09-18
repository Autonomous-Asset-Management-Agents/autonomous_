#!/usr/bin/env python
# scripts/build_financials_snapshot.py
# #3145 (bundle) — build the offline fundamentals snapshot shipped with the desktop.
"""Warm a fundamentals cache from SEC EDGAR and pack it into one gzipped snapshot.

The desktop bundles the output so FundamentalsAgent/ValuationAgent work offline on
first run (no key, no network); a later nightly ``prefetch_financials_universe``
supersedes it. Compact by design — the derived rows, not the raw companyfacts —
so the full S&P universe is ~1 MB gzipped.

    python scripts/build_financials_snapshot.py --out data/financials_snapshot.json.gz
    python scripts/build_financials_snapshot.py --symbols MSFT,ABNB --out /tmp/snap.json.gz
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.report import financials_snapshot as _snap  # noqa: E402
from core.report import sec_fundamentals as _sec  # noqa: E402
from scripts.prefetch_financials_universe import (  # noqa: E402
    _resolve_universe,
    prefetch_universe,
)

logger = logging.getLogger("build_financials_snapshot")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Build the offline SEC fundamentals snapshot."
    )
    p.add_argument("--out", default=os.path.join("data", "financials_snapshot.json.gz"))
    p.add_argument(
        "--symbols", default=None, help="Comma list (default: full S&P 500)."
    )
    p.add_argument("--max-symbols", type=int, default=None)
    p.add_argument("--throttle-seconds", type=float, default=0.15, help="SEC 10 req/s.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    symbols = _resolve_universe(args.symbols, args.max_symbols)
    cik_map = _sec.ticker_cik_map()

    def fetch_results(symbol):
        cik = cik_map.get(symbol.upper()) or cik_map.get(
            symbol.upper().replace(".", "-")
        )
        if not cik:
            return None
        facts = _sec.fetch_companyfacts(cik)
        return _sec.rows_from_companyfacts(facts) or None if facts is not None else None

    cache_dir = tempfile.mkdtemp(prefix="fin_snapshot_")
    stats = prefetch_universe(
        cache_dir=cache_dir,
        symbols=symbols,
        as_of=datetime.now(timezone.utc).date(),
        api_key=None,
        limit=8,
        timeframe="annual",
        throttle_seconds=args.throttle_seconds,
        skip_fresh_days=0,
        force=True,
        dry_run=False,
        fetch_results=fetch_results,
        source_label="sec edgar companyfacts (data.sec.gov, annual)",
    )
    count = _snap.build_snapshot(cache_dir, args.out)
    size = os.path.getsize(args.out) if os.path.exists(args.out) else 0
    logging.warning(
        "snapshot built: %d symbols (%d fetched, %d failed) -> %s (%d bytes)",
        count,
        stats["fetched"],
        stats["failed"],
        args.out,
        size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
