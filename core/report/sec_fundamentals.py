"""#3145 — SEC EDGAR fundamentals adapter (free, no key, public-domain, bundleable).

Turns a SEC ``companyfacts`` payload (``data.sec.gov/api/xbrl/companyfacts/CIK…``)
into the SAME Polygon-vX ``results`` rows that
:func:`core.report.financials_feed.derive_fundamentals` already consumes — so the
FundamentalsAgent/ValuationAgent read real numbers with NO agent or reader change.
The cache file layout ``{"results": [...rows...]}`` is byte-compatible with the
existing nightly cache, and PIT filtering still happens at read time.

Design (#3145): SEC is the only source that ships **by default** in the desktop
(no API key, public-domain → redistributable, offline via a bundled snapshot). All
network I/O is isolated + injectable here; the pure normalizer is deterministic.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from core.report.financials_feed import cache_path

logger = logging.getLogger(__name__)

# SEC "fair access" requires a descriptive User-Agent with a contact. Project
# contact only — never a personal address.
SEC_USER_AGENT = "AAAgents Desktop fundamentals adapter (contact: dev@aaagents.de)"

# Annual as-reported forms. 20-F/40-F cover foreign filers (e.g. SPOT/CB) that a
# 10-K-only filter would drop — a measured coverage gap in the #3145 scan.
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# Canonical Polygon-vX (statement, key) -> ordered SEC us-gaap tag synonyms.
# First tag WITH annual data wins. The synonym lists come straight from the
# 515-symbol coverage scan's non-coverage patterns (energy/financial/foreign tag
# variants). Editorial start set — extend as new gaps surface (annual review).
SEC_TAG_MAP: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("income_statement", "revenues"): (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenuesNetOfInterestExpense",
        "SalesRevenueNet",
        "TotalRevenuesAndOtherIncome",
    ),
    ("income_statement", "net_income_loss"): (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    ("income_statement", "gross_profit"): ("GrossProfit",),
    ("income_statement", "operating_income_loss"): ("OperatingIncomeLoss",),
    ("income_statement", "diluted_earnings_per_share"): (
        "EarningsPerShareDiluted",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
    ),
    ("income_statement", "basic_earnings_per_share"): (
        "EarningsPerShareBasic",
        "IncomeLossFromContinuingOperationsPerBasicShare",
    ),
    ("income_statement", "diluted_average_shares"): (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    ("income_statement", "basic_average_shares"): (
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfSharesOutstanding",
    ),
    ("balance_sheet", "assets"): ("Assets",),
    ("balance_sheet", "liabilities"): ("Liabilities",),
    ("balance_sheet", "equity"): (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    ("balance_sheet", "current_assets"): ("AssetsCurrent",),
    ("balance_sheet", "current_liabilities"): ("LiabilitiesCurrent",),
    ("cash_flow_statement", "net_cash_flow_from_operating_activities"): (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
}


def _annual_points(units: Optional[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """All annual (fp=FY, 10-K/20-F) data points across a concept's unit buckets."""
    out: List[Mapping[str, Any]] = []
    for pts in (units or {}).values():
        for p in pts or []:
            if not isinstance(p, Mapping):
                continue
            if (
                p.get("fp") == "FY"
                and p.get("form") in _ANNUAL_FORMS
                and p.get("fy") is not None
                and p.get("filed")
                and p.get("val") is not None
            ):
                out.append(p)
    return out


def rows_from_companyfacts(facts: Optional[Mapping[str, Any]]) -> List[dict]:
    """Pure: SEC ``companyfacts`` -> Polygon-vX annual ``results`` rows.

    One row per fiscal year. For each canonical concept the first tag synonym with
    annual data is used; within a fiscal year the LATEST-filed value wins (so a
    10-K/A restatement supersedes the original). Deterministic; no clock, no I/O.
    """
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}

    # Group by the period END DATE, not SEC's ``fy`` — ``fy`` is the FILING's fiscal
    # year, so one 10-K tags all its comparative years (current + 2 priors) under the
    # same ``fy``; grouping on it conflates three periods. ``end`` uniquely identifies
    # the period. Within a period the EARLIEST-filed fact wins = as-originally-reported
    # (point-in-time correct: the period's own annual filing, not a later comparative
    # or restatement), which also gives each period its own filing_date.
    per_concept: Dict[Tuple[str, str], Dict[str, Mapping[str, Any]]] = {}
    for concept, tags in SEC_TAG_MAP.items():
        points: List[Mapping[str, Any]] = []
        for tag in tags:
            node = gaap.get(tag)
            if isinstance(node, Mapping):
                points = _annual_points(node.get("units"))
                if points:
                    break  # first synonym with data wins
        if not points:
            continue
        by_end: Dict[str, Mapping[str, Any]] = {}
        for p in points:
            end = p.get("end")
            if not end:
                continue
            prev = by_end.get(end)
            if prev is None or str(p["filed"]) < str(prev["filed"]):
                by_end[end] = p
        per_concept[concept] = by_end

    ends = set()
    for by_end in per_concept.values():
        ends |= set(by_end)

    rows: List[dict] = []
    for end in ends:
        financials: Dict[str, Dict[str, dict]] = {}
        filed_min: Optional[str] = None
        for (statement, key), by_end in per_concept.items():
            p = by_end.get(end)
            if p is None:
                continue
            financials.setdefault(statement, {})[key] = {"value": p["val"]}
            filed = str(p["filed"])
            if filed_min is None or filed < filed_min:
                filed_min = filed
        if not financials:
            continue
        rows.append(
            {
                "fiscal_year": int(str(end)[:4]) if str(end)[:4].isdigit() else None,
                "fiscal_period": "FY",
                "timeframe": "annual",
                "filing_date": filed_min,
                "end_date": end,
                "financials": financials,
            }
        )
    return rows


def build_cache_payload(facts: Optional[Mapping[str, Any]]) -> dict:
    """Wrap the derived rows in the cache file's ``{"results": [...]}`` envelope."""
    return {"results": rows_from_companyfacts(facts)}


# ---------------------------------------------------------------------------
# Network I/O (isolated + injectable — the pure path above never touches it).
# ---------------------------------------------------------------------------
Opener = Callable[..., Any]


def _http_json(url: str, opener: Optional[Opener] = None) -> Any:
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with opener(req, timeout=45) as resp:
        return json.load(resp)


def fetch_companyfacts(cik: Any, opener: Optional[Opener] = None) -> Optional[dict]:
    """SEC companyfacts for a zero-padded CIK. ``None`` on any error (honest gap)."""
    c = str(cik).strip().lstrip("CIK").zfill(10)
    try:
        return _http_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", opener
        )
    except Exception as exc:  # noqa: BLE001 — a data gap must never break a run
        logger.warning("SEC companyfacts fetch failed for CIK%s (%s).", c, exc)
        return None


def ticker_cik_map(opener: Optional[Opener] = None) -> Dict[str, str]:
    """Ticker -> zero-padded CIK from SEC ``company_tickers.json``.

    Registers both the SEC dash form and a dot variant (``BRK-B`` and ``BRK.B``)
    so class-share tickers from ``sp500_universe`` resolve either way.
    """
    data = _http_json("https://www.sec.gov/files/company_tickers.json", opener)
    out: Dict[str, str] = {}
    for row in data.values():
        t = str(row["ticker"]).strip().upper()
        cik = str(row["cik_str"]).zfill(10)
        out[t] = cik
        out[t.replace("-", ".")] = cik
    return out


def write_symbol_cache(
    symbol: str,
    cik: Any,
    cache_dir: str,
    fetch: Optional[Callable[[Any], Optional[dict]]] = None,
) -> int:
    """Fetch SEC facts for ``symbol`` and write ``<SYMBOL>.json``. Returns row count.

    Writes nothing (returns 0) on a cold/empty result — an honest gap, never a stub.
    """
    fetch = fetch or fetch_companyfacts
    facts = fetch(cik)
    payload = build_cache_payload(facts)
    if not payload["results"]:
        logger.warning(
            "SEC: no annual rows for %s (CIK %s) — cache not written.", symbol, cik
        )
        return 0
    path = cache_path(cache_dir, symbol)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return len(payload["results"])
