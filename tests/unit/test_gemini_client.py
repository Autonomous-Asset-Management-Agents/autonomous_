# tests/unit/test_gemini_client.py
# TDD-First: Tests written BEFORE core/gemini_client.py exists.
# These tests define the contract of the new gemini_client module.
# Epic 1.7 / PR-A

from unittest.mock import MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Module-level import (will fail = RED until gemini_client.py is created)
# ---------------------------------------------------------------------------
from core.gemini_client import (
    GeminiModelWrapper,
    _reply_indicates_insufficient,
    answer_chat_with_fallback,
    answer_trading_chat,
    answer_with_gemini_general,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_wrapper():
    """A GeminiModelWrapper whose generate_content is mocked."""
    with patch("core.gemini_client.new_genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = MagicMock(
            text="mocked reply"
        )
        wrapper = GeminiModelWrapper("gemini-2.0-flash")
        wrapper._client = mock_client
    return wrapper


# ---------------------------------------------------------------------------
# GeminiModelWrapper
# ---------------------------------------------------------------------------
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestGeminiModelWrapper:
    def test_init_creates_client(self):
        with patch("core.gemini_client.new_genai") as mock_genai:
            mock_genai.Client.return_value = MagicMock()
            w = GeminiModelWrapper("gemini-2.0-flash")
            assert w.model_name == "gemini-2.0-flash"
            mock_genai.Client.assert_called_once()

    def test_generate_content_returns_text(self, mock_wrapper):
        mock_wrapper._client.models.generate_content.return_value = MagicMock(
            text="hello"
        )
        result = mock_wrapper.generate_content("test prompt")
        assert result == "hello"

    @pytest.mark.anyio
    async def test_generate_content_async_returns_text(self):
        with patch("core.gemini_client.new_genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            # async mock
            import asyncio

            future = asyncio.Future()
            future.set_result(MagicMock(text="async reply"))
            mock_client.aio.models.generate_content = MagicMock(return_value=future)
            w = GeminiModelWrapper("gemini-2.0-flash")
            w._client = mock_client
            result = await w.generate_content_async("any prompt")
            assert result == "async reply"


# ---------------------------------------------------------------------------
# _reply_indicates_insufficient
# ---------------------------------------------------------------------------
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestReplyIndicatesInsufficient:
    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "  ",
            "I don't have enough context",
            "The data doesn't contain this info",
            "I would need more information",
            "Cannot answer with given data",
        ],
    )
    def test_insufficient_replies(self, text):
        assert _reply_indicates_insufficient(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "The portfolio is up 3% today.",
            "AAPL is currently the top performer.",
            "Based on RSI 14 the signal is bullish.",
        ],
    )
    def test_sufficient_replies(self, text):
        assert _reply_indicates_insufficient(text) is False


# ---------------------------------------------------------------------------
# answer_trading_chat
# ---------------------------------------------------------------------------
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestAnswerTradingChat:
    def test_returns_none_when_no_model(self):
        with patch("core.gemini_client.get_gemini_instance", return_value=None):
            result = answer_trading_chat("some context", "what is my pnl?")
            assert result is None

    def test_returns_none_when_context_empty(self):
        mock_model = MagicMock()
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_trading_chat("   ", "question?")
            assert result is None

    def test_returns_answer_when_model_available(self):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = "Portfolio is up."
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_trading_chat("context data", "how are we doing?")
            assert result == "Portfolio is up."

    def test_returns_none_on_rate_limit_429(self):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED")
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_trading_chat("context", "question")
            assert result is None

    def test_returns_none_on_generic_error(self):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("Unexpected error")
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_trading_chat("context", "question")
            assert result is None


# ---------------------------------------------------------------------------
# answer_with_gemini_general
# ---------------------------------------------------------------------------
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestAnswerWithGeminiGeneral:
    def test_returns_none_when_no_model(self):
        with patch("core.gemini_client.get_gemini_instance", return_value=None):
            result = answer_with_gemini_general("What is the S&P 500?")
            assert result is None

    def test_returns_answer(self):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = "The S&P 500 is an index."
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_with_gemini_general("What is the S&P 500?")
            assert "S&P 500" in result

    def test_returns_none_on_error(self):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("API error")
        with patch("core.gemini_client.get_gemini_instance", return_value=mock_model):
            result = answer_with_gemini_general("question")
            assert result is None


# ---------------------------------------------------------------------------
# answer_chat_with_fallback
# ---------------------------------------------------------------------------
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestAnswerChatWithFallback:
    def test_uses_trading_context_when_sufficient(self):
        with (
            patch(
                "core.gemini_client.answer_trading_chat", return_value="Direct answer."
            ),
            patch("core.gemini_client.answer_with_gemini_general") as mock_general,
        ):
            result = answer_chat_with_fallback("ctx", "question")
            assert result == "Direct answer."
            mock_general.assert_not_called()

    def test_falls_back_to_general_when_insufficient(self):
        with (
            patch(
                "core.gemini_client.answer_trading_chat",
                return_value="I don't have that info.",
            ),
            patch(
                "core.gemini_client.answer_with_gemini_general",
                return_value="General answer.",
            ),
        ):
            result = answer_chat_with_fallback("ctx", "question")
            assert result == "General answer."

    def test_returns_fallback_string_when_both_fail(self):
        with (
            patch("core.gemini_client.answer_trading_chat", return_value=None),
            patch("core.gemini_client.answer_with_gemini_general", return_value=None),
        ):
            result = answer_chat_with_fallback("ctx", "question")
            assert isinstance(result, str)
            assert len(result) > 0
