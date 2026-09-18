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
from datetime import date
from typing import Callable, List, Optional, Sequence, Tuple

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
        logger.warning("EarningsGuard: cache unreadable at %s — fail-open.", p)
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
