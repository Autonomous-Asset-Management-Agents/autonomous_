"""
tests/integration/test_golden_path.py
========================================
Q2 Golden Path Story-Tests — Vollstaendiger Trade-Zyklus

Testet das korrekte ZUSAMMENSPIEL von:
  RiskManager -> ComplianceGuardian -> should_sell_smart (SmartExit)

Kein Alpaca-API-Call. Kein Netzwerk. 100% deterministisch.
Alle externen Abhaengigkeiten (Redis, CloudLogger, KillSwitch) werden gemockt.

Szenarien:
  STORY-01: Profitabler Trade -- BUY -> HOLD -> TAKE-PROFIT
  STORY-02: Verlust mit korrektem Stop-Loss (-7%)
  STORY-03: Compliance blockiert Wash-Trade
  STORY-04: Iron Dome -- Drawdown-Block + Reset-Zyklus

Coding Policy: SS11.5 TDD | SS14 Docker-First (kein lokaler Redis noetig)
Run: pytest tests/integration/test_golden_path.py -v
"""

import time
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _suppress_cloud_logger():
    """Verhindert Cloud-Logging-Calls in allen Story-Tests."""
    with patch("core.compliance.get_cloud_logger") as mock_gl:
        mock_gl.return_value = MagicMock()
        yield


@pytest.fixture
def guardian():
    """Sauberer ComplianceGuardian fuer jeden Test."""
    from core.compliance import ComplianceGuardian

    g = ComplianceGuardian()
    g._recent_trades = []
    g.daily_trades = 0
    return g


@pytest.fixture
def risk_manager():
    """RiskManager mit 100k Kapital, Redis-frei, CloudLogger stumm."""
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        with patch("core.risk_manager.AILearnedRules") as MockRules:
            MockRules.return_value.get_rules.return_value = []
            from core.risk_manager import RiskManager

            mock_client = MagicMock()
            mock_client.get_all_positions.return_value = []

            rm = RiskManager(
                client=mock_client,
                total_capital=100_000.0,
                risk_per_trade_percent=0.02,
                daily_drawdown_limit_percent=0.175,
            )
            rm.ai_rules_singleton = MockRules.return_value
            return rm


def _make_order(symbol="AAPL", side="buy", qty=10, price=150.0):
    return {
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "strategy_id": "golden_path_test",
        "timestamp": time.time(),
    }


def _calc_position_size(rm, vix=15.0, price=150.0, cash=50_000.0):
    """Berechnet Positionsgroesse mit gemocktem KillSwitch."""
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        return rm.calculate_position_size(
            stop_loss_atr_multiplier=2.0,
            atr=1.5,
            confidence="medium",
            market_data={"vix": vix},
            current_price=price,
            account_cash=cash,
            conviction_score=0.7,
        )


# ---------------------------------------------------------------------------
# STORY-01: Profitabler Trade -- BUY -> HOLD -> TAKE-PROFIT
# ---------------------------------------------------------------------------


class TestStory01ProfitableTrade:
    """
    Szenario: Normaler Markt, starkes Signal, korrekte Ausfuehrung.

    Given: System mit 100k Kapital, VIX=15, Conviction=0.7
    When:  BUY-Order durch Compliance + RiskManager
    Then:  Order wird genehmigt, Positionsgroesse > 0
    And:   Nach +28% Kursanstieg loest SmartExit TAKE-PROFIT aus
    """

    def test_story01a_order_passes_compliance(self, guardian):
        """Order mit korrekten MiFID-Feldern muss genehmigt werden."""
        order = _make_order(price=150.0, qty=5)  # Wert = 750 < 10k Limit
        approved = guardian.check_order(order)
        assert approved is True, "Valide Order muss Compliance passieren"
        assert guardian.cloud_logger.log_compliance_event.called

    def test_story01b_risk_manager_allocates_position(self, risk_manager):
        """RiskManager soll bei normalen Bedingungen eine Positionsgroesse > 0 zuteilen."""
        size = _calc_position_size(risk_manager, price=150.0)
        assert size > 0, "RiskManager muss bei normalen Bedingungen Kapital zuteilen"

    def test_story01c_take_profit_triggers_after_gain(self):
        """SmartExit muss TAKE-PROFIT bei +28% ausloesen (ueber TAKE_PROFIT_PCT=25%)."""
        from core.smart_exit import should_sell_smart

        # smart_take_profit=False: reines Festziel ohne ATR-Scaling
        result = should_sell_smart(
            symbol="AAPL",
            entry_price=100.0,
            current_price=128.0,  # +28% -- klar ueber 25% Limit
            high_water_mark=128.0,
            hours_held=5.0,
            in_top_n=True,
            lstm_rank=3,
            atr_pct=None,
            smart_take_profit=False,
        )
        assert (
            result.action == "SELL"
        ), f"Take-Profit muss SELL ausloesen, got: {result.action} | {result.reason}"
        assert (
            "Take-profit" in result.reason
        ), f"Grund muss Take-profit sein, got: {result.reason}"

    def test_story01_full_cycle_no_exception(self, guardian, risk_manager):
        """Gesamter Zyklus (Compliance -> RiskManager -> SmartExit) darf nicht crashen."""
        from core.smart_exit import should_sell_smart

        # Step 1: Compliance
        order = _make_order(price=150.0, qty=5)
        assert guardian.check_order(order) is True

        # Step 2: RiskManager skaliert Position
        size = _calc_position_size(risk_manager, price=150.0)
        assert size >= 0

        # Step 3: SmartExit bei +26% Gewinn
        result = should_sell_smart(
            "AAPL",
            150.0,
            189.0,
            189.0,
            hours_held=5.0,
            in_top_n=True,
            lstm_rank=3,
            smart_take_profit=False,
        )
        assert result.action in ("SELL", "HOLD"), f"Unerwartete Aktion: {result.action}"


# ---------------------------------------------------------------------------
# STORY-02: Stop-Loss bei Kursverlust
# ---------------------------------------------------------------------------


class TestStory02StopLoss:
    """
    Szenario: Kurs faellt nach Buy um mehr als 7% -- Stop-Loss muss greifen.

    Given: Position bei Einstieg 150.0
    When:  Kurs faellt auf 138.5 (-7.67%, unter STOP_LOSS_PCT=7.0%)
    Then:  SmartExit gibt SELL mit reason='Stop-loss' zurueck
    """

    def test_story02_stop_loss_triggers(self):
        """Stop-Loss bei > -7% muss SELL ausloesen."""
        from core.smart_exit import should_sell_smart

        result = should_sell_smart(
            symbol="AAPL",
            entry_price=150.0,
            current_price=138.5,  # -7.67% -- unter STOP_LOSS_PCT=7.0%
            high_water_mark=152.0,
            hours_held=3.0,
            in_top_n=True,
            lstm_rank=3,
        )
        assert (
            result.action == "SELL"
        ), f"Stop-Loss muss SELL ausloesen, got: {result.action} | {result.reason}"
        assert (
            "Stop-loss" in result.reason
        ), f"Grund muss Stop-loss sein, got: {result.reason}"

    def test_story02_loss_within_expected_bound(self):
        """Verlust beim Stop-Loss muss innerhalb des STOP_LOSS_PCT-Limits sein."""
        from core.smart_exit import should_sell_smart, STOP_LOSS_PCT

        entry = 150.0
        # 1 Cent unter Stop-Loss-Schwelle -- soll triggern
        trigger_price = entry * (1 - STOP_LOSS_PCT / 100) - 0.01
        result = should_sell_smart(
            "AAPL",
            entry,
            trigger_price,
            entry,
            hours_held=3.0,
            in_top_n=True,
            lstm_rank=3,
        )

        assert result.action == "SELL", (
            f"Stop-Loss muss triggern bei {trigger_price:.2f} "
            f"(entry={entry}, STOP_LOSS_PCT={STOP_LOSS_PCT}%)"
        )
        # Verlust darf nicht mehr als 10% ueber Limit liegen
        actual_loss_pct = ((entry - trigger_price) / entry) * 100
        assert (
            actual_loss_pct <= STOP_LOSS_PCT * 1.1
        ), f"Verlust {actual_loss_pct:.2f}% liegt weit ueber Limit {STOP_LOSS_PCT}%"


# ---------------------------------------------------------------------------
# STORY-03: Compliance blockiert Wash-Trade
# ---------------------------------------------------------------------------


class TestStory03WashTradeBlock:
    """
    Szenario: BUY dann sofort SELL auf dasselbe Symbol -- Wash-Trade.

    Given: AAPL wurde soeben gekauft (innerhalb 60s)
    When:  SELL-Order fuer AAPL wird submitted
    Then:  ComplianceGuardian.check_order() gibt False zurueck
    """

    def test_story03_wash_trade_blocked(self, guardian):
        """Sofortiger Gegenhandel muss als Wash-Trade erkannt und geblockt werden."""
        buy_order = _make_order(side="buy", price=150.0, qty=5)
        assert guardian.check_order(buy_order) is True

        sell_order = _make_order(side="sell", price=150.0, qty=5)
        assert (
            guardian.check_order(sell_order) is False
        ), "Wash-Trade muss geblockt werden"

    def test_story03_wash_trade_does_not_increment_counter(self, guardian):
        """Geblockter Wash-Trade darf daily_trades nicht erhoehen."""
        initial_count = guardian.daily_trades

        guardian.check_order(_make_order(side="buy"))
        guardian.check_order(_make_order(side="sell"))  # wird geblockt

        assert (
            guardian.daily_trades == initial_count
        ), "Geblockter Wash-Trade darf daily_trades nicht beeinflussen"

    def test_story03_different_symbol_not_blocked(self, guardian):
        """SELL auf anderem Symbol nach AAPL-BUY ist kein Wash-Trade."""
        guardian.check_order(_make_order(symbol="AAPL", side="buy"))
        result = guardian.check_order(_make_order(symbol="MSFT", side="sell"))
        assert result is True, "SELL auf anderem Symbol muss durchgehen"


# ---------------------------------------------------------------------------
# STORY-04: Iron Dome -- Drawdown-Block
# ---------------------------------------------------------------------------


class TestStory04IronDomeDrawdownBlock:
    """
    Szenario: Account-Equity faellt um 17.5% -- Kill-Switch greift.

    Given: System startet mit 100k Kapital
    When:  Equity faellt auf 82.4k (> 17.5% Drawdown)
    Then:  trading_halted=True, neue Position = 0
    """

    def test_story04_drawdown_triggers_halt(self, risk_manager):
        """17.5% Drawdown muss trading_halted=True setzen."""
        risk_manager.peak_daily_equity = 100_000.0
        risk_manager.daily_drawdown_limit = 17_500.0

        with patch("core.kill_switch.kill_switch"):
            risk_manager.update_account_equity(82_400.0)  # -17.6%

        assert risk_manager.trading_halted is True

    def test_story04_halted_system_rejects_new_orders(self, risk_manager):
        """Neuer Trade darf nicht ausgefuehrt werden wenn System halted ist."""
        risk_manager.trading_halted = True

        with patch("core.kill_switch.kill_switch") as ks:
            ks.is_halted.return_value = True
            size = risk_manager.calculate_position_size(
                stop_loss_atr_multiplier=2.0,
                atr=1.5,
                confidence="high",
                market_data={"vix": 15},
                current_price=150.0,
                account_cash=50_000.0,
                conviction_score=0.9,
            )
        assert size == 0.0, f"Halted System darf keine Position zuteilen, got: {size}"

    def test_story04_warning_phase_reduces_but_not_stops(self, risk_manager):
        """~60% des DD-Limits -> trading_reduced=True, aber noch nicht halted."""
        risk_manager.peak_daily_equity = 100_000.0
        risk_manager.daily_drawdown_limit = 17_500.0
        risk_manager.portfolio_stop_loss_pct = 0.0

        with patch("core.kill_switch.kill_switch"):
            risk_manager.update_account_equity(89_000.0)  # ~63% des Limits

        assert risk_manager.trading_reduced is True
        assert risk_manager.trading_halted is False

    def test_story04_full_iron_dome_cycle(self, risk_manager):
        """Normal -> Warnung -> Halt -> Reset -> Wiederaufnahme."""
        risk_manager.peak_daily_equity = 100_000.0
        risk_manager.daily_drawdown_limit = 17_500.0
        risk_manager.portfolio_stop_loss_pct = 0.0

        with patch("core.kill_switch.kill_switch"):
            risk_manager.update_account_equity(100_000.0)
            assert not risk_manager.trading_halted
            assert not risk_manager.trading_reduced

            risk_manager.update_account_equity(89_000.0)
            assert risk_manager.trading_reduced
            assert not risk_manager.trading_halted

            risk_manager.update_account_equity(82_000.0)
            assert risk_manager.trading_halted

        with patch("core.kill_switch.kill_switch"):
            risk_manager.reset_daily_limit(100_000.0)

        assert (
            not risk_manager.trading_halted
        ), "Nach Reset muss Handel wieder moeglich sein"
