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


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestNewsSentimentFloatParsing:
    """Robust float extraction from LLM responses."""

    @pytest.mark.anyio
    async def test_clean_float_parsed(self):
        """LLM returns clean '0.7' → score=0.7"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="0.7")
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert abs(result.score - 0.7) < 0.001

    @pytest.mark.anyio
    async def test_process_local_cache_skips_llm_without_redis(self):
        """Desktop has no Redis → the Redis cache is a no-op → the LLM was re-run for every symbol
        every cycle (the calls serialize on a local CPU model → ~14s/cycle). A process-local cache
        must serve a repeat vote for the same symbol WITHOUT a second LLM call."""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="0.63")
        with patch(
            "core.round_table.agents.get_llm_provider", return_value=mock_llm
        ), patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=None)
        ):
            r1 = await agent.vote(_make_state("NVDA"))
            r2 = await agent.vote(_make_state("NVDA"))
        assert abs(r1.score - 0.63) < 1e-6
        assert abs(r2.score - 0.63) < 1e-6
        assert (
            mock_llm.generate_content_async.await_count == 1
        ), "2nd vote for the same symbol must hit the process-local cache, not re-run the LLM"

    @pytest.mark.anyio
    async def test_local_cache_purges_expired_entries_on_write(self):
        """The process-local cache must stay bounded: an expired entry is purged when a new score is
        written (addresses the unbounded-growth review finding)."""
        from core.round_table import agents as _agents

        # Seed an already-expired entry (expiry far in the past on the monotonic clock).
        _agents._LOCAL_SENTIMENT_CACHE["STALE"] = (0.9, 0.0)
        agent = _agents.NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="0.63")
        with patch(
            "core.round_table.agents.get_llm_provider", return_value=mock_llm
        ), patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=None)
        ):
            await agent.vote(_make_state("NVDA"))
        assert (
            "STALE" not in _agents._LOCAL_SENTIMENT_CACHE
        ), "expired entry must be purged on write"
        assert "NVDA" in _agents._LOCAL_SENTIMENT_CACHE

    @pytest.mark.anyio
    async def test_prose_response_extracts_float(self):
        """LLM returns 'The sentiment score is 0.72' → score=0.72"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(
            return_value="The sentiment score is 0.72"
        )
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert abs(result.score - 0.72) < 0.001

    @pytest.mark.anyio
    async def test_newline_response_extracts_float(self):
        """LLM returns '0.85\\n\\nBullish momentum.' → score=0.85"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(
            return_value="0.85\n\nBullish momentum."
        )
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert abs(result.score - 0.85) < 0.001

    @pytest.mark.anyio
    async def test_unparseable_falls_back_to_neutral(self):
        """LLM returns 'I cannot determine sentiment.' → score=0.5 (no float at all)"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(
            return_value="I cannot determine sentiment."
        )
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert result.score == 0.5
        assert result.weight == 0.0, "Unparseable response must be excluded (weight=0)"

    @pytest.mark.anyio
    async def test_comma_locale_decimal_extracts_correctly(self):
        """LLM returns German-locale '0,7' → score=0.7 (comma normalised to dot)"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="0,7")
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert abs(result.score - 0.7) < 0.001
        assert result.weight > 0.0

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "raw_response",
        [
            "2.0",  # out-of-range high — must NOT clamp to 1.0 with weight
            "5.0",  # nonsense large value
            "-0.5",  # negative — out-of-range low
            "1.5",  # slightly out-of-range
            "42.5",  # multi-digit, no valid 0..1 substring
            "999",  # large integer
        ],
    )
    async def test_out_of_range_excluded_from_consensus(self, raw_response):
        """
        Hallucinating LLM returns a numeric value outside [0.0, 1.0].
        Must fall through to neutral 0.5 with weight=0 — NOT a clamped
        max-bullish/max-bearish vote at full weight (which would let a
        broken LLM dominate the consensus).
        """
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value=raw_response)
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert (
            result.score == 0.5
        ), f"Out-of-range {raw_response!r} must yield neutral, got {result.score}"
        assert result.weight == 0.0, (
            f"Out-of-range {raw_response!r} must be excluded from consensus "
            f"(weight=0), got weight={result.weight}"
        )

    @pytest.mark.anyio
    async def test_multi_float_picks_first(self):
        """LLM returns 'score: 0.7, confidence 0.95' → score=0.7 (first match)"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(
            return_value="score: 0.7, confidence 0.95"
        )
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert abs(result.score - 0.7) < 0.001
        assert result.weight > 0.0

    @pytest.mark.anyio
    async def test_clean_zero_extracts_zero(self):
        """LLM returns clean '0.0' → score=0.0, weighted (very bearish)"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="0.0")
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert result.score == 0.0
        assert result.weight > 0.0

    @pytest.mark.anyio
    async def test_clean_one_extracts_one(self):
        """LLM returns clean '1.0' → score=1.0, weighted (very bullish)"""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_llm = MagicMock()
        mock_llm.generate_content_async = AsyncMock(return_value="1.0")
        with patch("core.round_table.agents.get_llm_provider", return_value=mock_llm):
            result = await agent.vote(_make_state())
        assert result.score == 1.0
        assert result.weight > 0.0
