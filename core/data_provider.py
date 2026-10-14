# data_provider.py
# --- Alpaca primary; Databento for historical training/test data; Polygon for VIX/indices ---  # noqa: E501
# Epic 2.7: yfinance removed — replaced by Databento (institutional, MiFID-II compliant)  # noqa: E501

# pylint: disable=logging-fstring-interpolation

import logging
import os
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests
from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

import config
from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    DATABENTO_API_KEY,
    DATABENTO_ENABLED,
    DATABENTO_GCS_BUCKET,
    DEFAULT_SYMBOLS,
    POLYGON_API_KEY,
)

# ADR-OBS-01 / PR E: data-provider feed-health instrumentation (PURE OBSERVATION).  # noqa: E501
# The bumps below are fail-safe at the module boundary AND wrapped again here via  # noqa: E501
# ``_obs`` so a counter failure can never raise into — or alter the result/fallback  # noqa: E501
# of — a real data fetch on the research/data path.
from core.data_provider_telemetry import bump_source as _bump_source
from core.data_provider_telemetry import mark_universe as _mark_universe
from core.polygon_data import fetch_bars as polygon_fetch_bars


def _obs(source: str, ok: bool) -> None:
    """Fail-safe call-site guard around the per-source waterfall counter (PR E).  # noqa: E501

    DOUBLE-guarded (this wrapper + ``bump_source`` itself) so even a wholly-replaced  # noqa: E501
    bump can NEVER raise into the fetch waterfall. PURE OBSERVATION."""
    try:
        _bump_source(source, ok)
    except Exception:  # noqa: BLE001 — a broken counter must never break a fetch
        pass


# Databento is an optional dependency (Epic 2.7 — activated when DATABENTO_API_KEY is set)  # noqa: E501
# Not required for CI or basic operation; install separately: pip install databento>=0.40.0  # noqa: E501
try:
    from core.data_provider_databento import DatabentoHistoricalClient

    _DATABENTO_AVAILABLE = True
except ImportError:
    DatabentoHistoricalClient = None  # type: ignore[assignment,misc]
    _DATABENTO_AVAILABLE = False

# --- Define a cache directory ---
DATA_CACHE_DIR = "market_data_cache"

# LSTM-panel-starvation fix: symbols per batched StockBarsRequest in
# ``warm_universe_cache``. Alpaca cannot reliably serve a ~500-symbol universe in a
# single request, so the once/day warm-up chunks the universe (~5 requests for 500).
WARM_CACHE_CHUNK_SIZE = 100

# --- ML-1 Phase 5: Point-in-time S&P 500 membership CSV (survivorship bias fix) ---  # noqa: E501
# ADR-D01: Point-in-time index membership prevents survivorship bias per ESMA backtesting guidelines.  # noqa: E501
# CSV format: symbol,start_date,end_date  (end_date empty = still in index)
SP500_MEMBERSHIP_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "sp500_historical_membership.csv",
)

# Alpaca does not support index symbols like ^VIX; use None to skip or a placeholder  # noqa: E501
ALPACA_VIX_SYMBOL = None  # Set to "VIX" if your Alpaca data feed includes it


def _default_data_client() -> Optional[StockHistoricalDataClient]:
    """Build an Alpaca data client from config, or return None (R10).

    Never raises: a provider that cannot be constructed would take the engine down, and
    the Polygon fallback — slow as it is — still works. But it degrades LOUDLY. The
    symptom of silent degradation (a ~100-minute warm-up, or reports claiming "no data"
    for symbols that have data, because a throttled Polygon burst returns an empty frame
    instead of an error) is impossible to diagnose from a quiet log.
    CODING_POLICY §5.6: a fallback value is logged at WARNING, never DEBUG.
    """
    from config import get_secret_str

    try:
        key = get_secret_str(ALPACA_API_KEY)
        secret = get_secret_str(ALPACA_SECRET_KEY)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — config trouble must not break the provider
        logging.warning(
            "DataProvider: could not read Alpaca credentials (%s) — bars will fall back "
            "to Polygon at 5 req/min (~100 min for a 500-symbol universe).",
            exc,
        )
        return None

    if not key or not secret:
        logging.warning(
            "DataProvider: no Alpaca credentials configured — bars fall back to Polygon "
            "at 5 req/min (~100 min for a 500-symbol universe, and throttled bursts "
            "return EMPTY frames that read as 'no data')."
        )
        return None

    try:
        try:
            from core.client_factory import create_data_client
        except ImportError:
            from ai_trading_bot.core.client_factory import create_data_client

        return create_data_client(key, secret)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — degrade to Polygon, never crash the engine
        # ⚠ Log the exception TYPE, never str(exc): `key` and `secret` were just passed
        # to this constructor, and an SDK that echoes its arguments back in the message
        # ("invalid credentials: <key>") would write the secret straight into a log file
        # that gets pasted into issues. The type is enough to act on; the value is not
        # worth the exposure.
        logging.warning(
            "DataProvider: Alpaca data client construction failed (%s) — falling back "
            "to Polygon at 5 req/min.",
            type(exc).__name__,
        )
        return None


def _alpaca_symbol(symbol: str) -> Optional[str]:
    """Map external symbols to Alpaca symbols; return None if not available from Alpaca."""  # noqa: E501
    s = str(symbol).strip().upper()
    if s.startswith("^"):
        if s == "^VIX" and ALPACA_VIX_SYMBOL:
            return ALPACA_VIX_SYMBOL
        return None  # Index symbols not in Alpaca stock data
    # Class-share symbology: Alpaca's data AND trading APIs use DOT notation
    # (BRK.B, BF.B). Our internal universe carries the yfinance-era HYPHEN form
    # — get_sp500_symbols() does `.replace(".", "-")`. A single hyphenated ticker
    # makes Alpaca reject an ENTIRE snapshot batch atomically (HTTP 400 "invalid
    # symbol: BRK-B"), so the canonical external->Alpaca mapper must undo it.
    # US equities never use a hyphen on Alpaca; idempotent on the dot form.
    return s.replace("-", ".")


def to_alpaca_symbols(symbols: Iterable[str]) -> List[str]:
    """Normalise an external universe to Alpaca-native symbols for the live path.

    Maps every symbol through :func:`_alpaca_symbol` (hyphenated class shares
    ``BRK-B`` -> ``BRK.B``), drops the ones Alpaca cannot serve (index symbols
    map to ``None``), and preserves input order while de-duplicating.

    Applied to ``live_universe`` at engine boot so the live path's symbol
    identity is Alpaca-native end-to-end: the snapshot-batch key, the per-symbol
    ``snapshots[symbol]`` lookup, and the order/position key all agree. Without
    this, one hyphenated ticker 400s the whole snapshot batch -> ``snapshots={}``
    -> every symbol skipped -> the round table never runs -> 0 decisions.
    """
    out: List[str] = []
    seen: set = set()
    for sym in symbols:
        mapped = _alpaca_symbol(sym)
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out


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


def survivorship_adjusted(
    symbol_sample_mode: str, has_membership: bool
) -> bool:  # noqa: E501
    """Whether a backtest's universe is point-in-time (survivorship-adjusted), for honest UI  # noqa: E501
    labelling (SIM-1 T2, #1485). True only for the S&P 500 universe WITH a PIT-capable  # noqa: E501
    membership table covering the window — ``has_membership`` must come from  # noqa: E501
    ``has_point_in_time_membership(as_of=<backtest end>)``, which since #1907 refuses the  # noqa: E501
    claim for the shipped removals-only table (bare CSV existence is NOT adequacy).  # noqa: E501
    Full-market or an inadequate table ⇒ False (the Console then shows a survivorship-limitation flag).  # noqa: E501
    """
    return symbol_sample_mode == "sp500" and bool(has_membership)


_DEFAULT_API_SENTINEL = object()

# Hard LRU ceiling on data_cache entries (defense-in-depth behind the per-(symbol,days) latest-date
# retention below). Steady-state usage is ~universe × distinct days-params (a few thousand); this only
# backstops pathological key growth. Env-tunable.
_DATA_CACHE_MAX_ENTRIES = int(os.getenv("DATA_CACHE_MAX_ENTRIES", "4096") or "4096")


class _BoundedDataCache(OrderedDict):
    """Bounded replacement for ``HistoricalDataProvider.data_cache``.

    The cache key is ``f"{symbol}_{YYYY-MM-DD}_{days}"`` (get_data, ~line 347) — the calendar DATE is
    embedded, so a long-running engine (grounding/report fetches + the warm-universe cache run 24/7
    since #2529/#2519) mints a fresh key per symbol EVERY calendar day. The old plain dict never
    evicted, so it grew by one universe of multi-year DataFrames per day → RAM climbed to ~95% over
    days → OOM (Windows exit 0xFFFFFFFF) → an orphaned engine held :8001 → "cannot restart".

    A symbol only ever needs its LATEST-date frame per ``days`` param, so each set drops stale-date
    siblings for the same ``(symbol, days)`` and enforces a hard LRU ceiling → memory plateaus instead
    of climbing. Thread-safe via its own lock; a caller may also hold the provider lock — the two never
    deadlock (order is always provider-lock → cache-lock, and the cache lock is only ever taken here).
    """

    def __init__(self, maxsize: int = _DATA_CACHE_MAX_ENTRIES):
        super().__init__()
        self._maxsize = max(1, int(maxsize))
        self._cache_lock = threading.Lock()

    def __setitem__(self, key, value):
        with self._cache_lock:
            try:
                sym, _date, days = str(key).rsplit("_", 2)
                stale = [
                    k
                    for k in list(self.keys())
                    if k != key
                    and k.endswith("_" + days)
                    and str(k).rsplit("_", 2)[0] == sym
                ]
                for k in stale:
                    OrderedDict.__delitem__(self, k)
            except ValueError:
                pass  # unexpected key shape → skip sibling eviction, still LRU-capped
            if key in self:
                OrderedDict.__delitem__(self, key)
            OrderedDict.__setitem__(self, key, value)
            while len(self) > self._maxsize:
                self.popitem(last=False)


class HistoricalDataProvider:
    """Provides historical price data with use_case-aware source routing (ML-1).  # noqa: E501

    Historical (backtest/ML):  Databento → Alpaca → Polygon  (Databento-first, MiFID-II compliant)  # noqa: E501
    Live (order execution):    Alpaca → Polygon               (Databento never called for live)  # noqa: E501
    VIX / indices:             Polygon always                 (Databento doesn't cover CBOE products)  # noqa: E501
    """

    def __init__(
        self,
        api: Any = _DEFAULT_API_SENTINEL,
        trading_api: Optional[TradingClient] = None,
    ):
        # Memory-leak fix: bounded cache (per-(symbol,days) latest-date retention + LRU ceiling).
        # The old plain dict grew one universe of multi-year frames per calendar day → OOM over days.
        self.data_cache = _BoundedDataCache()
        self._lock = threading.Lock()
        self.symbol_cache: Optional[List[str]] = None
        # R10: an absent data client is a 40x silent degradation, not a neutral default.
        # With self.api None the Alpaca branch below is skipped and EVERY fetch falls
        # through to polygon_fetch_bars:
        #     Alpaca       ~200 req/min -> 500 symbols ~=   3 min
        #     Polygon free    5 req/min -> 500 symbols ~= 100 min
        # The engine injects a client (base.py). The REPORT path did not: engine_reader
        # and stock_specialist each build their own bare provider, and nothing is
        # pre-shipped (both caches are gitignored and absent from the desktop bundle),
        # so every user's machine fetches its own bars. The engine only warms its ~10
        # live_universe symbols; with the full-universe report registry on, the other
        # ~490 are cache MISSES on the report path -> a ~100-minute first run, and a
        # throttled Polygon burst answers with an EMPTY FRAME rather than an error, so
        # reports render "no data" for symbols whose data exists.
        # So: resolve a client from config when the caller did not pass one. An
        # explicitly passed api always wins (that is how the engine and tests inject).
        if api is _DEFAULT_API_SENTINEL:
            self.api = _default_data_client()
        else:
            self.api = api
        self.trading_api = trading_api  # Trading client for assets/account

        # Databento client (lazy init — only when package installed AND DATABENTO_ENABLED=True)  # noqa: E501
        # ML-1 Phase 6: wrapped in GCSDatabentoCache when DATABENTO_GCS_BUCKET is set,  # noqa: E501
        # so Cloud Run cold starts hit GCS instead of re-fetching from Databento API.  # noqa: E501
        self._databento: Optional[DatabentoHistoricalClient] = None
        if DATABENTO_ENABLED and _DATABENTO_AVAILABLE:
            try:
                raw_client = DatabentoHistoricalClient(
                    api_key=DATABENTO_API_KEY
                )  # noqa: E501
                if DATABENTO_GCS_BUCKET:
                    from core.data_cache_gcs import GCSDatabentoCache

                    self._databento = GCSDatabentoCache(  # type: ignore[assignment]  # noqa: E501
                        databento_client=raw_client,
                        gcs_bucket=DATABENTO_GCS_BUCKET,
                    )
                    logging.info(
                        "Databento + GCS cache initialized (bucket=%s). ML-1 Phase 6.",  # noqa: E501
                        DATABENTO_GCS_BUCKET,
                    )
                else:
                    self._databento = raw_client
                    logging.info(
                        "Databento Historical Client initialized (no GCS cache)."  # noqa: E501
                    )
            except Exception as e:
                logging.warning(
                    "Databento init failed: %s — falling back to Polygon.", e
                )
        elif DATABENTO_ENABLED and not _DATABENTO_AVAILABLE:
            logging.warning(
                "DATABENTO_API_KEY is set but databento package is not installed. "  # noqa: E501
                "Install with: pip install databento>=0.40.0"
            )

        os.makedirs(DATA_CACHE_DIR, exist_ok=True)
        logging.info(
            "Historical Data Provider initialized "
            "(Alpaca primary; Databento institutional; Polygon for VIX/indices)."  # noqa: E501
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
        use_case: str = "historical",  # "historical" = Databento-first (ML-1); "live" = Alpaca-first  # noqa: E501
        allow_yfinance: bool = False,  # Deprecated: yfinance removed in ML-1
    ) -> pd.DataFrame:
        if end_date is None:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

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

        # C9 (#2375) defense-in-depth: os.path.basename strips any path separators so a crafted
        # symbol cannot traverse out of DATA_CACHE_DIR (the /stock-history endpoint also validates
        # ticker shape). Legit tickers (e.g. BRK.B) are unaffected.
        cache_file_path = os.path.join(
            DATA_CACHE_DIR, os.path.basename(f"{symbol}.parquet")
        )

        # --- FIX: Check Cache Depth ---
        if os.path.exists(cache_file_path):
            try:
                df_disk = pd.read_parquet(cache_file_path)
                if not df_disk.empty:
                    disk_start = df_disk.index.min().tz_localize(None)
                    disk_end = df_disk.index.max().tz_localize(None)

                    # Check if cache covers BOTH the end date and the required start date (with some buffer)  # noqa: E501
                    # DATE-granularity end check (LSTM-panel-starvation fix): daily bars are
                    # stamped 00:00, but end_date carries an intraday time (e.g. 14:30). A raw
                    # ``disk_end >= end_date_naive`` is False all day for today's still-forming
                    # bar, so after a restart (in-memory data_cache cleared) get_data would
                    # wrongly refetch live per symbol — re-opening the rate-limit outage the
                    # once/day warm-up closes. Comparing DATES lets today's 00:00 daily bar
                    # satisfy an intraday end. (Deeper history than requested is fine — the
                    # start check below still guards depth.)
                    if disk_end.date() >= end_date_naive.date() and disk_start <= (
                        required_start_naive + timedelta(days=10)
                    ):
                        logging.debug(
                            "[%s] Loaded from disk cache (Depth OK).", symbol
                        )  # noqa: E501
                        hist_filtered = df_disk[
                            df_disk.index <= end_date_naive
                        ].copy()  # noqa: E501
                        with self._lock:
                            self.data_cache[cache_key] = hist_filtered.copy()
                        return hist_filtered
                    else:
                        logging.debug(
                            f"[{symbol}] Disk cache stale or too shallow. "
                            f"Disk Start: {disk_start.date()}, Req Start: {required_start_naive.date()}"  # noqa: E501
                        )
            except Exception as e:
                logging.warning(
                    "Could not read cache file %s: %s", cache_file_path, e
                )  # noqa: E501

        with self._lock:
            if cache_key in self.data_cache:
                return self.data_cache[cache_key].copy()

        start_date = end_date - timedelta(days=days + 200)
        fetch_end_date = end_date + timedelta(days=2)
        limit_bars = min(10000, (days + 200) * 2)

        alpaca_sym = _alpaca_symbol(symbol)

        # ML-1: Route by use_case. "historical" puts Databento first (MiFID-II compliant  # noqa: E501
        # point-in-time pricing). "live" keeps Alpaca first (integrated with order execution).  # noqa: E501
        use_databento_first = (
            use_case == "historical"
            and DATABENTO_ENABLED
            and self._databento is not None
        )

        if use_databento_first:
            # 1) Databento — institutional historical data, MiFID-II compliant (ML-1)  # noqa: E501
            try:
                db_bars = self._databento.get_bars(
                    symbol=alpaca_sym or symbol,
                    start=start_date,
                    end=fetch_end_date,
                )
                if not db_bars.empty:
                    hist_filtered = db_bars[
                        db_bars.index <= end_date_naive
                    ].copy()  # noqa: E501
                    if not hist_filtered.empty:
                        hist_filtered.to_parquet(cache_file_path)
                        with self._lock:
                            self.data_cache[cache_key] = hist_filtered.copy()
                        logging.info(
                            "[DataProvider] Using Databento for %s (historical).",  # noqa: E501
                            symbol,
                        )
                        _obs(
                            "databento", ok=True
                        )  # PR E: fail-safe observation  # noqa: E501
                        return hist_filtered.copy()
            except Exception as e:
                _obs("databento", ok=False)  # PR E: fail-safe observation
                logging.warning("Databento primary for %s: %s", symbol, e)

            # 2) Alpaca fallback when Databento returns empty or errors
            if self.api and alpaca_sym is not None:
                try:
                    logging.warning(
                        "Databento empty/failed for %s — falling back to Alpaca.",  # noqa: E501
                        alpaca_sym,
                    )
                    request_params = StockBarsRequest(
                        symbol_or_symbols=alpaca_sym,
                        timeframe=AlpacaTimeFrame.Day,
                        start=start_date,
                        end=fetch_end_date,
                        feed=config.ALPACA_DATA_FEED,
                    )
                    bars_response = self.api.get_stock_bars(request_params)
                    hist = _bars_to_dataframe(bars_response.df)
                    if not hist.empty:
                        hist_filtered = hist[
                            hist.index <= end_date_naive
                        ].copy()  # noqa: E501
                        if not hist_filtered.empty:
                            hist_filtered.to_parquet(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = (
                                    hist_filtered.copy()
                                )  # noqa: E501
                            _obs(
                                "alpaca", ok=True
                            )  # PR E: fail-safe observation  # noqa: E501
                            return hist_filtered.copy()
                except (APIError, Exception) as e:
                    _obs("alpaca", ok=False)  # PR E: fail-safe observation
                    logging.warning("Alpaca fallback for %s: %s", symbol, e)

            # 3) Polygon: indices or last resort
            poly_key = config.get_secret_str(POLYGON_API_KEY)
            if poly_key:
                try:
                    hist = polygon_fetch_bars(
                        poly_key,
                        symbol,
                        start_date,
                        fetch_end_date,
                        limit=limit_bars,
                    )
                    if not hist.empty:
                        hist_filtered = hist[
                            hist.index <= end_date_naive
                        ].copy()  # noqa: E501
                        if not hist_filtered.empty:
                            hist_filtered.to_parquet(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = (
                                    hist_filtered.copy()
                                )  # noqa: E501
                            _obs(
                                "polygon", ok=True
                            )  # PR E: fail-safe observation  # noqa: E501
                            return hist_filtered.copy()
                except Exception as e:
                    _obs("polygon", ok=False)  # PR E: fail-safe observation
                    logging.warning("Polygon bars for %s: %s", symbol, e)

        else:
            # Legacy waterfall for live use_case (or when Databento is disabled):  # noqa: E501
            # Alpaca → Polygon. Databento is never called for live data.

            # 1) Alpaca when available (stocks only; indices like ^VIX fall through)  # noqa: E501
            if self.api and alpaca_sym is not None:
                try:
                    logging.debug(
                        "Fetching %s from Alpaca (alpaca-py)", alpaca_sym
                    )  # noqa: E501
                    request_params = StockBarsRequest(
                        symbol_or_symbols=alpaca_sym,
                        timeframe=AlpacaTimeFrame.Day,
                        start=start_date,
                        end=fetch_end_date,
                        feed=config.ALPACA_DATA_FEED,
                    )
                    bars_response = self.api.get_stock_bars(request_params)
                    hist = _bars_to_dataframe(bars_response.df)
                    if not hist.empty:
                        hist_filtered = hist[
                            hist.index <= end_date_naive
                        ].copy()  # noqa: E501
                        if not hist_filtered.empty:
                            hist_filtered.to_parquet(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = (
                                    hist_filtered.copy()
                                )  # noqa: E501
                            _obs(
                                "alpaca", ok=True
                            )  # PR E: fail-safe observation  # noqa: E501
                            return hist_filtered.copy()
                except (APIError, Exception) as e:
                    _obs("alpaca", ok=False)  # PR E: fail-safe observation
                    logging.warning("Alpaca bars for %s: %s", symbol, e)

            # 2) Polygon: indices (e.g. ^VIX) or fallback when Alpaca not configured / returned empty  # noqa: E501
            poly_key = config.get_secret_str(POLYGON_API_KEY)
            if poly_key:
                try:
                    hist = polygon_fetch_bars(
                        poly_key,
                        symbol,
                        start_date,
                        fetch_end_date,
                        limit=limit_bars,
                    )
                    if not hist.empty:
                        hist_filtered = hist[
                            hist.index <= end_date_naive
                        ].copy()  # noqa: E501
                        if not hist_filtered.empty:
                            hist_filtered.to_parquet(cache_file_path)
                            with self._lock:
                                self.data_cache[cache_key] = (
                                    hist_filtered.copy()
                                )  # noqa: E501
                            _obs(
                                "polygon", ok=True
                            )  # PR E: fail-safe observation  # noqa: E501
                            return hist_filtered.copy()
                except Exception as e:
                    _obs("polygon", ok=False)  # PR E: fail-safe observation
                    logging.warning("Polygon bars for %s: %s", symbol, e)

        logging.warning(
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
            from datetime import datetime as _dt
            from datetime import timezone as _tz

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

        cache_file_name = (
            f"batch_{end_date_str}_{days}d_{len(symbols)}s.parquet"  # noqa: E501
        )
        cache_file_path = os.path.join(DATA_CACHE_DIR, cache_file_name)

        if os.path.exists(cache_file_path):
            logging.info(
                "Loading master batch cache from disk: %s", cache_file_name
            )  # noqa: E501
            try:
                master_df = pd.read_parquet(cache_file_path)
                results = {
                    symbol: master_df.xs(symbol, level="symbol")
                    for symbol in master_df.index.get_level_values(
                        "symbol"
                    ).unique()  # noqa: E501
                }
                logging.info(
                    f"Successfully loaded {len(results)} symbols from master cache."  # noqa: E501
                )
                return results
            except Exception as e:
                logging.warning(
                    f"Failed to read master cache file {cache_file_path}: {e}. Re-downloading..."  # noqa: E501
                )
                os.remove(cache_file_path)

        logging.info("Master batch cache '%s' not found.", cache_file_name)
        start_date = end_date - timedelta(days=days + 100)
        fetch_end_date = end_date + timedelta(days=2)
        limit_bars = min(10000, (days + 100) * 2)
        results = {}

        try:
            if self.api:
                # Optimization: alpaca-py can do real multi-symbol batch fetch efficiently  # noqa: E501
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
                    poly_key = config.get_secret_str(POLYGON_API_KEY)
                    if poly_key:
                        for symbol in symbols:
                            if symbol not in results:
                                try:
                                    hist = polygon_fetch_bars(
                                        poly_key,
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
                                    logging.warning(
                                        "Polygon batch skip %s: %s", symbol, e
                                    )  # noqa: E501
            elif config.get_secret_str(POLYGON_API_KEY):
                poly_key = config.get_secret_str(POLYGON_API_KEY)
                logging.info(
                    f"Starting batch Polygon bar fetch for {len(symbols)} symbols..."  # noqa: E501
                )
                for symbol in symbols:
                    try:
                        hist = polygon_fetch_bars(
                            poly_key,
                            symbol,
                            start_date,
                            fetch_end_date,
                            limit=limit_bars,
                        )
                        if not hist.empty:
                            hist_filtered = hist[
                                hist.index <= end_date_naive
                            ].copy()  # noqa: E501
                            if not hist_filtered.empty:
                                results[symbol] = hist_filtered
                    except Exception as e:
                        logging.warning("Polygon batch skip %s: %s", symbol, e)
            else:
                logging.warning(
                    "Neither Alpaca nor Polygon configured; cannot fetch batch historical data."  # noqa: E501
                )

            if results:
                logging.info(
                    f"Saving {len(results)} symbols to master cache: {cache_file_name}"  # noqa: E501
                )
                try:
                    pd.concat(
                        results, names=["symbol", "timestamp"]
                    ).to_parquet(  # noqa: E501
                        cache_file_path
                    )
                    logging.info("Master cache saved successfully.")
                except Exception as e:
                    logging.error("Failed to save master cache file: %s", e)
        except Exception as e:
            logging.error(
                f"Historical batch data fetch FAILED: {e}", exc_info=True
            )  # noqa: E501

        logging.info(
            f"Total symbols loaded for simulation: {len(results)}/{len(symbols)}"  # noqa: E501
        )
        return results

    def warm_universe_cache(
        self, symbols: List[str], end_date: datetime, days: int = 365
    ) -> int:
        """LSTM-panel-starvation fix (Part 1) — warm the per-symbol daily-bar cache
        ONCE with chunked, batched requests so the per-cycle read path
        (``get_data``) is an in-memory / disk cache HIT and never touches a live
        provider on the hot cycle.

        Mirrors ``get_batch_data``'s batched Alpaca request + MultiIndex split. For
        each symbol it writes BOTH:
          (a) the in-memory ``data_cache`` under the EXACT key ``get_data`` computes
              and checks FIRST (``f"{symbol}_{end.strftime('%Y-%m-%d')}_{days}"``,
              data_provider.py:337/343) — a date-stable, all-day key — so every
              per-cycle ``get_data(symbol, end, days)`` returns from memory; and
          (b) the ``{symbol}.parquet`` disk file (restart-robustness — after a
              restart the in-memory cache is empty, but the date-aware disk-depth
              check in ``get_data`` then serves today's bar from disk).

        Provider-agnostic: the FILL uses the configured provider (Alpaca), the READ
        path never touches it. Returns the number of symbols warmed.

        Fail-safe (CODING_POLICY §5.6): NEVER raises. Any error logs a WARNING and
        returns the count warmed so far, leaving the per-symbol ``get_data`` fallback
        fully intact (the flag OFF path stays byte-identical).
        """
        if not symbols:
            return 0
        # No live Alpaca client -> nothing to batch-warm; the per-symbol get_data
        # waterfall (Databento/Polygon) stays intact. Mirrors get_data/get_batch_data,
        # which gate the batched Alpaca request on ``self.api`` truthiness. (The
        # DataProvider has no SimulationAdapter handle — the backtest path reads
        # ``strategy.client.get_bars``, never this cache, and the warm-up is wired only
        # into the LIVE loop, so the simulation path is untouched either way.)
        if not self.api:
            return 0

        # Normalise end_date EXACTLY like get_data (:321-336) so the cache key the
        # warm-up writes is byte-identical to the key get_data computes + checks first.
        if end_date is None:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

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
        start_date = end_date - timedelta(days=days + 200)
        fetch_end_date = end_date + timedelta(days=2)

        warmed = 0
        try:
            for i in range(0, len(symbols), WARM_CACHE_CHUNK_SIZE):
                chunk = symbols[i : i + WARM_CACHE_CHUNK_SIZE]
                alpaca_symbols = [_alpaca_symbol(s) for s in chunk if _alpaca_symbol(s)]
                if not alpaca_symbols:
                    continue
                request_params = StockBarsRequest(
                    symbol_or_symbols=alpaca_symbols,
                    timeframe=AlpacaTimeFrame.Day,
                    start=start_date,
                    end=fetch_end_date,
                    feed=config.ALPACA_DATA_FEED,
                )
                bars_response = self.api.get_stock_bars(request_params)
                batch_df = bars_response.df
                if batch_df is None or batch_df.empty:
                    continue
                level0 = batch_df.index.get_level_values(0)
                for symbol in chunk:
                    a_sym = _alpaca_symbol(symbol)
                    if a_sym is None or a_sym not in level0:
                        continue
                    hist = _bars_to_dataframe(batch_df.xs(a_sym))
                    if hist.empty:
                        continue
                    # Filter to <= end (like get_data at :368-370) so the warmed frame
                    # is byte-identical to what get_data would have returned.
                    df_sym = hist[hist.index <= end_date_naive].copy()
                    if df_sym.empty:
                        continue

                    cache_key = f"{symbol}_{end_date_str}_{days}"
                    # (a) in-memory FIRST — this is what get_data returns at :343-344
                    # with no depth check, so the hot path is a pure memory hit today.
                    with self._lock:
                        self.data_cache[cache_key] = df_sym.copy()
                    # (b) disk parquet (restart-robustness); a single-symbol disk error
                    # must not abort the whole warm-up — the memory entry already
                    # covers today's reads.
                    cache_file_path = os.path.join(
                        DATA_CACHE_DIR, os.path.basename(f"{symbol}.parquet")
                    )
                    try:
                        df_sym.to_parquet(cache_file_path)
                    except Exception as exc:  # noqa: BLE001 — disk best-effort
                        logging.warning(
                            "warm_universe_cache: parquet write failed for %s (%s)",
                            symbol,
                            type(exc).__name__,
                        )
                    warmed += 1
        except Exception:  # noqa: BLE001 — §5.6: a warm-up must never raise
            logging.warning(
                "warm_universe_cache failed after warming %d/%d symbols — per-symbol "
                "get_data fallback stays intact.",
                warmed,
                len(symbols),
                exc_info=True,
            )
            return warmed

        logging.info(
            "warm_universe_cache: warmed %d/%d symbols (end=%s, days=%d).",
            warmed,
            len(symbols),
            end_date_str,
            days,
        )
        return warmed

    def _get_alpaca_symbols(self) -> Optional[List[str]]:
        if not self.trading_api:
            logging.debug(
                "Alpaca Trading API not available to DataProvider, cannot fetch symbols."  # noqa: E501
            )
            return None

        try:
            logging.info(
                "Fetching all tradable US stock symbols from Alpaca (modern)..."  # noqa: E501
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
            logging.info(
                f"Successfully loaded {len(symbols)} symbols from Alpaca."
            )  # noqa: E501
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

        Survivorship bias note: This returns the *current* S&P 500 list. When used for  # noqa: E501
        backtests on past dates, constituents that were added after that date are still  # noqa: E501
        included, so backtest returns may be slightly optimistic. For point-in-time accuracy  # noqa: E501
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
                    "Could not find a table with 'Symbol' or 'Ticker symbol' column on Wikipedia page."  # noqa: E501
                )

            symbols = [
                s.replace(".", "-") for s in df[symbol_col].tolist()
            ]  # BRK.B -> BRK-B
            logging.info(
                f"Successfully loaded {len(symbols)} S&P 500 symbols from Wikipedia."  # noqa: E501
            )
            return symbols

        except Exception as e:
            logging.warning(
                f"Failed to fetch S&P 500 list: {e}. Falling back to default list."  # noqa: E501
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

    def get_nasdaq_symbols(self) -> List[str]:
        """
        Tries to fetch NASDAQ-100 symbols from Wikipedia, falling back to a static list of tech leaders.  # noqa: E501
        """
        try:
            logging.info("Fetching NASDAQ-100 symbol list from Wikipedia...")
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            headers = {
                "User-Agent": "AI-Trading-Bot/1.0 (contact@aaagents.de)"
            }  # noqa: E501
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            tables = pd.read_html(StringIO(response.text))
            df = None

            for t in tables:
                if "Ticker" in t.columns:
                    df = t
                    symbol_col = "Ticker"
                    break
                elif "Symbol" in t.columns:
                    df = t
                    symbol_col = "Symbol"
                    break

            if df is None:
                raise KeyError(
                    "Could not find a table with 'Ticker' or 'Symbol' column."
                )

            symbols = [s.replace(".", "-") for s in df[symbol_col].tolist()]
            logging.info(
                f"Successfully loaded {len(symbols)} NASDAQ-100 symbols from Wikipedia."  # noqa: E501
            )
            return symbols
        except Exception as e:
            logging.warning(
                "Failed to fetch NASDAQ symbols from Wikipedia: %s. Using fallback list.",  # noqa: E501
                e,
            )
            return [
                "AAPL",
                "MSFT",
                "GOOGL",
                "AMZN",
                "META",
                "TSLA",
                "NVDA",
                "AVGO",
                "COST",
                "NFLX",
                "AMD",
                "QCOM",
                "TXN",
                "INTC",
                "HON",
                "AMGN",
                "SBUX",
                "MDLZ",
                "ISRG",
                "GILD",
            ]

    @staticmethod
    def has_point_in_time_membership(as_of: Optional["datetime"] = None) -> bool:
        """Whether the membership table can support a survivorship-adjusted claim.

        ADR-SURV-01 — this used to be `os.path.exists(CSV)`: EXISTENCE, not adequacy. The
        result feeds ``survivorship_adjusted()`` and lands in the simulation output as
        ``"survivorship_adjusted": True`` (core/engine/simulation_runner.py), at which
        point the Console SUPPRESSES its survivorship-limitation flag. So a partial, stale
        correction was presented as a complete one.

        What the shipped table actually is (measured, pinned by
        tests/unit/test_survivorship_claim_honesty.py):
          * 22 data rows, ALL carrying an end_date — ZERO open-ended. It lists only who
            LEFT the index, never who was IN it.
          * Coverage 2018-06-01 .. 2024-12-23. A backtest running to 2026-05-01 is
            uncorrected for its last ~17 months.
          * 12 removals inside a 2.33-year window where the index sees ~20-25 changes a
            year, i.e. ~50 expected.

        THE FIX IS THE CLAIM, NOT THE DATA. Nobody can source a decade of index changes
        here, and a partial table is still better than none — it just cannot be called
        complete. So: pass ``as_of`` and the answer is False whenever the table stops
        before that date. Under-claiming is honest; over-claiming is a false label on an
        artifact we sell as auditable.

        #1907 (MLR-7 RC-1/RC-3) — STRUCTURAL gate on top of the coverage gate: a table
        where EVERY data row carries an ``end_date`` is removals-only. It can say who
        LEFT the index, but never who was IN it on a given day, so the overlay built
        from it is "today's list ∪ a few returnees" — still carrying look-ahead
        entrants. Such a table cannot support the claim for ANY ``as_of``, including
        the no-arg path (which used to answer True on bare file existence; the old
        docstring called flipping that "a decision, not a side effect" — the
        plan-approved #1907 IS that decision). Only a table with open-ended rows
        (current members, ``end_date`` empty) is structurally PIT-capable; for those
        the pre-#1907 semantics hold: no ``as_of`` -> True (no coverage judgement
        possible), ``as_of`` past the latest dated row -> False.

        Fail-CLOSED: an absent, empty, or unreadable table returns False. Garbage must
        never read as "adjusted".
        """
        if not os.path.exists(SP500_MEMBERSHIP_CSV):
            return False

        try:
            import csv as _csv

            with open(SP500_MEMBERSHIP_CSV, newline="", encoding="utf-8") as fh:
                ends = [
                    (row.get("end_date") or "").strip() for row in _csv.DictReader(fh)
                ]
            if not ends:
                return False  # header-only table knows nobody's membership
            if all(ends):
                # #1907 RC-1: removals-only — structurally unable to answer
                # "who was in the index on date D". Refuse the claim outright.
                logging.warning(
                    "Survivorship membership table %s is removals-only (all %d rows "
                    "carry an end_date, zero open-ended) — it cannot reconstruct "
                    "point-in-time membership; refusing the survivorship-adjusted "
                    "claim (as_of=%s). See #1907.",
                    SP500_MEMBERSHIP_CSV,
                    len(ends),
                    as_of,
                )
                return False
            if as_of is None:
                # Structurally capable table, but no window given -> no coverage
                # judgement possible (pre-#1907 back-compat, capable tables only).
                return True
            latest = max((e for e in ends if e), default="")
            if not latest:
                return False  # no dated rows -> nothing to judge coverage by
            as_of_str = (
                as_of.date().isoformat() if hasattr(as_of, "date") else str(as_of)[:10]
            )
            return latest >= as_of_str
        except Exception as exc:  # noqa: BLE001 — unreadable table -> cannot claim
            logging.warning(
                "Survivorship table unreadable (%s) — refusing the "
                "survivorship-adjusted claim for %s.",
                type(exc).__name__,
                as_of,
            )
            return False

    def get_sp500_symbols_at_date(self, query_date: datetime) -> List[str]:
        """Historical S&P 500 universe for ``query_date`` (ML-1 Phase 5; #1907 Inc 2).

        # ADR-D01: Point-in-time index membership prevents survivorship bias per ESMA backtesting guidelines.  # noqa: E501
        # Stocks removed from the index (e.g. SIVB, FRC after bank failures) are included  # noqa: E501
        # in backtests that cover dates when they were still members.

        Two regimes, decided by what SP500_MEMBERSHIP_CSV structurally IS:

        * PIT-CAPABLE table (open-ended rows present — the #1907 Inc 2
          reconstruction): a real SET-DIFF. A name is in the universe iff one of
          its recorded membership intervals covers ``query_date`` — today's names
          whose membership had not started yet are DISCARDED (RC-2 look-ahead
          healed: no PLTR in a 2019 universe), departed members inside their
          window are included. Names entirely unknown to the table cannot be
          judged and are kept best-effort — loudly, at WARNING (§5.6).

        * REMOVALS-ONLY table (every row carries an end_date — the pre-Inc-2
          state): nothing to key a discard on, so the overlay stays
          ADDITIVE-ONLY (today's list + historical members re-added) and the
          residual look-ahead bias is flagged at WARNING on every application.

        Falls back to get_sp500_symbols() (with WARNING) if the CSV is missing
        or unreadable.
        """
        current = set(self.get_sp500_symbols())

        if not os.path.exists(SP500_MEMBERSHIP_CSV):
            logging.warning(
                "[DataProvider] sp500_historical_membership.csv not found at %s "  # noqa: E501
                "— using survivorship-biased current S&P 500 list.",
                SP500_MEMBERSHIP_CSV,
            )
            return list(current)

        try:
            df = pd.read_csv(
                SP500_MEMBERSHIP_CSV, parse_dates=["start_date", "end_date"]
            )
            query_ts = pd.Timestamp(query_date)

            covers = (df["start_date"].isna() | (df["start_date"] <= query_ts)) & (
                df["end_date"].isna() | (df["end_date"] >= query_ts)
            )
            members_then = set(df.loc[covers, "symbol"])
            in_table = set(df["symbol"])
            pit_capable = bool(len(df)) and bool(df["end_date"].isna().any())

            if pit_capable:
                # #1907 Inc 2 — real set-diff on the open-interval table.
                unknown_kept = current - in_table
                discarded = (current & in_table) - members_then
                result = members_then | unknown_kept
                if unknown_kept:
                    # §5.6 — a residual data limitation is never silent: these
                    # names are not in the membership table at all, so their
                    # history cannot be judged; they stay in best-effort.
                    logging.warning(
                        "[DataProvider] %d current-universe symbol(s) unknown to "
                        "the membership table kept best-effort for %s (cannot "
                        "judge their history): %s",
                        len(unknown_kept),
                        query_date.strftime("%Y-%m-%d"),
                        ", ".join(sorted(unknown_kept)[:20]),
                    )
                logging.info(
                    "[DataProvider] Point-in-time S&P 500 for %s: %d symbols "
                    "(set-diff on open-interval table: %d members, %d post-dated "
                    "entrants discarded, %d unknown kept).",
                    query_date.strftime("%Y-%m-%d"),
                    len(result),
                    len(members_then),
                    len(discarded),
                    len(unknown_kept),
                )
                return sorted(result)

            # Removals-only table (pre-#1907-Inc-2 state) — additive-only
            # back-compat. §5.6: the known look-ahead limitation is never silent.
            current |= members_then
            logging.warning(
                "[DataProvider] S&P 500 universe for %s is ADDITIVE-ONLY (today's "
                "list + %d historical rows re-added where applicable) — post-%s "
                "index entrants are NOT removed; the universe retains look-ahead "
                "bias and is not point-in-time. Rebuild the membership table via "
                "scripts/rebuild_sp500_membership.py. See #1907.",
                query_date.strftime("%Y-%m-%d"),
                len(df),
                query_date.strftime("%Y-%m-%d"),
            )
            logging.info(
                "[DataProvider] Best-effort historical S&P 500 for %s: %d symbols (additive CSV overlay applied).",  # noqa: E501
                query_date.strftime("%Y-%m-%d"),
                len(current),
            )
            return list(current)
        except Exception as e:
            logging.warning(
                "[DataProvider] Failed to read historical membership CSV: %s — using current list.",  # noqa: E501
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
            # PR E: fail-safe universe observation (source + aggregate count only)  # noqa: E501
            self._mark_universe_safe("alpaca", len(self.symbol_cache))
            return self.symbol_cache

        # 2. Try Wikipedia second
        wiki_symbols = self.get_sp500_symbols()

        # 3. Combine with default list and cache
        self.symbol_cache = list(set(wiki_symbols + DEFAULT_SYMBOLS))
        # PR E: fail-safe universe observation. ``wikipedia`` covers the Wikipedia  # noqa: E501
        # scrape AND its internal static fallback (get_sp500_symbols never raises).  # noqa: E501
        self._mark_universe_safe("wikipedia", len(self.symbol_cache))
        return self.symbol_cache

    @staticmethod
    def _mark_universe_safe(source: str, count: int) -> None:
        """Fail-safe call-site guard around the universe observation (PR E). PURE  # noqa: E501
        OBSERVATION — never raises into universe resolution."""
        try:
            _mark_universe(source, count)
        except Exception:  # noqa: BLE001 — a broken counter must never break resolution
            pass

    def clear_cache(self):
        """Clears the data cache to free memory."""
        self.data_cache.clear()
        logging.info("Historical data cache cleared.")
