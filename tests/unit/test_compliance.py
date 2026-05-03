import pytest
import time
import importlib
import logging
from unittest.mock import MagicMock, patch
from core.compliance import ComplianceGuardian


@pytest.fixture
def guardian():
    """Fixture to provide a clean ComplianceGuardian with a mocked logger."""
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        g = ComplianceGuardian()
        # Reset trades for clean state
        g._recent_trades = []
        return g


def test_valid_order(guardian):
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(order) is True
    # Verify cloud logger was called
    guardian.cloud_logger.log_compliance_event.assert_called()


def test_restricted_symbol(guardian):
    order = {
        "symbol": "SCAM_TOKEN",
        "side": "buy",
        "quantity": 10,
        "price": 10.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(order) is False
    # Verify cloud logger was called
    guardian.cloud_logger.log_compliance_event.assert_called()


def test_missing_mifid_fields(guardian):
    order = {
        "symbol": "AAPL",
        # Missing side
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
    }
    assert guardian.check_order(order) is False


def test_wash_trade_prevention(guardian):
    # 1. Buy AAPL
    buy_order = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(buy_order) is True

    # 2. Sell AAPL immediately (Wash Trade)
    sell_order = {
        "symbol": "AAPL",
        "side": "sell",
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(sell_order) is False


def test_risk_limits(guardian):
    # Max value is 10,000. Try 10,001
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 100,
        "price": 101.0,  # 10,100 value
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(order) is False


@pytest.fixture
def integration_guardian():
    """Fixture for integration-style tests with custom config."""
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        g = ComplianceGuardian()
        g._recent_trades = []
        return g


def _valid_order(symbol="AAPL"):
    return {
        "symbol": symbol,
        "side": "buy",
        "quantity": 5,
        "price": 100.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }


def test_daily_trade_limit(integration_guardian):
    """check_trade should block once daily_trades >= max_daily_trades."""
    integration_guardian.max_daily_trades = 3
    integration_guardian.daily_trades = 0

    order = _valid_order()
    # First 3 trades should pass
    for _ in range(3):
        assert integration_guardian.check_trade(order) is True
        integration_guardian.daily_trades += 1

    # 4th trade must be blocked
    assert integration_guardian.check_trade(order) is False


def test_config_overrides(integration_guardian):
    """Guardian should respect overridden max_order_value."""
    integration_guardian.max_order_value = 500.0  # very low limit

    small_order = _valid_order()
    small_order["price"] = 10.0
    small_order["quantity"] = 5  # value = 50
    assert integration_guardian.check_order(small_order) is True

    big_order = _valid_order()
    big_order["price"] = 200.0
    big_order["quantity"] = 5  # value = 1000 > 500 limit
    assert integration_guardian.check_order(big_order) is False


def test_daily_trades_reset(integration_guardian):
    """reset_daily_limit should clear the counter."""
    integration_guardian.daily_trades = 99
    integration_guardian.reset_daily_limit()
    assert integration_guardian.daily_trades == 0


def test_compliance_check_order_exception(guardian):
    """Test the exception block in check_order."""
    with patch.object(
        guardian, "_check_risk_limits", side_effect=ValueError("Mock Error")
    ):
        order = _valid_order()
        assert guardian.check_order(order) is False
        guardian.cloud_logger.log_compliance_event.assert_called()


def test_check_risk_limits_exception(guardian):
    """Test the exception block in _check_risk_limits."""
    order = _valid_order()
    order["quantity"] = "invalid"  # This will cause a ValueError
    assert guardian._check_risk_limits(order) is False


def test_module_logging_fallback_isdir():
    """Test the module-level fallback when _audit_log_path is a directory."""
    import core.compliance

    with patch("os.path.isdir", return_value=True):
        with patch.object(
            logging.getLogger("ComplianceGuardian"), "addHandler"
        ) as mock_add:
            importlib.reload(core.compliance)
            assert mock_add.called


def test_module_logging_fallback_exception():
    """Test the module-level fallback when FileHandler throws an exception."""
    import core.compliance

    with patch("os.path.isdir", return_value=False):
        with patch(
            "logging.FileHandler", side_effect=Exception("Permission Denied")
        ):  # noqa: E501
            with patch.object(
                logging.getLogger("ComplianceGuardian"), "addHandler"
            ) as mock_add:
                importlib.reload(core.compliance)
                assert mock_add.called


def test_tenant_id_in_audit_log(guardian):
    """
    TODO (Epic 2.4/2.5 - Multi-Tenancy Rules):
    The ComplianceGuardian must ensure all log attributes / messages
    are prefixed with [Tenant: <tenant_id>].
    We must mock the order dictionary to include `tenant_id` and assert
    the logger was called with that identifier.
    """
    pass
