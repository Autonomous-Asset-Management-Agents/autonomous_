# tests/unit/test_news_processor.py
# TDD-First: Tests written BEFORE core/news_processor.py exists.
# Defines the contract of NewsProcessor extracted from ai_components.py.
# Epic 1.7 / PR-A

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import pytest

# RED: will fail until core/news_processor.py is created
from core.news_processor import NewsProcessor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def processor_no_model():
    """NewsProcessor with no LLM provider available — uses neutral fallback."""
    with patch("core.news_processor.get_llm_provider", return_value=None):
        yield NewsProcessor()


@pytest.fixture()
def processor_with_model():
    """NewsProcessor with a mocked Gemini model."""
    mock_model = MagicMock()
    with patch("core.gemini_client._gemini_instance", mock_model):
        proc = NewsProcessor()
    proc.sentiment_model = mock_model
    return proc, mock_model


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestNewsProcessorInit:
    def test_initializes_without_error(self, processor_no_model):
        assert processor_no_model is not None

    def test_starts_with_empty_cache(self, processor_no_model):
        assert len(processor_no_model.sentiment_cache) == 0

    def test_simulation_mode_off_by_default(self, processor_no_model):
        assert processor_no_model._is_simulation is False


# ---------------------------------------------------------------------------
# set_simulation_mode
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestSetSimulationMode:
    def test_enables_simulation_mode(self, processor_no_model):
        processor_no_model.set_simulation_mode(True)
        assert processor_no_model._is_simulation is True

    def test_disables_simulation_mode(self, processor_no_model):
        processor_no_model.set_simulation_mode(True)
        processor_no_model.set_simulation_mode(False)
        assert processor_no_model._is_simulation is False


# ---------------------------------------------------------------------------
# analyze_sentiments_batch — no model available
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAnalyzeSentimentsBatchNoModel:
    def test_returns_neutral_for_all_headlines(self, processor_no_model):
        results = processor_no_model.analyze_sentiments_batch(
            ["AAPL hits new high", "Fed raises rates"]
        )
        for r in results.values():
            assert r["sentiment"] == "neutral"
            assert r["score"] == 0.0

    def test_returns_neutral_in_simulation_mode(self, processor_no_model):
        processor_no_model.set_simulation_mode(True)
        results = processor_no_model.analyze_sentiments_batch(["Some headline"])
        for r in results.values():
            assert r["sentiment"] == "neutral"

    def test_empty_headlines_returns_empty_dict(self, processor_no_model):
        results = processor_no_model.analyze_sentiments_batch([])
        assert results == {}

    def test_uses_cache_on_second_call(self, processor_no_model):
        # Manually seed cache
        processor_no_model.sentiment_cache["cached headline"] = {
            "sentiment": "positive",
            "score": 0.8,
            "reason": "test",
        }
        results = processor_no_model.analyze_sentiments_batch(["cached headline"])
        assert results["cached headline"]["sentiment"] == "positive"


# ---------------------------------------------------------------------------
# analyze_sentiments_batch — with model (mocked Gemini response)
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAnalyzeSentimentsBatchWithModel:
    def test_parses_valid_gemini_response(self, processor_with_model):
        proc, mock_model = processor_with_model
        mock_model.generate_content.return_value = (
            '[{"headline": "AAPL rallies", "sentiment": "positive", '
            '"score": 0.9, "reason": "strong earnings"}]'
        )
        results = proc.analyze_sentiments_batch(["AAPL rallies"])
        assert results["AAPL rallies"]["sentiment"] == "positive"
        assert results["AAPL rallies"]["score"] == pytest.approx(0.9)

    def test_handles_malformed_json_gracefully(self, processor_with_model):
        proc, mock_model = processor_with_model
        mock_model.generate_content.return_value = "this is not json"
        results = proc.analyze_sentiments_batch(["some headline"])
        # Should fall back to default neutral
        assert "some headline" in results
        assert results["some headline"]["sentiment"] == "neutral"

    def test_score_clamped_to_minus_one_plus_one(self, processor_with_model):
        proc, mock_model = processor_with_model
        mock_model.generate_content.return_value = (
            '[{"headline": "test", "sentiment": "positive", '
            '"score": 5.0, "reason": "extreme"}]'
        )
        results = proc.analyze_sentiments_batch(["test"])
        assert results["test"]["score"] <= 1.0
        assert results["test"]["score"] >= -1.0


# ---------------------------------------------------------------------------
# analyze_sentiment — single headline
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAnalyzeSentiment:
    def test_empty_headline_returns_neutral(self, processor_no_model):
        result = processor_no_model.analyze_sentiment("")
        assert result["sentiment"] == "neutral"

    def test_wraps_batch_correctly(self, processor_no_model):
        processor_no_model.sentiment_cache["TSLA drops"] = {
            "sentiment": "negative",
            "score": -0.7,
            "reason": "recall",
        }
        result = processor_no_model.analyze_sentiment("TSLA drops")
        assert result["sentiment"] == "negative"


# ---------------------------------------------------------------------------
# get_historical_news — needs Polygon API (mocked)
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestGetHistoricalNews:
    def test_returns_empty_list_when_no_api_token(self, processor_no_model):
        processor_no_model.api_token = None
        result = processor_no_model.get_historical_news(
            ["AAPL"], datetime(2024, 1, 1), datetime(2024, 2, 1)
        )
        assert result == []

    def test_returns_empty_list_when_no_symbols(self, processor_no_model):
        result = processor_no_model.get_historical_news(
            [], datetime(2024, 1, 1), datetime(2024, 2, 1)
        )
        assert result == []

    @patch("core.news_processor.requests.get")
    def test_returns_processed_articles(self, mock_get, processor_no_model):
        processor_no_model.api_token = "fake-token"
        # Provide a mock sentiment model so fetch doesn't abort early
        mock_sentiment = MagicMock()
        mock_sentiment.generate_content.return_value = (
            '[{"headline": "AAPL hits record", "sentiment": "positive", '
            '"score": 0.8, "reason": "surge"}]'
        )
        processor_no_model.sentiment_model = mock_sentiment
        # Patch time.sleep to skip the 13s delay in tests
        with patch("core.news_processor.time.sleep"):
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "results": [
                    {
                        "id": "art1",
                        "title": "AAPL hits record",
                        "published_utc": "2024-01-15T10:00:00Z",
                        "tickers": ["AAPL"],
                        "description": "Apple stock surge",
                    }
                ]
            }
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            result = processor_no_model.get_historical_news(
                ["AAPL"], datetime(2024, 1, 1), datetime(2024, 1, 20)
            )
        assert len(result) >= 1
        assert result[0]["headline"] == "AAPL hits record"
