#!/usr/bin/env python3
# scripts/seed_databento_cache.py
# ML-1 Phase 6b — Pre-warm GCS Databento cache for all DEFAULT_SYMBOLS.
#
# Run once (or before training) to seed the GCS cache so production Cloud Run
# containers never cold-start a full Databento fetch.
#
# Cost: fetches DEFAULT_SYMBOLS × HISTORY_DAYS of ohlcv-1d data once.
# After seeding, daily production fetches hit GCS and cost $0 (Databento not called).
#
# Usage:
#   python scripts/seed_databento_cache.py
#   python scripts/seed_databento_cache.py --symbols AAPL MSFT NVDA
#   python scripts/seed_databento_cache.py --days 730  # 2 years
#
# Prerequisites:
#   DATABENTO_API_KEY and DATABENTO_GCS_BUCKET must be set.
#   pip install databento google-cloud-storage

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-warm GCS Databento cache.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to seed. Defaults to DEFAULT_SYMBOLS from config.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=565,
        help="History depth in days (default: 565 = 365 + 200 buffer).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without calling Databento.",
    )
    args = parser.parse_args()

    # Import after arg parsing so --help works without GCP creds
    try:
        from config import DATABENTO_API_KEY, DATABENTO_GCS_BUCKET, DEFAULT_SYMBOLS
    except Exception as e:
        logger.error("Failed to import config: %s", e)
        sys.exit(1)

    if not DATABENTO_API_KEY:
        logger.error("DATABENTO_API_KEY is not set. Cannot seed cache.")
        sys.exit(1)

    if not DATABENTO_GCS_BUCKET:
        logger.error("DATABENTO_GCS_BUCKET is not set. Cannot seed GCS cache.")
        sys.exit(1)

    symbols = args.symbols or DEFAULT_SYMBOLS
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=args.days)

    logger.info(
        "Seeding GCS cache: %d symbols | %s → %s | bucket=%s",
        len(symbols),
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
        DATABENTO_GCS_BUCKET,
    )

    if args.dry_run:
        logger.info("DRY RUN — no Databento calls will be made.")
        from core.data_cache_gcs import (
            GCSDatabentoCache,
            _normalize_end,
            _normalize_start,
        )

        for sym in symbols:
            key = f"databento-cache/{sym}/{_normalize_start(start_date).strftime('%Y-%m-%d')}_{_normalize_end(end_date).strftime('%Y-%m-%d')}.pkl"
            logger.info("  Would cache: gs://%s/%s", DATABENTO_GCS_BUCKET, key)
        return

    try:
        from core.data_provider_databento import DatabentoHistoricalClient
        from core.data_cache_gcs import GCSDatabentoCache
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        sys.exit(1)

    raw_client = DatabentoHistoricalClient(api_key=DATABENTO_API_KEY)
    cache = GCSDatabentoCache(
        databento_client=raw_client,
        gcs_bucket=DATABENTO_GCS_BUCKET,
    )

    success, skipped, failed = 0, 0, 0

    for symbol in symbols:
        logger.info("Seeding %s ...", symbol)
        try:
            df = cache.get_bars(symbol, start_date, end_date)
            if df.empty:
                logger.warning("  %s: empty response — skipped.", symbol)
                skipped += 1
            else:
                logger.info("  %s: %d bars cached.", symbol, len(df))
                success += 1
        except Exception as e:
            logger.error("  %s: FAILED — %s", symbol, e)
            failed += 1

    logger.info(
        "Seed complete: %d succeeded | %d skipped | %d failed",
        success,
        skipped,
        failed,
    )
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
