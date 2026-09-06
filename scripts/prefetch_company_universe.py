# scripts/prefetch_company_universe.py
# ADR-RPT-COMPANY-01 — company-identity prefetch for the auditable report pipeline.
"""Prefetch Polygon company facts for the whole S&P universe into a local cache.

Why a prefetch? §0 "Das Unternehmen" needs name / description / sector / size for up to
~500 symbols, but the Polygon free tier caps at **5 requests/min**. Pulling on demand
during report generation would blow the budget and stall. So this script walks the
universe once, throttled at ``--throttle-seconds`` (default 13 s), and writes one JSON
file per symbol to ``data/company_cache/<SYMBOL>.json``. The report then reads the cache
offline via :class:`core.report.company_feed.CachedCompanyReader`.

~492 symbols at 13 s is ~107 minutes for a full backfill — a cached one-off, mirroring
``scripts/prefetch_financials_universe.py``.

⚠ NO SHEBANG — deliberate, and it must stay that way
----------------------------------------------------
On Windows the ``py`` launcher READS the shebang line and dispatches on it. A
``#!/usr/bin/env python`` line makes ``py scripts/...`` hand the file to whatever
``python`` resolves to — frequently the Microsoft Store stub, which exits silently or
opens the Store — instead of the interpreter the operator actually invoked. The script
is run as ``py scripts/prefetch_company_universe.py``; with no shebang the launcher uses
its own default and the trap cannot fire. (``prefetch_financials_universe.py`` still
carries one; it is not this PR's to change.)

⚠ TWO TRAPS THIS SCRIPT EXISTS TO STEP AROUND, both verified on this box
-----------------------------------------------------------------------
1. **The key is NOT in the environment.** ``POLYGON_API_KEY`` lives in the OS keychain
   (``core/keychain.py``, SEC-5/ADR_006 — Windows Credential Manager here). Verified:
   ``os.environ.get("POLYGON_API_KEY")`` is empty on a fresh shell, and
   ``load_secrets_from_keychain()`` populates it. ``company_feed`` reads the env var, so
   the keychain load must happen FIRST or every symbol degrades to an honest gap and the
   run silently caches nothing.
2. **Importing the universe drags the whole engine config in.** Verified:
   ``import core.round_table.sp500_universe`` pulls ``config`` and initialises a
   KillSwitch with a RedisClient as an import side effect. So the universe import is
   LAZY — inside ``_resolve_universe``, only when no ``--symbols`` override is given —
   exactly as ``prefetch_financials_universe.py`` does it. ``core.keychain`` and
   ``core.report.company_feed`` are both config-free by design (BORA), so importing them
   at module scope is safe.

⚠ WHY THIS ONE ALWAYS REWRITES (diverging from the financials prefetch)
-----------------------------------------------------------------------
``prefetch_financials_universe._write_if_changed`` skips the write when ``results`` are
unchanged, to keep the cache byte-stable. That is wrong HERE for two reasons. First,
``market_cap`` moves every day, so byte-stability is unachievable anyway. Second, and
decisively: skipping the write would keep the OLD ``fetched_at``, and §0 RENDERS that
date ("Marktkapitalisierung 18.72 Mrd USD (Stand ...)"). A stale stamp would make the
report misstate which day its figure is from — turning a freshness optimisation into a
false statement. Freshness accounting stays honest instead.

Report-only and DORMANT: this touches no trading path. It only warms a cache the
(flag-gated) report reader consumes. Network lives entirely in
``core.report.company_feed`` — this script only orchestrates + throttles.

Usage
-----
    # full universe, default 13 s throttle (~107 min for ~492 symbols)
    py scripts/prefetch_company_universe.py --verbose

    # a quick subset / smoke test
    py scripts/prefetch_company_universe.py --symbols LYB,NVDA,XOM

    # see what a run would do; no network, no writes
    py scripts/prefetch_company_universe.py --dry-run --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

# Anchor sys.path to THIS checkout (ai_trading_bot/), not to the caller's cwd — the
# script must import the same core/ it ships next to, whatever directory it is run from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.keychain import load_secrets_from_keychain  # noqa: E402
from core.report.company_feed import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    cache_path,
    fetch_raw_company,
)

logger = logging.getLogger("prefetch_company")


def _resolve_universe(
    symbols_arg: Optional[str], max_symbols: Optional[int]
) -> List[str]:
    if symbols_arg:
        symbols = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    else:
        # LAZY on purpose — this import pulls config + a KillSwitch/RedisClient as an
        # import side effect (see the module docstring). A --symbols run must not pay it.
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = list(get_sp500_symbols())
    if max_symbols:
        symbols = symbols[:max_symbols]
    return symbols


def _is_fresh(path: str, skip_fresh_days: int) -> bool:
    """True if ``path`` exists and was fetched within ``skip_fresh_days``."""
    if skip_fresh_days <= 0 or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            fetched_at = json.load(handle).get("fetched_at")
        if not fetched_at:
            return False
        fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except (OSError, ValueError):
        return False
    age_days = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400.0
    return age_days < skip_fresh_days


def _write(path: str, symbol: str, results: dict) -> None:
    """Write the cache payload atomically. Always rewrites — see the module docstring.

    Stores the RAW payload; ``company_feed.map_polygon_company`` transforms at READ time,
    so a mapping change never needs a 107-minute re-fetch.
    """
    payload = {
        "symbol": symbol,
        "source": "polygon v3 /v3/reference/tickers/{ticker}",
        # The stamp §0 renders as "Stand <date>". It must reflect THIS fetch.
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def prefetch_universe(
    *,
    cache_dir: str,
    symbols: List[str],
    api_key: Optional[str],
    throttle_seconds: float,
    skip_fresh_days: int,
    force: bool,
    dry_run: bool,
    sleep=time.sleep,
) -> dict:
    """Walk ``symbols`` and warm the cache. Returns a summary counter dict."""
    stats = {"total": len(symbols), "fetched": 0, "skipped": 0, "failed": 0}
    for idx, symbol in enumerate(symbols, start=1):
        path = cache_path(cache_dir, symbol)
        if not force and _is_fresh(path, skip_fresh_days):
            stats["skipped"] += 1
            logger.info("[%d/%d] %s — fresh, skip", idx, stats["total"], symbol)
            continue
        if dry_run:
            logger.info("[%d/%d] %s — DRY-RUN would fetch", idx, stats["total"], symbol)
            continue

        results = fetch_raw_company(symbol, api_key=api_key)
        if results is None:
            stats["failed"] += 1
            logger.warning(
                "[%d/%d] %s — fetch failed (honest gap)", idx, stats["total"], symbol
            )
        else:
            stats["fetched"] += 1
            _write(path, symbol, dict(results))
            logger.info(
                "[%d/%d] %s — cached (%s)",
                idx,
                stats["total"],
                symbol,
                results.get("name") or "no name in payload",
            )
        # Throttle only after a real network call (skips cost no budget).
        if idx < stats["total"] and throttle_seconds > 0:
            sleep(throttle_seconds)
    return stats


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Polygon company-facts prefetch (§0).")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    p.add_argument(
        "--symbols", default=None, help="Comma list override (default: full S&P 500)."
    )
    p.add_argument("--max-symbols", type=int, default=None)
    p.add_argument(
        "--api-key", default=None, help="Default: keychain / $POLYGON_API_KEY."
    )
    p.add_argument(
        "--throttle-seconds", type=float, default=13.0, help="5/min free tier => 13 s."
    )
    p.add_argument(
        "--skip-fresh-days",
        type=int,
        default=7,
        help="Skip symbols cached within N days (company facts move slowly).",
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

    # TRAP 1 (see module docstring): the key lives in the OS keychain, not the env.
    # Skipped when --api-key is explicit, and it never overwrites an existing env var.
    if not args.api_key:
        load_secrets_from_keychain()
        if not os.environ.get("POLYGON_API_KEY"):
            logging.warning(
                "prefetch_company: no POLYGON_API_KEY in keychain or environment — "
                "every symbol would degrade to an honest gap. Nothing fetched."
            )
            return 1

    symbols = _resolve_universe(args.symbols, args.max_symbols)
    logger.info("Prefetching %d symbols -> %s", len(symbols), args.cache_dir)

    t0 = time.time()
    stats = prefetch_universe(
        cache_dir=args.cache_dir,
        symbols=symbols,
        api_key=args.api_key,
        throttle_seconds=args.throttle_seconds,
        skip_fresh_days=args.skip_fresh_days,
        force=args.force,
        dry_run=args.dry_run,
    )
    elapsed = time.time() - t0
    # Summary always printed (WARNING baseline), so cron logs stay useful.
    logging.warning(
        "prefetch_company done in %.0fs: total=%d fetched=%d skipped=%d failed=%d",
        elapsed,
        stats["total"],
        stats["fetched"],
        stats["skipped"],
        stats["failed"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
