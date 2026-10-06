#!/usr/bin/env python
# scripts/prefetch_financials_universe.py
# RPT-2 (#2001, Epic #1998) — nightly point-in-time fundamentals prefetch.
"""Prefetch Polygon vX fundamentals for the whole S&P universe into a local cache.

Why a nightly prefetch? The report pipeline needs point-in-time fundamentals for
up to ~503 symbols, but the Polygon free tier caps at **5 requests/min**. Pulling
on demand during report generation would blow the budget and stall. So this
script walks the universe once per night, throttled at ``--throttle-seconds``
(default 12 s → 5/min), and writes one JSON file per symbol to
``data/financials_cache/<SYMBOL>.json``. The report then reads the cache offline
via :class:`core.report.financials_feed.CachedFundamentalsReader` (PIT applied at
read time).

Quarter-stable / idempotent by design:

* Fundamentals only change ~quarterly, so ``--skip-fresh-days`` (default 25) skips
  any symbol whose cache was refreshed within that window — a nightly rerun spends
  rate-limit budget only on genuinely stale symbols.
* A refreshed file is only rewritten when the ``results`` actually changed, so the
  on-disk cache stays byte-stable across reruns.

Report-only and DORMANT: this touches no trading path. It only warms a cache the
(still dormant) report reader consumes. Network lives entirely in
``core.report.financials_feed`` — this script just orchestrates + throttles.

Usage
-----
    # full universe, default 12 s throttle (~101 min for 503 symbols)
    python scripts/prefetch_financials_universe.py

    # a quick subset / smoke test
    python scripts/prefetch_financials_universe.py --symbols NVDA,AAPL,MSFT --throttle-seconds 13

    # force a full refresh regardless of cache age
    python scripts/prefetch_financials_universe.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import List, Optional

# Ensure the repo root (ai_trading_bot/) is importable when run as a script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.report.financials_feed import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    cache_path,
    fetch_raw_financials,
)

logger = logging.getLogger("prefetch_financials")


# ---------------------------------------------------------------------------
# Universe resolution.
# ---------------------------------------------------------------------------
def _resolve_universe(
    symbols_arg: Optional[str], max_symbols: Optional[int]
) -> List[str]:
    if symbols_arg:
        symbols = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    else:
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = list(get_sp500_symbols())
    if max_symbols:
        symbols = symbols[:max_symbols]
    return symbols


# ---------------------------------------------------------------------------
# Freshness / idempotent write.
# ---------------------------------------------------------------------------
def _is_fresh(path: str, skip_fresh_days: int) -> bool:
    """True if ``path`` exists and was fetched within ``skip_fresh_days``."""
    if skip_fresh_days <= 0 or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            fetched_at = json.load(handle).get("fetched_at")
        if not fetched_at:
            return False
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except (OSError, ValueError):
        return False
    age_days = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400.0
    return age_days < skip_fresh_days


def _write_if_changed(
    path: str,
    symbol: str,
    results: list,
    as_of: date,
    source: str = "polygon vX /vX/reference/financials (timeframe=annual)",
) -> bool:
    """Write the cache payload only when ``results`` changed. Returns True if written."""
    existing = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                existing = json.load(handle).get("results")
        except (OSError, ValueError):
            existing = None
    if existing == results:
        return False
    payload = {
        "symbol": symbol,
        "source": source,
        "as_of": as_of.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)
    return True


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------
def prefetch_universe(
    *,
    cache_dir: str,
    symbols: List[str],
    as_of: date,
    api_key: Optional[str],
    limit: int,
    timeframe: str,
    throttle_seconds: float,
    skip_fresh_days: int,
    force: bool,
    dry_run: bool,
    fetch_results=None,
    source_label: str = "polygon vX /vX/reference/financials (timeframe=annual)",
    sleep=time.sleep,
) -> dict:
    """Walk ``symbols`` and warm the cache. Returns a summary counter dict.

    ``fetch_results`` (``symbol -> Optional[list]``) selects the producer; when None
    the legacy Polygon path is used, so existing callers are byte-identical. #3145
    passes a SEC-EDGAR fetcher here.
    """
    if fetch_results is None:

        def fetch_results(symbol):  # noqa: E306 — legacy Polygon default
            return fetch_raw_financials(
                symbol, as_of, api_key=api_key, timeframe=timeframe, limit=limit
            )

    stats = {
        "total": len(symbols),
        "fetched": 0,
        "written": 0,
        "skipped": 0,
        "failed": 0,
    }
    for idx, symbol in enumerate(symbols, start=1):
        path = cache_path(cache_dir, symbol)
        if not force and _is_fresh(path, skip_fresh_days):
            stats["skipped"] += 1
            logger.info("[%d/%d] %s — fresh, skip", idx, stats["total"], symbol)
            continue
        if dry_run:
            logger.info("[%d/%d] %s — DRY-RUN would fetch", idx, stats["total"], symbol)
            continue

        results = fetch_results(symbol)
        if results is None:
            stats["failed"] += 1
            logger.warning(
                "[%d/%d] %s — fetch failed (honest gap)", idx, stats["total"], symbol
            )
        else:
            stats["fetched"] += 1
            if _write_if_changed(path, symbol, results, as_of, source=source_label):
                stats["written"] += 1
            logger.info(
                "[%d/%d] %s — %d period(s) cached",
                idx,
                stats["total"],
                symbol,
                len(results),
            )
        # Throttle only after a real network call (skips cost no budget).
        if idx < stats["total"] and throttle_seconds > 0:
            sleep(throttle_seconds)
    return stats


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nightly Polygon vX fundamentals prefetch.")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    p.add_argument(
        "--symbols", default=None, help="Comma list override (default: full S&P 500)."
    )
    p.add_argument("--max-symbols", type=int, default=None)
    p.add_argument("--as-of", default=None, help="ISO date; default = today (UTC).")
    p.add_argument("--api-key", default=None, help="Default: $POLYGON_API_KEY.")
    p.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Annual periods per symbol (YoY needs >=2).",
    )
    p.add_argument("--timeframe", default="annual", choices=["annual", "quarterly"])
    p.add_argument(
        "--source",
        default=None,
        choices=["sec", "polygon"],
        help="Fundamentals producer. Default: config.FUNDAMENTALS_SOURCE ('sec').",
    )
    p.add_argument(
        "--throttle-seconds",
        type=float,
        default=None,
        help="Between network calls. Default: 12 s (polygon 5/min) / 0.15 s (SEC 10/s).",
    )
    p.add_argument(
        "--skip-fresh-days",
        type=int,
        default=25,
        help="Skip symbols cached within N days.",
    )
    p.add_argument("--force", action="store_true", help="Refresh even fresh symbols.")
    p.add_argument(
        "--dry-run", action="store_true", help="List actions; no network, no writes."
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    as_of = (
        date.fromisoformat(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc).date()
    )
    symbols = _resolve_universe(args.symbols, args.max_symbols)

    # #3145: resolve the producer. Default = config.FUNDAMENTALS_SOURCE ('sec').
    source = args.source
    if source is None:
        try:
            import config as _cfg

            source = str(getattr(_cfg, "FUNDAMENTALS_SOURCE", "sec")).lower()
        except (
            Exception
        ) as e:  # noqa: BLE001 — a config gap must not break the producer
            logger.warning(
                f"Could not load FUNDAMENTALS_SOURCE from config, falling back to 'sec' (error: {e})"
            )
            source = "sec"

    fetch_results = None
    source_label = "polygon vX /vX/reference/financials (timeframe=annual)"
    throttle = args.throttle_seconds
    if source == "sec":
        from core.report import sec_fundamentals as _sec  # noqa: E402

        cik_map = _sec.ticker_cik_map()
        source_label = "sec edgar companyfacts (data.sec.gov, annual)"
        if throttle is None:
            throttle = 0.15  # SEC 10 req/s → comfortably under the limit

        def fetch_results(symbol):  # noqa: E306
            cik = cik_map.get(symbol.upper()) or cik_map.get(
                symbol.upper().replace(".", "-")
            )
            if not cik:
                return None
            facts = _sec.fetch_companyfacts(cik)
            if facts is None:
                return None
            return _sec.rows_from_companyfacts(facts) or None

    if throttle is None:
        throttle = 12.0  # legacy Polygon free-tier default

    logger.info(
        "Prefetching %d symbols as of %s -> %s (source=%s)",
        len(symbols),
        as_of,
        args.cache_dir,
        source,
    )

    t0 = time.time()
    stats = prefetch_universe(
        cache_dir=args.cache_dir,
        symbols=symbols,
        as_of=as_of,
        api_key=args.api_key,
        limit=args.limit,
        timeframe=args.timeframe,
        throttle_seconds=throttle,
        skip_fresh_days=args.skip_fresh_days,
        force=args.force,
        dry_run=args.dry_run,
        fetch_results=fetch_results,
        source_label=source_label,
    )
    elapsed = time.time() - t0
    # Summary always printed (WARNING level baseline), so cron logs stay useful.
    logging.warning(
        "prefetch done in %.0fs: total=%d fetched=%d written=%d skipped=%d failed=%d",
        elapsed,
        stats["total"],
        stats["fetched"],
        stats["written"],
        stats["skipped"],
        stats["failed"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
