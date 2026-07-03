import importlib
import logging
import time
from unittest.mock import MagicMock, patch

import allure
import pytest

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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_missing_mifid_fields(guardian):
    order = {
        "symbol": "AAPL",
        # Missing side
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
    }
    assert guardian.check_order(order) is False


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_risk_limits(guardian):
    # Max value is configured via get_config().COMPLIANCE_MAX_ORDER_VALUE.
    # Set to a explicit value for the test to remain independent of environment config.
    guardian.max_order_value = 10000.0
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_daily_trades_reset(integration_guardian):
    """reset_daily_limit should clear the counter and the alert flag."""
    integration_guardian.daily_trades = 99
    integration_guardian._daily_limit_alert_sent = True
    integration_guardian.reset_daily_limit()
    assert integration_guardian.daily_trades == 0
    assert getattr(integration_guardian, "_daily_limit_alert_sent", None) is False


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_compliance_check_order_exception(guardian):
    """Test the exception block in check_order."""
    with patch.object(
        guardian, "_check_risk_limits", side_effect=ValueError("Mock Error")
    ):
        order = _valid_order()
        assert guardian.check_order(order) is False
        guardian.cloud_logger.log_compliance_event.assert_called()


# ── BUG-AI-101 / #1237: single audit per order (no double-log) ───────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_rejected_order_is_audited_exactly_once(guardian):
    """Every early reject path explicitly called _log_audit AND the finally re-logged →
    each rejection produced TWO audit entries, corrupting the (BaFin) compliance trail.
    A reject must be audited exactly ONCE."""
    order = {
        "symbol": "SCAM_TOKEN",  # on the restricted list → reject (first gate)
        "side": "buy",
        "quantity": 10,
        "price": 10.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(order) is False
    assert guardian.cloud_logger.log_compliance_event.call_count == 1  # not 2


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_risk_reject_is_audited_exactly_once(guardian):
    """The 4th gate (risk limits) reject path — also exactly one audit entry."""
    guardian.max_order_value = 10000.0
    order = _valid_order()
    order["quantity"] = 100
    order["price"] = 101.0  # 10,100 > limit → reject
    assert guardian.check_order(order) is False
    assert guardian.cloud_logger.log_compliance_event.call_count == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_approved_order_is_audited_exactly_once(guardian):
    """Regression guard: an approved order stays at exactly one audit entry."""
    assert guardian.check_order(_valid_order()) is True
    assert guardian.cloud_logger.log_compliance_event.call_count == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_execution_outcome_is_audited_for_reconciliation(guardian):
    """Honesty fix: log_execution_outcome writes a reconcilable 'execution' entry into the
    same audit trail, so an approved-but-dropped order (e.g. cash-gated AFTER the 'approved'
    pre-trade check) is visible instead of being misread as executed."""
    import json as _json

    order = _valid_order()
    with patch("core.compliance.compliance_logger") as mock_logger:
        guardian.log_execution_outcome(
            order, submitted=False, reason="cash gate: over budget"
        )

    assert mock_logger.info.called
    entry = _json.loads(mock_logger.info.call_args[0][0])
    assert entry["event"] == "execution"
    assert entry["submitted"] is False
    assert entry["symbol"] == order["symbol"]
    assert "cash gate" in entry["reason"]


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_exception_after_approval_is_audited_as_rejected(guardian):
    """If a post-checks step throws AFTER the decision was tentatively True, the order is
    rejected (returns False) — the single audit entry MUST record approved=False, never a
    phantom approval."""
    with patch.object(
        guardian, "_cleanup_recent_trades", side_effect=RuntimeError("boom")
    ):
        assert guardian.check_order(_valid_order()) is False
        assert guardian.cloud_logger.log_compliance_event.call_count == 1
        _, kwargs = guardian.cloud_logger.log_compliance_event.call_args
        assert kwargs.get("approved") is False  # reset, not a phantom True


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_mifid_reject_is_audited_exactly_once(guardian):
    """Gate 2 (MiFID fields) reject path — exactly one audit entry (a _log_audit call was
    removed from this path too)."""
    order = {
        "symbol": "AAPL",
        # 'side' deliberately missing → MiFID-completeness gate rejects
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    assert guardian.check_order(order) is False
    assert guardian.cloud_logger.log_compliance_event.call_count == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_wash_trade_reject_is_audited_exactly_once(guardian):
    """Gate 3 (wash-trade) reject path — exactly one audit entry for the rejected sell."""
    buy = _valid_order()
    buy["side"] = "buy"
    assert guardian.check_order(buy) is True  # records the buy (audited once)
    guardian.cloud_logger.log_compliance_event.reset_mock()

    sell = _valid_order()
    sell["side"] = "sell"  # immediate opposite for the same symbol → wash-trade reject
    assert guardian.check_order(sell) is False
    assert guardian.cloud_logger.log_compliance_event.call_count == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_check_risk_limits_exception(guardian):
    """Test the exception block in _check_risk_limits."""
    order = _valid_order()
    order["quantity"] = "invalid"  # This will cause a ValueError
    assert guardian._check_risk_limits(order) is False


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_module_logging_fallback_isdir():
    """Test the module-level fallback when _audit_log_path is a directory."""
    import core.compliance

    with patch("os.path.isdir", return_value=True):
        with patch.object(
            logging.getLogger("ComplianceGuardian"), "addHandler"
        ) as mock_add:
            importlib.reload(core.compliance)
            assert mock_add.called


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
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


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_tenant_id_in_audit_log(guardian):
    """
    TODO (Epic 2.4/2.5 - Multi-Tenancy Rules):
    The ComplianceGuardian must ensure all log attributes / messages
    are prefixed with [Tenant: <tenant_id>].
    We must mock the order dictionary to include `tenant_id` and assert
    the logger was called with that identifier.
    """
    pass
