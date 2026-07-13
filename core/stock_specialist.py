# core/stock_specialist.py
# Epic 3.3 — Stock Specialist System (99% API-Kostensenkung)
# Policy: CODING_POLICY.md §1 Compliance-First, §5 KI-Agenten-Lifecycle
"""
Stock Specialist Agent — Deep Research Engine
==============================================
Each specialist is assigned one stock symbol and continuously gathers
intelligence from multiple free data sources WITHOUT using Gemini Search
Grounding (which costs $0.035/call). Instead it fetches raw data in parallel,
then makes ONE cheap Gemini text call (~$0.0004) to synthesize everything.

Data sources gathered (all free):
  1. SEC EDGAR Form 4        — insider buy/sell filings
  2. SEC EDGAR Form 8-K      — material events (earnings, deals, management)
  3. SEC EDGAR Schedule 13D  — activist investor disclosures (>5% stakes)
  4. OpenInsider             — cluster insider buy/sell patterns
  5. Quiver Quant            — congressional trading disclosures
  6. Polygon.io News         — recent headlines (already in bot)
  7. Wikipedia pageviews     — unusual research interest spikes
  8. Reddit mentions         — WSB / r/stocks community attention
  9. FINRA short interest    — abnormal short interest changes
 10. Google Trends           — unusual public search interest

Gemini then synthesizes all gathered raw data and scores the stock
(bullish/neutral/bearish, 0-100, escalation flag).

Cost: ~$0.0004 per research cycle vs $0.035 with Search Grounding = 99% cheaper.  # noqa: E501
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from urllib.parse import quote

# Cost guard: config for _call_gemini_sync
from google.genai import types as genai_types

if (
    TYPE_CHECKING
):  # RPAR T6a: type-only import (no runtime cost; guard is lazy-imported)
    from core.data_integrity import DataIntegrityResult

from config import get_config  # Fusion: two-flag TFT wiring (dormant)

# ADR-OBS-01 / PR E: specialist free-API feed-health observation (PURE OBSERVATION).  # noqa: E501
from core.data_provider_telemetry import (  # noqa: E501
    bump_specialist_source as _bump_specialist,
)
from core.llm.provider import (  # ADR-014 seam (G4a-2c)  # noqa: E501
    OllamaProvider,
    get_llm_provider,
)
from core.specialist.cards import (  # RPAR T2 (#1264): flag-gated card fields
    _HIGH_SHORT_PCT,
    _REDDIT_BUZZ_MIN,
    build_pros_cons,
    build_summary,
    select_headlines,
)
from core.specialist.edgar_cik import (  # RQ-1 B1 (#1521): ticker->CIK resolver  # noqa: E501
    maybe_refresh,
    resolve_cik,
)
from core.specialist.insight_quality import (  # RPAR T6b (#1271)
    LLMJudge,
    enforce_insight_quality,
)
from core.specialist.insight_quality.prompt import (  # RPAR T6b (#1271)  # noqa: E501
    cap_transcript,
)
from core.specialist.news import (  # RPAR T3 (#1267): headline merge  # noqa: E501
    merge_headlines,
)

logger = logging.getLogger(__name__)


# PR E: fixed source roster (bounded) aligned 1:1 with the ``_gather`` results tuple.  # noqa: E501
_SPECIALIST_SOURCE_NAMES = (
    "edgar_form4",
    "edgar_8k",
    "edgar_13d",
    "congressional_trades",
    "polygon_news",
    "wiki_pageviews",
    "reddit_mentions",
    "finra_short_interest",
    "google_trends",
    "ml_prediction",
)


def _observe_specialist_results(results) -> None:
    """Fail-safe per-source ok/fail observation for the specialist gather (PR E).  # noqa: E501

    Maps each ``asyncio.gather(..., return_exceptions=True)`` result to its source  # noqa: E501
    name: an ``Exception`` result → fail, anything else → ok. PURE OBSERVATION —  # noqa: E501
    fully guarded so a counter failure can never perturb the specialist research."""  # noqa: E501
    try:
        for name, val in zip(_SPECIALIST_SOURCE_NAMES, results):
            _bump_specialist(name, ok=not isinstance(val, Exception))
    except Exception:  # noqa: BLE001 — a broken counter must never break the gather
        pass


# Fusion: lazy singleton for the canonical bar source + the TFT↔LLM convergence blend.  # noqa: E501
# Both are only reached on the flag-gated _fetch_ml_prediction / blend paths.
_DATA_PROVIDER = None


def _get_data_provider():
    """Lazy HistoricalDataProvider singleton (Alpaca + Databento + GCS cache)."""  # noqa: E501
    global _DATA_PROVIDER
    if _DATA_PROVIDER is None:
        from core.data_provider import HistoricalDataProvider

        _DATA_PROVIDER = HistoricalDataProvider()
    return _DATA_PROVIDER


def _blend_ml_sentiment(ml_pred: dict, llm_score: float, symbol: str) -> float:
    """TFT↔LLM convergence blend (the caller gates it behind ML_SENTIMENT_BLEND_ENABLED).  # noqa: E501
    Returns the blended sentiment, or the unchanged ``llm_score`` on any error (logged at  # noqa: E501
    WARNING — never silent). Mirrors the prototype's P3-B convergence math."""
    try:
        cfg = get_config()
        sat = float(getattr(cfg, "SPECIALIST_ML_SATURATION_PCT", 2.0))
        # Guard the `/ (2.0 * sat)` divisor below: a misconfigured saturation of  # noqa: E501
        # 0 (or negative) would otherwise raise ZeroDivisionError mid-blend. Fail  # noqa: E501
        # loud + clean here so the except-fallback logs a descriptive WARNING and  # noqa: E501
        # returns the unchanged llm_score (RPAR T5 review #1316 F-02).
        if sat <= 0.0:
            raise ValueError(
                f"SPECIALIST_ML_SATURATION_PCT must be > 0 (got {sat})"
            )  # noqa: E501
        hi = float(getattr(cfg, "SPECIALIST_ML_LLM_AGREEMENT_HIGH", 0.75))
        md = float(getattr(cfg, "SPECIALIST_ML_LLM_AGREEMENT_MID", 0.50))
        conv_ml = float(getattr(cfg, "SPECIALIST_BLEND_CONVERGED_ML_W", 0.55))
        conv_llm = float(
            getattr(cfg, "SPECIALIST_BLEND_CONVERGED_LLM_W", 0.45)
        )  # noqa: E501
        part_ml = float(getattr(cfg, "SPECIALIST_BLEND_PARTIAL_ML_W", 0.40))
        part_llm = float(getattr(cfg, "SPECIALIST_BLEND_PARTIAL_LLM_W", 0.60))
        div_shrink = float(
            getattr(cfg, "SPECIALIST_BLEND_DIVERGED_SHRINK", 0.30)
        )  # noqa: E501
        ml_score = max(
            0.0,
            min(
                100.0, (ml_pred["base_return_pct"] + sat) / (2.0 * sat) * 100.0
            ),  # noqa: E501
        )
        agreement = 1.0 - abs(ml_score - llm_score) / 100.0
        if agreement >= hi:
            return ml_score * conv_ml + llm_score * conv_llm
        if agreement >= md:
            return ml_score * part_ml + llm_score * part_llm
        return 50.0 + (llm_score - 50.0) * div_shrink
    except Exception as exc:
        logger.warning(
            "[%s] convergence blend fell back to llm_only: %s", symbol, exc
        )  # noqa: E501
        return llm_score


# RPAR T3 (#1267): pure Google+Polygon headline merge (flag SPECIALIST_NEWS_V2, default OFF).  # noqa: E501

# ─────────────────────────────────────────────────────────────
# SpecialistReport
# ─────────────────────────────────────────────────────────────
# The schema lives in core/specialist/report.py (RPAR Epic #1262, Task V0).
# Re-exported here so existing importers — e.g.
# `from core.stock_specialist import SpecialistReport` in
# core/specialist_registry.py — keep working unchanged.
from core.specialist.report import SpecialistReport  # noqa: E402  (re-export)

# ─────────────────────────────────────────────────────────────
# StockSpecialistAgent
# ─────────────────────────────────────────────────────────────


# RQ-1 A1 (#1517, Epic #1516): ETFs file no own insider / Form-4 / 13D / 8-K issuer  # noqa: E501
# filings. The EDGAR fetchers below search by the bare ticker string, so for an ETF they  # noqa: E501
# surface unrelated registrants ("Spy Inc.", "Magnum Opus") as fake filings that then  # noqa: E501
# inflate sentiment. Short-circuit these tickers. (B1/#1521 ticker->CIK resolution  # noqa: E501
# generalises this by issuer; until then this curated allowlist is the gate.)
_ETF_NO_INSIDER_FILINGS = frozenset(
    {
        "SPY",
        "VOO",
        "IVV",
        "VTI",
        "QQQ",
        "DIA",
        "IWM",
        "XLF",
        "XLK",
        "XLE",
        "XLV",
        "XLI",
        "XLY",
        "XLP",
        "XLU",
        "XLB",
        "XLRE",
        "XLC",
        "AGG",
        "BND",
        "TLT",
        "HYG",
        "LQD",
        "GLD",
        "SLV",
        "EEM",
        "EFA",
        "VEA",
        "VWO",
    }
)


class StockSpecialistAgent:
    """
    Autonomous research agent for a single stock symbol.

    Gathers intelligence from 10 free data sources in parallel, then
    uses one cheap Gemini text call (no Search Grounding) to synthesize
    everything into a structured SpecialistReport.
    """

    # Shared semaphore: cap concurrent Gemini synthesis calls across all instances  # noqa: E501
    _gemini_semaphore: Optional[asyncio.Semaphore] = None

    @classmethod
    def get_semaphore(cls) -> asyncio.Semaphore:
        if cls._gemini_semaphore is None:
            cls._gemini_semaphore = asyncio.Semaphore(5)
        return cls._gemini_semaphore

    def __init__(
        self, symbol: str, gemini_api_key: str, polygon_api_key: str = ""
    ):  # noqa: E501
        self.symbol = symbol.upper().strip()
        self._gemini_api_key = gemini_api_key
        self._polygon_api_key = polygon_api_key
        self._last_report: Optional[SpecialistReport] = None
        self._last_refresh: Optional[datetime] = None

    async def research(self) -> SpecialistReport:
        """
        Full research cycle:
          1. Gather all data sources in parallel (all free, no LLM)
          2. Synthesize with one Gemini text call (no search grounding)
        """
        # B1 (#1521): refresh the ticker->CIK map at most once per cycle (single-flight,  # noqa: E501
        # serves last-known-good on failure) BEFORE the gather, so the 3 EDGAR fetchers share  # noqa: E501
        # one resolved map and N specialists do not each burst SEC (<=10 req/s fair-access).  # noqa: E501
        await maybe_refresh()

        # Phase 1: Parallel data gathering
        (
            insider_trades,
            material_events,
            activist_stakes,
            political_trades,
            polygon_news,
            wiki_data,
            reddit_data,
            short_interest,
            google_trend,
            ml_prediction,
        ) = await asyncio.gather(
            self._fetch_edgar_form4(),
            self._fetch_edgar_8k(),
            self._fetch_edgar_13d(),
            self._fetch_congressional_trades(),
            self._fetch_polygon_news(),
            self._fetch_wiki_pageviews(),
            self._fetch_reddit_mentions(),
            self._fetch_finra_short_interest(),
            self._fetch_google_trends(),
            self._fetch_ml_prediction(),
            return_exceptions=True,
        )

        # PR E: fail-safe per-source feed-health observation (order matches the gather).  # noqa: E501
        _observe_specialist_results(
            (
                insider_trades,
                material_events,
                activist_stakes,
                political_trades,
                polygon_news,
                wiki_data,
                reddit_data,
                short_interest,
                google_trend,
                ml_prediction,
            )
        )

        # Normalise exceptions to empty defaults
        def _safe(val, default):
            return default if isinstance(val, Exception) else val

        insider_trades = _safe(insider_trades, [])
        material_events = _safe(material_events, [])
        activist_stakes = _safe(activist_stakes, [])
        political_trades = _safe(political_trades, [])
        polygon_news = _safe(polygon_news, [])
        wiki_data = _safe(wiki_data, {})
        reddit_data = _safe(reddit_data, {})
        short_interest = _safe(short_interest, None)
        google_trend = _safe(google_trend, None)
        ml_prediction = _safe(ml_prediction, None)

        # RPAR T3 (#1267, dormant default OFF): merge Google-News-RSS headlines with the  # noqa: E501
        # Polygon headlines. Flag-FIRST: when SPECIALIST_NEWS_V2 is OFF the Google fetcher is  # noqa: E501
        # never reached and ``merged`` is exactly today's ``polygon_news`` -> recent_headlines  # noqa: E501
        # below is byte-identical. The merge only changes the NEWS *inputs* to the LLM  # noqa: E501
        # synthesis, not the deterministic _build_report scoring math (NEWS-8).
        if getattr(get_config(), "SPECIALIST_NEWS_V2", False):
            google_news = await self._fetch_google_news()
            merged_headlines = merge_headlines(
                polygon_news, google_news, cap=10
            )  # noqa: E501
        else:
            merged_headlines = polygon_news

        # Phase 2: Gemini synthesis (one cheap text call — NO search grounding)
        gathered = {
            "insider_trades": insider_trades[:10],
            "material_events": material_events[:5],
            "activist_stakes": activist_stakes[:5],
            "political_trades": political_trades[:5],
            "recent_headlines": merged_headlines[:8],
            "wiki_spike": wiki_data.get("spike", False),
            "wiki_views_7d": wiki_data.get("views_7d", 0),
            "reddit_mentions_24h": reddit_data.get("mentions", 0),
            "reddit_sentiment": reddit_data.get("sentiment", "neutral"),
            "short_interest_pct": short_interest,
            "google_trend_score": google_trend,
            "ml_prediction": ml_prediction,
        }

        # RPAR T6a (dormant, default OFF): data-integrity guard. When enabled it  # noqa: E501
        # derives the display-only data_quality/degraded fields from `gathered`; on a  # noqa: E501
        # hard data failure it signals skip_llm, in which case we reuse the EXISTING  # noqa: E501
        # V0-default synthesis ({} -> _parse_synthesis("") -> hold/50.0) instead of  # noqa: E501
        # calling the LLM. The guard NEVER changes the decision (score/recommendation/  # noqa: E501
        # reasons are produced unchanged in _build_report).
        integrity = None
        if getattr(get_config(), "DATA_INTEGRITY_GUARD_ENABLED", False):
            from core.data_integrity import assess

            integrity = assess(gathered)

        if integrity is not None and integrity.skip_llm:
            logger.warning(
                "[%s] data-integrity hard fail -> skip-LLM (degraded)",
                self.symbol,  # noqa: E501
            )
            synthesis = {}
        else:
            synthesis = await self._gemini_synthesize(gathered)

        # Build final report
        report = self._build_report(gathered, synthesis, integrity=integrity)
        # RPAR T6b (#1271, dormant default OFF): the insight-quality ratchet grades the synthesized  # noqa: E501
        # prose and keeps the better of {fresh, prior} (LLMJudge() is the conservative PASS no-op  # noqa: E501
        # until the Gemini-backed adapter + parity fixtures land — follow-up). last_report is the  # noqa: E501
        # PRIOR (set below). Flag OFF -> this block is skipped -> report + DTO byte-identical (BORA).  # noqa: E501
        if getattr(get_config(), "INSIGHT_QUALITY_ENABLED", False):
            gathered["earnings_transcript"] = cap_transcript(
                self._fetch_earnings_transcript()
            )
            report = enforce_insight_quality(
                report,
                gathered=gathered,
                last_report=self._last_report,
                cfg=get_config(),
                judge=LLMJudge(),
            )
        self._last_report = report
        self._last_refresh = datetime.now(timezone.utc)
        return report

    def _fetch_earnings_transcript(self) -> str:
        """RPAR T6b (#1271) PR-2: latest earnings-call transcript snippet for the IQ prompt.  # noqa: E501

        Graceful stub (Dual Design Option A): returns "" until the transcript data source is  # noqa: E501
        decided. The caller caps it via ``cap_transcript`` and the IQ path is flag-gated  # noqa: E501
        (``INSIGHT_QUALITY_ENABLED``), so "" simply means no transcript injected — never a crash.  # noqa: E501
        Wiring the real source is the follow-up once the provider + bundle snapshot are confirmed.  # noqa: E501
        """
        return ""

    # ─────────────────────────────────────────────────────────
    # Data gatherers — all free, no LLM
    # ─────────────────────────────────────────────────────────

    async def _fetch_ml_prediction(self) -> Optional[Dict[str, Any]]:
        """Fusion (flag-gated): the per-symbol TFT prediction. **Flag-FIRST** — returns  # noqa: E501
        None before any data I/O unless ``ML_PREDICTION_ENABLED``. Bars come from the  # noqa: E501
        canonical ``data_provider`` (sync → ``asyncio.to_thread``); FeatureBuilder +  # noqa: E501
        model_registry do the rest. Any failure → None (logged at WARNING). The order path  # noqa: E501
        never depends on this; it only populates the report's ``ml_*`` fields."""  # noqa: E501
        if not getattr(get_config(), "ML_PREDICTION_ENABLED", False):
            return None
        try:
            bars_df = await asyncio.to_thread(
                _get_data_provider().get_data,
                self.symbol,
                datetime.now(timezone.utc),
                5 * 365,
            )
            if bars_df is None or len(bars_df) < 325:
                return None
            # Local, not self.* — returned in the dict so concurrent research() calls on  # noqa: E501
            # the same agent can't race on a shared instance attribute (review fix).  # noqa: E501
            forecast_vol = None
            try:
                from core.ml.vol_model import forecast_forward_vol

                forecast_vol = forecast_forward_vol(bars_df)
            except Exception as exc:
                logger.warning(
                    "[ML] %s: forecast_vol failed: %s", self.symbol, exc
                )  # noqa: E501

            from core.ml.feature_builder import FeatureBuilder

            features_df = FeatureBuilder().build(bars_df, symbol=self.symbol)
            if (
                features_df is None or features_df.empty or len(features_df) < 60
            ):  # noqa: E501
                return None

            from core.ml.model_registry import model_registry

            prediction = await model_registry.get_or_train(
                self.symbol, features_df
            )  # noqa: E501
            if prediction is None:
                return None
            # attention_weights is intentionally NOT returned — it is a non-scalar and must  # noqa: E501
            # never reach the §5.9/BORA-scalar-only state["ml"]; nothing on main reads it.  # noqa: E501
            return {
                "direction": prediction.direction,
                "bear_return_pct": prediction.bear_return_pct,
                "base_return_pct": prediction.base_return_pct,
                "bull_return_pct": prediction.bull_return_pct,
                "confidence": prediction.confidence,
                "forecast_vol": forecast_vol,
            }
        except Exception as exc:
            logger.warning(
                "[ML] prediction failed for %s: %s", self.symbol, exc
            )  # noqa: E501
            return None

    async def _fetch_edgar(
        self,
        *,
        forms: str,
        cutoff_days: int,
        cap: int,
        build_row,
        enrich=None,
    ) -> List[Dict]:
        """Shared SEC EDGAR full-text fetch (RQ-1 B1, #1521). Pipeline per fetcher:  # noqa: E501
          STEP 0 (A1): ETF short-circuit -> [] (no I/O; ETFs have no own issuer filings).  # noqa: E501
          STEP 1 (B1): resolve ticker->CIK, scope the query with ``&ciks=`` + a ``ciks``  # noqa: E501
                       membership match-back so only the issuer's OWN filings survive -- kills  # noqa: E501
                       the "Spy Inc."/"Magnum Opus" false positives where a 3-letter ticker  # noqa: E501
                       matched as a word in an unrelated registrant's filing. Unknown ticker  # noqa: E501
                       keeps the free-text ``q=`` fallback (never regress new/illiquid symbols).  # noqa: E501
          STEP 2 (A2): client-side recency guard on file_date (efts ranks by relevance, NOT  # noqa: E501
                       date, so server-side ``startdt`` alone leaks stale filings).  # noqa: E501
        ``build_row(src)`` produces the per-form output dict; its keys are UNCHANGED vs the  # noqa: E501
        pre-B1 fetchers, so the prompt / escalation / serializer-count contract is preserved.  # noqa: E501
        B1 changes only WHICH filings populate the lists."""
        try:
            import httpx

            # STEP 0 -- A1 ETF short-circuit (before any I/O)
            if self.symbol in _ETF_NO_INSIDER_FILINGS:
                return []

            # STEP 1 -- B1: resolve CIK (None -> free-text fallback, WARNING-logged)  # noqa: E501
            cik = resolve_cik(self.symbol)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=cutoff_days)
            ).strftime("%Y-%m-%d")
            base = "https://efts.sec.gov/LATEST/search-index"
            if cik:
                url = (
                    f"{base}?q=%22{self.symbol}%22&forms={forms}"
                    f"&ciks={cik}&dateRange=custom&startdt={cutoff}"
                )
            else:
                logger.warning(
                    "[%s] resolve_cik miss -> free-text EDGAR fallback (forms=%s)",  # noqa: E501
                    self.symbol,
                    forms,
                )
                url = (
                    f"{base}?q=%22{self.symbol}%22&forms={forms}"
                    f"&dateRange=custom&startdt={cutoff}"
                )

            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                hits = r.json().get("hits", {}).get("hits", [])

            out: List[Dict] = []
            passed: List[Dict] = (
                []
            )  # B3b: hits parallel to `out`, for optional enrichment
            for hit in hits:
                src = hit.get("_source", {})

                # STEP 1 (cont.) -- B1 match-back: keep only hits whose `ciks` array contains  # noqa: E501
                # the resolved issuer CIK (MEMBERSHIP -- a Form 4 carries the reporting owner's  # noqa: E501
                # CIK AND the issuer's). Normalise both sides to int (map gives padded str;  # noqa: E501
                # `ciks` are padded strs) -- comparing padded-str to int would silently drop  # noqa: E501
                # every row. Skipped on the free-text fallback (no CIK).
                if cik:
                    src_ciks = src.get("ciks") or []
                    if int(cik) not in {
                        int(c) for c in src_ciks if str(c).strip()
                    }:  # noqa: E501
                        continue

                # STEP 2 -- A2 recency guard (client-side; YYYY-MM-DD lexical == chronological)  # noqa: E501
                filed = src.get("file_date", "")
                if filed and filed < cutoff:
                    continue

                out.append(build_row(src))
                passed.append(hit)
                if len(out) >= cap:
                    break
            # RQ-1 B3b (#1536): optional per-row enrichment (e.g. Form 4 buy/sell direction),  # noqa: E501
            # flag-gated INSIDE the enricher -> the default path stays byte-identical.  # noqa: E501
            if enrich is not None:
                await enrich(out, passed, cik)
            return out
        except Exception as e:
            logger.debug("[%s] EDGAR %s: %s", self.symbol, forms, e)
            return []

    async def _fetch_edgar_form4(self) -> List[Dict]:
        """SEC EDGAR Form 4 — insider buy/sell filings (last 45 days)."""
        return await self._fetch_edgar(
            forms="4",
            cutoff_days=45,
            cap=15,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "filer": (src.get("display_names") or ["Unknown"])[0],
                "form": "Form 4",
                "period": src.get("period_of_report", ""),
            },
            enrich=self._enrich_form4_directions,
        )

    async def _enrich_form4_directions(self, rows, hits, cik):
        """RQ-1 B3b (#1536): flag-gated. When SPECIALIST_FORM4_DIRECTION_ENABLED, fetch each  # noqa: E501
        Form 4 document + set rows[i]["direction"] (buy/sell/mixed/neutral). Default OFF -> a  # noqa: E501
        no-op (no extra SEC requests; the row keys stay unchanged). Best-effort: any failure  # noqa: E501
        leaves the row without a direction key rather than raising."""
        if not getattr(
            get_config(), "SPECIALIST_FORM4_DIRECTION_ENABLED", False
        ):  # noqa: E501
            return
        if not cik:
            return
        import httpx

        from core.specialist.form4_direction import classify_form4_direction

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                for row, hit in zip(rows, hits):
                    _id = hit.get("_id", "")
                    if ":" not in _id:
                        continue
                    adsh, fname = _id.split(":", 1)
                    url = (
                        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                        f"{adsh.replace('-', '')}/{fname}"
                    )
                    row["direction"] = await classify_form4_direction(
                        client, url
                    )  # noqa: E501
        except Exception as e:  # noqa: BLE001 -- enrichment is best-effort, never fatal
            logger.warning(
                "[%s] form4 direction enrichment failed: %s", self.symbol, e
            )  # noqa: E501

    async def _fetch_edgar_8k(self) -> List[Dict]:
        """SEC EDGAR Form 8-K — material events (last 30 days)."""
        return await self._fetch_edgar(
            forms="8-K",
            cutoff_days=30,
            cap=8,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "description": src.get("period_of_report", ""),
                "entity": (
                    src.get("entity_name", "")
                    or (src.get("display_names") or [""])[0]  # noqa: E501
                )[:80],
            },
        )

    async def _fetch_edgar_13d(self) -> List[Dict]:
        """SEC EDGAR Schedule 13D/G — activist investor or large stake disclosures."""  # noqa: E501
        return await self._fetch_edgar(
            forms="SC+13D,SC+13G",
            cutoff_days=90,
            cap=5,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "filer": (src.get("display_names") or ["Unknown"])[0],
                "form": src.get("form_type", "13D/G"),
            },
        )

    async def _fetch_congressional_trades(self) -> List[Dict]:
        """Quiver Quant — congressional trading disclosures."""
        try:
            import httpx

            url = f"https://api.quiverquant.com/beta/live/congresstrading/{self.symbol}"  # noqa: E501
            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                data = r.json()
                if not isinstance(data, list):
                    return []
                return [
                    {
                        "politician": item.get("Representative", ""),
                        "transaction": item.get("Transaction", ""),
                        "amount": item.get("Amount", ""),
                        "date": item.get("TransactionDate", ""),
                    }
                    for item in data[:5]
                ]
        except Exception as e:
            logger.debug(f"[{self.symbol}] Congressional trades: {e}")
            return []

    async def _fetch_polygon_news(self) -> List[str]:
        """Polygon.io News API — recent headlines (uses existing bot API key).

        Results are cached in Redis for 300s (5 min) to reduce Polygon API calls  # noqa: E501
        and outbound Networking costs.
        """
        if not self._polygon_api_key:
            return []

        # --- Redis cache check (async) ---
        _CACHE_KEY = f"polygon:news:{self.symbol}"
        _CACHE_TTL = 300  # 5 minutes
        try:
            from core.redis_client import RedisClient

            r = await RedisClient.get_redis()
            if r is not None:
                cached = await r.get(_CACHE_KEY)
                if cached is not None:
                    import json as _json

                    logger.debug("[%s] polygon:news cache HIT", self.symbol)
                    return _json.loads(cached)
        except Exception as _e:
            logger.debug(
                "[%s] polygon:news Redis check failed: %s", self.symbol, _e
            )  # noqa: E501

        # --- Live Polygon request ---
        try:
            import httpx

            url = (
                f"https://api.polygon.io/v2/reference/news"
                f"?ticker={self.symbol}&limit=10&apiKey={self._polygon_api_key}"  # noqa: E501
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                r_http = await client.get(url)
                if r_http.status_code != 200:
                    return []
                results = r_http.json().get("results", [])
                headlines = [
                    item.get("title", "")
                    for item in results
                    if item.get("title")  # noqa: E501
                ]

            # --- Populate cache ---
            try:
                import json as _json

                from core.redis_client import RedisClient

                r = await RedisClient.get_redis()
                if r is not None:
                    await r.set(
                        _CACHE_KEY, _json.dumps(headlines), ex=_CACHE_TTL
                    )  # noqa: E501
                    logger.debug(
                        "[%s] polygon:news cached (%ds TTL)",
                        self.symbol,
                        _CACHE_TTL,  # noqa: E501
                    )
            except Exception as _e:
                logger.debug(
                    "[%s] polygon:news Redis set failed: %s", self.symbol, _e
                )  # noqa: E501

            return headlines
        except Exception as e:
            logger.debug(f"[{self.symbol}] Polygon news: {e}")
            return []

    async def _fetch_google_news(self) -> List[str]:
        """Google-News-RSS headlines for ``self.symbol`` (RPAR T3, #1267).

        Mirrors the ``_fetch_polygon_news`` I/O shape (httpx.AsyncClient, parse titles ->  # noqa: E501
        ``List[str]``). Flag-FIRST: when ``SPECIALIST_NEWS_V2`` is OFF this returns ``[]``  # noqa: E501
        WITHOUT any network call (no httpx client constructed), so the OFF path is byte-  # noqa: E501
        neutral. Any error (HTTP, parse, non-200) degrades to ``[]`` logged at WARNING  # noqa: E501
        (CODING_POLICY §5.6 - never DEBUG for a fallback value).
        """
        # Flag-first: no network I/O while dormant.
        if not getattr(get_config(), "SPECIALIST_NEWS_V2", False):
            return []

        try:
            import httpx

            # Google-News RSS search feed for the ticker symbol. URL-encode the
            # symbol so tickers with special chars (e.g. BRK.A, BF-B) cannot
            # corrupt the query string (RPAR T3 review #1312 F-03).
            url = (
                "https://news.google.com/rss/search"
                f"?q={quote(self.symbol)}&hl=en-US&gl=US&ceid=US:en"
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(
                        "[%s] Google news RSS non-200 (%s) -> no Google headlines",  # noqa: E501
                        self.symbol,
                        resp.status_code,
                    )
                    return []
                # RSS <item><title>...</title> - parse titles defensively (no XML deps).  # noqa: E501
                titles = re.findall(
                    r"<item>.*?<title>(.*?)</title>", resp.text, re.DOTALL
                )
                # Unescape HTML entities (&amp;, &#39;, ...) so raw entities don't  # noqa: E501
                # leak into the LLM synthesis prompt (RPAR T3 review #1312 F-01).  # noqa: E501
                headlines = [
                    html.unescape(t.strip()) for t in titles if t and t.strip()
                ]
            return headlines
        except Exception as e:
            logger.warning(
                "[%s] Google news RSS error -> []: %s", self.symbol, e
            )  # noqa: E501
            return []

    async def _fetch_wiki_pageviews(self) -> Dict:
        """Wikipedia pageviews API — detect unusual research interest spikes."""  # noqa: E501
        try:
            import httpx

            # Use company name as title guess (common Wikipedia convention)
            title = self.symbol
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=14)
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")
            url = (
                f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"  # noqa: E501
                f"/en.wikipedia/all-access/all-agents/{title}/daily/{start_str}/{end_str}"  # noqa: E501
            )
            headers = {"User-Agent": "AI-Trading-Bot/1.0 research@aaagents.de"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return {"spike": False, "views_7d": 0}
                items = r.json().get("items", [])
                if len(items) < 7:
                    return {"spike": False, "views_7d": 0}
                views = [item.get("views", 0) for item in items]
                views_7d = sum(views[-7:])
                views_prev = sum(views[:-7]) / max(len(views[:-7]), 1)
                views_recent = views_7d / 7
                spike = views_prev > 0 and (views_recent / views_prev) > 2.5
                return {
                    "spike": spike,
                    "views_7d": views_7d,
                    "recent_avg": views_recent,
                    "prev_avg": views_prev,
                }
        except Exception as e:
            logger.debug(f"[{self.symbol}] Wiki pageviews: {e}")
            return {"spike": False, "views_7d": 0}

    async def _fetch_reddit_mentions(self) -> Dict:
        """Reddit API — mention count and rough sentiment from WSB + r/stocks."""  # noqa: E501
        try:
            import httpx

            headers = {"User-Agent": "AI-Trading-Bot/1.0 research@aaagents.de"}
            # Reddit search API (no auth needed for basic search)
            url = (
                f"https://www.reddit.com/search.json"
                f"?q={self.symbol}&restrict_sr=false&sort=new&t=day&limit=25"
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return {"mentions": 0, "sentiment": "neutral"}
                posts = r.json().get("data", {}).get("children", [])
                # Count posts and score rough sentiment from titles
                count = 0
                pos, neg = 0, 0
                bullish_words = [
                    "buy",
                    "bull",
                    "calls",
                    "moon",
                    "rocket",
                    "breakout",
                    "undervalued",
                ]
                bearish_words = [
                    "sell",
                    "bear",
                    "puts",
                    "crash",
                    "overvalued",
                    "short",
                    "dump",
                ]
                for post in posts:
                    d = post.get("data", {})
                    title = (
                        d.get("title", "") + " " + d.get("selftext", "")
                    ).lower()  # noqa: E501
                    if self.symbol.lower() in title:
                        count += 1
                        pos += sum(1 for w in bullish_words if w in title)
                        neg += sum(1 for w in bearish_words if w in title)
                sentiment = "neutral"
                if count > 0:
                    sentiment = (
                        "bullish"
                        if pos > neg + 1
                        else ("bearish" if neg > pos + 1 else "neutral")
                    )
                return {"mentions": count, "sentiment": sentiment}
        except Exception as e:
            logger.debug(f"[{self.symbol}] Reddit: {e}")
            return {"mentions": 0, "sentiment": "neutral"}

    async def _fetch_finra_short_interest(self) -> Optional[float]:
        """FINRA short interest — detect abnormal short positions."""
        try:
            import httpx

            # FINRA short interest API
            url = (
                f"https://api.finra.org/data/group/OTCmarket/name/otcShortInterest"  # noqa: E501
                f"?compareFilters=compareFilters%5B0%5D.fieldName%3DissueSymbolIdentifier"  # noqa: E501
                f"%26compareFilters%5B0%5D.compareType%3Dequals"
                f"%26compareFilters%5B0%5D.fieldValue%3D{self.symbol}"
                f"&limit=1&sortFields=sortFields%5B0%5D.fieldName%3DsettlementDate"  # noqa: E501
                f"%26sortFields%5B0%5D.sortOrder%3DDESC"
            )
            headers = {"Accept": "application/json"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        short_vol = data[0].get("shortInterestQty", 0)
                        total_vol = data[0].get("totalVolume", 1)
                        if total_vol and total_vol > 0:
                            return round(
                                float(short_vol) / float(total_vol) * 100, 2
                            )  # noqa: E501
            return None
        except Exception as e:
            logger.debug(f"[{self.symbol}] FINRA short interest: {e}")
            return None

    async def _fetch_google_trends(self) -> Optional[float]:
        """Google Trends via pytrends — detect unusual public search interest."""  # noqa: E501
        try:
            import asyncio

            from pytrends.request import TrendReq

            loop = asyncio.get_event_loop()

            def _sync_trends():
                pt = TrendReq(hl="en-US", tz=360, timeout=(4, 8))
                pt.build_payload([self.symbol], timeframe="now 7-d", geo="US")
                df = pt.interest_over_time()
                if df.empty or self.symbol not in df.columns:
                    return None
                vals = df[self.symbol].tolist()
                return float(sum(vals) / len(vals)) if vals else None

            return await loop.run_in_executor(None, _sync_trends)
        except ImportError:
            return None
        except Exception as e:
            logger.debug(f"[{self.symbol}] Google Trends: {e}")
            return None

    # ─────────────────────────────────────────────────────────
    # Gemini synthesis — one cheap text call, NO Search Grounding
    # ─────────────────────────────────────────────────────────

    async def _gemini_synthesize(
        self, gathered: Dict[str, Any]
    ) -> Dict[str, Any]:  # noqa: E501
        """
        Synthesise all gathered raw data via the configured LLM provider (seam).  # noqa: E501

        Plain text generation (no Google Search Grounding). Cost on Gemini:
        ~$0.0004 per call vs $0.035 with Grounding.

        Provider seam (ADR-014, G4a-2c):
          - Ollama (desktop local LLM): synthesise via the provider; NO Gemini
            daily-budget gate (local/free) and no Gemini key required — without
            this branch a keyless desktop returned {} (zero LLM synthesis).
          - Gemini (cloud default): byte-identical to before — key guard +
            daily-budget gate + the own genai.Client in _call_gemini_sync
            (temperature 0.3). The seam's wrapper is deliberately NOT used on
            this path because its decoding params differ (temp 0.7, safety
            settings) and would change the synthesis output.
        """
        provider = get_llm_provider()

        # ── Ollama branch (desktop, local, free): no key, no budget gate ──────  # noqa: E501
        if isinstance(provider, OllamaProvider):
            prompt = self._synthesis_prompt(gathered)
            async with self.get_semaphore():
                try:
                    text = await provider.generate_content_async(
                        prompt, max_output_tokens=800
                    )
                except Exception as e:
                    logger.warning(
                        f"[{self.symbol}] Ollama synthesis error: {e}"
                    )  # noqa: E501
                    return {}
            return {"text": text or ""}

        # ── Gemini branch — byte-identical to pre-G4a-2c ─────────────────────
        if not self._gemini_api_key:
            return {}

        # ADR (G4a-2c): the daily-budget gate is Gemini-ONLY — Ollama above is
        # local and free, so it never checks or increments the Gemini call budget.  # noqa: E501
        # Hard daily call limit — free-tier guard (1M tokens/day free on Gemini 2.5 Flash).  # noqa: E501
        # Returns {} so caller falls back to raw-data scoring without LLM synthesis.  # noqa: E501
        from core.gemini_budget import get_budget

        if not get_budget().check_and_increment():
            logger.warning(
                "[%s] Gemini daily budget exhausted — returning raw-data synthesis only.",  # noqa: E501
                self.symbol,
            )
            return {}

        # Build a structured prompt with all gathered data - built BEFORE the
        # flag branch so `prompt` is in scope for both paths (key-guard +
        # budget-gate above run unchanged in either case).
        prompt = self._synthesis_prompt(gathered)

        # RPAR-T4 (#1268, LLM_OUTPUT_PARITY, dormant default OFF): when ON, route  # noqa: E501
        # the Gemini synthesis through the unified ADR-014 provider seam (the same  # noqa: E501
        # path the bundle + the Ollama branch above use) instead of the hand-rolled  # noqa: E501
        # _call_gemini_sync. The key-guard + daily-budget gate (incl. its increment)  # noqa: E501
        # already ran above, so Free-Tier protection is preserved on this path too.  # noqa: E501
        # OFF reproduces the historical run_in_executor(_call_gemini_sync) byte-for-byte.  # noqa: E501
        if getattr(get_config(), "LLM_OUTPUT_PARITY", False):
            async with self.get_semaphore():
                try:
                    text = await provider.generate_content_async(
                        prompt, max_output_tokens=800
                    )
                except Exception as e:
                    logger.warning(
                        f"[{self.symbol}] Seam synthesis error: {e}",
                        exc_info=True,  # noqa: E501
                    )
                    return {}
            return {"text": text or ""}

        sem = self.get_semaphore()
        async with sem:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, self._call_gemini_sync, prompt
                )  # noqa: E501
            except Exception as e:
                logger.warning(f"[{self.symbol}] Gemini synthesis error: {e}")
                return {}

    def _call_gemini_sync(self, prompt: str) -> Dict[str, Any]:
        """Plain Gemini text call — no Search Grounding tool attached."""
        try:
            from google import genai

            client = genai.Client(api_key=self._gemini_api_key)
            # Use config model name if available
            try:
                import config

                model_name = getattr(
                    config, "GEMINI_MODEL_NAME", "gemini-2.5-flash"
                )  # noqa: E501
            except ImportError:
                model_name = "gemini-2.5-flash"

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=800,  # Cost guard: structured output ~400-700 tokens  # noqa: E501
                    temperature=0.3,  # Lower temp → more deterministic parsing
                ),
                # NO google_search tool — this is the key cost saving
            )
            text = ""
            if response and response.candidates:
                for cand in response.candidates:
                    if cand.content and cand.content.parts:
                        for part in cand.content.parts:
                            if hasattr(part, "text") and part.text:
                                text += part.text
            return {"text": text}
        except Exception as e:
            logger.debug(f"[{self.symbol}] _call_gemini_sync: {e}")
            return {}

    def _build_synthesis_prompt(self, gathered: Dict[str, Any]) -> str:
        """Build a structured synthesis prompt from all gathered data."""
        lines = [
            f"You are a stock research analyst. Analyse {self.symbol} using ONLY the data below.",  # noqa: E501
            "Do NOT use external knowledge or search. Synthesise only what is provided.",  # noqa: E501
            "",
            f"## RAW DATA FOR {self.symbol}",
            "",
        ]

        headlines = gathered.get("recent_headlines", [])
        if headlines:
            lines.append("### Recent Headlines")
            for h in headlines[:6]:
                lines.append(f"- {h}")
            lines.append("")

        insider = gathered.get("insider_trades", [])
        if insider:
            lines.append(f"### Insider Trades ({len(insider)} filings)")
            for t in insider[:5]:
                lines.append(
                    f"- {t.get('filed', '')} | {t.get('filer', '')} | {t.get('form', '')}"  # noqa: E501
                    + (
                        f" | {t['direction'].upper()}" if t.get("direction") else ""
                    )  # noqa: E501
                )
            lines.append("")

        events = gathered.get("material_events", [])
        if events:
            lines.append(f"### Material Events / 8-K Filings ({len(events)})")
            for e in events[:3]:
                lines.append(f"- {e.get('filed', '')} | {e.get('entity', '')}")
            lines.append("")

        activists = gathered.get("activist_stakes", [])
        if activists:
            lines.append(
                f"### Activist/Large Investor Disclosures ({len(activists)} 13D/G filings)"  # noqa: E501
            )
            for a in activists[:3]:
                lines.append(
                    f"- {a.get('filed', '')} | {a.get('filer', '')} | {a.get('form', '')}"  # noqa: E501
                )
            lines.append("")

        political = gathered.get("political_trades", [])
        if political:
            lines.append(
                f"### Congressional Trading ({len(political)} transactions)"
            )  # noqa: E501
            for p in political[:3]:
                lines.append(
                    f"- {p.get('date', '')} | {p.get('politician', '')} | {p.get('transaction', '')} "  # noqa: E501
                    f"| {p.get('amount', '')}"
                )
            lines.append("")

        reddit_mentions = gathered.get("reddit_mentions_24h", 0)
        reddit_sent = gathered.get("reddit_sentiment", "neutral")
        if reddit_mentions > 0:
            lines.append("### Social Signal")
            lines.append(
                f"- Reddit mentions (24h): {reddit_mentions} | Sentiment: {reddit_sent}"  # noqa: E501
            )
            lines.append("")

        wiki_spike = gathered.get("wiki_spike", False)
        wiki_views = gathered.get("wiki_views_7d", 0)
        if wiki_spike or wiki_views > 1000:
            lines.append("### Alternative Data")
            if wiki_spike:
                lines.append(
                    f"- Wikipedia: SPIKE detected (views 7d: {wiki_views:,}) — unusual research interest"  # noqa: E501
                )
            short_pct = gathered.get("short_interest_pct")
            if short_pct is not None:
                lines.append(f"- Short interest: {short_pct:.1f}% of volume")
            google_score = gathered.get("google_trend_score")
            if google_score is not None:
                lines.append(
                    f"- Google Trends score (7d): {google_score:.0f}/100"
                )  # noqa: E501
            lines.append("")

        lines += [
            "## YOUR TASK",
            "Based ONLY on the data above:",
            "1. Write a 2-sentence news/event summary.",
            "2. Write a 1-sentence alternative signal summary (insider activity, political trades, social signals).",  # noqa: E501
            "3. Give an overall outlook: bullish / neutral / bearish.",
            "4. Give a sentiment score 0-100 (50=neutral; >50 bullish, <50 bearish).",  # noqa: E501
            "   Judge DIRECTION and substance, NOT the NUMBER of filings: insider Form 4s",  # noqa: E501
            "   may be sells and 13D/8-K filings may be neutral or negative -- presence of a",  # noqa: E501
            "   filing is not itself bullish.",
            "5. List up to 3 key reasons (one line each).",
            "",
            "Format your response EXACTLY as:",
            "SUMMARY: <2 sentences>",
            "SIGNALS: <1 sentence>",
            "OUTLOOK: <bullish|neutral|bearish>",
            "SCORE: <0-100>",
            "REASONS:",
            "- <reason 1>",
            "- <reason 2>",
            "- <reason 3>",
        ]
        return "\n".join(lines)

    def _synthesis_prompt(self, gathered: Dict[str, Any]) -> str:
        """Flag-gated synthesis prompt (RPAR T1, dormant - default OFF).

        ``SPECIALIST_PROMPT_V2`` OFF -> the legacy ``_build_synthesis_prompt``
        (byte-identical to today). ON -> the V2 prompt (``core.specialist.prompt``)  # noqa: E501
        which additionally asks for COMPANY/BULL/BEAR/THESIS prose. This is one of  # noqa: E501
        the THREE sites the flag switches atomically (both prompt-build sites +
        the parser in ``_build_report``); a V2 prompt is never paired with the V1  # noqa: E501
        parser.
        """
        if getattr(get_config(), "SPECIALIST_PROMPT_V2", False):
            from core.specialist.prompt import build_prompt_v2

            return build_prompt_v2(self.symbol, gathered)
        return self._build_synthesis_prompt(gathered)

    # ─────────────────────────────────────────────────────────
    # Report builder
    # ─────────────────────────────────────────────────────────

    def _build_report(
        self,
        gathered: Dict[str, Any],
        synthesis: Dict[str, Any],
        integrity: "Optional[DataIntegrityResult]" = None,
    ) -> SpecialistReport:
        """Parse Gemini synthesis output and raw data into a SpecialistReport.

        ``integrity`` is the RPAR T6a data-integrity guard result (or ``None`` when  # noqa: E501
        the guard is disabled). It only feeds the DISPLAY-ONLY ``data_quality`` /  # noqa: E501
        ``degraded`` fields below - never the score / recommendation / reasons.
        """
        text = synthesis.get("text", "")
        # RPAR T1 (dormant): the parser is gated TOGETHER with the prompt sites -  # noqa: E501
        # SPECIALIST_PROMPT_V2 ON => V2 prompt AND V2 parser (never mixed). OFF =>  # noqa: E501
        # the legacy 6-tuple parser, byte-identical to today; prose stays empty.  # noqa: E501
        company_summary_v2 = ""
        bull_case_v2 = ""
        bear_case_v2 = ""
        investment_thesis_v2 = ""
        if getattr(get_config(), "SPECIALIST_PROMPT_V2", False):
            from core.specialist.parser import parse_synthesis_v2

            parsed = parse_synthesis_v2(text)
            news_summary = parsed.news_summary
            alt_signals = parsed.alternative_signals
            recommendation = parsed.recommendation
            sentiment_score = parsed.sentiment_score
            confidence = parsed.confidence
            reasons = list(parsed.reasons)
            company_summary_v2 = parsed.company_summary
            bull_case_v2 = parsed.bull_case
            bear_case_v2 = parsed.bear_case
            investment_thesis_v2 = parsed.investment_thesis
        else:
            (
                news_summary,
                alt_signals,
                recommendation,
                sentiment_score,
                confidence,
                reasons,
            ) = self._parse_synthesis(text)

        insider = gathered.get("insider_trades", [])
        political = gathered.get("political_trades", [])
        events = gathered.get("material_events", [])
        activists = gathered.get("activist_stakes", [])

        # RQ-1 B3 (#1523): the +4/+5 filing-COUNT bonuses are REMOVED (supersedes A3's  # noqa: E501
        # flag-gating). Raw filing counts must not inflate the score -- a Form 4 may be a  # noqa: E501
        # sell and a 13D may be neutral; real buy/sell direction needs the filing documents  # noqa: E501
        # (deferred follow-up). The score is now the LLM's content-based SCORE on the  # noqa: E501
        # entity-correct, recent inputs (A1/A2/B1) + only the directional short-interest  # noqa: E501
        # penalty below. (The legacy SPECIALIST_COUNT_BONUS_ENABLED config entry is now dead  # noqa: E501
        # + ignored -- removed from the code path here.)
        if political:
            reasons.append(
                f"Congressional trading: {len(political)} transaction(s)"
            )  # noqa: E501
        if gathered.get("wiki_spike"):
            reasons.append(
                "Wikipedia research spike — unusual public interest"
            )  # noqa: E501
        reddit_mentions = gathered.get("reddit_mentions_24h", 0)
        if reddit_mentions >= _REDDIT_BUZZ_MIN:
            reasons.append(
                f"Reddit buzz: {reddit_mentions} mentions in 24h ({gathered.get('reddit_sentiment', 'neutral')})"  # noqa: E501
            )
        short_pct = gathered.get("short_interest_pct")
        if short_pct is not None and short_pct > _HIGH_SHORT_PCT:
            sentiment_score = max(0, sentiment_score - 5)
            reasons.append(f"High short interest: {short_pct:.1f}%")

        # Fusion (two-flag, dormant): TFT↔LLM convergence. The ml_* fields are populated  # noqa: E501
        # from gathered["ml_prediction"] on the SpecialistReport below regardless (what the  # noqa: E501
        # dormant Shadow-TFT-Vote reads); the sentiment BLEND that changes the decision is  # noqa: E501
        # gated behind ML_SENTIMENT_BLEND_ENABLED (default False) — validate-before-activate.  # noqa: E501
        ml_pred = gathered.get("ml_prediction")
        blend_applied = ml_pred is not None and getattr(
            get_config(), "ML_SENTIMENT_BLEND_ENABLED", False
        )
        if blend_applied:
            sentiment_score = _blend_ml_sentiment(
                ml_pred, sentiment_score, self.symbol
            )  # noqa: E501

        # Escalation logic
        escalate = False
        escalate_reason = ""
        if sentiment_score >= 82:
            escalate = True
            escalate_reason = (
                f"Very high sentiment ({sentiment_score:.0f}/100)"  # noqa: E501
            )
        elif len(insider) >= 4:
            escalate = True
            escalate_reason = (
                f"Heavy insider activity ({len(insider)} filings)"  # noqa: E501
            )
        elif activists:
            escalate = True
            escalate_reason = f"Activist investor disclosure ({activists[0].get('filer', 'Unknown')})"  # noqa: E501
        elif len(political) >= 2:
            escalate = True
            escalate_reason = (
                f"Multiple congressional trades ({len(political)})"  # noqa: E501
            )
        elif gathered.get("wiki_spike") and reddit_mentions >= 3:
            escalate = True
            escalate_reason = "Cross-signal spike: Wikipedia + Reddit activity"

        # RQ-1 B4 (#1524): confidence reflects DATA QUALITY, not the LLM score's extremity.  # noqa: E501
        # With the guard on (B5), integrity.data_quality in [0, 1] is how much real data backs  # noqa: E501
        # the report -- a thin-data report is low-confidence even on an extreme score. Guard  # noqa: E501
        # off / integrity None -> the parsed score-based confidence stands (backward-compat).  # noqa: E501
        if integrity is not None:
            confidence = round(0.25 + 0.55 * integrity.data_quality, 2)

        # RQ-1 A4 (#1520): if the cycle gathered NO substantive data (no filings, news, or  # noqa: E501
        # alt-signals) the score/recommendation is an ungrounded guess -> abstain (hold +  # noqa: E501
        # capped confidence) instead of a confident BUY/SELL next to "overview unavailable".  # noqa: E501
        # (B4 #1524 derives confidence from data_quality fully; escalate stays as computed.)  # noqa: E501
        has_signal = bool(
            insider
            or events
            or activists
            or political
            or gathered.get("recent_headlines")
            or gathered.get("wiki_spike")
            or reddit_mentions
            or gathered.get("short_interest_pct") is not None
            or gathered.get("google_trend_score") is not None
        )
        if not has_signal:
            recommendation = "hold"
            confidence = min(confidence, 0.3)
            if "Insufficient data this cycle - abstaining" not in reasons:
                reasons.insert(0, "Insufficient data this cycle - abstaining")

        # RQ-1 B4 (#1524): a DEGRADED report (guard data_quality at/below threshold, B5) cannot  # noqa: E501
        # present a confident directional call -> clamp to hold + low confidence even if some  # noqa: E501
        # signal exists. The MiFID-relevant "no confident BUY on low-quality data" gate; it  # noqa: E501
        # generalises the empty-cycle abstention above. Decision-relevant -> human sign-off.  # noqa: E501
        if integrity is not None and integrity.degraded:
            recommendation = "hold"
            confidence = min(confidence, 0.3)
            if not any("degraded" in r.lower() for r in reasons):
                reasons.insert(
                    0, "Data degraded -- low-quality/insufficient sources"
                )  # noqa: E501

        # RPAR T2 (#1264) - deterministic, LLM-free card fields (flag-gated, dormant).  # noqa: E501
        # The score/recommendation/reasons above are now FULLY computed; the card  # noqa: E501
        # helpers only DERIVE from the same already-gathered signals and NEVER write  # noqa: E501
        # back (FINDINGS NEWS-8). With SPECIALIST_CARDS_ENABLED OFF (default) the  # noqa: E501
        # fields keep their V0 defaults so the serialized DTO is byte-identical.  # noqa: E501
        pros: List[str] = []
        cons: List[str] = []
        summary: str = ""
        headlines: List[Dict[str, Any]] = []
        if getattr(get_config(), "SPECIALIST_CARDS_ENABLED", False):
            pros, cons = build_pros_cons(gathered)
            summary = build_summary(
                news_summary=news_summary,
                alt_signals=alt_signals,
                recommendation=recommendation,
                sentiment_score=round(min(100, max(0, sentiment_score)), 1),
                existing_summary="",
            )
            headlines = select_headlines(gathered)

        return SpecialistReport(
            symbol=self.symbol,
            updated_at=datetime.now(timezone.utc),
            news_summary=news_summary,
            company_summary=company_summary_v2,
            alternative_signals=alt_signals,
            insider_trades=insider,
            political_trades=political,
            material_events=events,
            activist_stakes=activists,
            reddit_mentions=reddit_mentions,
            wiki_spike=gathered.get("wiki_spike", False),
            short_interest_pct=gathered.get("short_interest_pct"),
            google_trend_score=gathered.get("google_trend_score"),
            sentiment_score=round(min(100, max(0, sentiment_score)), 1),
            recommendation=recommendation,
            confidence=round(confidence, 2),
            reasons=reasons[:5],
            escalate=escalate,
            escalate_reason=escalate_reason,
            ml_direction=(ml_pred or {}).get("direction", "unavailable"),
            ml_confidence=(ml_pred or {}).get("confidence"),
            ml_base_return_pct=(ml_pred or {}).get("base_return_pct"),
            ml_bear_return_pct=(ml_pred or {}).get("bear_return_pct"),
            ml_bull_return_pct=(ml_pred or {}).get("bull_return_pct"),
            forecast_vol=(ml_pred or {}).get("forecast_vol"),
            # RPAR T6a (display-only). P0-1: 0.0 is a legitimate data_quality, so use an  # noqa: E501
            # object-presence ternary - NEVER an `or`-default. integrity is None when the  # noqa: E501
            # guard is OFF -> exactly the V0 schema defaults (1.0 / False).
            data_quality=(integrity.data_quality if integrity else 1.0),
            degraded=(integrity.degraded if integrity else False),
            # RPAR T5 (#1269): signal_quality is the ONLY producer (grep-verified). It reflects  # noqa: E501
            # whether the ML<->LLM blend actually changed the score - "llm_only" at flag-OFF  # noqa: E501
            # (default) == V0 default == byte-identical; "llm_plus_ml" only when blend_applied.  # noqa: E501
            signal_quality=("llm_plus_ml" if blend_applied else "llm_only"),
            # Forward-compat passthrough: today's prediction dict carries none of these keys, so  # noqa: E501
            # .get() returns None/[] = byte-identical to the current serializer. They flow through  # noqa: E501
            # automatically once the model-provisioning epic adds them. P0-1: use .get() (defaults  # noqa: E501
            # to None), NEVER `or` - a walkforward_ic of 0.0 must pass through as real 0.0.  # noqa: E501
            walkforward_ic=(ml_pred or {}).get("walkforward_ic"),
            walkforward_sharpe=(ml_pred or {}).get("walkforward_sharpe"),
            ml_attention_features=(ml_pred or {}).get("ml_attention_features")
            or [],  # noqa: E501
            # RPAR T1 prose (empty unless SPECIALIST_PROMPT_V2 is ON).
            bull_case=bull_case_v2,
            bear_case=bear_case_v2,
            investment_thesis=investment_thesis_v2,
            pros=pros,
            cons=cons,
            summary=summary,
            headlines=headlines,
        )

    def _parse_synthesis(self, text: str):
        """Parse the structured Gemini response into typed fields."""
        if not text:
            return (
                "",
                "",
                "hold",
                50.0,
                0.3,
                ["Insufficient data for analysis"],
            )  # noqa: E501

        news_summary = ""
        alt_signals = ""
        recommendation: Literal["buy", "hold", "sell"] = "hold"
        sentiment_score = 50.0
        confidence = 0.4
        reasons = []

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("SUMMARY:"):
                news_summary = line[8:].strip()
            elif line.startswith("SIGNALS:"):
                alt_signals = line[8:].strip()
            elif line.startswith("OUTLOOK:"):
                raw = line[8:].strip().lower()
                if "bullish" in raw:
                    recommendation = "buy"
                elif "bearish" in raw:
                    recommendation = "sell"
                else:
                    recommendation = "hold"
            elif line.startswith("SCORE:"):
                try:
                    val = float(re.search(r"[\d.]+", line[6:]).group())
                    sentiment_score = max(0.0, min(100.0, val))
                except Exception:
                    pass
            elif line.startswith("- ") and len(reasons) < 3:
                reasons.append(line[2:].strip()[:120])

        # Align recommendation with score if Gemini didn't match
        if sentiment_score >= 70 and recommendation == "hold":
            recommendation = "buy"
        elif sentiment_score <= 35 and recommendation == "hold":
            recommendation = "sell"

        confidence = min(0.9, 0.3 + abs(sentiment_score - 50) / 100)
        if not reasons:
            reasons = [f"Gemini score: {sentiment_score:.0f}/100"]

        return (
            news_summary,
            alt_signals,
            recommendation,
            sentiment_score,
            confidence,
            reasons,
        )
