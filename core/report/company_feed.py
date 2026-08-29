# core/report/company_feed.py
# ADR-RPT-COMPANY-01 — the company-identity feed for the auditable report pipeline.
"""Map Polygon's ``/v3/reference/tickers/{ticker}`` payload into report company facts.

WHY THIS MODULE EXISTS
----------------------
The note printed the ticker ``LYB`` and jumped straight to a rank and a table of
numbers. A reader never learned that LyondellBasell produces petrochemicals. Georg:
*"genau aussagen: das ist die firma, das macht die"*. This module is the source of that
sentence — and, incidentally, of the real ``market_cap`` that §6 used to render as
``~n/a Bio USD``.

It has three parts, mirroring :mod:`core.report.financials_feed`:

* :func:`map_polygon_company` — a **pure, deterministic** transform from the raw
  ``results`` object to a flat company-facts dict.
* :func:`fetch_raw_company` / :func:`fetch_company` — the **only** network path. Every
  failure mode (no key, timeout, HTTP error, rate-limit exhaustion) degrades to ``None``
  — an honest gap, never a fabricated number. The free tier is 5 requests/min, so 429s
  are retried with exponential backoff (honouring ``Retry-After``).
* :class:`CachedCompanyReader` — the DI adapter serving RPT-1's
  ``FactSetReader.get_company(symbol)`` seam off the cache that
  ``scripts/prefetch_company_universe.py`` warms.

⚠ NOT POINT-IN-TIME — AND THAT IS SAID OUT LOUD, NOT HIDDEN
-----------------------------------------------------------
Every other feed in this pipeline is PIT-reconstructable: bars are filtered at ``as_of``,
filings are dropped after their ``filing_date``. This endpoint is **current-state only**.
It has no as-of dimension: the ``market_cap`` it returns is today's, and a report dated
three months ago would otherwise silently quote a market cap from the future — a
lookahead wearing a provenance badge.

Dropping the fact whenever ``fetched_at > as_of`` was considered and rejected: the cache
is always warmed *now*, so that rule would kill the feature for every historical report
— the exact dead-on-arrival shape this pipeline has shipped three times. Instead the
fetch date is **carried as data** (``fetched_at``), stamped into the provenance string
(``fetched <date>``), and rendered next to the number as ``Stand <date>``. The reader is
told which day the figure is from, so nothing is passed off as an as-of value.

Name / description / sector / list date are quasi-static, so the staleness is immaterial
for them; ``market_cap`` and ``employees`` move, and are dated for exactly that reason.

⚠ THE SHAPE TRAP — the fixture is not the producer
--------------------------------------------------
``tests/fixtures/report/{lyb,xom}_company.json`` are the REAL, live payloads (HTTP 200,
free tier, 2026-07-17), not hand-built dicts in the shape this module wishes for. Three
features shipped dead this week because the consumer was written against an invented
schema and the tests passed because the fixture agreed with the invention. Two live
divergences the fixtures pin by name:

    live                                  our facts
    ──────────────────────────────────    ──────────────────
    total_employees: 18970             →  employees: 18970       (not `employees`!)
    list_date: "2010-04-28"  (str)     →  list_date: date(...)   (a date OBJECT)

⚠ TWO AUDITOR CONTRACTS, both load-bearing (``core/report/auditor.py``)
----------------------------------------------------------------------
The auditor pools Fact VALUES verbatim (``_Pool._absorb`` recurses into dicts) and then
proves the rendered text against that pool. Two consequences shape this mapper:

1. ``list_date`` must be a ``date`` OBJECT. ``_absorb`` adds a ``date`` to the PIT date
   pool, but only word-scans a ``str``. A string ``list_date`` renders "2010-04-28" and
   then STRIKES as an unsourced date.
2. ``market_cap`` must be pre-scaled to the units the report prints. The pool would hold
   only the raw ``18721516834.0``, which can never source a rendered ``"18.72 Mrd"``.
   So ``market_cap_mrd`` / ``market_cap_bio`` are emitted alongside the raw value. This
   is the same contract ``financials_feed.to_report_fundamentals`` already honours by
   pre-scaling ``revenue_bn`` — the producer owns its presentation contract.

Report-only and DORMANT: nothing here is read by the decision path;
``SPECIALIST_ALPHA_WEIGHT`` stays 0. This module deliberately does not touch
``core/polygon_data.py`` (a live market-data module) — it is a disjoint report feed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional

from core.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

POLYGON_TICKER_DETAILS_URL = "https://api.polygon.io/v3/reference/tickers"
_USER_AGENT = "aaagents-oss/1.0 (report-company-feed)"

# The prefetch writes one JSON file per symbol here (relative to the repo root);
# the reader loads the same layout. Deliberately NOT the financials cache dir: a
# different endpoint, a different refresh cadence, a different payload.
DEFAULT_CACHE_DIR = os.path.join("data", "company_cache")

_TRILLION = 1e12
_BILLION = 1e9

# The facts that make §0 worth printing. If the payload carries none of them, the source
# answered about nothing — which is `{}` (asked, empty), NOT None (never asked).
_SUBSTANTIVE_FIELDS = (
    "name",
    "description",
    "sic_description",
    "employees",
    "market_cap",
)

# ---------------------------------------------------------------------------
# ⚠ THE QUOTED-PROSE / AUDITOR CONTRACT. Measured, not theorised: without the two
# rules below, 15 of 30 REAL S&P 500 reports STRIKE the audit and blank
# (generate_report.py exits non-zero on integrity_ok=False). §0 is the first path in
# this pipeline to render long provider prose verbatim, and the auditor's plain-string
# scanner was never built for it.
#
# The asymmetry, proven directly against auditor.py:
#
#     "operating over 10,700 stores and a $1 billion charge"
#     pool   (auditor _STR_NUMBER_RE  \d+(?:\.\d+)?)   -> ['10', '700', '1']
#     report (auditor _NUMBER_RE, thousands-aware)     -> ['10,700', '$1']
#
# So "10,700" traces to nothing and STRIKEs — a figure quoted verbatim from the very
# source the section attributes. The auditor already solved this ONCE, for news headlines
# (ADR-RPT4-05 / _absorb_news_numbers): a figure quoted verbatim in a PIT source IS
# sourced. News gets a thousands-aware scanner; a plain string does not. We cannot reach
# that scanner from here (auditor.py is not ours to change), so the producer supplies the
# same substance the same way it pre-scales market_cap: as FactSet data the pool absorbs.
# ---------------------------------------------------------------------------
_QUOTED_FIGURE_RE = re.compile(
    r"(?<![A-Za-z0-9.,])"  # not the tail of a label (MA20, FY2026)
    r"(\d{1,3}(?:,\d{3})+|\d+)"  # thousands-grouped OR plain — the auditor's own shape
    r"(\.\d+)?"  # optional decimals
    r"(?![A-Za-z0-9])"  # not glued to a unit label
)

# The one figure shape the producer CANNOT make traceable, so the description carrying it
# is dropped at this boundary instead of striking the whole report.
#
# auditor._hit rejects EVERY candidate in [0, 9] when the claim carries a money unit —
# the guard that stops a fabricated "5 Bio" matching horizon_days=5. News may bypass it
# via a matching money-SCALE class (news_scales); prose has no such path, and putting the
# value in the pool cannot help: the guard tests the CANDIDATE, not the claim. So a bare
# "$1" is unprovable here by construction.
#
# Live: 2 of 30 real names trip it (PG "generate annual global sales of more than $1
# billion each", DG "2,000 priced at $1 or less"). Dropping the prose costs those two
# readers a paragraph; NOT dropping it costs them the entire report. news_feed.py made
# exactly this call for exactly this reason: "Failing at the boundary beats failing in
# the report."
#
# ⚠ This duplicates auditor knowledge and should not have to: the durable fix is to treat
# verbatim source prose like a news headline (ADR-RPT4-05). That is auditor.py, owned by
# another agent right now — tracked as a follow-up.
_MONEY_UNIT_AFTER = r"Bio|Mrd|Mio|USD|EUR|Billion|Trillion"
_UNTRACEABLE_MONEY_RE = re.compile(
    r"(?<![A-Za-z0-9.,])(?:"
    r"[$]\s?[0-9](?![\d.,])"  # "$1", "$5 billion"  -> unit "$" via _parse_number
    r"|[0-9]\s*(?:" + _MONEY_UNIT_AFTER + r")(?![A-Za-z])"  # "5 Bio", "3 USD"
    r")"
)


# ---------------------------------------------------------------------------
# Small deterministic cleaners. Each returns None — never "" and never 0 — so a
# missing field stays an honest gap the renderer can SEE instead of a falsy value
# it would silently print.
# ---------------------------------------------------------------------------
def _clean_str(raw: Any) -> Optional[str]:
    """A non-blank string, else ``None``. ``""`` / ``"   "`` are gaps, not values."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _clean_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _clean_float(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # NaN / inf are not facts. (NaN != NaN is the cheapest reliable test.)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _quoted_figures(text: str) -> List[float]:
    """Every figure the quoted prose will render, parsed the way the AUDITOR reads it.

    Thousands-aware on purpose (``10,700`` -> one figure 10700.0, not 10 and 700). These
    are not our numbers: they are the source's, quoted verbatim two lines away in §0 and
    attributed. Carrying them as FactSet data is what lets the gate trace them — the same
    substance ADR-RPT4-05 gives news headlines, delivered from the side we control.
    """
    out: List[float] = []
    for int_part, dec_part in _QUOTED_FIGURE_RE.findall(text or ""):
        try:
            out.append(float(int_part.replace(",", "") + (dec_part or "")))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _clean_date(raw: Any) -> Optional[date]:
    """Normalise a ``date`` / ``datetime`` / ISO string to a calendar ``date``.

    Tolerates a full timestamp ("2026-07-17T00:00:00+00:00") by taking its date head —
    the cache's ``fetched_at`` is written as one.
    """
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip()[:10]).date()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Pure mapping.
# ---------------------------------------------------------------------------
def map_polygon_company(
    raw: Any, *, fetched_at: Any = None
) -> Optional[Dict[str, Any]]:
    """Map a raw ``/v3/reference/tickers`` ``results`` object to flat company facts.

    Never raises: a malformed payload must not take down a 500-symbol report run.

    Returns
    -------
    dict or None
        * ``None`` — the payload is UNUSABLE (fail-closed: not a mapping at all). The
          report then says plainly that it has no company data.
        * ``{}`` — the source ANSWERED and carried no company facts for this ticker.
        * a dict — the mapped facts; each individual field may still be ``None``
          (XOM's live payload really does carry ``sic_description: None``).

    ``None`` and ``{}`` are DIFFERENT CLAIMS — "nobody looked" vs "we looked and there
    is nothing" — and callers must never collapse them with a falsy check. That exact
    defect shipped twice this week (news feed, fundamentals).
    """
    if not isinstance(raw, Mapping):
        logger.warning(
            "company_feed: unusable payload (%s) — no company facts",
            type(raw).__name__,
        )
        return None

    ticker = _clean_str(raw.get("ticker")) or "?"
    market_cap = _clean_float(raw.get("market_cap"))

    # -- the quoted description, and whether we may quote it at all --------------
    description = _clean_str(raw.get("description"))
    description_withheld = False
    if description is not None and _UNTRACEABLE_MONEY_RE.search(description):
        # Dropped at the boundary rather than struck in the report — see
        # _UNTRACEABLE_MONEY_RE. §0 says plainly that the prose was withheld and why; it
        # must never claim the source HAS no description, because it does.
        logger.warning(
            "company_feed: %s description withheld — it quotes a small money figure "
            "($0-$9) the report cannot source past the auditor's money guard "
            "(ADR-RPT4-05 follow-up); name/sector/size still render",
            ticker,
        )
        description = None
        description_withheld = True

    facts: Dict[str, Any] = {
        "name": _clean_str(raw.get("name")),
        "description": description,
        # A bool, so auditor._Pool._absorb skips it — it is a rendering instruction,
        # not a fact, and must never enter the number/word pool.
        "description_withheld": description_withheld,
        "sic_description": _clean_str(raw.get("sic_description")),
        # ⚠ the live field is `total_employees`, ours is `employees`.
        "employees": _clean_int(raw.get("total_employees")),
        "market_cap": market_cap,
        "homepage_url": _clean_str(raw.get("homepage_url")),
        # ⚠ a date OBJECT, not the payload's string — see the auditor contract above.
        "list_date": _clean_date(raw.get("list_date")),
        "fetched_at": _clean_date(fetched_at),
    }

    if not any(facts[key] is not None for key in _SUBSTANTIVE_FIELDS):
        logger.warning(
            "company_feed: payload for %r carried no usable company facts", ticker
        )
        return {}

    # Figures quoted by the prose we are about to render (empty when withheld).
    facts["description_figures"] = _quoted_figures(description) if description else []

    # -- market cap, pre-scaled to the ONE unit the report will print -----------
    # ⚠ Exactly one scale is emitted, deliberately. Emitting both put an unrendered
    # market_cap_bio (LYB: 0.0187) into the auditor pool, and since |0.0187| <= 1.5 the
    # pool's fraction rule ALSO admitted 1.87 — two values that appear nowhere in the
    # source and can never legitimately be rendered, each able to shield a fabrication in
    # the gate that is the entire moat. The pool may only ever learn what we actually
    # print.
    if market_cap is None:
        facts["market_cap_display"] = None
        facts["market_cap_display_unit"] = None
    elif market_cap >= _TRILLION:
        facts["market_cap_display"] = market_cap / _TRILLION
        facts["market_cap_display_unit"] = "Bio"
    else:
        facts["market_cap_display"] = market_cap / _BILLION
        facts["market_cap_display_unit"] = "Mrd"
    return facts


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


def fetch_raw_company(
    symbol: str,
    *,
    api_key: Optional[str] = None,
    timeout: float = 15.0,
    session: Any = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    sleep: Any = None,
) -> Optional[Mapping[str, Any]]:
    """Fetch the raw ``results`` object from Polygon v3, or ``None`` on any failure.

    Network is isolated here. ``session`` (anything exposing ``.get``) and ``sleep`` are
    injectable so the whole path is testable without touching the network or the wall
    clock. Mirrors ``financials_feed.fetch_raw_financials`` deliberately — same free
    tier, same 5/min cap, same failure taxonomy.
    """
    key = api_key or os.environ.get("POLYGON_API_KEY", "")
    if not key:
        logger.warning(
            "company_feed: no POLYGON_API_KEY — company facts unavailable for %s",
            symbol,
        )
        return None

    sleep_fn = sleep or time.sleep
    if session is not None:
        getter = session.get
    else:  # lazy import keeps the pure core free of the network dependency
        import requests

        getter = requests.get

    ticker = str(symbol).strip().upper()
    url = f"{POLYGON_TICKER_DETAILS_URL}/{ticker}"
    params = {"apiKey": key}
    headers = {"User-Agent": _USER_AGENT}

    attempt = 0
    while True:
        try:
            response = getter(url, params=params, timeout=timeout, headers=headers)
        except Exception as exc:  # noqa: BLE001 — network error / timeout: honest gap
            logger.warning(
                "company_feed: request failed for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        if getattr(response, "status_code", None) == 429:
            if attempt >= max_retries:
                logger.warning(
                    "company_feed: rate-limited (429) for %s after %d retries",
                    symbol,
                    attempt,
                )
                return None
            wait = _parse_retry_after(response)
            if wait is None:
                wait = backoff_base * (2**attempt)
            logger.warning(
                "company_feed: 429 for %s — backoff %.2fs (attempt %d/%d)",
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "company_feed: HTTP error for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "company_feed: bad JSON for %s: %s",
                symbol,
                redact_secrets(repr(exc)),
            )
            return None

        results = body.get("results") if isinstance(body, Mapping) else None
        if not isinstance(results, Mapping):
            logger.warning(
                "company_feed: payload for %s has no 'results' object", symbol
            )
            return None
        return results


def fetch_company(
    symbol: str,
    *,
    api_key: Optional[str] = None,
    fetched_at: Any = None,
    timeout: float = 15.0,
    session: Any = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    sleep: Any = None,
) -> Optional[Dict[str, Any]]:
    """Fetch + map company facts for ``symbol``. Thin composition, ``None`` on failure."""
    raw = fetch_raw_company(
        symbol,
        api_key=api_key,
        timeout=timeout,
        session=session,
        max_retries=max_retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    if raw is None:
        return None
    return map_polygon_company(raw, fetched_at=fetched_at)


# ---------------------------------------------------------------------------
# Cache layout + reader adapter (RPT-1 FactSetReader DI seam).
# ---------------------------------------------------------------------------
def cache_path(cache_dir: str, symbol: str) -> str:
    """Return the per-symbol cache file path (creates ``cache_dir`` if needed)."""
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{str(symbol).strip().upper()}.json")


@dataclass(frozen=True)
class CachedCompanyReader:
    """Reads the company cache and serves RPT-1's ``get_company`` seam.

    Never hits the network — ``scripts/prefetch_company_universe.py`` populates the
    cache, and this reads it offline. The cache stores the RAW payload (as the
    financials cache stores raw ``results``), so the mapping stays a pure transform
    applied at read time and a re-mapped field needs no re-fetch.

    A cold cache yields ``None`` — an honest gap, never an empty dict.
    """

    cache_dir: str = DEFAULT_CACHE_DIR

    def get_company(self, symbol: str) -> Optional[Mapping[str, Any]]:
        path = os.path.join(self.cache_dir, f"{str(symbol).strip().upper()}.json")
        if not os.path.exists(path):
            # Cold cache = nobody ever asked for this symbol. None, not {}.
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("company_feed: unreadable cache for %s: %r", symbol, exc)
            return None
        if not isinstance(payload, Mapping):
            logger.warning("company_feed: malformed cache payload for %s", symbol)
            return None
        # fetched_at comes off the CACHE, never off a clock — the reader stays pure.
        return map_polygon_company(
            payload.get("results"), fetched_at=payload.get("fetched_at")
        )


__all__ = [
    "POLYGON_TICKER_DETAILS_URL",
    "DEFAULT_CACHE_DIR",
    "CachedCompanyReader",
    "cache_path",
    "fetch_company",
    "fetch_raw_company",
    "map_polygon_company",
]
