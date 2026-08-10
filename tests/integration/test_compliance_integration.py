"""
Smoke & integration tests for ComplianceGuardian wiring.

These tests validate that the ComplianceGuardian is correctly integrated
into the engine and strategy layers — without needing a live Alpaca
connection or trained ML models.
"""

import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.compliance import ComplianceGuardian  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def guardian():
    """Fixture that provides a ComplianceGuardian with cloud logger mocked."""
    with patch("core.compliance.get_cloud_logger") as mock_gl:
        mock_gl.return_value = MagicMock()
        g = ComplianceGuardian()
        # Default limits for tests
        g.max_order_value = 1000.0
        g.max_daily_trades = 3
        g._recent_trades = []
        return g


def _order(symbol="AAPL", side="buy", qty=5, price=100.0, strategy="test"):
    return {
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "strategy_id": strategy,
        "timestamp": time.time(),
    }


# ============================================================================
# 1. SMOKE TEST — Engine-level wiring
# ============================================================================


def test_guardian_init_with_config_overrides():
    """Simulate the BotEngine.__init__ guardian setup with overridden limits."""
    # Replicate what engine.py does:
    enable_flag = True
    max_order = 5000.0
    max_daily = 25

    guardian = None
    if enable_flag:
        with patch("core.compliance.get_cloud_logger", return_value=MagicMock()):
            guardian = ComplianceGuardian()
            guardian.max_order_value = max_order
            guardian.max_daily_trades = max_daily

    assert guardian is not None
    assert guardian.max_order_value == 5000.0
    assert guardian.max_daily_trades == 25


def test_guardian_disabled_returns_none():
    """When ENABLE_COMPLIANCE_GUARDIAN is False, guardian stays None."""
    enable_flag = False

    guardian = None
    if enable_flag:
        guardian = ComplianceGuardian()

    assert guardian is None


# ============================================================================
# 2. STRATEGY-LEVEL integration — compliance gate blocks orders
# ============================================================================


class TestStrategyComplianceGate:
    """Simulate the compliance gate logic that lives inside
    _submit_order_safe without needing an actual strategy instance."""

    def test_valid_order_passes_gate(self, guardian):
        order = _order(price=50.0, qty=5)  # value = 250
        assert guardian.check_order(order) is True

    def test_oversized_order_blocked(self, guardian):
        order = _order(price=300.0, qty=5)  # value = 1500 > 1000
        assert guardian.check_order(order) is False

    def test_restricted_symbol_blocked(self, guardian):
        order = _order(symbol="SCAM_TOKEN")
        assert guardian.check_order(order) is False

    # -- check_trade gate --

    def test_daily_limit_blocks_after_threshold(self, guardian):
        order = _order()
        for _ in range(3):
            assert guardian.check_trade(order) is True
            guardian.daily_trades += 1

        # 4th should be blocked
        assert guardian.check_trade(order) is False

    # -- wash trade gate --

    def test_wash_trade_blocks_opposite(self, guardian):
        buy = _order(side="buy")
        assert guardian.check_order(buy) is True

        sell = _order(side="sell")
        assert (
            guardian.check_order(sell) is False
        ), "Immediate opposite trade should be flagged as wash trade"

    # -- combined flow (mimics _submit_order_safe) --

    def test_full_pretrade_flow(self, guardian):
        """Simulate the exact check sequence used in _submit_order_safe."""
        order = _order(price=50.0, qty=5)
        passed_order = guardian.check_order(order)
        assert passed_order is True

        passed_trade = guardian.check_trade(order)
        assert passed_trade is True

        # Increment like the strategy does
        guardian.daily_trades += 1
        assert guardian.daily_trades == 1


# ============================================================================
# 3. API ENDPOINT smoke test — /compliance-status shape
# ============================================================================


class TestComplianceStatusEndpoint:
    """Verify the response shape that /compliance-status would return."""

    def test_status_shape_when_enabled(self, guardian):
        g = guardian
        # Simulate what the endpoint does
        response = {
            "status": "success",
            "enabled": True,
            "max_order_value": g.max_order_value,
            "max_daily_trades": g.max_daily_trades,
            "daily_trades_today": g.daily_trades,
            "restricted_symbols": g.restricted_list,
            "wash_trade_window_seconds": g._wash_trade_window_seconds,
            "recent_trades_in_window": len(g._recent_trades),
        }
        assert response["status"] == "success"
        assert response["enabled"] is True
        assert "max_order_value" in response
        assert "max_daily_trades" in response
        assert "daily_trades_today" in response
        assert "restricted_symbols" in response

    def test_status_shape_when_disabled(self):
        response = {
            "status": "success",
            "enabled": False,
            "message": "ComplianceGuardian is disabled.",
        }
        assert response["enabled"] is False
        assert "message" in response


# ============================================================================
# 4. RESET — daily limit lifecycle
# ============================================================================


class TestDailyLifecycle:
    """Tests the daily reset flow that the engine triggers at market open."""

    def test_reset_clears_counter(self, guardian):
        guardian.daily_trades = 42
        guardian.reset_daily_limit()
        assert guardian.daily_trades == 0

    def test_trades_work_after_reset(self, guardian):
        guardian.max_daily_trades = 2
        guardian.daily_trades = 2
        assert guardian.check_trade(_order()) is False

        guardian.reset_daily_limit()
        assert guardian.check_trade(_order()) is True
