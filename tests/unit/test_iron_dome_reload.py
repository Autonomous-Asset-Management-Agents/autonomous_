# ADR-SEC-06 (#1583) · sub-issue #1596 — dynamic reload + single-source read. TDD RED first.
# reload_policy() lets a running ComplianceGuardian / RiskManager pick up an admin policy
# change WITHOUT a restart (ADR §5a). Every value is clamped to the immutable hard-floor and
# fails closed to the strict default. Together with the base.py cleanup this closes the
# 50-vs-10 drift (#1584).

from unittest.mock import MagicMock, patch

import pytest

from core.compliance import ComplianceGuardian
from core.risk_manager import RiskManager

pytestmark = pytest.mark.iron_dome


def _guardian():
    with patch("core.compliance.get_cloud_logger", return_value=MagicMock()):
        return ComplianceGuardian()


def _risk_manager():
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(client=MagicMock(), total_capital=100_000.0)


def test_guardian_reload_applies_and_clamps():
    g = _guardian()
    g.reload_policy(
        {
            "max_daily_trades": 999,
            "wash_trade_window_seconds": 5,
            "max_order_value": 5000.0,
        }
    )
    assert g.max_daily_trades == 50  # clamped to ADR-C04 ceiling
    assert g._wash_trade_window_seconds == 30  # clamped to ADR-C03 floor
    assert g.max_order_value == 5000.0  # within bounds → preserved


def test_guardian_reload_none_fails_closed():
    g = _guardian()
    g.max_daily_trades = 999  # a stale/odd in-memory value
    g.reload_policy(None)
    assert g.max_daily_trades == 10  # STRICT_DEFAULT
    assert g.max_order_value == 10_000.0
    assert g._wash_trade_window_seconds == 60


def test_risk_manager_reload_applies_and_clamps():
    rm = _risk_manager()
    rm.reload_policy({"portfolio_stop_loss_pct": 0.50, "daily_drawdown_pct": 0.50})
    assert rm.portfolio_stop_loss_pct == 0.10  # clamped to ceiling
    assert rm.daily_drawdown_limit_percent == 0.20  # clamped to ceiling
    assert rm.daily_drawdown_limit == 20_000.0  # recomputed = total_capital * pct


def test_risk_manager_reload_none_fails_closed():
    rm = _risk_manager()
    rm.reload_policy(None)
    assert rm.portfolio_stop_loss_pct == 0.07  # STRICT_DEFAULT
    assert rm.daily_drawdown_limit_percent == 0.175
    assert rm.daily_drawdown_limit == 17_500.0
