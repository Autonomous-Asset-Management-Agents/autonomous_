# Compliance-Quick-Wins (GAP5 + GAP1-Residual) — TDD Red first.
# Plan: 2026-06-11-bugfix-batch/plan_B_compliance_quickwins.md (Papa: "Starte die Verifikation").
#
# Covers:
#   e) engine/base.py getattr-fallback: a config variant LACKING the
#      ENABLE_COMPLIANCE_GUARDIAN field must still create the guardian
#      (fail-closed fallback True — today the fallback is False = silent disable).
#   f) ComplianceGuardian class defaults enforce ADR-C01 (blocks >10k, allows <10k).
#   g-i) fail-closed lock-ins for the check paths (exception / malformed / wash-trade)
#      — these pass today and cement the §1.3 residual-audit result against regression.

import time
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.compliance import ComplianceGuardian


def _order(**overrides):
    base = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def guardian():
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_get_logger.return_value = MagicMock()
        g = ComplianceGuardian()
        # Set to explicit class default for independence from environment/dotenv config
        g.max_order_value = 10000.0
        g._recent_trades = []
        return g


class _ConfigProxy:
    """Forwards getattr to the real config module but HIDES given names —
    simulates a config variant that lacks a field (the GAP1-residual case)."""

    def __init__(self, real, hidden):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_hidden", set(hidden))

    def __getattr__(self, name):
        if name in self._hidden:
            raise AttributeError(name)
        return getattr(self._real, name)


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestComplianceDefaults:
    # ------------------------------------------------------------------ (f)
    def test_guardian_blocks_order_over_class_default(self, guardian):
        # ADR-C01: class default max_order_value = 10_000.0 — order value
        # 10_001 must be blocked, 9_999 must pass (all other fields valid).
        assert guardian.check_order(_order(quantity=1, price=10_001.0)) is False
        assert guardian.check_order(_order(quantity=1, price=9_999.0)) is True

    # ------------------------------------------------------------------ (g)
    def test_check_order_exception_fail_closed(self, guardian):
        # Any internal error during a compliance check must BLOCK, never allow.
        with patch.object(
            guardian, "_detect_wash_trade", side_effect=RuntimeError("boom")
        ):
            assert guardian.check_order(_order()) is False

    # ------------------------------------------------------------------ (h)
    def test_risk_limits_malformed_order_fail_closed(self, guardian):
        # Non-numeric quantity → _check_risk_limits returns False (no raise).
        assert guardian._check_risk_limits(_order(quantity="abc")) is False

    # ------------------------------------------------------------------ (i)
    def test_wash_trade_detected_blocks(self, guardian):
        # buy then immediate sell on the same symbol → second order blocked.
        assert guardian.check_order(_order(side="buy")) is True
        assert guardian.check_order(_order(side="sell")) is False

    # ------------------------------------------------------------------ (e)
    def test_engine_creates_guardian_when_config_lacks_flag(self, monkeypatch):
        """GAP1-Residual: if a config variant LACKS ENABLE_COMPLIANCE_GUARDIAN,
        the engine must still create the guardian (fallback True, fail-closed).
        RED today: engine/base.py uses getattr(..., False) → silent disable."""
        import config as real_config
        import core.engine.base as base_mod
        from core.engine.base import BotEngine

        # fail-fast at base.py:60-67 requires GEMINI_API_KEY (same pattern as
        # test_engine_base._make_engine); the proxy forwards it to real config.
        monkeypatch.setattr(
            real_config, "GEMINI_API_KEY", "test-key-compliance", raising=False
        )
        # Force default limit of 10000.0 regardless of environment/dotenv config
        monkeypatch.setattr(
            real_config.get_config(), "COMPLIANCE_MAX_ORDER_VALUE", 10000.0
        )
        monkeypatch.setattr(
            base_mod,
            "config",
            _ConfigProxy(real_config, hidden={"ENABLE_COMPLIANCE_GUARDIAN"}),
        )

        mock_api = MagicMock()
        mock_api.get_account.return_value = MagicMock(equity="50000.0")
        mock_api.get_all_positions.return_value = []

        with patch(
            "core.engine.base.HistoricalDataProvider", return_value=MagicMock()
        ), patch("core.engine.base.NewsProcessor", return_value=MagicMock()), patch(
            "core.engine.base.MarketRegimeModel", return_value=MagicMock()
        ), patch(
            "core.engine.base.AIMarketScanner", return_value=MagicMock()
        ), patch(
            "core.engine.base.AILearningEngine", return_value=MagicMock()
        ), patch(
            "core.engine.base.AgentRegistry", return_value=MagicMock()
        ), patch(
            "core.engine.base.set_global_registry"
        ):
            engine = BotEngine(trading_client=mock_api, data_client=MagicMock())

        assert engine.compliance_guardian is not None, (
            "config variant without ENABLE_COMPLIANCE_GUARDIAN silently disabled "
            "the guardian — the getattr fallback must be True (fail-closed)"
        )
        # the value fallbacks stay ADR-C01-conform
        assert engine.compliance_guardian.max_order_value == 10_000.0
