# tests/unit/test_portfolio_equity_sync_logging.py
# BUG-AI-114 (#1240): the equity-sync in PortfolioManager.refresh_positions() must NOT
# swallow the API error silently. The last live total_capital is kept (self-healing next
# cycle — verified P3, not a hardcode), but the failure must be logged so an API flap is
# visible rather than invisible.
import logging
from unittest.mock import MagicMock

from core.portfolio_manager import PortfolioManager


def test_equity_sync_failure_is_logged_and_keeps_last_value(caplog):
    client = MagicMock()
    client.get_account.side_effect = RuntimeError("API timeout / flap")
    client.get_all_positions.return_value = []  # so refresh_positions completes

    pm = PortfolioManager(client=client, total_capital=50000.0)

    with caplog.at_level(logging.WARNING):
        pm.refresh_positions()

    # Self-healing: the last live value is kept (NOT reset / hardcoded).
    assert pm.total_capital == 50000.0
    # No longer silent — the sync failure is visible.
    assert any(
        "equity" in r.getMessage().lower()
        and "portfoliomanager" in r.getMessage().lower()
        for r in caplog.records
    ), "the equity-sync failure must be logged (BUG-AI-114), not swallowed"
