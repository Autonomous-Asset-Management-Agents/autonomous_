"""#3349 — Earnings-Proximity-Guard: BUY-only entry gate around earnings reports.

Evidence (Epic #2963, Entry-Time-Churn of the 08/2026 paper-loss week): a clean
sub-slice of the −4% loss-cuts were highest-consensus names bought +1 day after
their 8-K Item 2.02 earnings release and reversed ~−5% within days ("sell-the-news").
This guard blocks NEW BUYs inside a window around a symbol's earnings report.

Design (see docs/3349-earnings-proximity-guard/implementation_plan.md, merged):
- **Pure core** :func:`earnings_block_reason` — no I/O, no clock; the caller passes
  ``now`` from the ENGINE clock (``engine_now``), never wall-clock, so sims are
  deterministic (the sim-clock lesson, portfolio_context.py).
- **POST** window uses the exact 8-K/2.02 date (EDGAR ships it on the report day).
- **PRE** window uses a cadence ESTIMATE of the next report (EDGAR has no forward
  calendar) — default off.
- **Fail modes** (Archon review decision): a data-FETCH failure ⇒ fail-CLOSED
  (``earnings_data_missing`` → block, risk unmeasurable). A successful lookup that
  simply has NO reports (e.g. an ETF) ⇒ fail-OPEN (no veto). A COLD/missing cache
  entry (never fetched) ⇒ fail-OPEN — a non-authoritative side-feed must never turn
  a market-wide cache miss into a market-wide buy halt (PR #3350 review note).

Guard scope: BUY entries only. It never sees SELL / risk exits.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Sentinel reason a fetch failure returns (mapped to blocked:earnings_data_missing).
DATA_MISSING = "earnings_data_missing"


def next_earnings_estimate(report_dates: Sequence[date]) -> Optional[date]:
    """Estimate the next report date = last report + median inter-report gap.

    Needs ≥ 2 known reports; returns ``None`` otherwise (⇒ PRE window inactive).
    Heuristic — EDGAR has no forward calendar; this only feeds the PRE window,
    which ships default-off.
    """
    ds = sorted(set(report_dates))
    if len(ds) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(ds[:-1], ds[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    return ds[-1] + _timedelta_days(int(statistics.median(gaps)))


def _timedelta_days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def earnings_block_reason(
    report_dates: Sequence[date],
    now: date,
    post_days: int,
    pre_days: int,
    next_estimate: Optional[date] = None,
    fetch_failed: bool = False,
) -> Optional[str]:
    """Return a block reason string, or ``None`` for no veto (fail-open).

    - ``fetch_failed`` ⇒ :data:`DATA_MISSING` (fail-closed; risk unmeasurable).
    - POST: a report date ``r`` with ``0 <= (now - r).days <= post_days`` ⇒ block.
    - PRE: ``next_estimate`` with ``0 <= (next_estimate - now).days <= pre_days`` ⇒ block.
    - otherwise ``None`` (incl. a successful lookup with no reports — e.g. ETF).
    """
    if fetch_failed:
        return DATA_MISSING
    if post_days > 0 and report_dates:
        recent = [r for r in report_dates if 0 <= (now - r).days <= post_days]
        if recent:
            r = max(recent)  # closest prior report
            return f"earnings +{(now - r).days}d (report {r.isoformat()})"
    if pre_days > 0 and next_estimate is not None:
        d = (next_estimate - now).days
        if 0 <= d <= pre_days:
            return f"earnings -{d}d (est {next_estimate.isoformat()})"
    return None


# --------------------------------------------------------------------------- #
# Cache read (per-symbol earnings dates, populated daily by refresh_earnings_cache
# in core/report/sec_fundamentals.py). Kept tiny + injectable for the seam.
# --------------------------------------------------------------------------- #

# Cache layout (one JSON file): {"AAPL": {"dates": ["2026-07-28", ...], "error": false}}
_CACHE_FILENAME = "earnings_dates.json"
# Review #3480 FINDING-01: one lock for every read and the swap of the cache file. On
# Windows ``os.replace`` fails with PermissionError (WinError 32) while the target is
# open — and the order path (reader) and the producer thread (writer) live in THIS
# process. Reads are a few milliseconds of JSON, so the order path never waits long;
# the network part of a refresh runs OUTSIDE the lock.
_CACHE_LOCK = threading.Lock()
# ADR-EG-04: up to 5 swap attempts, 50 ms apart.
# Rationale: the lock covers this process only; a virus scanner or backup tool can
# still hold the file for a moment. 250 ms total is far below one refresh interval.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05


def _default_cache_path() -> Optional[str]:
    try:
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        return os.path.join(DEFAULT_CACHE_DIR, _CACHE_FILENAME)
    except Exception:  # noqa: BLE001 — no cache path ⇒ treat as cold (fail-open)
        return None


def load_cached_report_dates(
    symbol: str, cache_path_str: Optional[str] = None
) -> Tuple[Optional[List[date]], bool]:
    """Read (dates, fetch_failed) for ``symbol`` from the daily cache.

    Returns ``(None, False)`` for a MISSING/cold entry (⇒ fail-open) and
    ``(None, True)`` when the last refresh recorded a fetch error (⇒ fail-closed).
    """
    p = cache_path_str or _default_cache_path()
    if not p or not os.path.exists(p):
        return None, False
    try:
        with _CACHE_LOCK:  # never hold the file open while the producer swaps it
            with open(p, encoding="utf-8") as fh:
                blob = json.load(fh)
        entry = (blob or {}).get(symbol)
        if not isinstance(entry, dict):
            return None, False
        if entry.get("error"):
            return None, True
        dates = [date.fromisoformat(d) for d in (entry.get("dates") or [])]
        return (dates or None), False
    except Exception:  # noqa: BLE001 — unreadable cache ⇒ cold (fail-open, WARNING)
        logger.warning(
            "EarningsGuard: cache unreadable at %s — fail-open.", p, exc_info=True
        )
        return None, False


def earnings_guard_block(
    symbol: str,
    now: date,
    post_days: int,
    pre_days: int,
    report_provider: Optional[
        Callable[[str], Tuple[Optional[List[date]], bool]]
    ] = None,
) -> Optional[Tuple[str, str]]:
    """Tie cache + pure rule together. Returns ``(outcome_tag, reason)`` or ``None``.

    ``report_provider(symbol) -> (dates|None, fetch_failed)`` is injectable (tests /
    the seam); defaults to the daily cache reader.
    """
    provider = report_provider or (lambda s: load_cached_report_dates(s))
    dates, fetch_failed = provider(symbol)
    if dates is None and not fetch_failed:
        return None  # cold/missing cache ⇒ fail-open (never a market-wide halt)
    est = next_earnings_estimate(dates or []) if pre_days > 0 else None
    reason = earnings_block_reason(
        dates or [], now, post_days, pre_days, est, fetch_failed
    )
    if reason is None:
        return None
    tag = (
        "blocked:earnings_data_missing"
        if reason == DATA_MISSING
        else "blocked:earnings"
    )
    return tag, reason


# ---------------------------------------------------------------------------
# Daily cache producer (#3349 completion). The guard above only ever READS the
# cache; without a producer it stays cold and the guard is inert (fail-open) no
# matter how it is configured. This is that producer.
# ---------------------------------------------------------------------------
_META_KEY = "_meta"  # not a ticker: load_cached_report_dates never looks it up
# ADR-EG-01: pause between EDGAR calls = 0.15 s (~6.7 req/s).
# Basis: SEC fair-access limit of 10 req/s. Rationale: headroom for the other EDGAR
# users in the same process (fundamentals feed, specialist), which share the limit.
_REQUEST_PAUSE_SEC = 0.15
# ADR-EG-02: a symbol whose fetch FAILED is retried after 30 minutes.
# Rationale: a failed fetch blocks that symbol's BUYs (fail-closed, plan decision);
# a transient EDGAR hiccup must not hold that veto for the rest of the day.
_ERROR_RETRY_MINUTES = 30
# ADR-EG-03: 5 consecutive fetch failures abort the refresh run.
# Rationale: that pattern is an EDGAR/network outage, not a per-symbol problem.
# The symbols not yet fetched stay COLD (fail-open) instead of all being stamped
# "error" — an outage of a side-feed must not become a market-wide buy halt
# (PR #3350 review note). The ones that did fail keep their veto per the plan.
_MAX_CONSECUTIVE_FAILURES = 5


def _read_blob(cache_file: str) -> Dict[str, Any]:
    if not os.path.exists(cache_file):
        return {}
    try:
        with _CACHE_LOCK:
            with open(cache_file, encoding="utf-8") as fh:
                blob = json.load(fh)
        return blob if isinstance(blob, dict) else {}
    except Exception:  # noqa: BLE001 — unreadable ⇒ rebuild from scratch
        logger.warning(
            "EarningsGuard: cache unreadable at %s — rebuilding.",
            cache_file,
            exc_info=True,
        )
        return {}


def _write_blob(cache_file: str, blob: Dict[str, Any]) -> bool:
    """Write-then-swap. The JSON goes to a UNIQUE temp file in the same directory and is
    swapped in under ``_CACHE_LOCK`` (no in-process reader has the target open then);
    a PermissionError from another PROCESS is retried. If the swap never succeeds the
    old cache stays, the temp file is removed and the next cycle tries again — a cache
    write must never take the producer down."""
    tmp = None
    try:
        directory = os.path.dirname(cache_file) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".earnings_dates.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
            fh.flush()
            os.fsync(fh.fileno())
        last_exc: Optional[BaseException] = None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                with _CACHE_LOCK:
                    os.replace(tmp, cache_file)
                return True
            except PermissionError as exc:
                last_exc = exc
                if attempt + 1 < _REPLACE_ATTEMPTS:
                    time.sleep(_REPLACE_BACKOFF_SEC)
        raise last_exc if last_exc else OSError("cache swap failed")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EarningsGuard: cache write failed (%s) — previous cache kept.",
            exc,
            exc_info=True,
        )
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                logger.warning(
                    "EarningsGuard: temp file %s not removed.", tmp, exc_info=True
                )
        return False


def ensure_fresh_earnings_cache(
    symbols: Iterable[str],
    now: datetime,
    cache_file: Optional[str] = None,
    resolve_cik: Optional[Callable[[str], Optional[str]]] = None,
    fetch: Optional[Callable[[Any], Optional[dict]]] = None,
    pause_sec: float = _REQUEST_PAUSE_SEC,
) -> int:
    """Bring today's cache up to date for ``symbols``; returns the number of fetches.

    - A new day starts an empty cache (yesterday's dates must not linger).
    - Within the day only symbols WITHOUT an entry are fetched, so repeated calls
      are cheap no-ops and a symbol that enters the universe mid-day is covered.
    - Failed entries are retried after ``_ERROR_RETRY_MINUTES``.
    - No CIK (ETF, foreign listing) ⇒ "no reports" ⇒ no veto, no network call.
    Blocking (network + sleep): call it from a worker thread, never the event loop.
    """
    path = cache_file or _default_cache_path()
    if not path:
        return 0
    if resolve_cik is None:
        from core.specialist.edgar_cik import resolve_cik as _resolve

        resolve_cik = _resolve
    from core.report.sec_fundamentals import earnings_report_dates

    today = now.date().isoformat()
    blob = _read_blob(path)
    meta = blob.get(_META_KEY) if isinstance(blob.get(_META_KEY), dict) else {}
    if meta.get("computed_on") != today:
        blob, meta = {}, {"computed_on": today}

    retry_errors = True
    last_retry = meta.get("errors_retried_at")
    if last_retry:
        try:
            age = now - datetime.fromisoformat(last_retry)
            retry_errors = age >= timedelta(minutes=_ERROR_RETRY_MINUTES)
        except (TypeError, ValueError):
            retry_errors = True

    wanted = sorted({str(s).upper().strip() for s in symbols if s})
    todo = [
        s for s in wanted if s not in blob or (retry_errors and blob[s].get("error"))
    ]
    if not todo:
        return 0
    if any(blob.get(s, {}).get("error") for s in todo):
        meta["errors_retried_at"] = now.isoformat()

    fetched = 0
    consecutive_failures = 0
    for sym in todo:
        cik = resolve_cik(sym)
        if not cik:
            blob[sym] = {"dates": [], "error": False}
            continue
        if fetched and pause_sec > 0:
            time.sleep(pause_sec)
        dates, failed = earnings_report_dates(cik, fetch=fetch)
        fetched += 1
        if failed:
            blob[sym] = {"error": True}
            meta.setdefault("errors_retried_at", now.isoformat())
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "EarningsGuard: %d consecutive EDGAR failures — refresh aborted; "
                    "symbols not fetched yet stay cold (BUYs proceed for them).",
                    consecutive_failures,
                )
                break
        else:
            blob[sym] = {"dates": dates or [], "error": False}
            consecutive_failures = 0

    blob[_META_KEY] = meta
    _write_blob(path, blob)
    return fetched
