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
from datetime import date
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
# Per period the frontmost tag reporting that period wins (#4567). The lists come from
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
        # #4567, gemessen an XEL: Versorger melden ihren GESAMTUMSATZ unter diesem
        # Kennzeichen; `Revenues` endet dort 2018. Der GESAMTumsatz, nicht der
        # unregulierte Anteil: `UnregulatedOperatingRevenue` traegt bei XEL 57 Mio statt
        # 14,7 Mrd — damit waere die Netto-Marge 3540 % statt 13,8 %. Steht hinten, weil
        # die Auswahl pro Periode greift: nur Perioden, die kein vorderes Kennzeichen
        # meldet. In den fuenf ueberlappenden Perioden 2017/2018 sind die Werte
        # identisch.
        "RegulatedAndUnregulatedOperatingRevenue",
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


# #4567: Ein 10-K kennzeichnet auch QUARTALSdauern mit ``fp=FY`` — ueber die 19
# gemessenen Titel passieren 11.154 solcher Punkte den Filter aus ``fp`` + ``form``.
# Nur eine Dauer von 330 bis 400 Tagen ist ein Geschaeftsjahr (52/53-Wochen-Jahre
# liegen bei 364/371 Tagen). Instant-Konzepte der Bilanz (``Assets``, ``Liabilities``,
# ``StockholdersEquity``) tragen KEIN ``start`` und bleiben unberuehrt.
_MIN_ANNUAL_DAYS = 330
_MAX_ANNUAL_DAYS = 400


def _is_annual_span(point: Mapping[str, Any]) -> bool:
    """Deckt ``start``..``end`` ein Geschaeftsjahr ab? Ohne ``start``: Bilanzstichtag."""
    start = point.get("start")
    end = point.get("end")
    if not start:
        return True
    try:
        span = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except (TypeError, ValueError):
        return False
    return _MIN_ANNUAL_DAYS <= span <= _MAX_ANNUAL_DAYS


def _annual_points(units: Optional[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """All annual (fp=FY, 10-K/20-F, one-year span) points across a concept's buckets."""
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
                and _is_annual_span(p)
            ):
                out.append(p)
    return out


def rows_from_companyfacts(facts: Optional[Mapping[str, Any]]) -> List[dict]:
    """Pure: SEC ``companyfacts`` -> Polygon-vX annual ``results`` rows.

    One row per fiscal year, newest period first. For each canonical concept the
    tag synonym is chosen PER PERIOD (#4567): for every period end the frontmost
    synonym of the list that reports THAT period wins. Within one period the
    EARLIEST-filed value wins (as-originally-reported, point-in-time).
    Deterministic; no clock, no I/O.
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
        # #4567: Das Kennzeichen wird PRO PERIODE gewaehlt, nicht einmal je Titel.
        # Vorher gewann das erste Synonym mit IRGENDWELCHEN Jahresdaten und die Schleife
        # brach ab. Endete dessen Reihe 2018 (Kennzeichenwechsel zur Umsatzrealisierung
        # nach ASC 606; Banken und Versorger melden zusaetzlich branchenspezifisch),
        # bekam keine spaetere Periode einen Wert — obwohl ein Synonym weiter hinten in
        # DERSELBEN Liste bis FY 2025 reicht. Gemessen am 12.10.2026 an NEE (Reihe endet
        # 2012), MS (2014), CVS und O (2018), BA (2019), TER und FIX (2020), PNC (2021).
        # Die Reihenfolge der Liste bleibt die Rangfolge; sie gilt nun je Periode.
        by_end: Dict[str, Mapping[str, Any]] = {}
        rang_je_ende: Dict[str, int] = {}
        for rang, tag in enumerate(tags):
            node = gaap.get(tag)
            if not isinstance(node, Mapping):
                continue
            for p in _annual_points(node.get("units")):
                end = p.get("end")
                if not end:
                    continue
                vorheriger_rang = rang_je_ende.get(end)
                if vorheriger_rang is None:
                    by_end[end] = p
                    rang_je_ende[end] = rang
                elif vorheriger_rang == rang and str(p["filed"]) < str(
                    by_end[end]["filed"]
                ):
                    # Gleiches Kennzeichen, gleiche Periode: frueheste Einreichung
                    # gewinnt (Point-in-Time, unveraendert). Ein vorderes Synonym wird
                    # von einem hinteren nie ueberschrieben, weil die Raenge
                    # aufsteigend durchlaufen werden.
                    by_end[end] = p
        if not by_end:
            continue
        per_concept[concept] = by_end

    ends = set()
    for by_end in per_concept.values():
        ends |= set(by_end)

    rows: List[dict] = []
    # Neueste Periode zuerst (#4567): `financials_feed._pit_select` sortiert nur
    # nach `filing_date` und stabil — teilen mehrere Perioden ein
    # Einreichungsdatum, entscheidet diese Reihenfolge, welche als „aktuell“ gilt.
    for end in sorted(ends, reverse=True):
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


# ---------------------------------------------------------------------------
# #3349 — Earnings REPORT DATES (SEC ``submissions`` API, distinct from the
# ``companyfacts`` fundamentals above). Feeds the Earnings-Proximity-Guard.
# The 8-K Item 2.02 ("Results of Operations") filing date IS the earnings-release
# day; 10-Q/10-K/20-F ``filed`` is the periodic fallback when no 8-K/2.02 exists.
# ---------------------------------------------------------------------------
_EARNINGS_8K_ITEM = "2.02"
_EARNINGS_PERIODIC_FORMS = {"10-Q", "10-K", "20-F"}


def parse_earnings_dates(submissions: Optional[Mapping[str, Any]]) -> List[str]:
    """Pure: SEC ``submissions`` payload -> sorted (desc) ISO earnings-report dates.

    Prefers 8-K Item 2.02 (the exact release day). Falls back to periodic-report
    ``filed`` dates only when the panel contains no 8-K/2.02. Deterministic, no I/O.
    """
    recent = (((submissions or {}).get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    filed = recent.get("filingDate") or []
    items = recent.get("items") or [""] * len(forms)
    eight_k: List[str] = []
    periodic: List[str] = []
    for i, form in enumerate(forms):
        if i >= len(filed):
            break
        d = filed[i]
        if not d:
            continue
        it = items[i] if i < len(items) else ""
        if form == "8-K" and _EARNINGS_8K_ITEM in (it or ""):
            eight_k.append(d)
        elif form in _EARNINGS_PERIODIC_FORMS:
            periodic.append(d)
    chosen = eight_k or periodic
    return sorted(set(chosen), reverse=True)


def fetch_submissions(cik: Any, opener: Optional[Opener] = None) -> Optional[dict]:
    """SEC ``submissions`` for a zero-padded CIK. ``None`` on any error (honest gap)."""
    c = str(cik).strip().lstrip("CIK").zfill(10)
    try:
        return _http_json(f"https://data.sec.gov/submissions/CIK{c}.json", opener)
    except Exception as exc:  # noqa: BLE001 — a data gap must never break a run
        logger.warning("SEC submissions fetch failed for CIK%s (%s).", c, exc)
        return None


def earnings_report_dates(
    cik: Any, fetch: Optional[Callable[[Any], Optional[dict]]] = None
) -> Tuple[Optional[List[str]], bool]:
    """Return ``(iso_dates, fetch_failed)`` for the Earnings-Guard cache.

    A fetch error ⇒ ``(None, True)`` (the guard fails CLOSED). A successful fetch
    with no earnings forms ⇒ ``([], False)`` (fails OPEN — e.g. an ETF/foreign
    filer without 8-K/2.02).
    """
    fetch = fetch or fetch_submissions
    sub = fetch(cik)
    if sub is None:
        return None, True
    return parse_earnings_dates(sub), False


def refresh_earnings_cache(
    symbol_ciks: Mapping[str, Any],
    cache_file: str,
    fetch: Optional[Callable[[Any], Optional[dict]]] = None,
) -> Dict[str, dict]:
    """Populate the Earnings-Guard cache ``{SYM: {"dates":[...], "error":bool}}``.

    Called by the daily refresh (a symbol whose fetch fails is stamped
    ``error=True`` so the guard fails CLOSED for it until the next good refresh).
    Writes the JSON blob and returns it.
    """
    blob: Dict[str, dict] = {}
    for sym, cik in symbol_ciks.items():
        dates, failed = earnings_report_dates(cik, fetch=fetch)
        blob[str(sym).upper()] = (
            {"error": True} if failed else {"dates": dates or [], "error": False}
        )
    os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as handle:
        json.dump(blob, handle)
    return blob
