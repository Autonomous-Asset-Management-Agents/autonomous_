"""Sim-Day corpus builder (#2548) — the sustainable data pipeline.

Populates a **pinned, offline** corpus of ``{SYMBOL}.parquet`` daily bars once, so every
``python -m core.sim`` run is offline + reproducible + real (pro-tool pattern: fetch once, run
offline forever). Two sources:

  * ``build_corpus_from_cache`` — import from an existing ``market_data_cache`` (real bars the
    desktop already downloaded) → no creds needed.
  * ``fetch_corpus_from_alpaca`` — one-time download via the Alpaca data client (free IEX feed)
    when creds are present.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# A focused, liquid default universe for a fast first run (override with --symbols).
DEFAULT_UNIVERSE: List[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "JNJ",
    "WMT",
    "PG",
    "HD",
    "MA",
    "AVGO",
    "COST",
    "MRK",
    "ABBV",
    "PEP",
    "KO",
    "ADBE",
    "CRM",
    "NFLX",
    "AMD",
    "INTC",
    "CSCO",
    "QCOM",
    "TXN",
]


def _default_desktop_cache() -> Optional[str]:
    """The installed desktop's market_data_cache (real bars), if present."""
    p = os.path.expandvars(
        r"%LOCALAPPDATA%\autonomous\app\ai_trading_bot\market_data_cache"
    )
    return p if os.path.isdir(p) else None


def build_corpus_from_cache(
    out_dir: str,
    cache_dir: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    up_to_date: Optional[str] = None,
) -> int:
    """Build a pinned corpus in ``out_dir`` from an existing daily-bar cache.

    ``up_to_date`` (YYYY-MM-DD) truncates every frame to bars ``<= that date`` so the corpus is
    pinned to the sim day + warm-up (reproducible; no future bars). Returns the symbol count.
    """
    cache_dir = cache_dir or _default_desktop_cache()
    if not cache_dir or not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"No bar cache found (cache_dir={cache_dir!r}). Pass --cache-dir or set up the desktop."
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    syms = [s.upper() for s in (symbols or DEFAULT_UNIVERSE)]
    cutoff = pd.Timestamp(up_to_date) if up_to_date else None

    written = 0
    for s in syms:
        src = Path(cache_dir) / f"{s}.parquet"
        if not src.exists():
            logger.warning("corpus: %s not in cache — skipped.", s)
            continue
        if cutoff is None:
            shutil.copyfile(src, out / f"{s}.parquet")
        else:
            df = pd.read_parquet(src)
            idx = df.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
                df.index = idx
            # keep bars on/before the cutoff DATE (compare on date, not intraday stamp)
            df = df[pd.DatetimeIndex(df.index).normalize() <= cutoff.normalize()]
            if df.empty:
                logger.warning("corpus: %s has no bars <= %s — skipped.", s, up_to_date)
                continue
            df.to_parquet(out / f"{s}.parquet")
        written += 1
    logger.info(
        "corpus: wrote %d symbols to %s (up_to=%s).", written, out_dir, up_to_date
    )
    return written


def fetch_corpus_from_alpaca(
    out_dir: str,
    symbols: List[str],
    start: str,
    end: str,
) -> int:  # pragma: no cover - needs live creds
    """One-time daily-bar download via the Alpaca data client (free IEX). Requires creds."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError(
            "Alpaca data creds not set (APCA_API_KEY_ID / APCA_API_SECRET_KEY)."
        )
    client = StockHistoricalDataClient(key, sec)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for s in symbols:
        req = StockBarsRequest(
            symbol_or_symbols=s, timeframe=TimeFrame.Day, start=start, end=end
        )
        df = client.get_stock_bars(req).df
        if df is None or df.empty:
            continue
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(s)
        df.to_parquet(out / f"{s}.parquet")
        written += 1
    return written
