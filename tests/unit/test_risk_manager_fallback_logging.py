"""C7 (#2373, CLAUDE.md §5.6): a fallback that changes money-relevant sizing behaviour
MUST log at WARNING. When COMPLIANCE_MAX_ORDER_VALUE can't be read, the sizing-time
compliance size-cap is silently skipped (0.0) — this pins that it now logs a WARNING."""

import logging
from unittest.mock import MagicMock, patch

import config
from core.risk_manager import RiskManager


def test_compliance_cap_unreadable_logs_warning(caplog):
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        rm = RiskManager(client=MagicMock(), total_capital=1_000_000.0)
    with patch.object(config, "COMPLIANCE_MAX_ORDER_VALUE", "not-a-number"):
        with caplog.at_level(logging.WARNING):
            rm.calculate_position_size(
                stop_loss_atr_multiplier=3.0,
                atr=5.0,
                confidence="high",
                market_data={"vix": 15.0},
                conviction_score=0.95,
                current_price=100.0,
                account_cash=1_000_000.0,
                num_stocks_in_strategy=1,
                allow_fractional=True,
            )
    assert any(
        "COMPLIANCE_MAX_ORDER_VALUE unreadable" in r.getMessage()
        for r in caplog.records
    ), "the compliance-cap read failure must be logged at WARNING (§5.6)"
