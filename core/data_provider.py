# data_provider.py
# --- Alpaca primary; Databento for historical training/test data; Polygon for VIX/indices ---
# Epic 2.7: yfinance removed — replaced by Databento (institutional, MiFID-II compliant)

import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import threading
import requests
from io import StringIO
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus
import os
import pickle

from config import (
    DEFAULT_SYMBOLS,
    DATABENTO_ENABLED,
    DATABENTO_API_KEY,
    ALPACA_DATA_FEED,
    DATABENTO_GCS_BUCKET,
    POLYGON_API_KEY,
)
from core.polygon_data import fetch_bars as polygon_fetch_bars

# Databento is an optional dependency (Epic 2.7 — activated when DATABENTO_API_KEY is set)
# Not required for CI or basic operation; install separately: pip install databento>=0.40.0
try:
    from core.data_provider_databento import DatabentoHistoricalClient

    _DATABENTO_AVAILABLE = True
except ImportError:
    DatabentoHistoricalClient = None  # type: ignore[assignment,misc]
    _DATABENTO_AVAILABLE = False

# --- Define a cache directory ---
DATA_CACHE_DIR = "market_data_cache"

# --- ML-1 Phase 5: Point-in-time S&P 500 membership CSV (survivorship bias fix) ---
# ADR-D01: Point-in-time index membership prevents survivorship bias per ESMA backtesting guidelines.
# CSV format: symbol,start_date,end_date  (end_date empty = still in index)
SP500_MEMBERSHIP_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "sp500_historical_membership.csv",
)

# Alpaca does not support index symbols like ^VIX; use None to skip or a placeholder
ALPACA_VIX_SYMBOL = None  # Set to "VIX" if your Alpaca data feed includes it


def _alpaca_symbol(symbol: str) -> Optional[str]:
    """Map external symbols to Alpaca symbols; return None if not available from Alpaca."""
    s = str(symbol).strip().upper()
    if s.startswith("^"):
        if s == "^VIX" and ALPACA_VIX_SYMBOL:
            return ALPACA_VIX_SYMBOL
        return None  # Index symbols not in Alpaca stock data
    return s


def _bars_to_dataframe(bars_df: pd.DataFrame) -> pd.DataFrame:
    """Standardize Alpaca-py DataFrame to open, high, low, close, volume."""
    if bars_df is None or bars_df.empty:
        return pd.DataFrame()

    df = bars_df.copy()
    # Alpaca-py returns MultiIndex (symbol, timestamp) or just (timestamp)
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=0, drop=True)

    df.columns = [str(c).lower() for c in df.columns]
    required = ["open", "high", "low", "close", "volume"]

    # Ensure columns exist
    for col in required:
        if col not in df.columns:
            # Fallback for weird column names or missing data
            if col == "volume" and "v" in df.columns:
                df["volume"] = df["v"]
            elif col == "open" and "o" in df.columns:
                df["open"] = df["o"]
            elif col == "high" and "h" in df.columns:
                df["high"] = df["h"]
            elif col == "low" and "l" in df.columns:
                df["low"] = df["l"]
            elif col == "close" and "c" in df.columns:
                df["close"] = df["c"]
            else:
                return pd.DataFrame()

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df[required]


class HistoricalDataProvider:
    """Provides historical price data with use_case-aware source routing (ML-1).

    Historical (backtest/ML):  Databento → Alpaca → Polygon  (Databento-first, MiFID-II compliant)
    Live (order execution):    Alpaca → Polygon               (Databento never called for live)
    VIX / indices:             Polygon always                 (Databento doesn't cover CBOE products)
    """

    def __init__(
        self,
        api: Optional[StockHistoricalDataClient] = None,
        trading_api: Optional[TradingClient] = None,
    ):
        self.data_cache = {}
        self._lock = threading.Lock()
        self.symbol_cache: Optional[List[str]] = None
        self.api = api  # Data client
        self.trading_api = trading_api  # Trading client for assets/account

        # Databento client (lazy init — only when package installed AND DATABENTO_ENABLED=True)
        # ML-1 Phase 6: wrapped in GCSDatabentoCache when DATABENTO_GCS_BUCKET is set,
        # so Cloud Run cold starts hit GCS instead of re-fetching from Databento API.
        self._databento: Optional[DatabentoHistoricalClient] = None
        if DATABENTO_ENABLED and _DATABENTO_AVAILABLE:
            try:
                raw_client = DatabentoHistoricalClient(api_key=DATABENTO_API_KEY)
                if DATABENTO_GCS_BUCKET:
                    from core.data_cache_gcs import GCSDatabentoCache

                    self._databento = GCSDatabentoCache(  # type: ignore[assignment]
                        databento_client=raw_client,
                        gcs_bucket=DATABENTO_GCS_BUCKET,
                    )
                    logging.info(
                        "Databento + GCS cache initialized (bucket=%s). ML-1 Phase 6.",
                        DATABENTO_GCS_BUCKET,
                    )
                else:
                    self._databento = raw_client
                    logging.info(
                        "Databento Historical Client initialized (no GCS cache)."
                    )
            except Exception as e:
                logging.warning(
                    "Databento init failed: %s — falling back to Polygon.", e
                )
        elif DATABENTO_ENABLED and not _DATABENTO_AVAILABLE:
            logging.warning(
                "DATABENTO_API_KEY is set but databento package is not installed. "
                "Install with: pip install databento>=0.40.0"
            )

        os.makedirs(DATA_CACHE_DIR, exist_ok=True)
        logging.info(
            "Historical Data Provider initialized "
            "(Alpaca primary; Databento institutional; Polygon for VIX/indices)."
        )
        logging.info(f"Using disk cache at: {os.path.abspath(DATA_CACHE_DIR)}")

    def get_bars(
        self, symbol: str, timeframe: str = "1Day", limit: int = 100
    ) -> pd.DataFrame:
        """Alias for get_data to support legacy calls and test suite."""
        return self.get_data(symbol, datetime.now(), days=limit)

    def get_data(
        self,
        symbol: str,
        end_date: datetime,
        days: int = 365,
        *,
        use_case: str = "historical",  # "historical" = Databento-first (ML-1); "live" = Alpaca-first
        allow_yfinance: bool = False,  # Deprecated: yfinance removed in ML-1
    ) -> pd.DataFrame:
        if end_date is None:
            from datetime import datetime as _dt, timezone as _tz

            end_date = _dt.now(_tz.utc)
        elif isinstance(end_date, str):
            from datetime import datetime as _dt

            try:
                end_date = _dt.fromisoformat(end_date)
            except ValueError:
                from datetime import timezone as _tz

                end_date = _dt.now(_tz.utc)

        end_date_str = end_date.strftime("%Y-%m-%d")
        cache_key = f"{symbol}_{end_date_str}_{days}"
        end_date_naive = pd.Timestamp(end_date).tz_localize(None)

        # Calculate the required start date to ensure we have enough depth
        required_start_naive = end_date_naive - timedelta(days=days)

        if cache_key in self.data_cache:
            return self.data_cache[cache_key].copy()

        cache_file_path = os.path.join(DATA_CACHE_DIR, f"{symbol}.pkl")

        # --- FIX: Check Cache Depth ---
        if os.path.exists(cache_file_path):
            try:
                df_disk = pd.read_pickle(cache_file_path)
                if not df_disk.empty:
                    disk_start = df_disk.index.min().tz_localize(None)
                    disk_end = df_disk.index.max().tz_localize(None)

                    # Check if cache covers BOTH the end date and the required start date (with some buffer)
                    if disk_end >= end_date_naive and disk_start <= (
                        required_start_naive + timedelta(days=10)
                    ):
                        logging.debug("[%s] Loaded from disk cache (Depth OK).", symbol)
                        hist_filtered = df_disk[df_disk.index <= end_date_naive].copy()
                        with self._lock:
                            self.data_cache[cache_key] = hist_filtered.copy()
                        return hist_filtered
                    else:
                        logging.debug(
                            f"[{symbol}] Disk cache stale or too shallow. "
                            f"Disk Start: {disk_start.date()}, Req Start: {required_start_naive.date()}"
                        )
            except Exception as e:
                logging.warning("Could not read cache file %s: %s", cache_file_path, e)

        with self._lock:
            if cache_key in self.data_cache:
                return self.data_cache[cache_key].copy()

        start_date = end_date - timedelta(days=days + 200)
        fetch_end_date = end_date + timedelta(days=2)
        limit_bars = min(10000, (days + 200) * 2)

        alpaca_sym = _alpaca_symbol(symbol)

        # ML-1: Route by use_case. "historical" puts Databento first (MiFID-II compliant
        # point-in-time pricing). "live" keeps Alpaca first (integrated with order execution).
        use_databento_first = (
            use_case == "historical"
            and DATABENTO_ENABLED
            and self._databento is not None
        )

        if use_databento_first:
            # 1) Databento — institutional historical data, MiFID-II compliant (ML-1)
            try:
                db_bars = self._databento.get_bars(
                    symbol=alpaca_sym or symbol,
                    start=start_date,
                    end=fetch_end_date,
                )
                if not db_bars.empty:
                    hist_filtered = db_bars[db_bars.index <= end_date_naive].copy()
                    if not hist_filtered.empty:
                        hist_filtered.to_pickle(cache_file_path)
                        with self._lock:
                            self.data_cache[cache_key] = hist_filtered.copy()
                        logging.info(
                            "[DataProvider] Using Databento for %s (historical).",
                            symbol,
                        )
                        return hist_filtered.copy()
            except Exception as e:
                logging.debug("Databento primary for %s: %s", symbol, e)

            # 2) Alpaca fallback when Databento returns empty or errors
            if self.api and alpaca_sym is not None:
                try:
                    logging.debug(
                        "Databento empty/failed for %s — falling back to Alpaca.",
                        alpaca_sym,
                    )
                    request_params = StockBarsRequest(
                        symbol_or_symbols=alpaca_sym,
                        timeframe=AlpacaTimeFrame.Day,
                        start=start_date,
                        end=fetch_end_date,
                        feed=ALPACA_DATA_FEED,
                    )
                    bars_response = self.api.get_stock_bars(request_params)
                    hist = _bars_to_dataframe(bars_response.df)
                    if not hist.empty:
                        hist_filtered = hist[hist.index <= end_date_naive].copy()
                        if not hist_filtered.empty:
                            hist_filtered.to_pickle(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = hist_filtered.copy()
                            return hist_filtered.copy()
                except (APIError, Exception) as e:
                    logging.debug("Alpaca fallback for %s: %s", symbol, e)

            # 3) Polygon: indices or last resort
            if POLYGON_API_KEY:
                try:
                    hist = polygon_fetch_bars(
                        POLYGON_API_KEY,
                        symbol,
                        start_date,
                        fetch_end_date,
                        limit=limit_bars,
                    )
                    if not hist.empty:
                        hist_filtered = hist[hist.index <= end_date_naive].copy()
                        if not hist_filtered.empty:
                            hist_filtered.to_pickle(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = hist_filtered.copy()
                            return hist_filtered.copy()
                except Exception as e:
                    logging.debug("Polygon bars for %s: %s", symbol, e)

        else:
            # Legacy waterfall for live use_case (or when Databento is disabled):
            # Alpaca → Polygon. Databento is never called for live data.

            # 1) Alpaca when available (stocks only; indices like ^VIX fall through)
            if self.api and alpaca_sym is not None:
                try:
                    logging.debug("Fetching %s from Alpaca (alpaca-py)", alpaca_sym)
                    request_params = StockBarsRequest(
                        symbol_or_symbols=alpaca_sym,
                        timeframe=AlpacaTimeFrame.Day,
                        start=start_date,
                        end=fetch_end_date,
                        feed=ALPACA_DATA_FEED,
                    )
                    bars_response = self.api.get_stock_bars(request_params)
                    hist = _bars_to_dataframe(bars_response.df)
                    if not hist.empty:
                        hist_filtered = hist[hist.index <= end_date_naive].copy()
                        if not hist_filtered.empty:
                            hist_filtered.to_pickle(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = hist_filtered.copy()
                            return hist_filtered.copy()
                except (APIError, Exception) as e:
                    logging.debug("Alpaca bars for %s: %s", symbol, e)

            # 2) Polygon: indices (e.g. ^VIX) or fallback when Alpaca not configured / returned empty
            if POLYGON_API_KEY:
                try:
                    hist = polygon_fetch_bars(
                        POLYGON_API_KEY,
                        symbol,
                        start_date,
                        fetch_end_date,
                        limit=limit_bars,
                    )
                    if not hist.empty:
                        hist_filtered = hist[hist.index <= end_date_naive].copy()
                        if not hist_filtered.empty:
                            hist_filtered.to_pickle(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = hist_filtered.copy()
                            return hist_filtered.copy()
                except Exception as e:
                    logging.debug("Polygon bars for %s: %s", symbol, e)

        logging.debug(
            "No historical data for %s (use_case=%s, Databento-first=%s).",
            symbol,
            use_case,
            use_databento_first,
        )
        self.data_cache[cache_key] = pd.DataFrame()
        return pd.DataFrame()

    def get_batch_data(
        self, symbols: List[str], end_date: datetime, days: int = 365
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches historical data for a batch of symbols.
        """
        if not symbols:
            return {}

        if end_date is None:
            from datetime import datetime as _dt, timezone as _tz

            end_date = _dt.now(_tz.utc)
        elif isinstance(end_date, str):
            from datetime import datetime as _dt

            try:
                end_date = _dt.fromisoformat(end_date)
            except ValueError:
                from datetime import timezone as _tz

                end_date = _dt.now(_tz.utc)

        end_date_str = end_date.strftime("%Y-%m-%d")
        end_date_naive = pd.Timestamp(end_date).tz_localize(None)

        cache_file_name = f"batch_{end_date_str}_{days}d_{len(symbols)}s.pkl"
        cache_file_path = os.path.join(DATA_CACHE_DIR, cache_file_name)

        if os.path.exists(cache_file_path):
            logging.info("Loading master batch cache from disk: %s", cache_file_name)
            try:
                with open(cache_file_path, "rb") as f:
                    results = pickle.load(f)
                logging.info(
                    f"Successfully loaded {len(results)} symbols from master cache."
                )
                return results
            except Exception as e:
                logging.warning(
                    f"Failed to read master cache file {cache_file_path}: {e}. Re-downloading..."
                )
                os.remove(cache_file_path)

        logging.info("Master batch cache '%s' not found.", cache_file_name)
        start_date = end_date - timedelta(days=days + 100)
        fetch_end_date = end_date + timedelta(days=2)
        limit_bars = min(10000, (days + 100) * 2)
        results = {}

        try:
            if self.api:
                # Optimization: alpaca-py can do real multi-symbol batch fetch efficiently
                alpaca_symbols = [
                    _alpaca_symbol(s) for s in symbols if _alpaca_symbol(s)
                ]
                if alpaca_symbols:
                    try:
                        request_params = StockBarsRequest(
                            symbol_or_symbols=alpaca_symbols,
                            timeframe=AlpacaTimeFrame.Day,
                            start=start_date,
                            end=fetch_end_date,
                        )
                        bars_response = self.api.get_stock_bars(request_params)
                        batch_df = bars_response.df

                        for symbol in symbols:
                            a_sym = _alpaca_symbol(symbol)
                            if a_sym in batch_df.index.get_level_values(0):
                                symbol_df = batch_df.xs(a_sym)
                                hist = _bars_to_dataframe(symbol_df)
                                if not hist.empty:
                                    hist_filtered = hist[
                                        hist.index <= end_date_naive
                                    ].copy()
                                    if not hist_filtered.empty:
                                        results[symbol] = hist_filtered
                    except Exception as e:
                        logging.error("Alpaca batch fetch error: %s", e)
                    if POLYGON_API_KEY:
                        try:
                            hist = polygon_fetch_bars(
                                POLYGON_API_KEY,
                                symbol,
                                start_date,
                                fetch_end_date,
                                limit=limit_bars,
                            )
                            if not hist.empty:
                                hist_filtered = hist[
                                    hist.index <= end_date_naive
                                ].copy()
                                if not hist_filtered.empty:
                                    results[symbol] = hist_filtered
                        except Exception as e:
                            logging.debug("Polygon batch skip %s: %s", symbol, e)
            elif POLYGON_API_KEY:
                logging.info(
                    f"Starting batch Polygon bar fetch for {len(symbols)} symbols..."
                )
                for symbol in symbols:
                    try:
                        hist = polygon_fetch_bars(
                            POLYGON_API_KEY,
                            symbol,
                            start_date,
                            fetch_end_date,
                            limit=limit_bars,
                        )
                        if not hist.empty:
                            hist_filtered = hist[hist.index <= end_date_naive].copy()
                            if not hist_filtered.empty:
                                results[symbol] = hist_filtered
                    except Exception as e:
                        logging.debug("Polygon batch skip %s: %s", symbol, e)
            else:
                logging.warning(
                    "Neither Alpaca nor Polygon configured; cannot fetch batch historical data."
                )

            if results:
                logging.info(
                    f"Saving {len(results)} symbols to master cache: {cache_file_name}"
                )
                try:
                    with open(cache_file_path, "wb") as f:
                        pickle.dump(results, f)
                    logging.info("Master cache saved successfully.")
                except Exception as e:
                    logging.error("Failed to save master cache file: %s", e)
        except Exception as e:
            logging.error(f"Historical batch data fetch FAILED: {e}", exc_info=True)

        logging.info(
            f"Total symbols loaded for simulation: {len(results)}/{len(symbols)}"
        )
        return results

    def _get_alpaca_symbols(self) -> Optional[List[str]]:
        if not self.trading_api:
            logging.debug(
                "Alpaca Trading API not available to DataProvider, cannot fetch symbols."
            )
            return None

        try:
            logging.info(
                "Fetching all tradable US stock symbols from Alpaca (modern)..."
            )
            request_params = GetAssetsRequest(
                status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY
            )
            assets = self.trading_api.get_all_assets(request_params)
            symbols = [
                a.symbol
                for a in assets
                if a.tradable and a.exchange != "OTC" and "." not in a.symbol
            ]
            # TODO(PR-D): Complex f-string, review manually:             logging.info(f"Successfully loaded {len(symbols)} symbols from Alpaca.")
            logging.info(f"Successfully loaded {len(symbols)} symbols from Alpaca.")
            return symbols
        except APIError as e:
            logging.error("Alpaca API error fetching symbols: %s", e)
            return None
        except Exception as e:
            logging.error("Error fetching Alpaca symbols: %s", e)
            return None

    # --- FIX: Robust S&P 500 Scraping ---
    def get_sp500_symbols(self) -> List[str]:
        """
        Tries to fetch S&P 500 symbols from Wikipedia.
        Iterates through all tables to find the correct one.

        Survivorship bias note: This returns the *current* S&P 500 list. When used for
        backtests on past dates, constituents that were added after that date are still
        included, so backtest returns may be slightly optimistic. For point-in-time accuracy
        use a data source that provides historical index constituents.
        """
        try:
            logging.info("Fetching S&P 500 symbol list from Wikipedia...")
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

            headers = {"User-Agent": "aaagents-oss/1.0"}
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            tables = pd.read_html(StringIO(response.text))
            df = None

            # Iterate through all found tables to find the one with symbols
            for t in tables:
                if "Symbol" in t.columns:
                    df = t
                    symbol_col = "Symbol"
                    break
                elif "Ticker symbol" in t.columns:
                    df = t
                    symbol_col = "Ticker symbol"
                    break

            if df is None:
                raise KeyError(
                    "Could not find a table with 'Symbol' or 'Ticker symbol' column on Wikipedia page."
                )

            symbols = [
                s.replace(".", "-") for s in df[symbol_col].tolist()
            ]  # BRK.B -> BRK-B
            logging.info(
                f"Successfully loaded {len(symbols)} S&P 500 symbols from Wikipedia."
            )
            return symbols

        except Exception as e:
            logging.warning(
                f"Failed to fetch S&P 500 list: {e}. Falling back to default list."
            )
            fallback_list = [
                "SPY",
                "QQQ",
                "IWM",
                "DIA",
                "VTI",
                "AAPL",
                "MSFT",
                "GOOGL",
                "AMZN",
                "META",
                "TSLA",
                "NVDA",
                "JPM",
                "BAC",
                "WFC",
                "GS",
                "JNJ",
                "PFE",
                "UNH",
                "MRK",
                "XOM",
                "CVX",
                "COP",
                "WMT",
                "TGT",
                "COST",
                "DIS",
                "NFLX",
            ]
            return fallback_list

    def get_sp500_symbols_at_date(self, query_date: datetime) -> List[str]:
        """Point-in-time S&P 500 membership to prevent survivorship bias (ML-1 Phase 5).

        # ADR-D01: Point-in-time index membership prevents survivorship bias per ESMA backtesting guidelines.
        # Stocks removed from the index (e.g. SIVB, FRC after bank failures) are included
        # in backtests that cover dates when they were still members.

        Reads SP500_MEMBERSHIP_CSV (symbol, start_date, end_date) and adds historical
        members to the current Wikipedia list for the given query_date. Falls back to
        get_sp500_symbols() if the CSV is not found.
        """
        current = set(self.get_sp500_symbols())

        if not os.path.exists(SP500_MEMBERSHIP_CSV):
            logging.warning(
                "[DataProvider] sp500_historical_membership.csv not found at %s "
                "— using survivorship-biased current S&P 500 list.",
                SP500_MEMBERSHIP_CSV,
            )
            return list(current)

        try:
            df = pd.read_csv(
                SP500_MEMBERSHIP_CSV, parse_dates=["start_date", "end_date"]
            )
            query_ts = pd.Timestamp(query_date)

            for _, row in df.iterrows():
                symbol = row["symbol"]
                start = row["start_date"]
                end = row["end_date"]
                # Symbol was in index on query_date if: added before (or no start) AND not yet removed
                was_in_index = (pd.isna(start) or start <= query_ts) and (
                    pd.isna(end) or end >= query_ts
                )
                if was_in_index:
                    current.add(symbol)

            logging.info(
                "[DataProvider] Point-in-time S&P 500 for %s: %d symbols (CSV overlay applied).",
                query_date.strftime("%Y-%m-%d"),
                len(current),
            )
            return list(current)
        except Exception as e:
            logging.warning(
                "[DataProvider] Failed to read historical membership CSV: %s — using current list.",
                e,
            )
            return list(current)

    def get_available_symbols(self) -> List[str]:
        """
        Returns a list of symbols for simulation.
        Tries Alpaca API first, then Wikipedia, then default list.
        """
        if self.symbol_cache:
            return self.symbol_cache

        # 1. Try Alpaca first
        alpaca_symbols = self._get_alpaca_symbols()
        if alpaca_symbols:
            self.symbol_cache = list(set(alpaca_symbols + DEFAULT_SYMBOLS))
            return self.symbol_cache

        # 2. Try Wikipedia second
        wiki_symbols = self.get_sp500_symbols()

        # 3. Combine with default list and cache
        self.symbol_cache = list(set(wiki_symbols + DEFAULT_SYMBOLS))
        return self.symbol_cache

    def clear_cache(self):
        """Clears the data cache to free memory."""
        self.data_cache.clear()
        logging.info("Historical data cache cleared.")
