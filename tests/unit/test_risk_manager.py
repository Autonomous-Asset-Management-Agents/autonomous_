import logging
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.risk_manager import RiskManager

pytestmark = pytest.mark.iron_dome


@pytest.fixture(autouse=True)
def reset_kill_switch_fixture():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


@pytest.fixture
def risk_manager():
    client_mock = MagicMock()
    # Mock config to avoid import side-effects
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        rm = RiskManager(client=client_mock, total_capital=100_000.0)
        return rm


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestRiskManager:
    def test_initialization_defaults(self, risk_manager):
        """Test default risk manager limits."""
        assert risk_manager.total_capital == 100_000.0
        assert risk_manager.risk_per_trade_percent == 0.02
        assert risk_manager.daily_drawdown_limit_percent == 0.175
        assert risk_manager.daily_drawdown_limit == 17_500.0
        assert risk_manager.max_loss_per_trade_percent == 0.015
        assert risk_manager.portfolio_stop_loss_pct == 0.07

    def test_position_size_capped_at_compliance_max_order_value(self):
        """ADR-C01: a single order's notional must never exceed COMPLIANCE_MAX_ORDER_VALUE.

        Without this cap the risk-sized order (~10% exposure of a 1M book ≈ $100k) is built and
        then HARD-BLOCKED by the ComplianceGuardian (compliance.py _check_risk_limits:
        ``value > max_order_value``) — so NO trade ever executes. Regression for the desktop
        finding where every BUY was 🛡️ BLOCKED ("Order exceeds Max Order Value")."""
        from unittest.mock import MagicMock, patch

        from config import get_config

        # Construct a large-book RM directly so the risk-sized order is unambiguously ≫ the 10k
        # cap (dynamic sizing → ~25% of capital, then the 10% exposure cap → ~$100k order).
        with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
            rm = RiskManager(client=MagicMock(), total_capital=1_000_000.0)
        max_order_value = get_config().COMPLIANCE_MAX_ORDER_VALUE
        price = 100.0
        qty = rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=5.0,
            confidence="high",
            market_data={"vix": 15.0},
            conviction_score=0.95,  # high conviction → ~25% of 1M (~$250k) ≫ 10k cap
            current_price=price,
            account_cash=1_000_000.0,
            num_stocks_in_strategy=1,
            allow_fractional=True,
        )
        assert qty > 0, "sanity: a high-conviction order should be sized > 0"
        assert qty * price <= max_order_value + 1e-6, (
            f"order notional ${qty * price:,.2f} exceeds COMPLIANCE_MAX_ORDER_VALUE "
            f"${max_order_value:,.2f} → the ComplianceGuardian would block every order"
        )

    def test_progressive_halt_tier1_warning(self, risk_manager):
        """Test Tier 1 warning phase (60% drawdown)."""
        risk_manager.portfolio_stop_loss_pct = 0.0  # Disable portfolio stop loss
        # Drop to 65% of limit ($11,375 drawdown)
        current_equity = 100_000 - 11_375

        risk_manager.update_account_equity(current_equity)

        assert risk_manager.trading_reduced is True
        assert risk_manager.trading_halted is False

        # Recover to 50% of limit ($8,750 drawdown) -> 91,250 equity
        risk_manager.update_account_equity(91_250.0)

        assert risk_manager.trading_reduced is False

    @patch("core.kill_switch.KillSwitch.trip")
    def test_circuit_breaker_tier2(self, mock_trip, risk_manager):
        """Test Tier 2 circuit breaker (100% drawdown)."""
        risk_manager.portfolio_stop_loss_pct = 0.0  # Disable portfolio stop loss
        # Drop past daily limit ($17,500)
        risk_manager.update_account_equity(82_000.0)

        assert risk_manager.trading_halted is True
        mock_trip.assert_called_once()
        risk_manager.client.close_all_positions.assert_called_once_with(
            cancel_orders=True
        )

    @patch("core.kill_switch.KillSwitch.trip")
    def test_portfolio_stop_loss(self, mock_trip, risk_manager):
        """Test portfolio stop loss (7% from session start)."""
        # Drop exactly 7% from session start (100k -> 93k)
        # Note: Daily drawdown limit is 17.5%, so we don't hit Tier 1 or Tier 2 based on that.
        # But we DO hit portfolio stop loss.
        risk_manager.update_account_equity(93_000.0)

        assert risk_manager.trading_halted is True
        assert getattr(risk_manager, "_portfolio_stop_triggered", False) is True
        mock_trip.assert_called_once()

        # Recovery should NOT unlock it
        risk_manager.update_account_equity(100_000.0)
        assert risk_manager.trading_halted is True

    @patch("core.kill_switch.KillSwitch.reset")
    def test_intelligent_unlock(self, mock_reset, risk_manager):
        """Test unlock mechanism after circuit breaker."""
        risk_manager.portfolio_stop_loss_pct = 0.0  # Disable portfolio stop loss
        risk_manager.update_account_equity(82_000.0)  # Halts system
        assert risk_manager.trading_halted is True

        # Recover to just above 50% threshold
        risk_manager.update_account_equity(91_000.0)
        assert risk_manager.trading_halted is True  # Still halted

        # Recover to 50% threshold or below (17,500 * 0.50 = 8,750 allowable drawdown -> 91,250 equity)
        risk_manager.update_account_equity(91_250.0)

        assert risk_manager.trading_halted is False
        assert risk_manager.trading_reduced is False
        mock_reset.assert_called_once()

    @patch(
        "config.COMPLIANCE_MAX_ORDER_VALUE", 1e12
    )  # isolate vix math from the ADR-C01 order cap
    def test_calculate_position_size_vix_scaling(self, risk_manager):
        """Test ADR-R08 VIX scaling."""
        atr = 2.0
        sl_multiplier = 3.0
        price = 100.0

        # Base case (VIX <= 18 -> 1.0 scaler)
        size_normal = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=sl_multiplier,
            atr=atr,
            market_data={"vix": 15.0},
            current_price=price,
            conviction_score=0.5,
        )

        # Extreme VIX case (VIX > 40 -> 0.3 scaler)
        size_extreme = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=sl_multiplier,
            atr=atr,
            market_data={"vix": 45.0},
            current_price=price,
            conviction_score=0.5,
        )

        # Extreme should be roughly 30% of normal
        # Check against ZeroDivisionError by ensuring > 0
        assert size_normal > 0
        assert size_extreme > 0
        assert abs((size_extreme / size_normal) - 0.3) < 0.05

    def test_calculate_position_size_cash_constraint(self, risk_manager):
        """Test ADR-R09 cash constraints."""
        price = 100.0
        account_cash = 10_000.0  # Only 10k cash

        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=price,
            account_cash=account_cash,
            conviction_score=1.0,  # Max conviction -> requests 30k target
        )

        # Max shares by cash = (10000 - 50) / 100 = 99.5  (single-symbol → slots=1, unchanged)
        assert size == 99.5

    def test_calculate_position_size_diversified_cash_allocation(self, risk_manager):
        """Diversified sizing: available cash is split across the target universe so N
        concurrent BUYs collectively fit the budget, instead of the first BUYs consuming it
        all. 10k cash / 10 slots / $100 = 9.95 shares (vs 99.5 for a single-symbol strategy).
        """
        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=100.0,
            account_cash=10_000.0,
            num_stocks_in_strategy=10,
            conviction_score=1.0,
        )
        # (10000 - 50) / 10 slots / $100 = 9.95 shares
        assert size == pytest.approx(9.95, abs=0.01)

    def test_calculate_position_size_halt_checks(self, risk_manager):
        """Test position size returns 0 when halted."""
        risk_manager.trading_halted = True

        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=100.0,
        )
        assert size == 0.0

    @patch("core.risk_manager.AILearnedRules.get_rules")
    def test_evaluate_new_trade_ai_rules(self, mock_get_rules, risk_manager):
        """Test AI rule filtering in evaluate_new_trade."""
        mock_get_rules.return_value = [
            {
                "trigger": {"side": "BUY", "indicators.features.rsi_14.gt": 70},
                "action": "block_trade",
                "reason": "RSI Overbought",
                "status": "active",
            },
            {
                "trigger": {"side": "SELL", "vix_gt": 30},
                "action": "reduce_size",
                "value": 0.5,
                "status": "active",
            },
        ]

        # Should be blocked
        is_allowed, reason, mods = risk_manager.evaluate_new_trade(
            symbol="AAPL",
            side="BUY",
            market_data={"indicators": {"features": {"rsi_14": 75}}},
            current_sl_multiplier=3.0,
        )
        assert is_allowed is False
        assert "Blocked by AI Rule" in reason

        # Should reduce size
        is_allowed, reason, mods = risk_manager.evaluate_new_trade(
            symbol="AAPL",
            side="SELL",
            market_data={"vix": 35},
            current_sl_multiplier=3.0,
        )
        assert is_allowed is True
        assert mods["size_scaler"] == 0.5

    @patch("core.risk_manager.AILearnedRules.get_rules")
    def test_evaluate_new_trade_ai_rules_additional_actions(
        self, mock_get_rules, risk_manager
    ):
        """Test AI rule filtering with tighten_sl, widen_sl, and increase_size."""
        mock_get_rules.return_value = [
            {
                "trigger": {"side": "BUY", "indicators.features.rsi_14.lt": 30},
                "action": "increase_size",
                "value": 1.5,
                "status": "active",
            },
            {
                "trigger": {"side": "BUY", "indicators.features.macd.gt": 0},
                "action": "tighten_sl",
                "value": 1.0,
                "status": "active",
            },
            {
                "trigger": {"side": "SELL", "indicators.features.adx.gt": 40},
                "action": "widen_sl",
                "value": 4.0,
                "status": "active",
            },
        ]

        # Test increase_size and tighten_sl (BUY)
        is_allowed, reason, mods = risk_manager.evaluate_new_trade(
            symbol="AAPL",
            side="BUY",
            market_data={"indicators": {"features": {"rsi_14": 20, "macd": 1}}},
            current_sl_multiplier=3.0,
        )
        assert is_allowed is True
        assert mods["size_scaler"] == 1.5
        assert mods["sl_multiplier"] == 1.0

        # Test widen_sl (SELL)
        is_allowed, reason, mods = risk_manager.evaluate_new_trade(
            symbol="AAPL",
            side="SELL",
            market_data={"indicators": {"features": {"adx": 45}}},
            current_sl_multiplier=2.0,
        )
        assert is_allowed is True
        assert mods["sl_multiplier"] == 4.0

    # --- #1236: AI-rule eval must not fail silently (CLAUDE.md §5.6) ---

    @patch("core.risk_manager.AILearnedRules.get_rules")
    def test_malformed_ai_rule_is_logged_not_swallowed(
        self, mock_get_rules, risk_manager, caplog
    ):
        """A rule whose evaluation raises must emit a WARNING (with traceback),
        not be silently swallowed by `except: pass` (#1236)."""
        # `value="boom"` makes `float(rule["value"])` raise inside the eval try.
        mock_get_rules.return_value = [
            {
                "trigger": {},
                "action": "reduce_size",
                "value": "boom",
                "status": "active",
                "id": "rule-boom",
            },
        ]

        with caplog.at_level(logging.WARNING):
            is_allowed, _reason, _mods = risk_manager.evaluate_new_trade(
                symbol="AAPL",
                side="BUY",
                market_data={"indicators": {"features": {}}},
                current_sl_multiplier=3.0,
            )

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "AI rule evaluation failed" in r.getMessage()
        ]
        assert len(warnings) == 1, "malformed AI rule was swallowed silently"
        assert warnings[0].exc_info is not None, "WARNING must carry the traceback"
        assert "rule-boom" in warnings[0].getMessage()
        # Per-rule fail-open is intentional: evaluation still returns Approved.
        assert is_allowed is True

    @patch("core.risk_manager.AILearnedRules.get_rules")
    def test_malformed_ai_rule_does_not_abort_evaluation(
        self, mock_get_rules, risk_manager
    ):
        """A single malformed rule must not abort evaluation of the rest:
        the following valid rule still takes effect (per-rule fail-open)."""
        mock_get_rules.return_value = [
            {
                "trigger": {},
                "action": "reduce_size",
                "value": "boom",  # raises
                "status": "active",
            },
            {
                "trigger": {},
                "action": "reduce_size",
                "value": 0.5,  # valid → must still apply
                "status": "active",
            },
        ]

        is_allowed, _reason, mods = risk_manager.evaluate_new_trade(
            symbol="AAPL",
            side="BUY",
            market_data={"indicators": {"features": {}}},
            current_sl_multiplier=3.0,
        )

        assert is_allowed is True
        assert mods["size_scaler"] == 0.5

    @patch("core.risk_manager.AILearnedRules.get_rules")
    def test_clean_ai_rules_emit_no_failure_warning(
        self, mock_get_rules, risk_manager, caplog
    ):
        """Well-formed rules must not produce any rule-failure WARNING."""
        mock_get_rules.return_value = [
            {
                "trigger": {},
                "action": "reduce_size",
                "value": 0.5,
                "status": "active",
            },
        ]

        with caplog.at_level(logging.WARNING):
            risk_manager.evaluate_new_trade(
                symbol="AAPL",
                side="BUY",
                market_data={"indicators": {"features": {}}},
                current_sl_multiplier=3.0,
            )

        assert not [
            r for r in caplog.records if "AI rule evaluation failed" in r.getMessage()
        ]

    @patch("config.MAX_TOTAL_EXPOSURE_PCT", 0.5, create=True)
    @patch(
        "config.COMPLIANCE_MAX_ORDER_VALUE", 1e12
    )  # isolate exposure cap from the ADR-C01 order cap
    def test_calculate_position_size_total_exposure_cap(self, risk_manager):
        """Test ADR exposure cap where sum of positions is limited."""
        risk_manager.client.get_all_positions.return_value = [
            {"market_value": "40000.0"}  # Existing 40k exposure (40%)
        ]

        # We try to buy max conviction -> $30,000 (30% cap)
        # But Max exposure = 50% ($50,000). Existing is $40,000.
        # Max new exposure should be $10,000.
        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=100.0,
            conviction_score=1.0,
        )

        # size should be 100 shares ($10,000)
        assert size == 100.0

    # --- BUG-AI-105 (#1240): exposure-cap verification failure must fail CLOSED ---

    @patch("config.MAX_TOTAL_EXPOSURE_PCT", 0.5, create=True)
    def test_exposure_cap_failure_fails_closed(self, risk_manager, caplog):
        """If the aggregate-exposure check cannot be verified (get_all_positions
        raises), sizing must fail CLOSED (0 shares) and log a WARNING — never
        silently skip the cap and let the order exceed it (BUG-AI-105)."""
        risk_manager.client.get_all_positions.side_effect = RuntimeError(
            "broker positions endpoint down"
        )

        with caplog.at_level(logging.WARNING):
            size = risk_manager.calculate_position_size(
                stop_loss_atr_multiplier=3.0,
                atr=2.0,
                current_price=100.0,
                conviction_score=1.0,  # would otherwise size a large position
            )

        # Fail-closed: no unverified exposure is added.
        assert size == 0.0
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "exposure cap check failed" in r.getMessage().lower()
        ]
        assert len(warnings) == 1, "exposure-cap failure was swallowed silently"
        assert warnings[0].exc_info is not None, "WARNING must carry the traceback"

    @patch("config.MAX_TOTAL_EXPOSURE_PCT", None, create=True)
    def test_exposure_cap_disabled_does_not_fail_closed(self, risk_manager):
        """When the cap is disabled (unset), the block is skipped entirely:
        positions are never queried and sizing is unaffected (no fail-closed)."""
        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=100.0,
            conviction_score=1.0,
        )

        assert size > 0.0
        risk_manager.client.get_all_positions.assert_not_called()

    @patch("config.KELLY_FRACTION_CAP", 0.5, create=True)
    @patch(
        "config.COMPLIANCE_MAX_ORDER_VALUE", 1e12
    )  # isolate kelly math from the ADR-C01 order cap
    def test_calculate_position_size_kelly_fraction(self, risk_manager):
        """Test ADR Kelly Fraction cap."""
        # Max conviction -> targets 25% (cap) of 100k = 25k
        # VIX scaling = 0.9 -> 22.5k -> 225 shares
        # Kelly Fraction = 0.5 -> Should halve the target to 112.5 shares
        size = risk_manager.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            current_price=100.0,
            conviction_score=1.0,
        )
        assert size == 112.5
