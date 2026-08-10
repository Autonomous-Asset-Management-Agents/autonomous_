"""
Tests for robust float extraction from LLM responses in NewsSentimentAgent.

Background: LLM responses (Gemini, Ollama) often wrap the requested float in
prose ("The sentiment score is 0.72") or trailing newlines/commentary. The
prior `float(raw)` parse raised ValueError on anything but a bare number and
silently fell back to neutral 0.5 — losing every signal except clean output.

Re.search-based extractor accepts the first standalone 0/1 or 0.x/1.0/0.0
token, falling back to 0.5 only when no float is present at all.

Ported from feat/live-trading-activation @ 4af1b06 (2026-04-09).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest


def _make_state(symbol="AAPL"):
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 1_000_000.0,
        },
    }


@pytest.fixture(autouse=True)
def _clear_local_sentiment_cache():
    """The process-local sentiment cache is module-level → clear it around every test so a cached
    score from one test cannot leak into the next."""
    from core.round_table.agents import _LOCAL_SENTIMENT_CACHE

    _LOCAL_SENTIMENT_CACHE.clear()
    yield
    _LOCAL_SENTIMENT_CACHE.clear()


# ---------------------------------------------------------------------------
# RTR-1 (#1948) — NLP branch behind NEWS_SENTIMENT_NLP_ENABLED (default OFF).
# Plan §8: R6 flag-off regression · R7 flag-on NLP score · R8 no headlines →
# transparent abstain + WARNING · R9 cache key hangs on the headline content hash.
# ---------------------------------------------------------------------------


def _nlp_headlines(titles=("AAPL beats Q3 estimates",)):
    return [
        {
            "title": t,
            "published_utc": "2026-07-22T12:00:00+00:00",
            "source": "reuters",
        }
        for t in titles
    ]


def _arm_nlp(monkeypatch, enabled: bool, max_headlines: int = 8) -> None:
    """Pin the flag via config.get_config — the finance-core's only config seam.

    CI-Prevention R2 (ac375ee8 pattern): patch ALL import paths the consumer
    could resolve get_config through — `config`, `ai_trading_bot.config` and
    the consumer module itself (raising=False: agents.py does `import config`,
    so no module-level get_config attribute may exist there).
    """
    from types import SimpleNamespace

    import config

    cfg_obj = SimpleNamespace(
        NEWS_SENTIMENT_NLP_ENABLED=enabled,
        NEWS_SENTIMENT_MAX_HEADLINES=max_headlines,
        NEWS_SENTIMENT_MODEL="finbert",
    )
    monkeypatch.setattr(config, "get_config", lambda: cfg_obj)
    try:
        import ai_trading_bot.config as bot_cfg

        monkeypatch.setattr(bot_cfg, "get_config", lambda: cfg_obj)
    except Exception:
        pass
    try:
        import core.round_table.agents as agents_mod

        monkeypatch.setattr(agents_mod, "get_config", lambda: cfg_obj, raising=False)
    except Exception:
        pass


def _sentiment_result(score=0.71):
    from core.nlp.news_sentiment import SentimentResult

    return SentimentResult(
        score=score,
        details=[
            {
                "title": "AAPL beats Q3 estimates",
                "source": "reuters",
                "label": "pos",
                "prob": 0.9,
            }
        ],
        backend="FinBERT",
    )


class TestNewsSentimentNlpBranch:
    """#1948 — three-way branch: off=LLM path (byte-identical), on=NLP, gaps=abstain."""

    @pytest.mark.anyio
    async def test_r6_flag_off_abstains(self, monkeypatch):
        """R6: flag OFF (default) + headlines PRESENT in state → the legacy LLM prior
        is dropped, agent abstains (weight=0.0)."""
        import core.nlp.news_sentiment as ns
        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=False)
        scorer = MagicMock()
        monkeypatch.setattr(ns, "score_headlines", scorer)
        agent = NewsSentimentAgent()
        state = {**_make_state(), "news_headlines": _nlp_headlines()}
        result = await agent.vote(state)
        assert result.score == 0.5
        assert result.weight == 0.0
        assert "dormant" in result.reasoning
        scorer.assert_not_called()

    @pytest.mark.anyio
    async def test_r7_flag_on_scores_from_nlp_not_llm(self, monkeypatch):
        """R7: flag ON + headlines → score from the NLP scorer at full weight 0.35;
        reasoning names backend + headline + source + label (audit moat); the LLM
        is NEVER called."""
        import core.nlp.news_sentiment as ns
        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=True)
        scorer = MagicMock(return_value=_sentiment_result(0.71))
        monkeypatch.setattr(ns, "score_headlines", scorer)
        agent = NewsSentimentAgent()
        state = {**_make_state(), "news_headlines": _nlp_headlines()}
        result = await agent.vote(state)
        assert abs(result.score - 0.71) < 1e-9
        assert abs(result.weight - 0.35) < 1e-9
        assert "FinBERT" in result.reasoning
        assert "AAPL beats Q3 estimates" in result.reasoning
        assert "reuters" in result.reasoning
        assert "pos" in result.reasoning
        scorer.assert_called_once()

    @pytest.mark.anyio
    async def test_r8_flag_on_no_headlines_abstains_with_warning(
        self, monkeypatch, caplog
    ):
        """R8: flag ON + no/empty headlines → weight 0.0 (excluded from consensus)
        and a WARNING (§5.6 — fail-transparent, RT-BUG-5 #1971 stays healed)."""
        import logging

        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=True)
        agent = NewsSentimentAgent()
        for headlines in (None, []):
            state = {**_make_state(), "news_headlines": headlines}
            with caplog.at_level(logging.WARNING):
                result = await agent.vote(state)
            assert result.score == 0.5
            assert result.weight == 0.0
            assert any(r.levelno == logging.WARNING for r in caplog.records)

    @pytest.mark.anyio
    async def test_r8b_flag_on_scorer_unavailable_abstains(self, monkeypatch, caplog):
        """Flag ON + headlines but NO NLP backend (scorer returns None) → transparent
        abstain (w=0) + WARNING — never a silent guess."""
        import logging

        import core.nlp.news_sentiment as ns
        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=True)
        monkeypatch.setattr(ns, "score_headlines", MagicMock(return_value=None))
        agent = NewsSentimentAgent()
        state = {**_make_state(), "news_headlines": _nlp_headlines()}
        with caplog.at_level(logging.WARNING):
            result = await agent.vote(state)
        assert result.weight == 0.0
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    @pytest.mark.anyio
    async def test_r9_cache_key_hangs_on_headline_content_hash(self, monkeypatch):
        """R9: same symbol, CHANGED headlines ⇒ re-score (no stale hit); same
        symbol, SAME headlines ⇒ served from cache (one scorer call)."""
        import core.nlp.news_sentiment as ns
        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=True)
        scorer = MagicMock(return_value=_sentiment_result(0.71))
        monkeypatch.setattr(ns, "score_headlines", scorer)
        agent = NewsSentimentAgent()
        state_a = {**_make_state("NVDA"), "news_headlines": _nlp_headlines(("A",))}
        state_b = {**_make_state("NVDA"), "news_headlines": _nlp_headlines(("B",))}
        await agent.vote(state_a)
        await agent.vote(state_b)
        assert scorer.call_count == 2, "changed headlines must invalidate the cache"
        r = await agent.vote(state_a)
        assert scorer.call_count == 2, "identical headlines must hit the cache"
        assert abs(r.score - 0.71) < 1e-9
        assert abs(r.weight - 0.35) < 1e-9

    @pytest.mark.anyio
    async def test_r9b_max_headlines_cap_applied(self, monkeypatch):
        """NEWS_SENTIMENT_MAX_HEADLINES caps what reaches the scorer."""
        import core.nlp.news_sentiment as ns
        from core.round_table.agents import NewsSentimentAgent

        _arm_nlp(monkeypatch, enabled=True, max_headlines=2)
        scorer = MagicMock(return_value=_sentiment_result(0.6))
        monkeypatch.setattr(ns, "score_headlines", scorer)
        agent = NewsSentimentAgent()
        state = {
            **_make_state(),
            "news_headlines": _nlp_headlines(("A", "B", "C", "D")),
        }
        await agent.vote(state)
        passed = scorer.call_args[0][0]
        assert len(passed) == 2
