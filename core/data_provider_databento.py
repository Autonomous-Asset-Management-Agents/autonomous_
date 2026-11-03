# core/data_provider_databento.py
# Databento Historical Data Client — Epic 2.7
# Replaces yfinance for historical training and test data.
#
# Waterfall in data_provider.py:
#   1. Alpaca (live + recent historical, stocks only)
#   2. Databento (full historical, institutional quality, MiFID-II compliant)
#   3. Polygon (indices like ^VIX, fallback)
#
# Usage:
#   DATABENTO_API_KEY env var must be set.
#   DATABENTO_ENABLED is set automatically in config.py.

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Databento dataset for US equities (historical daily bars)
# DBEQ.BASIC = consolidated equities, all US exchanges (NYSE Arca + Nasdaq + NYSE).
# Required for ETFs (SPY, QQQ, IWM, DIA) which are NYSE Arca listed — XNAS.ITCH
# (Nasdaq only) would silently return empty for these symbols.
# Same ohlcv-1d price as XNAS.ITCH when fetching daily bars for specific symbols.
DATABENTO_DATASET = "DBEQ.BASIC"
DATABENTO_SCHEMA = (
    "ohlcv-1d"  # Daily OHLCV bars — cheapest schema, sufficient for daily signals
)


def _databento_to_dataframe(records) -> pd.DataFrame:
    """Convert Databento response records to standard OHLCV DataFrame."""
    if not records:
        return pd.DataFrame()

    rows = []
    for r in records:
        rows.append(
            {
                "open": r.open / 1e9,  # Databento prices are in fixed-point (1/1e9 USD)
                "high": r.high / 1e9,
                "low": r.low / 1e9,
                "close": r.close / 1e9,
                "volume": r.volume,
            }
        )
        index = pd.to_datetime(r.ts_event, unit="ns", utc=True).tz_localize(None)
        rows[-1]["_ts"] = index

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("_ts")
    df.index.name = None
    return df[["open", "high", "low", "close", "volume"]]


class DatabentoHistoricalClient:
    """
    Databento Historical Data Client.

    Provides point-in-time accurate OHLCV bars for US equities.
    Used exclusively for backtesting and ML model training — NOT for live trading.

    Args:
        api_key: Databento API key. Defaults to DATABENTO_API_KEY env var.
        dataset: Databento dataset identifier. Default: XNAS.ITCH
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        dataset: str = DATABENTO_DATASET,
    ):
        self._api_key = api_key or os.getenv("DATABENTO_API_KEY")
        self._dataset = dataset
        self._client = None

        if not self._api_key:
            raise ValueError(
                "DATABENTO_API_KEY is not set. "
                "Set the environment variable or pass api_key explicitly."
            )

    def _get_client(self):
        """Lazy-initialize Databento client to avoid import at module load time."""
        if self._client is None:
            try:
                import databento as db  # noqa: F401 (guarded import)

                self._client = db.Historical(key=self._api_key)
            except ImportError as exc:
                raise ImportError(
                    "databento package is not installed. " "Run: pip install databento"
                ) from exc
        return self._client

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV bars for a single symbol.

        Args:
            symbol: Ticker symbol (e.g. "AAPL").
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            DataFrame with columns [open, high, low, close, volume],
            indexed by date (tz-naive). Empty DataFrame on error.
        """
        try:
            if not start or not end:
                logger.warning(
                    "Databento get_bars(%s) failed: start or end date is None", symbol
                )
                return pd.DataFrame()

            client = self._get_client()
            logger.debug(
                "Databento: fetching %s from %s to %s", symbol, start.date(), end.date()
            )

            data = client.timeseries.get_range(
                dataset=self._dataset,
                symbols=[symbol],
                schema=DATABENTO_SCHEMA,
                start=start.strftime("%Y-%m-%dT00:00:00"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00"),
            )

            records = list(data)
            df = _databento_to_dataframe(records)
            if df.empty:
                logger.debug("Databento: no records for %s", symbol)
            return df

        except Exception as e:
            logger.warning("Databento get_bars(%s) failed: %s", symbol, e)
            return pd.DataFrame()

    def get_batch_bars(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch daily OHLCV bars for multiple symbols in a single API call.

        Args:
            symbols: List of ticker symbols.
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            Dict mapping symbol → OHLCV DataFrame.
        """
        if not symbols:
            return {}

        try:
            if not start or not end:
                logger.warning(
                    "Databento get_batch_bars failed: start or end date is None"
                )
                return {}

            client = self._get_client()
            logger.info(
                "Databento: batch-fetching %d symbols from %s to %s",
                len(symbols),
                start.date(),
                end.date(),
            )

            import databento as db  # noqa: F401

            data = client.timeseries.get_range(
                dataset=self._dataset,
                symbols=symbols,
                schema=DATABENTO_SCHEMA,
                start=start.strftime("%Y-%m-%dT00:00:00"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00"),
            )

            # Group records by symbol
            per_symbol: Dict[str, list] = {s: [] for s in symbols}
            for record in data:
                sym = getattr(record, "symbol", None)
                if sym and sym in per_symbol:
                    per_symbol[sym].append(record)

            result = {}
            for sym, records in per_symbol.items():
                df = _databento_to_dataframe(records)
                if not df.empty:
                    result[sym] = df

            logger.info("Databento: loaded %d/%d symbols", len(result), len(symbols))
            return result

        except Exception as e:
            logger.warning("Databento get_batch_bars failed: %s", e)
            return {}
