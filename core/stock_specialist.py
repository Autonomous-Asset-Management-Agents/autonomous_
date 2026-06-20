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

Cost: ~$0.0004 per research cycle vs $0.035 with Search Grounding = 99% cheaper.
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
from core.llm.provider import OllamaProvider, get_llm_provider  # ADR-014 seam (G4a-2c)
from core.specialist.cards import (  # RPAR T2 (#1264): flag-gated card fields
    _HIGH_SHORT_PCT,
    _INSIDER_CLUSTER_MIN,
    _REDDIT_BUZZ_MIN,
    build_pros_cons,
    build_summary,
    select_headlines,
)
from core.specialist.news import merge_headlines  # RPAR T3 (#1267): headline merge

logger = logging.getLogger(__name__)

# Fusion: lazy singleton for the canonical bar source + the TFT↔LLM convergence blend.
# Both are only reached on the flag-gated _fetch_ml_prediction / blend paths.
_DATA_PROVIDER = None


def _get_data_provider():
    """Lazy HistoricalDataProvider singleton (Alpaca + Databento + GCS cache)."""
    global _DATA_PROVIDER
    if _DATA_PROVIDER is None:
        from core.data_provider import HistoricalDataProvider

        _DATA_PROVIDER = HistoricalDataProvider()
    return _DATA_PROVIDER


def _blend_ml_sentiment(ml_pred: dict, llm_score: float, symbol: str) -> float:
    """TFT↔LLM convergence blend (the caller gates it behind ML_SENTIMENT_BLEND_ENABLED).
    Returns the blended sentiment, or the unchanged ``llm_score`` on any error (logged at
    WARNING — never silent). Mirrors the prototype's P3-B convergence math."""
    try:
        cfg = get_config()
        sat = float(getattr(cfg, "SPECIALIST_ML_SATURATION_PCT", 2.0))
        # Guard the `/ (2.0 * sat)` divisor below: a misconfigured saturation of
        # 0 (or negative) would otherwise raise ZeroDivisionError mid-blend. Fail
        # loud + clean here so the except-fallback logs a descriptive WARNING and
        # returns the unchanged llm_score (RPAR T5 review #1316 F-02).
        if sat <= 0.0:
            raise ValueError(f"SPECIALIST_ML_SATURATION_PCT must be > 0 (got {sat})")
        hi = float(getattr(cfg, "SPECIALIST_ML_LLM_AGREEMENT_HIGH", 0.75))
        md = float(getattr(cfg, "SPECIALIST_ML_LLM_AGREEMENT_MID", 0.50))
        conv_ml = float(getattr(cfg, "SPECIALIST_BLEND_CONVERGED_ML_W", 0.55))
        conv_llm = float(getattr(cfg, "SPECIALIST_BLEND_CONVERGED_LLM_W", 0.45))
        part_ml = float(getattr(cfg, "SPECIALIST_BLEND_PARTIAL_ML_W", 0.40))
        part_llm = float(getattr(cfg, "SPECIALIST_BLEND_PARTIAL_LLM_W", 0.60))
        div_shrink = float(getattr(cfg, "SPECIALIST_BLEND_DIVERGED_SHRINK", 0.30))
        ml_score = max(
            0.0, min(100.0, (ml_pred["base_return_pct"] + sat) / (2.0 * sat) * 100.0)
        )
        agreement = 1.0 - abs(ml_score - llm_score) / 100.0
        if agreement >= hi:
            return ml_score * conv_ml + llm_score * conv_llm
        if agreement >= md:
            return ml_score * part_ml + llm_score * part_llm
        return 50.0 + (llm_score - 50.0) * div_shrink
    except Exception as exc:
        logger.warning("[%s] convergence blend fell back to llm_only: %s", symbol, exc)
        return llm_score


# RPAR T3 (#1267): pure Google+Polygon headline merge (flag SPECIALIST_NEWS_V2, default OFF).

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


class StockSpecialistAgent:
    """
    Autonomous research agent for a single stock symbol.

    Gathers intelligence from 10 free data sources in parallel, then
    uses one cheap Gemini text call (no Search Grounding) to synthesize
    everything into a structured SpecialistReport.
    """

    # Shared semaphore: cap concurrent Gemini synthesis calls across all instances
    _gemini_semaphore: Optional[asyncio.Semaphore] = None

    @classmethod
    def get_semaphore(cls) -> asyncio.Semaphore:
        if cls._gemini_semaphore is None:
            cls._gemini_semaphore = asyncio.Semaphore(5)
        return cls._gemini_semaphore

    def __init__(self, symbol: str, gemini_api_key: str, polygon_api_key: str = ""):
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

        # RPAR T3 (#1267, dormant default OFF): merge Google-News-RSS headlines with the
        # Polygon headlines. Flag-FIRST: when SPECIALIST_NEWS_V2 is OFF the Google fetcher is
        # never reached and ``merged`` is exactly today's ``polygon_news`` -> recent_headlines
        # below is byte-identical. The merge only changes the NEWS *inputs* to the LLM
        # synthesis, not the deterministic _build_report scoring math (NEWS-8).
        if getattr(get_config(), "SPECIALIST_NEWS_V2", False):
            google_news = await self._fetch_google_news()
            merged_headlines = merge_headlines(polygon_news, google_news, cap=10)
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

        # RPAR T6a (dormant, default OFF): data-integrity guard. When enabled it
        # derives the display-only data_quality/degraded fields from `gathered`; on a
        # hard data failure it signals skip_llm, in which case we reuse the EXISTING
        # V0-default synthesis ({} -> _parse_synthesis("") -> hold/50.0) instead of
        # calling the LLM. The guard NEVER changes the decision (score/recommendation/
        # reasons are produced unchanged in _build_report).
        integrity = None
        if getattr(get_config(), "DATA_INTEGRITY_GUARD_ENABLED", False):
            from core.data_integrity import assess

            integrity = assess(gathered)

        if integrity is not None and integrity.skip_llm:
            logger.warning(
                "[%s] data-integrity hard fail -> skip-LLM (degraded)", self.symbol
            )
            synthesis = {}
        else:
            synthesis = await self._gemini_synthesize(gathered)

        # Build final report
        report = self._build_report(gathered, synthesis, integrity=integrity)
        self._last_report = report
        self._last_refresh = datetime.now(timezone.utc)
        return report

    # ─────────────────────────────────────────────────────────
    # Data gatherers — all free, no LLM
    # ─────────────────────────────────────────────────────────

    async def _fetch_ml_prediction(self) -> Optional[Dict[str, Any]]:
        """Fusion (flag-gated): the per-symbol TFT prediction. **Flag-FIRST** — returns
        None before any data I/O unless ``ML_PREDICTION_ENABLED``. Bars come from the
        canonical ``data_provider`` (sync → ``asyncio.to_thread``); FeatureBuilder +
        model_registry do the rest. Any failure → None (logged at WARNING). The order path
        never depends on this; it only populates the report's ``ml_*`` fields."""
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
            # Local, not self.* — returned in the dict so concurrent research() calls on
            # the same agent can't race on a shared instance attribute (review fix).
            forecast_vol = None
            try:
                from core.ml.vol_model import forecast_forward_vol

                forecast_vol = forecast_forward_vol(bars_df)
            except Exception as exc:
                logger.warning("[ML] %s: forecast_vol failed: %s", self.symbol, exc)

            from core.ml.feature_builder import FeatureBuilder

            features_df = FeatureBuilder().build(bars_df, symbol=self.symbol)
            if features_df is None or features_df.empty or len(features_df) < 60:
                return None

            from core.ml.model_registry import model_registry

            prediction = await model_registry.get_or_train(self.symbol, features_df)
            if prediction is None:
                return None
            # attention_weights is intentionally NOT returned — it is a non-scalar and must
            # never reach the §5.9/BORA-scalar-only state["ml"]; nothing on main reads it.
            return {
                "direction": prediction.direction,
                "bear_return_pct": prediction.bear_return_pct,
                "base_return_pct": prediction.base_return_pct,
                "bull_return_pct": prediction.bull_return_pct,
                "confidence": prediction.confidence,
                "forecast_vol": forecast_vol,
            }
        except Exception as exc:
            logger.warning("[ML] prediction failed for %s: %s", self.symbol, exc)
            return None

    async def _fetch_edgar_form4(self) -> List[Dict]:
        """SEC EDGAR Form 4 — insider buy/sell filings (last 45 days)."""
        try:
            import httpx

            cutoff = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
            url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{self.symbol}%22"
                f"&forms=4&dateRange=custom&startdt={cutoff}"
            )
            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                hits = r.json().get("hits", {}).get("hits", [])
                trades = []
                for hit in hits[:15]:
                    src = hit.get("_source", {})
                    trades.append(
                        {
                            "filed": src.get("file_date", ""),
                            "filer": (src.get("display_names") or ["Unknown"])[0],
                            "form": "Form 4",
                            "period": src.get("period_of_report", ""),
                        }
                    )
                return trades
        except Exception as e:
            logger.debug(f"[{self.symbol}] EDGAR Form4: {e}")
            return []

    async def _fetch_edgar_8k(self) -> List[Dict]:
        """SEC EDGAR Form 8-K — material events (last 30 days)."""
        try:
            import httpx

            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{self.symbol}%22"
                f"&forms=8-K&dateRange=custom&startdt={cutoff}"
            )
            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                hits = r.json().get("hits", {}).get("hits", [])
                events = []
                for hit in hits[:8]:
                    src = hit.get("_source", {})
                    events.append(
                        {
                            "filed": src.get("file_date", ""),
                            "description": src.get("period_of_report", ""),
                            "entity": (
                                src.get("entity_name", "")
                                or src.get("display_names", [""])[0]
                            )[:80],
                        }
                    )
                return events
        except Exception as e:
            logger.debug(f"[{self.symbol}] EDGAR 8-K: {e}")
            return []

    async def _fetch_edgar_13d(self) -> List[Dict]:
        """SEC EDGAR Schedule 13D/G — activist investor or large stake disclosures."""
        try:
            import httpx

            cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{self.symbol}%22"
                f"&forms=SC+13D,SC+13G&dateRange=custom&startdt={cutoff}"
            )
            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                hits = r.json().get("hits", {}).get("hits", [])
                stakes = []
                for hit in hits[:5]:
                    src = hit.get("_source", {})
                    stakes.append(
                        {
                            "filed": src.get("file_date", ""),
                            "filer": (src.get("display_names") or ["Unknown"])[0],
                            "form": src.get("form_type", "13D/G"),
                        }
                    )
                return stakes
        except Exception as e:
            logger.debug(f"[{self.symbol}] EDGAR 13D: {e}")
            return []

    async def _fetch_congressional_trades(self) -> List[Dict]:
        """Quiver Quant — congressional trading disclosures."""
        try:
            import httpx

            url = f"https://api.quiverquant.com/beta/live/congresstrading/{self.symbol}"
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

        Results are cached in Redis for 300s (5 min) to reduce Polygon API calls
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
            logger.debug("[%s] polygon:news Redis check failed: %s", self.symbol, _e)

        # --- Live Polygon request ---
        try:
            import httpx

            url = (
                f"https://api.polygon.io/v2/reference/news"
                f"?ticker={self.symbol}&limit=10&apiKey={self._polygon_api_key}"
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                r_http = await client.get(url)
                if r_http.status_code != 200:
                    return []
                results = r_http.json().get("results", [])
                headlines = [
                    item.get("title", "") for item in results if item.get("title")
                ]

            # --- Populate cache ---
            try:
                import json as _json

                from core.redis_client import RedisClient

                r = await RedisClient.get_redis()
                if r is not None:
                    await r.set(_CACHE_KEY, _json.dumps(headlines), ex=_CACHE_TTL)
                    logger.debug(
                        "[%s] polygon:news cached (%ds TTL)", self.symbol, _CACHE_TTL
                    )
            except Exception as _e:
                logger.debug("[%s] polygon:news Redis set failed: %s", self.symbol, _e)

            return headlines
        except Exception as e:
            logger.debug(f"[{self.symbol}] Polygon news: {e}")
            return []

    async def _fetch_google_news(self) -> List[str]:
        """Google-News-RSS headlines for ``self.symbol`` (RPAR T3, #1267).

        Mirrors the ``_fetch_polygon_news`` I/O shape (httpx.AsyncClient, parse titles ->
        ``List[str]``). Flag-FIRST: when ``SPECIALIST_NEWS_V2`` is OFF this returns ``[]``
        WITHOUT any network call (no httpx client constructed), so the OFF path is byte-
        neutral. Any error (HTTP, parse, non-200) degrades to ``[]`` logged at WARNING
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
                        "[%s] Google news RSS non-200 (%s) -> no Google headlines",
                        self.symbol,
                        resp.status_code,
                    )
                    return []
                # RSS <item><title>...</title> - parse titles defensively (no XML deps).
                titles = re.findall(
                    r"<item>.*?<title>(.*?)</title>", resp.text, re.DOTALL
                )
                # Unescape HTML entities (&amp;, &#39;, ...) so raw entities don't
                # leak into the LLM synthesis prompt (RPAR T3 review #1312 F-01).
                headlines = [
                    html.unescape(t.strip()) for t in titles if t and t.strip()
                ]
            return headlines
        except Exception as e:
            logger.warning("[%s] Google news RSS error -> []: %s", self.symbol, e)
            return []

    async def _fetch_wiki_pageviews(self) -> Dict:
        """Wikipedia pageviews API — detect unusual research interest spikes."""
        try:
            import httpx

            # Use company name as title guess (common Wikipedia convention)
            title = self.symbol
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=14)
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")
            url = (
                f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
                f"/en.wikipedia/all-access/all-agents/{title}/daily/{start_str}/{end_str}"
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
        """Reddit API — mention count and rough sentiment from WSB + r/stocks."""
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
                    title = (d.get("title", "") + " " + d.get("selftext", "")).lower()
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
                f"https://api.finra.org/data/group/OTCmarket/name/otcShortInterest"
                f"?compareFilters=compareFilters%5B0%5D.fieldName%3DissueSymbolIdentifier"
                f"%26compareFilters%5B0%5D.compareType%3Dequals"
                f"%26compareFilters%5B0%5D.fieldValue%3D{self.symbol}"
                f"&limit=1&sortFields=sortFields%5B0%5D.fieldName%3DsettlementDate"
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
                            return round(float(short_vol) / float(total_vol) * 100, 2)
            return None
        except Exception as e:
            logger.debug(f"[{self.symbol}] FINRA short interest: {e}")
            return None

    async def _fetch_google_trends(self) -> Optional[float]:
        """Google Trends via pytrends — detect unusual public search interest."""
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

    async def _gemini_synthesize(self, gathered: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesise all gathered raw data via the configured LLM provider (seam).

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

        # ── Ollama branch (desktop, local, free): no key, no budget gate ──────
        if isinstance(provider, OllamaProvider):
            prompt = self._synthesis_prompt(gathered)
            async with self.get_semaphore():
                try:
                    text = await provider.generate_content_async(
                        prompt, max_output_tokens=800
                    )
                except Exception as e:
                    logger.warning(f"[{self.symbol}] Ollama synthesis error: {e}")
                    return {}
            return {"text": text or ""}

        # ── Gemini branch — byte-identical to pre-G4a-2c ─────────────────────
        if not self._gemini_api_key:
            return {}

        # ADR (G4a-2c): the daily-budget gate is Gemini-ONLY — Ollama above is
        # local and free, so it never checks or increments the Gemini call budget.
        # Hard daily call limit — free-tier guard (1M tokens/day free on Gemini 2.5 Flash).
        # Returns {} so caller falls back to raw-data scoring without LLM synthesis.
        from core.gemini_budget import get_budget

        if not get_budget().check_and_increment():
            logger.warning(
                "[%s] Gemini daily budget exhausted — returning raw-data synthesis only.",
                self.symbol,
            )
            return {}

        # Build a structured prompt with all gathered data - built BEFORE the
        # flag branch so `prompt` is in scope for both paths (key-guard +
        # budget-gate above run unchanged in either case).
        prompt = self._synthesis_prompt(gathered)

        # RPAR-T4 (#1268, LLM_OUTPUT_PARITY, dormant default OFF): when ON, route
        # the Gemini synthesis through the unified ADR-014 provider seam (the same
        # path the bundle + the Ollama branch above use) instead of the hand-rolled
        # _call_gemini_sync. The key-guard + daily-budget gate (incl. its increment)
        # already ran above, so Free-Tier protection is preserved on this path too.
        # OFF reproduces the historical run_in_executor(_call_gemini_sync) byte-for-byte.
        if getattr(get_config(), "LLM_OUTPUT_PARITY", False):
            async with self.get_semaphore():
                try:
                    text = await provider.generate_content_async(
                        prompt, max_output_tokens=800
                    )
                except Exception as e:
                    logger.warning(
                        f"[{self.symbol}] Seam synthesis error: {e}", exc_info=True
                    )
                    return {}
            return {"text": text or ""}

        sem = self.get_semaphore()
        async with sem:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, self._call_gemini_sync, prompt)
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

                model_name = getattr(config, "GEMINI_MODEL_NAME", "gemini-2.5-flash")
            except ImportError:
                model_name = "gemini-2.5-flash"

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=800,  # Cost guard: structured output ~400-700 tokens
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
            f"You are a stock research analyst. Analyse {self.symbol} using ONLY the data below.",
            "Do NOT use external knowledge or search. Synthesise only what is provided.",
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
                    f"- {t.get('filed', '')} | {t.get('filer', '')} | {t.get('form', '')}"
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
                f"### Activist/Large Investor Disclosures ({len(activists)} 13D/G filings)"
            )
            for a in activists[:3]:
                lines.append(
                    f"- {a.get('filed', '')} | {a.get('filer', '')} | {a.get('form', '')}"
                )
            lines.append("")

        political = gathered.get("political_trades", [])
        if political:
            lines.append(f"### Congressional Trading ({len(political)} transactions)")
            for p in political[:3]:
                lines.append(
                    f"- {p.get('date', '')} | {p.get('politician', '')} | {p.get('transaction', '')} "
                    f"| {p.get('amount', '')}"
                )
            lines.append("")

        reddit_mentions = gathered.get("reddit_mentions_24h", 0)
        reddit_sent = gathered.get("reddit_sentiment", "neutral")
        if reddit_mentions > 0:
            lines.append("### Social Signal")
            lines.append(
                f"- Reddit mentions (24h): {reddit_mentions} | Sentiment: {reddit_sent}"
            )
            lines.append("")

        wiki_spike = gathered.get("wiki_spike", False)
        wiki_views = gathered.get("wiki_views_7d", 0)
        if wiki_spike or wiki_views > 1000:
            lines.append("### Alternative Data")
            if wiki_spike:
                lines.append(
                    f"- Wikipedia: SPIKE detected (views 7d: {wiki_views:,}) — unusual research interest"
                )
            short_pct = gathered.get("short_interest_pct")
            if short_pct is not None:
                lines.append(f"- Short interest: {short_pct:.1f}% of volume")
            google_score = gathered.get("google_trend_score")
            if google_score is not None:
                lines.append(f"- Google Trends score (7d): {google_score:.0f}/100")
            lines.append("")

        lines += [
            "## YOUR TASK",
            "Based ONLY on the data above:",
            "1. Write a 2-sentence news/event summary.",
            "2. Write a 1-sentence alternative signal summary (insider activity, political trades, social signals).",
            "3. Give an overall outlook: bullish / neutral / bearish.",
            "4. Give a sentiment score 0-100 (50=neutral, 75+=bullish, 25-=bearish).",
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
        (byte-identical to today). ON -> the V2 prompt (``core.specialist.prompt``)
        which additionally asks for COMPANY/BULL/BEAR/THESIS prose. This is one of
        the THREE sites the flag switches atomically (both prompt-build sites +
        the parser in ``_build_report``); a V2 prompt is never paired with the V1
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

        ``integrity`` is the RPAR T6a data-integrity guard result (or ``None`` when
        the guard is disabled). It only feeds the DISPLAY-ONLY ``data_quality`` /
        ``degraded`` fields below - never the score / recommendation / reasons.
        """
        text = synthesis.get("text", "")
        # RPAR T1 (dormant): the parser is gated TOGETHER with the prompt sites -
        # SPECIALIST_PROMPT_V2 ON => V2 prompt AND V2 parser (never mixed). OFF =>
        # the legacy 6-tuple parser, byte-identical to today; prose stays empty.
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

        # Bonus signals that push score up/down even if Gemini had no data.
        # Thresholds are single-sourced from core/specialist/cards.py (RPAR T2
        # review #1310 F-03) so the scoring block and the card bullets can never
        # silently drift apart.
        if len(insider) >= _INSIDER_CLUSTER_MIN:
            sentiment_score = min(100, sentiment_score + 4)
            reasons.append(f"Cluster insider activity: {len(insider)} Form 4 filings")
        if activists:
            sentiment_score = min(100, sentiment_score + 5)
            reasons.append(
                f"Activist/large investor filing detected ({len(activists)} 13D/G)"
            )
        if political:
            reasons.append(f"Congressional trading: {len(political)} transaction(s)")
        if gathered.get("wiki_spike"):
            reasons.append("Wikipedia research spike — unusual public interest")
        reddit_mentions = gathered.get("reddit_mentions_24h", 0)
        if reddit_mentions >= _REDDIT_BUZZ_MIN:
            reasons.append(
                f"Reddit buzz: {reddit_mentions} mentions in 24h ({gathered.get('reddit_sentiment', 'neutral')})"
            )
        short_pct = gathered.get("short_interest_pct")
        if short_pct is not None and short_pct > _HIGH_SHORT_PCT:
            sentiment_score = max(0, sentiment_score - 5)
            reasons.append(f"High short interest: {short_pct:.1f}%")

        # Fusion (two-flag, dormant): TFT↔LLM convergence. The ml_* fields are populated
        # from gathered["ml_prediction"] on the SpecialistReport below regardless (what the
        # dormant Shadow-TFT-Vote reads); the sentiment BLEND that changes the decision is
        # gated behind ML_SENTIMENT_BLEND_ENABLED (default False) — validate-before-activate.
        ml_pred = gathered.get("ml_prediction")
        blend_applied = ml_pred is not None and getattr(
            get_config(), "ML_SENTIMENT_BLEND_ENABLED", False
        )
        if blend_applied:
            sentiment_score = _blend_ml_sentiment(ml_pred, sentiment_score, self.symbol)

        # Escalation logic
        escalate = False
        escalate_reason = ""
        if sentiment_score >= 82:
            escalate = True
            escalate_reason = f"Very high sentiment ({sentiment_score:.0f}/100)"
        elif len(insider) >= 4:
            escalate = True
            escalate_reason = f"Heavy insider activity ({len(insider)} filings)"
        elif activists:
            escalate = True
            escalate_reason = (
                f"Activist investor disclosure ({activists[0].get('filer', 'Unknown')})"
            )
        elif len(political) >= 2:
            escalate = True
            escalate_reason = f"Multiple congressional trades ({len(political)})"
        elif gathered.get("wiki_spike") and reddit_mentions >= 3:
            escalate = True
            escalate_reason = "Cross-signal spike: Wikipedia + Reddit activity"

        # RPAR T2 (#1264) - deterministic, LLM-free card fields (flag-gated, dormant).
        # The score/recommendation/reasons above are now FULLY computed; the card
        # helpers only DERIVE from the same already-gathered signals and NEVER write
        # back (FINDINGS NEWS-8). With SPECIALIST_CARDS_ENABLED OFF (default) the
        # fields keep their V0 defaults so the serialized DTO is byte-identical.
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
            # RPAR T6a (display-only). P0-1: 0.0 is a legitimate data_quality, so use an
            # object-presence ternary - NEVER an `or`-default. integrity is None when the
            # guard is OFF -> exactly the V0 schema defaults (1.0 / False).
            data_quality=(integrity.data_quality if integrity else 1.0),
            degraded=(integrity.degraded if integrity else False),
            # RPAR T5 (#1269): signal_quality is the ONLY producer (grep-verified). It reflects
            # whether the ML<->LLM blend actually changed the score - "llm_only" at flag-OFF
            # (default) == V0 default == byte-identical; "llm_plus_ml" only when blend_applied.
            signal_quality=("llm_plus_ml" if blend_applied else "llm_only"),
            # Forward-compat passthrough: today's prediction dict carries none of these keys, so
            # .get() returns None/[] = byte-identical to the current serializer. They flow through
            # automatically once the model-provisioning epic adds them. P0-1: use .get() (defaults
            # to None), NEVER `or` - a walkforward_ic of 0.0 must pass through as real 0.0.
            walkforward_ic=(ml_pred or {}).get("walkforward_ic"),
            walkforward_sharpe=(ml_pred or {}).get("walkforward_sharpe"),
            ml_attention_features=(ml_pred or {}).get("ml_attention_features") or [],
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
            return "", "", "hold", 50.0, 0.3, ["Insufficient data for analysis"]

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
