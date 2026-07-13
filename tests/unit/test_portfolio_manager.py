# tests/unit/test_portfolio_manager.py

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.portfolio_manager import PortfolioManager


@pytest.fixture
def mock_client():
    client = MagicMock()
    return client


@pytest.fixture
def portfolio_manager(mock_client):
    pm = PortfolioManager(client=mock_client, total_capital=100000.0)
    # Set known config thresholds for testing
    pm._min_hold_hours = 0.5
    pm._consecutive_sell_threshold = 5
    return pm


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio Manager")
class TestPortfolioManagerAntiChurn:
    def test_can_sell_position_no_history(self, portfolio_manager):
        """If there is no trade history, we can sell immediately."""
        assert portfolio_manager.can_sell_position("AAPL") == (
            True,
            "No trade history - can sell",
        )

    def test_can_sell_position_under_hold_hours(self, portfolio_manager):
        """If bought recently (e.g. 1 minute ago), we cannot sell."""
        now = datetime.now()
        portfolio_manager._trade_history["AAPL"] = [now - timedelta(minutes=1)]

        can_sell, reason = portfolio_manager.can_sell_position("AAPL")
        assert can_sell is False
        assert "Minimum hold period not met" in reason

    def test_can_sell_position_after_hold_hours(self, portfolio_manager):
        """If held longer than the minimum hold hours, we can sell."""
        now = datetime.now()
        portfolio_manager._trade_history["AAPL"] = [
            now - timedelta(minutes=40)
        ]  # 40 min > 30 min (0.5h)

        can_sell, reason = portfolio_manager.can_sell_position("AAPL")
        assert can_sell is True
        assert "Hold period satisfied" in reason

    def test_can_sell_position_consecutive_sell_bypass(self, portfolio_manager):
        """If consecutive sells exceed threshold, bypass hold period."""
        now = datetime.now()
        portfolio_manager._trade_history["AAPL"] = [now - timedelta(minutes=1)]
        portfolio_manager._consecutive_sell_signals["AAPL"] = 5  # Threshold reached

        can_sell, reason = portfolio_manager.can_sell_position("AAPL")
        assert can_sell is True
        assert "Hold bypassed" in reason

    def test_record_trade_resets_and_cutoff(self, portfolio_manager):
        """Test trade recording and 30-day cutoff logic."""
        now = datetime.now()
        old_trade = now - timedelta(days=31)
        portfolio_manager._trade_history["AAPL"] = [old_trade]

        portfolio_manager.record_trade("AAPL", "buy")

        # Old trade should be pruned (30-day cutoff), and new trade recorded
        assert len(portfolio_manager._trade_history["AAPL"]) == 1
        assert portfolio_manager._trade_history["AAPL"][0] > now - timedelta(seconds=2)


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio Manager — user_id Parameter")
class TestPortfolioManagerUserId:
    def test_user_id_stored(self, mock_client):
        """user_id is stored on the instance for Redis key namespacing."""
        pm = PortfolioManager(
            client=mock_client, total_capital=100_000.0, user_id="test-user"
        )
        assert pm.user_id == "test-user"

    def test_user_id_default_is_oss_single(self, mock_client):
        """Default user_id is 'oss-single' (not 'default')."""
        pm = PortfolioManager(client=mock_client, total_capital=100_000.0)
        assert pm.user_id == "oss-single"
