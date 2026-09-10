# core/specialist/edgar_cik.py
"""RQ-1 B1 (#1521): ticker->CIK resolver for SEC EDGAR.

Replaces the free-text ``q=%22{ticker}%22`` EDGAR search guess in the three specialist
fetchers (core/stock_specialist.py) with deterministic ticker->CIK resolution against SEC's
documented, free, no-key company_tickers.json. The CIK then scopes the EDGAR query to the
issuer's own filings (``&ciks=``), eliminating the false positives where a 3-letter ticker
matched as a word in an unrelated registrant's filing ("Spy Inc.", "Magnum Opus").

Caching: process-local singleton (no Redis -- works with REDIS_URL empty, CLAUDE.md §5.3),
24h TTL, last-known-good on refresh failure, cold-start floor from a bundled snapshot
(offline-friendly). Unknown ticker -> None (fail-closed -> degraded, never raises).
SEC fair-access: a declared User-Agent is mandatory (empty UA -> HTTP 403; declared -> 200).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# SEC documented endpoint (free, no key). UA is MANDATORY + must be declarative.
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_HEADERS = {
    "User-Agent": "AI-Trading-Bot research@aaagents.de",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}
_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent / "data" / "company_tickers_snapshot.json"
)
_TTL = timedelta(hours=24)
_REFRESH_TIMEOUT = 8.0

# Module-level singleton state.
_map: Optional[Dict[str, str]] = None  # {UPPER_TICKER: "0000320193"}
_loaded_at: Optional[datetime] = (
    None  # wall-clock of the last successful network refresh
)
_refresh_inflight = False  # single-flight guard (asyncio is single-threaded)


def _parse(raw: dict) -> Dict[str, str]:
    """SEC contract: top-level dict keyed by a stringified index; each value is
    ``{"cik_str": <int>, "ticker": <str>, "title": <str>}``. Build
    ``{TICKER: zfill10(cik_str)}`` (tickers are verified unique + uppercase, flat dict safe;
    cik_str is the raw int, the submissions/ciks form needs 10-digit zero-pad)."""
    out: Dict[str, str] = {}
    for entry in raw.values():
        try:
            out[str(entry["ticker"]).upper().strip()] = str(entry["cik_str"]).zfill(10)
        except (KeyError, TypeError):
            continue
    return out


def _load_snapshot() -> Dict[str, str]:
    """Cold-start floor: parse the bundled snapshot. Never raises -- a missing/corrupt file
    logs WARNING and yields {} (every ticker then resolves to None -> degraded, not a crash).
    """
    try:
        return _parse(json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8")))
    except Exception as exc:  # FileNotFound / JSONDecodeError / ...
        logger.warning(
            "CIK snapshot load failed (%s): EDGAR resolution offline until refresh", exc
        )
        return {}


def _ensure_loaded() -> None:
    """Lazily build the in-memory map from the bundled snapshot on first use. Leaves
    ``_loaded_at`` None so the first ``maybe_refresh()`` pulls a fresh map from SEC."""
    global _map
    if _map is None:
        _map = _load_snapshot()


def _is_expired() -> bool:
    return _loaded_at is None or (datetime.now(timezone.utc) - _loaded_at) >= _TTL


def resolve_cik(symbol: str) -> Optional[str]:
    """Map a ticker (case-insensitive) to its 10-digit zero-padded SEC CIK, e.g.
    ``"AAPL" -> "0000320193"``. Returns None for unknown tickers (fail-closed -> degraded,
    never raises). SYNC + pure dict lookup -- safe to call directly from async code."""
    _ensure_loaded()
    return _map.get(symbol.upper().strip()) if _map else None


async def maybe_refresh() -> None:
    """Refresh company_tickers.json if the cache is expired (>=24h). Single-flight; serves
    last-known-good on failure (WARNING, never None/raise); cheap no-op when fresh. Awaited
    once per research() cycle BEFORE the fetcher gather so the three fetchers share one map
    and N specialists do not each burst SEC (<=10 req/s fair-access)."""
    global _map, _loaded_at, _refresh_inflight
    _ensure_loaded()
    # check + set is atomic in a single-threaded event loop (no await between) -> single-flight
    if not _is_expired() or _refresh_inflight:
        return
    _refresh_inflight = True
    try:
        async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
            r = await client.get(_TICKERS_URL, headers=_HEADERS)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            fresh = _parse(r.json())
        if not fresh:
            raise RuntimeError("parsed empty map")
        _map = fresh
        _loaded_at = datetime.now(timezone.utc)
        logger.info("CIK map refreshed: %d tickers", len(fresh))
    except Exception as exc:
        # Serve last-known-good; reset the clock so we wait a full TTL rather than hammering
        # SEC every cycle on a persistent failure (fair-access). §5.6: WARNING, never DEBUG.
        logger.warning("CIK refresh failed, serving last-known-good: %s", exc)
        _loaded_at = datetime.now(timezone.utc)
    finally:
        _refresh_inflight = False
