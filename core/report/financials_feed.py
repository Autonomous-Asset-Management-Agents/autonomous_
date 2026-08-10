# core/report/financials_feed.py
# RPT-2 (#2001, Epic #1998) — Polygon vX fundamentals feed for the auditable
# report pipeline.
"""Point-in-time fundamentals feed built on Polygon's ``vX/reference/financials``.

This module delivers the *fundamentals* substance that RPT-1's :class:`FactSet`
leaves optional (``fundamentals`` field). It has three parts:

* :func:`derive_fundamentals` — a **pure, deterministic** transform from raw
  Polygon result rows to a flat fundamentals dict (revenue, margins, EPS, YoY,
  balance sheet, and — when a close is supplied — P/E and P/S). Point-in-time by
  construction: filings dated after ``as_of`` are dropped, synthetic *trailing
  twelve month* (``ttm``) rows are dropped, and the newest in-window annual
  period drives the level while the next one drives year-over-year.
* :func:`fetch_financials_vX` / :func:`fetch_raw_financials` — the **only**
  network path. All I/O is isolated here (``requests`` is imported lazily) so the
  pure core stays edition-agnostic and import-light. Every failure mode
  (no key, timeout, HTTP error, rate-limit exhaustion) degrades to ``None`` — an
  honest gap, never a fabricated number. The free tier is 5 requests/min, so 429
  responses are retried with exponential backoff (honouring ``Retry-After``).
* :class:`CachedFundamentalsReader` — the **dependency-injection adapter** that
  serves RPT-1. It reads the nightly universe cache
  (``scripts/prefetch_financials_universe.py``) and implements the
  ``FactSetReader.get_fundamentals(symbol, as_of)`` seam, so a composed reader
  flows real fundamentals into ``build_fact_set`` → ``FactSet.fundamentals``.

BORA / design contract:

* **Pure core + config.** No Redis, no SQLite, no ``DEPLOYMENT_MODE``, no edition
  special-casing. The API key is read from the injected ``api_key`` argument or,
  failing that, the ``POLYGON_API_KEY`` environment variable — nothing else.
* **Deterministic.** Given the same rows (and close) :func:`derive_fundamentals`
  returns a byte-identical dict; it uses no clock, no randomness, no ``pandas``.
* **Report-only and DORMANT.** Nothing here is read by the decision path;
  ``SPECIALIST_ALPHA_WEIGHT`` stays 0. This module deliberately does **not** touch
  ``core/polygon_data.py`` (a live market-data module) — it is a disjoint,
  isolated report feed.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

POLYGON_FINANCIALS_URL = "https://api.polygon.io/vX/reference/financials"
_USER_AGENT = "aaagents-oss/1.0 (report-financials-feed)"

# The nightly universe prefetch writes one JSON file per symbol here (relative to
# the repo root); the reader loads the same layout.
DEFAULT_CACHE_DIR = os.path.join("data", "financials_cache")


# ---------------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------------
def _as_date(value: Any) -> date:
    """Normalise a ``date`` / ``datetime`` / ISO string to a calendar ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value[:10]).date()
    raise TypeError(f"Unsupported as_of/date type: {type(value)!r}")


def _ratio(numerator: Any, denominator: Any) -> Optional[float]:
    """``numerator / denominator``, or ``None`` on missing / zero denominator."""
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _growth(current: Any, prior: Any) -> Optional[float]:
    """Year-over-year growth ``current/prior - 1``, or ``None`` when undefined."""
    if current is None or prior in (None, 0):
        return None
    return float(current) / float(prior) - 1.0


def _ps(close: Any, shares: Any, revenue: Any) -> Optional[float]:
    """Price/Sales = market cap / revenue = ``close * shares / revenue``."""
    if close is None or shares is None or revenue in (None, 0):
        return None
    return float(close) * float(shares) / float(revenue)


def _stmt_value(financials: Mapping[str, Any], statement: str, key: str) -> Any:
    """Read ``financials[statement][key].value`` defensively (``None`` if absent)."""
    stmt = financials.get(statement) if isinstance(financials, Mapping) else None
    if not isinstance(stmt, Mapping):
        return None
    node = stmt.get(key)
    if isinstance(node, Mapping):
        return node.get("value")
    return node


def _pit_select(
    results: Sequence[Mapping[str, Any]], as_of: date
) -> List[Mapping[str, Any]]:
    """Return in-window rows sorted newest-filing-first.

    Drops rows filed after ``as_of`` (point-in-time), rows with an unparseable /
    missing ``filing_date``, and synthetic ``ttm`` rows (TTM-drop: only real,
    as-reported periods feed the report).
    """
    kept: List[tuple] = []
    for row in results or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("timeframe", "")).lower() == "ttm":
            continue
        raw_fd = row.get("filing_date")
        if not raw_fd:
            continue
        try:
            fd = _as_date(raw_fd)
        except (TypeError, ValueError):
            continue
        if fd <= as_of:
            kept.append((fd, row))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in kept]


# ---------------------------------------------------------------------------
# Pure derivation.
# ---------------------------------------------------------------------------
def derive_fundamentals(
    results: Sequence[Mapping[str, Any]],
    as_of: Any,
    *,
    close: Optional[float] = None,
) -> Optional[dict]:
    """Deterministically derive a flat, PIT fundamentals dict from raw vX rows.

    Parameters
    ----------
    results:
        Raw ``results`` list from the Polygon ``vX/reference/financials`` payload
        (any order). Typically several annual periods so YoY has a prior.
    as_of:
        Point-in-time cutoff (``date`` / ``datetime`` / ISO string). Filings dated
        after this calendar date are excluded.
    close:
        Optional as-of close price. Required for ``pe`` / ``ps``; when absent both
        are ``None`` (an honest gap, never fabricated).

    Returns
    -------
    dict or None
        ``None`` when no filing is in-window; otherwise a flat dict of facts.
    """
    as_of_d = _as_date(as_of)
    pit = _pit_select(results, as_of_d)
    if not pit:
        return None

    current = pit[0]
    prior = pit[1] if len(pit) > 1 else {}
    finc = current.get("financials") or {}
    pfinc = prior.get("financials") or {}

    revenue = _stmt_value(finc, "income_statement", "revenues")
    net_income = _stmt_value(finc, "income_statement", "net_income_loss")
    gross_profit = _stmt_value(finc, "income_statement", "gross_profit")
    operating_income = _stmt_value(finc, "income_statement", "operating_income_loss")
    eps_diluted = _stmt_value(finc, "income_statement", "diluted_earnings_per_share")
    eps_basic = _stmt_value(finc, "income_statement", "basic_earnings_per_share")
    diluted_shares = _stmt_value(finc, "income_statement", "diluted_average_shares")
    basic_shares = _stmt_value(finc, "income_statement", "basic_average_shares")

    total_assets = _stmt_value(finc, "balance_sheet", "assets")
    total_liabilities = _stmt_value(finc, "balance_sheet", "liabilities")
    total_equity = _stmt_value(finc, "balance_sheet", "equity")
    current_assets = _stmt_value(finc, "balance_sheet", "current_assets")
    current_liabilities = _stmt_value(finc, "balance_sheet", "current_liabilities")

    operating_cash_flow = _stmt_value(
        finc, "cash_flow_statement", "net_cash_flow_from_operating_activities"
    )

    # prior-period levels for YoY
    p_revenue = _stmt_value(pfinc, "income_statement", "revenues")
    p_net_income = _stmt_value(pfinc, "income_statement", "net_income_loss")
    p_eps = _stmt_value(pfinc, "income_statement", "diluted_earnings_per_share")
    if p_eps is None:
        p_eps = _stmt_value(pfinc, "income_statement", "basic_earnings_per_share")

    eps = eps_diluted if eps_diluted is not None else eps_basic
    shares = diluted_shares if diluted_shares is not None else basic_shares
    pe = _ratio(close, eps) if (close is not None and eps not in (None, 0)) else None

    return {
        # provenance / identity
        "fiscal_year": current.get("fiscal_year"),
        "fiscal_period": current.get("fiscal_period"),
        "timeframe": current.get("timeframe"),
        "filing_date": current.get("filing_date"),
        "start_date": current.get("start_date"),
        "end_date": current.get("end_date"),
        "company_name": current.get("company_name"),
        "source_filing_url": current.get("source_filing_url"),
        "prior_fiscal_year": prior.get("fiscal_year"),
        "prior_filing_date": prior.get("filing_date"),
        # income statement
        "revenue": revenue,
        "net_income": net_income,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "eps_diluted": eps_diluted,
        "eps_basic": eps_basic,
        "diluted_shares": diluted_shares,
        "basic_shares": basic_shares,
        # margins
        "gross_margin": _ratio(gross_profit, revenue),
        "operating_margin": _ratio(operating_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        # year-over-year
        "revenue_yoy": _growth(revenue, p_revenue),
        "net_income_yoy": _growth(net_income, p_net_income),
        "eps_yoy": _growth(eps, p_eps),
        # balance sheet
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "current_ratio": _ratio(current_assets, current_liabilities),
        # cash flow
        "operating_cash_flow": operating_cash_flow,
        # valuation (price-dependent — None without a close)
        "pe": pe,
        "ps": _ps(close, shares, revenue),
    }


# ---------------------------------------------------------------------------
# Network path (the ONLY I/O in this module).
# ---------------------------------------------------------------------------
def _parse_retry_after(response: Any) -> Optional[float]:
    """Best-effort parse of a ``Retry-After`` header (seconds)."""
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def fetch_raw_financials(
    symbol: str,
    as_of: Any,
    *,
    api_key: Optional[str] = None,
    timeframe: str = "annual",
    limit: int = 8,
    timeout: float = 15.0,
    session: Any = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    sleep: Any = None,
) -> Optional[List[Mapping[str, Any]]]:
    """Fetch the raw ``results`` list from Polygon vX, or ``None`` on any failure.

    Network is isolated here. ``session`` (anything exposing ``.get`` like a
    ``requests.Session``) and ``sleep`` are injectable so the whole path is
    testable without touching the network or the wall clock. 429 responses are
    retried up to ``max_retries`` times with exponential backoff (honouring
    ``Retry-After`` when present) — the free tier caps at 5 requests/min.
    """
    key = api_key or os.environ.get("POLYGON_API_KEY", "")
    if not key:
        logger.warning(
            "financials_feed: no POLYGON_API_KEY — fundamentals unavailable for %s",
            symbol,
        )
        return None

    sleep_fn = sleep or time.sleep
    if session is not None:
        getter = session.get
    else:  # lazy import keeps the pure core free of the network dependency
        import requests

        getter = requests.get

    params = {
        "ticker": str(symbol).strip().upper(),
        "filing_date.lte": _as_date(as_of).isoformat(),
        "timeframe": timeframe,
        "order": "desc",
        "sort": "filing_date",
        "limit": limit,
        "apiKey": key,
    }
    headers = {"User-Agent": _USER_AGENT}

    attempt = 0
    while True:
        try:
            response = getter(
                POLYGON_FINANCIALS_URL, params=params, timeout=timeout, headers=headers
            )
        except Exception as exc:  # network error / timeout — honest gap
            logger.warning(
                "financials_feed: request failed for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        if getattr(response, "status_code", None) == 429:
            if attempt >= max_retries:
                logger.warning(
                    "financials_feed: rate-limited (429) for %s after %d retries",
                    symbol,
                    attempt,
                )
                return None
            wait = _parse_retry_after(response)
            if wait is None:
                wait = backoff_base * (2**attempt)
            logger.warning(
                "financials_feed: 429 for %s — backoff %.2fs (attempt %d/%d)",
                symbol,
                wait,
                attempt + 1,
                max_retries,
            )
            sleep_fn(wait)
            attempt += 1
            continue

        try:
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "financials_feed: HTTP error for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        try:
            body = response.json()
        except Exception as exc:
            logger.warning(
                "financials_feed: bad JSON for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        return list(body.get("results") or [])


def fetch_financials_vX(
    symbol: str,
    as_of: Any,
    *,
    api_key: Optional[str] = None,
    close: Optional[float] = None,
    timeframe: str = "annual",
    limit: int = 8,
    timeout: float = 15.0,
    session: Any = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    sleep: Any = None,
) -> Optional[dict]:
    """Fetch + derive PIT fundamentals for ``symbol`` as of ``as_of``.

    Thin composition of :func:`fetch_raw_financials` and
    :func:`derive_fundamentals`. Returns ``None`` on any network failure or when
    no filing is in-window.
    """
    results = fetch_raw_financials(
        symbol,
        as_of,
        api_key=api_key,
        timeframe=timeframe,
        limit=limit,
        timeout=timeout,
        session=session,
        max_retries=max_retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    if results is None:
        return None
    return derive_fundamentals(results, as_of, close=close)


# ---------------------------------------------------------------------------
# Nightly-cache layout + reader adapter (RPT-1 FactSetReader DI seam).
# ---------------------------------------------------------------------------
def cache_path(cache_dir: str, symbol: str) -> str:
    """Return the per-symbol cache file path (creates ``cache_dir`` if needed)."""
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{str(symbol).strip().upper()}.json")


@dataclass(frozen=True)
class CachedFundamentalsReader:
    """Reads the nightly universe cache and serves RPT-1's fundamentals seam.

    Implements ``get_fundamentals(symbol, as_of)`` from RPT-1's
    :class:`~core.report.fact_set.FactSetReader` Protocol. It never hits the
    network — the nightly ``prefetch_financials_universe.py`` populates the cache,
    and PIT filtering is applied at read time so a single cache file serves any
    historical ``as_of``.

    ``close_lookup`` (``{symbol: close}``) is optional; when provided the reader
    enriches the result with P/E and P/S, otherwise those stay ``None``.
    """

    cache_dir: str = DEFAULT_CACHE_DIR
    close_lookup: Optional[Mapping[str, float]] = None

    def get_fundamentals(self, symbol: str, as_of: Any) -> Optional[Mapping[str, Any]]:
        path = os.path.join(self.cache_dir, f"{str(symbol).strip().upper()}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("financials_feed: unreadable cache for %s: %r", symbol, exc)
            return None
        results = payload.get("results") or []
        close = None
        if self.close_lookup:
            up = str(symbol).strip().upper()
            close = self.close_lookup.get(up)
            if close is None:
                close = self.close_lookup.get(symbol)
        return derive_fundamentals(results, as_of, close=close)


# ---------------------------------------------------------------------------
# The report's presentation contract (ADR-RPT2-02).
# ---------------------------------------------------------------------------
def to_report_fundamentals(
    derived: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Adapt :func:`derive_fundamentals` output to the schema §6 actually reads.

    ⚠ WHY THIS EXISTS — the two sides never spoke the same language, and nobody noticed:
    ``derive_fundamentals`` emits ``revenue`` / ``gross_margin`` / ``eps_yoy``;
    ``render._section_fundamentals`` reads ``revenue_bn`` / ``gross_margin_pct`` /
    ``eps_yoy_pct``. Of the 18 keys §6 reads, exactly FOUR existed
    (``eps_diluted``, ``filing_date``, ``pe``, ``ps``). The other fourteen returned
    ``None`` forever, so §6 rendered "Umsatz | n/a Mrd USD | +n/a%" on a PERFECTLY
    derived filing — and populating the PIT cache for 500 symbols would have produced
    500 reports of n/a.

    It survived because every test on both sides is written against a hand-built dict in
    the shape that side wants; nothing ever ran derive -> render. And the auditor is
    blind to it by construction: it proves that numbers PRESENT are sourced, so a
    missing number is not a claim. Zero flags meant "nothing to check", not "correct".

    The producer owns its presentation contract, so the adapter lives here — one place,
    next to the derivation whose units it knows.

    UNITS, and they are load-bearing:
    * the feed emits ratios as FRACTIONS (``gross_margin`` = 0.7107). §6 prints percent.
      Getting this backwards renders a 71 % margin as 0.71 % — real, sourced, and wrong
      by 100x. (The same trap shipped in FundamentalsAgent and had to be fixed once
      already.)
    * absolutes are dollars; §6 prints billions.
    * ``market_cap_bn`` is a MISNOMER we honour rather than silently redefine: the §6
      line reads "Marktkapitalisierung ~{market_cap_bn} Bio USD", so the value must be
      in TRILLIONS to match its own label (NVDA ~4.87, never ~4870). Renaming the key is
      a separate, deliberate change to the renderer.

    ``None`` in -> ``None`` out: a cold cache stays an honest gap and must never become
    an empty dict, which §6 would render as a table of n/a while citing a filing behind
    it.
    """
    if not derived:
        return None

    def _pct(v: Optional[float]) -> Optional[float]:
        return None if v is None else float(v) * 100.0

    def _bn(v: Optional[float]) -> Optional[float]:
        return None if v is None else float(v) / 1e9

    d = dict(derived)
    equity = d.get("total_equity")
    liabilities = d.get("total_liabilities")
    debt_to_equity = (
        float(liabilities) / float(equity)
        if liabilities is not None and equity
        else None
    )

    # Market cap = price x diluted shares. Both are needed; without a close the feed
    # cannot form it, and an invented one is exactly the fabrication we forbid.
    market_cap_tn = None
    pe, eps = d.get("pe"), d.get("eps_diluted")
    shares = d.get("diluted_shares") or d.get("basic_shares")
    if pe is not None and eps is not None and shares:
        market_cap_tn = (float(pe) * float(eps) * float(shares)) / 1e12

    fy = d.get("fiscal_year")
    period = d.get("fiscal_period") or ""
    fiscal_label = (
        f"FY{fy} {'10-K' if period == 'FY' else period}".strip() if fy else None
    )

    return {
        # identity / provenance
        "fiscal_label": fiscal_label,
        "filing_date": d.get("filing_date"),
        # income statement — dollars -> billions, fractions -> percent
        "revenue_bn": _bn(d.get("revenue")),
        "revenue_yoy_pct": _pct(d.get("revenue_yoy")),
        "gross_margin_pct": _pct(d.get("gross_margin")),
        "op_margin_pct": _pct(d.get("operating_margin")),
        "net_income_bn": _bn(d.get("net_income")),
        "net_income_yoy_pct": _pct(d.get("net_income_yoy")),
        "net_margin_pct": _pct(d.get("net_margin")),
        "eps_diluted": d.get("eps_diluted"),
        "eps_yoy_pct": _pct(d.get("eps_yoy")),
        "op_cash_flow_bn": _bn(d.get("operating_cash_flow")),
        # balance sheet
        "equity_bn": _bn(equity),
        "liabilities_bn": _bn(liabilities),
        "debt_to_equity": debt_to_equity,
        # valuation (price-dependent — None without a close, never guessed)
        "market_cap_bn": market_cap_tn,  # ⚠ TRILLIONS, to match §6's "Bio" label
        "pe": pe,
        "ps": d.get("ps"),
    }


def trigger_in_band_financials_prefetch(
    symbols: Sequence[str],
    *,
    api_key: Optional[str] = None,
    cache_dir: str = DEFAULT_CACHE_DIR,
    throttle_seconds: float = 12.0,
    skip_fresh_days: int = 25,
) -> None:
    """Non-blocking background trigger that prefetches fundamentals into data/financials_cache.

    Guarded: catches all network errors, runs in a background daemon thread, and respects
    Polygon rate limits (12s throttle, skip_fresh_days=25).
    """
    import threading
    from datetime import date

    sym_list = list(symbols)
    if not sym_list:
        return

    def _worker() -> None:
        try:
            from scripts.prefetch_financials_universe import prefetch_universe

            prefetch_universe(
                cache_dir=cache_dir,
                symbols=sym_list,
                as_of=date.today(),
                api_key=api_key,
                limit=1,
                timeframe="annual",
                throttle_seconds=throttle_seconds,
                skip_fresh_days=skip_fresh_days,
                force=False,
                dry_run=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "financials_feed: in-band prefetch background task exception: %r", exc
            )

    thread = threading.Thread(
        target=_worker, daemon=True, name="inband_financials_prefetch"
    )
    thread.start()
