# tests/unit/test_news_poller.py
import json
from unittest.mock import MagicMock, patch

import allure
import pytest
from alpaca.common.exceptions import APIError

from core.engine.news_poller import NewsPollerMixin


class DummyEngine(NewsPollerMixin):
    def __init__(self):
        self.api = MagicMock()
        self.config = MagicMock()
        self.compliance_guardian = None
        self.evaluator = MagicMock()
        self.ai_client = MagicMock()
        self.ai_rules = MagicMock()
        self.is_simulation = False

    def _log_strategy_thought(self, msg):
        pass


@pytest.mark.asyncio
async def test_fetch_news_graceful_fail_on_500():
    """
    TDD test for the fix/alpaca-position-error-clean branch.
    Ensures that a 500 error from Alpaca doesn't trigger a fail-open Buy signal
    but rather a fail-closed response.
    """
    engine = DummyEngine()

    # Simulate a network/backend failure (not a 404)
    class MockResp:
        status_code = 500

    class MockHttp:
        response = MockResp()

    err = APIError(
        json.dumps({"code": 500, "message": "internal server error"}), MockHttp()
    )
    engine.api.get_open_position.side_effect = err

    # Run the news poller evaluation
    with patch("core.engine.news_poller.logging") as mock_log:
        engine.ai_rules.get_rules.return_value = [
            {
                "action": "proactive_signal",
                "trigger": {
                    "headline_keywords": ["apple"],
                    "sentiment_gt": 0.5,
                    "signal_ticker": "AAPL",
                },
                "reason": "Test",
            }
        ]

        # Mock api account and snapshot
        mock_account = MagicMock()
        mock_account.equity = "100000.0"
        engine.api.get_account.return_value = mock_account

        mock_snapshot = MagicMock()
        mock_snapshot.latest_trade.p = 150.0
        engine.api.get_snapshot.return_value = mock_snapshot

        article = {
            "headline": "Apple is doing great",
            "symbols": ["AAPL"],
            "score": 0.9,
        }

        # Override evaluator to ensure we hit the position check
        engine.evaluator.evaluate_sentiment.return_value = {
            "AAPL": {
                "decision": "BUY",
                "trade_qty": 10,
                "current_price": 150.0,
            }
        }

        engine._check_proactive_rules(article)

        # It should log a WARNING and NOT proceed with any signal evaluation (fail closed)
        assert mock_log.warning.called
        # Check if the warning was logged (the first arg to logging.warning is the unformatted string)
        warning_calls = [call[0][0] for call in mock_log.warning.call_args_list]
        assert any(
            "NewsPoller position check failed for %s: %s" in str(msg)
            for msg in warning_calls
        )

        # Ensure it didn't try to buy
        assert not any("PROACTIVE BUY" in str(msg) for msg in warning_calls)


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Trading & Execution")
class TestNewsPollerProactiveRules:
    def test_check_proactive_rules_calls_get_position(self):
        class NestedDummyEngine(NewsPollerMixin):
            def __init__(self):
                self.is_simulation = False
                self.api = MagicMock()
                self.ai_rules = MagicMock()
                self.compliance_guardian = MagicMock()
                self.news_running = MagicMock()
                self._shutdown_event = MagicMock()

            def _log_strategy_thought(self, msg):
                pass

        engine = NestedDummyEngine()

        # Mock self.ai_rules.get_rules() to return a mock rule
        mock_rule = {
            "action": "proactive_signal",
            "trigger": {
                "headline_keywords": ["merger", "acquisition"],
                "sentiment_gt": 0.5,
                "signal_ticker": "AAPL",
            },
            "reason": "Acquisition news",
        }
        engine.ai_rules.get_rules.return_value = [mock_rule]

        # Mock self.api.get_account().equity
        mock_account = MagicMock()
        mock_account.equity = "100000.0"
        engine.api.get_account.return_value = mock_account

        # Mock self.api.get_snapshot(ticker)
        mock_snapshot = MagicMock()
        mock_snapshot.latest_trade.p = 150.0
        engine.api.get_snapshot.return_value = mock_snapshot

        # Mock get_open_position to return None (no position) so it proceeds
        engine.api.get_open_position.return_value = None

        # Prepare a mock article
        article = {
            "symbols": ["AAPL"],
            "score": 0.8,
            "headline": "Massive Apple merger announcement",
        }

        # Run the method
        engine._check_proactive_rules(article)

        # Assertions
        engine.api.get_open_position.assert_called_once_with("AAPL")
        engine.compliance_guardian.check_order.assert_called_once()
