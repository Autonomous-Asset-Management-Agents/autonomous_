# polygon_data.py
# Polygon.io as supplement: VIX, fundamentals, and bars when Alpaca is unavailable.
# Uses same API key as news (POLYGON_API_KEY). No yfinance.

import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional

POLYGON_BASE = "https://api.polygon.io"

# Polygon indices use I: prefix (e.g. I:VIX for VIX)
POLYGON_VIX_TICKER = "I:VIX"


def _polygon_stock_ticker(symbol: str) -> Optional[str]:
    """Map external symbol to Polygon stock ticker. Strips ^ for indices."""
    s = str(symbol).strip().upper()
    if s.startswith("^"):
        return None  # Indices handled separately
    return s


def _polygon_index_ticker(symbol: str) -> Optional[str]:
    """Map external index symbol to Polygon index ticker (e.g. ^VIX -> I:VIX)."""
    s = str(symbol).strip().upper()
    if s == "^VIX":
        return POLYGON_VIX_TICKER
    if s.startswith("^"):
        # Other indices: I:SPX, I:DJI, etc.
        return f"I:{s[1:]}"
    return None


def fetch_bars(
    api_key: str,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    limit: int = 50000,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars from Polygon (stocks or indices).
    Returns DataFrame with columns open, high, low, close, volume and DatetimeIndex.

    Results are cached in Redis for 3600s (1h) to reduce Polygon API calls
    and outbound Networking costs. Falls back to live request if Redis is unavailable.
    """
    if not api_key:
        return pd.DataFrame()
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    # --- Redis cache check (sync) ---
    _CACHE_KEY = f"polygon:bars:{symbol}:{start_str}:{end_str}"
    _CACHE_TTL = 3600  # 1 hour — daily bars change at most once per day
    try:
        from core.redis_client import RedisClient
        import json as _json

        _r = RedisClient.get_sync_redis()
        if _r is not None:
            _cached = _r.get(_CACHE_KEY)
            if _cached is not None:
                logging.debug("polygon:bars cache HIT for %s", symbol)
                rows = _json.loads(_cached)
                df = pd.DataFrame(rows)
                if not df.empty:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df.set_index("timestamp")
                    if df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                    return df
    except Exception as _e:
        logging.debug("polygon:bars Redis check failed for %s: %s", symbol, _e)

    # Index (e.g. VIX) vs stock endpoint
    index_ticker = _polygon_index_ticker(symbol)
    if index_ticker:
        url = f"{POLYGON_BASE}/v2/aggs/ticker/{index_ticker}/range/1/day/{start_str}/{end_str}"
    else:
        stock_ticker = _polygon_stock_ticker(symbol)
        if not stock_ticker:
            return pd.DataFrame()
        url = f"{POLYGON_BASE}/v2/aggs/ticker/{stock_ticker}/range/1/day/{start_str}/{end_str}"

    params = {"apiKey": api_key, "limit": limit, "sort": "asc", "adjusted": "true"}
    headers = {"User-Agent": "aaagents-oss/1.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.debug("Polygon bars fetch failed for %s: %s", symbol, e)
        return pd.DataFrame()

    results = data.get("results") or []
    if not results:
        return pd.DataFrame()

    rows = []
    for b in results:
        t_ms = b.get("t")
        if t_ms is None:
            continue
        # Indices may not have "v" (volume); use 0
        rows.append(
            {
                "open": float(b.get("o", 0)),
                "high": float(b.get("h", 0)),
                "low": float(b.get("l", 0)),
                "close": float(b.get("c", 0)),
                "volume": float(b.get("v", 0) or 0),
                "timestamp": pd.Timestamp(t_ms, unit="ms").isoformat(),
            }
        )
    df = pd.DataFrame(rows).set_index("timestamp")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # --- Populate cache ---
    try:
        from core.redis_client import RedisClient
        import json as _json

        _r = RedisClient.get_sync_redis()
        if _r is not None:
            df_reset = df.reset_index()
            df_reset["timestamp"] = df_reset["timestamp"].astype(str)
            _r.set(
                _CACHE_KEY,
                _json.dumps(df_reset.to_dict(orient="records")),
                ex=_CACHE_TTL,
            )
            logging.debug("polygon:bars cached %s (%ds TTL)", symbol, _CACHE_TTL)
    except Exception as _e:
        logging.debug("polygon:bars Redis set failed for %s: %s", symbol, _e)

    return df


def fetch_fundamentals(api_key: str, symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """
    Fetch market_cap and trailing P/E from Polygon (ratios or ticker details).
    Returns {symbol: {"marketCap": float, "trailingPE": float}}.

    Results are cached in Redis for 3600s (1h) to reduce Polygon API calls.
    Fundamental data is quarterly — 1h TTL is more than sufficient.
    """
    out: Dict[str, Dict[str, float]] = {}
    if not api_key:
        return out

    # Polygon ratios: GET /stocks/financials/v1/ratios?ticker=AAPL
    for symbol in symbols:
        ticker = _polygon_stock_ticker(symbol)
        if not ticker:
            out[symbol] = {"marketCap": 0.0, "trailingPE": 0.0}
            continue

        # --- Redis cache check (sync) ---
        _CACHE_KEY = f"polygon:fundamentals:{symbol}"
        _CACHE_TTL = 3600  # 1 hour — quarterly data is very stable
        try:
            from core.redis_client import RedisClient
            import json as _json

            _r = RedisClient.get_sync_redis()
            if _r is not None:
                _cached = _r.get(_CACHE_KEY)
                if _cached is not None:
                    logging.debug("polygon:fundamentals cache HIT for %s", symbol)
                    out[symbol] = _json.loads(_cached)
                    continue
        except Exception as _e:
            logging.debug(
                "polygon:fundamentals Redis check failed for %s: %s", symbol, _e
            )

        url = f"{POLYGON_BASE}/stocks/financials/v1/ratios"
        params = {"ticker": ticker, "apiKey": api_key}
        headers = {"User-Agent": "aaagents-oss/1.0"}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            results = data.get("results")
            if not results:
                out[symbol] = {"marketCap": 0.0, "trailingPE": 0.0}
                continue
            row = results[0] if isinstance(results, list) else results
            mc = float(row.get("market_cap", 0) or 0)
            pe = float(row.get("price_to_earnings", 0) or 0)
            entry = {"marketCap": mc, "trailingPE": pe}
            out[symbol] = entry

            # --- Populate cache ---
            try:
                from core.redis_client import RedisClient
                import json as _json

                _r = RedisClient.get_sync_redis()
                if _r is not None:
                    _r.set(_CACHE_KEY, _json.dumps(entry), ex=_CACHE_TTL)
                    logging.debug(
                        "polygon:fundamentals cached %s (%ds TTL)", symbol, _CACHE_TTL
                    )
            except Exception as _e:
                logging.debug(
                    "polygon:fundamentals Redis set failed for %s: %s", symbol, _e
                )

        except Exception as e:
            logging.debug("Polygon fundamentals for %s: %s", symbol, e)
            out[symbol] = {"marketCap": 0.0, "trailingPE": 0.0}
    return out
