# core/round_table/agenten/news_sentiment.py
# #4086 (ARC-E6 G-6d): NewsSentimentAgent samt Konfigurationshelfern, Gewicht und lokalem
# Sentiment-Cache, unveraendert aus core/round_table/agents.py umgezogen. agents.py importiert
# jeden Namen zurueck. Der Agent liest keine geteilte Abhaengigkeit aus agents (Zugriffsregel,
# agenten/). _LOCAL_SENTIMENT_CACHE wird nie neu gebunden — agents._LOCAL_SENTIMENT_CACHE ist
# dasselbe Dict.
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Tuple

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agenten._basis import (
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


def _news_sentiment_cfg() -> Tuple[bool, int, str]:
    """RTR-1 (#1948) — (enabled, max_headlines, model) for the NLP news path.

    Default OFF ⇒ NewsSentimentAgent runs today's LLM-/abstain-path byte-identical.
    ON ⇒ the agent scores REAL point-in-time headlines (state["news_headlines"])
    with a local NLP backend (core/nlp/news_sentiment.py: FinBERT, else vendored
    VADER lexicon) instead of the headline-less LLM symbol guess. The os.environ
    reads live in config.py/config.oss.py (CODING_POLICY §2.10), not here.
    Invalid values fall back to the dormant default.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "NEWS_SENTIMENT_NLP_ENABLED", False))
        max_headlines = int(getattr(cfg, "NEWS_SENTIMENT_MAX_HEADLINES", 8) or 8)
        # #3907: fallback = the schema default (settings.py), never a second truth.
        model = str(
            getattr(cfg, "NEWS_SENTIMENT_MODEL", "fin-distilroberta")
            or "fin-distilroberta"
        )
        return enabled, max_headlines, model
    except (TypeError, ValueError):
        return False, 8, "fin-distilroberta"


def _news_sentiment_model_dir() -> str:
    """#3907 — directory of the shipped news model (desktop bundle), "" = HF cache."""
    try:
        cfg = config.get_config()
        return str(getattr(cfg, "NEWS_SENTIMENT_MODEL_DIR", "") or "")
    except (TypeError, ValueError):
        return ""


# #3154 (UXC-1 S1): NewsSentiment was hardcoded 0.35 (Review B3) and now gets the same
# seam as the other directional voters. Unset env ⇒ byte-identical defaults.
_NEWS_SENTIMENT_WEIGHT = _consensus_weight("NEWS_SENTIMENT_WEIGHT", 0.20)  # #3698


# ---------------------------------------------------------------------------
# 8. NewsSentimentAgent (w:0.35) — Gemini-Flash (Fallback: 0.5)
# ---------------------------------------------------------------------------


# Process-local sentiment cache (symbol -> (score, expiry_monotonic)). Fallback for the Redis cache
# inside NewsSentimentAgent.vote() when Redis is absent (desktop / no-Redis): without it the round
# table re-runs the LLM for every symbol every cycle (the calls serialize on a local CPU model →
# ~14s/cycle). BOUNDED: expired entries are purged on write and the dict is capped at MAXSIZE (the
# live universe is small — ≤ S&P 500), so it cannot grow without limit. An autouse fixture clears it.
_LOCAL_SENTIMENT_CACHE: "dict[str, tuple[float, float]]" = {}
_LOCAL_SENTIMENT_CACHE_MAXSIZE = 512


class NewsSentimentAgent(VotingAgent):
    """
    Nutzt Gemini-Flash für News-Sentiment-Analyse (nicht-preis-basiert).
    Bekämpft Echo-Chamber-Risiko durch externe Informationsquelle.

    Fallback: 0.5 wenn Gemini nicht erreichbar (kein API-Key in CI).
    Non-blocking: generate_content_async wird innerhalb des vote()-Calls awaited.
    """

    default_weight: float = _NEWS_SENTIMENT_WEIGHT  # #3154: Env-Naht (Default 0.35)
    min_weight: float = 0.10
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("NEWS_SENTIMENT_AGENT_ENABLED"):
            return _disabled_abstain("NewsSentimentAgent", symbol)

        # --- RTR-1 (#1948): real NLP headline sentiment behind
        # NEWS_SENTIMENT_NLP_ENABLED (default ON). OFF -> the LLM-/abstain-path
        # below runs byte-identical to today. ON ⇒ score point-in-time headlines
        # (state["news_headlines"], free Google-News-RSS producer) with a local
        # NLP backend instead of the headline-less LLM symbol guess. ---
        _nlp_enabled, _nlp_max, _nlp_model = _news_sentiment_cfg()
        if _nlp_enabled:
            return await self._vote_nlp(state, symbol, _nlp_max, _nlp_model)

        return SignalCandidate(
            agent_name="NewsSentimentAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.DISABLED,
            reasoning=(
                "NewsSentimentAgent is dormant (NEWS_SENTIMENT_NLP_ENABLED=False). "
                "The legacy ticker-only LLM prior was dropped."
            ),
        )

    async def _vote_nlp(
        self,
        state: "SymbolEvalState",
        symbol: str,
        max_headlines: int,
        model: str,
    ) -> SignalCandidate:
        """RTR-1 (#1948): score REAL point-in-time headlines with a local NLP model.

        Three outcomes (plan §5.3, all fail-transparent — WARNING never DEBUG §5.6):
          * headlines + backend → NLP score at full weight; reasoning names the top
            headlines + source + label (MiFID audit moat / Senate log).
          * no headlines → weight 0.0 (excluded from consensus, RT-BUG-5 #1971
            posture) + WARNING.
          * no NLP backend/signal → weight 0.0 + WARNING (never a silent guess).
        The scorer runs in a thread (FinBERT forward is blocking CPU work — the
        AsyncAIAgent asyncio.to_thread rule).
        """
        headlines = list(state.get("news_headlines") or [])[:max_headlines]
        if not headlines:
            logger.warning(
                "NewsSentimentAgent[NLP]: no point-in-time headlines for %s — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
            )
            return SignalCandidate(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,  # ← EXCLUDED: Pydantic gt=0.0 → aus active_votes entfernt
                reasoning=(
                    "NewsSentiment(NLP): no point-in-time headlines → "
                    "vote EXCLUDED (w=0)"
                ),
            )

        # Content-hash cache key (plan §5.3/R9): the NLP score depends on the
        # HEADLINE SET, so the LLM path's symbol-only key would serve a stale
        # score after the news flow changes. Same bounded local cache + 5-min
        # TTL posture as the LLM path (_LOCAL_SENTIMENT_CACHE / _CACHE_TTL).
        import hashlib

        _titles = "\n".join(
            str(h.get("title", "")) if isinstance(h, dict) else str(h)
            for h in headlines
        )
        _key = (
            f"{symbol}|nlp|{hashlib.sha256(_titles.encode('utf-8')).hexdigest()[:16]}"
        )
        _cached = _LOCAL_SENTIMENT_CACHE.get(_key)
        if _cached is not None and _cached[1] > time.monotonic():
            return SignalCandidate(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=_cached[0],
                weight=self.weight,
                reasoning=f"NewsSentiment(NLP): local cache → score={_cached[0]:.3f}",
            )

        import asyncio

        from core.nlp import news_sentiment as _news_nlp

        try:
            result = await asyncio.to_thread(
                _news_nlp.score_headlines,
                headlines,
                model,
                model_dir=_news_sentiment_model_dir(),
            )
        except Exception as exc:  # noqa: BLE001 — a scorer crash must not kill the vote
            logger.warning(
                "NewsSentimentAgent[NLP]: scorer failed for %s (%s: %.120s) — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
                type(exc).__name__,
                exc,
            )
            result = None
        if result is None:
            logger.warning(
                "NewsSentimentAgent[NLP]: no local NLP backend/signal for %s — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
            )
            return SignalCandidate(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning=(
                    "NewsSentiment(NLP): no local NLP backend/signal → "
                    "vote EXCLUDED (w=0)"
                ),
            )

        score = self._clamp(float(result.score))
        # Audit moat (plan §5.3): name the strongest headlines + source + label.
        _parts = " | ".join(
            f"'{d['title'][:60]}' ({d['source']},{d['label']})"
            for d in result.details[:3]
        )
        reasoning = f"NewsSentiment({result.backend}): {score:.3f} | {_parts}"

        # Bounded cache write (purge expired + cap — the LLM path's idiom).
        _now = time.monotonic()
        for _expired in [
            _k for _k, (_, _exp) in _LOCAL_SENTIMENT_CACHE.items() if _exp <= _now
        ]:
            _LOCAL_SENTIMENT_CACHE.pop(_expired, None)
        if len(_LOCAL_SENTIMENT_CACHE) >= _LOCAL_SENTIMENT_CACHE_MAXSIZE:
            _soonest = min(
                _LOCAL_SENTIMENT_CACHE,
                key=lambda _k: _LOCAL_SENTIMENT_CACHE[_k][1],
            )
            _LOCAL_SENTIMENT_CACHE.pop(_soonest, None)
        _LOCAL_SENTIMENT_CACHE[_key] = (score, _now + 300)  # 300s = LLM _CACHE_TTL

        return SignalCandidate(
            agent_name="NewsSentimentAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
