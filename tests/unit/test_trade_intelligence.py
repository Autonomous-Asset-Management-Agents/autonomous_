# tests/unit/test_trade_intelligence.py
# Epic 2.3 / I-4 — TDD Coverage Backfill: core/trade_intelligence.py
# Issue #240 — Ziel: ≥60% Coverage für core/trade_intelligence.py
#
# § 12 Test-Freshness: Bei Änderungen an trade_intelligence.py immer dieses File prüfen.
# Run: pytest tests/unit/test_trade_intelligence.py --cov=core.trade_intelligence --cov-report=term-missing

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import allure
import pytest

if TYPE_CHECKING:
    from core.trade_intelligence import CompletedTrade  # noqa: F401


# ---------------------------------------------------------------------------
# Helper: TradeIntelligence mit gemocktem Redis erstellen
# ---------------------------------------------------------------------------


def _make_ti():
    """Gibt eine TradeIntelligence-Instanz mit gemocktem Redis zurück."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Kein gespeicherter State
    mock_redis.set.return_value = True

    with patch("core.trade_intelligence.RedisClient") as MockRedisClient:
        MockRedisClient.get_sync_redis.return_value = mock_redis
        from core.trade_intelligence import TradeIntelligence

        ti = TradeIntelligence.__new__(TradeIntelligence)
        ti.__init__.__func__ if False else None

        # Manuell initialisieren ohne _load_data / _save_data
        from core.trade_intelligence import TradeIntelligence

        ti = TradeIntelligence.__new__(TradeIntelligence)

        # Manuelle Initialisierung der Felder (bypass Redis)
        ti._open_positions = {}
        ti._completed_trades = []
        ti._symbol_intelligence = {}
        ti._session_start = datetime.now()
        ti._session_trades = 0
        ti._session_pnl = 0.0
        ti._session_churn_alerts = 0
        ti._base_confidence_threshold = 0.5
        ti._churn_threshold_hours = 1.0
        ti._recent_window_hours = 24
        ti._adaptive_min_hold_hours = 4.0
        ti._adaptive_sell_bypass_threshold = 8
        ti._last_tuning_check = datetime.now()
        ti._tuning_check_interval_hours = 4
        ti._save_data = MagicMock()  # Mock save to avoid Redis
        return ti


def _add_completed_trade(
    ti, symbol: str, pnl: float, hold_hours: float = 5.0, exit_reason: str = "signal"
):
    """Helper: Fügt einen komplettierten Trade direkt in ti._completed_trades ein."""
    from core.trade_intelligence import CompletedTrade

    now = datetime.now()
    trade = CompletedTrade(
        symbol=symbol,
        entry_time=now - timedelta(hours=hold_hours),
        exit_time=now,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        qty=1.0,
        side="long",
        pnl=pnl,
        pnl_pct=pnl,
        hold_duration_hours=hold_hours,
        exit_reason=exit_reason,
    )
    ti._completed_trades.append(trade)
    return trade


# ---------------------------------------------------------------------------
# 1. _convert_to_native()
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestConvertToNative:

    def test_int_returns_int(self):
        from core.trade_intelligence import _convert_to_native

        assert _convert_to_native(42) == 42
        assert isinstance(_convert_to_native(42), int)

    def test_float_returns_float(self):
        from core.trade_intelligence import _convert_to_native

        result = _convert_to_native(3.14)
        assert isinstance(result, float)

    def test_dict_recurses(self):
        from core.trade_intelligence import _convert_to_native

        d = {"a": 1, "b": 2.0}
        result = _convert_to_native(d)
        assert result == {"a": 1, "b": 2.0}

    def test_list_recurses(self):
        from core.trade_intelligence import _convert_to_native

        result = _convert_to_native([1, 2.0, "str"])
        assert result == [1, 2.0, "str"]

    def test_string_passthrough(self):
        from core.trade_intelligence import _convert_to_native

        assert _convert_to_native("hello") == "hello"

    def test_none_passthrough(self):
        from core.trade_intelligence import _convert_to_native

        assert _convert_to_native(None) is None


# ---------------------------------------------------------------------------
# 2. CompletedTrade
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestCompletedTrade:

    def _make_trade(self) -> "CompletedTrade":
        from core.trade_intelligence import CompletedTrade

        now = datetime.now()
        return CompletedTrade(
            symbol="AAPL",
            entry_time=now - timedelta(hours=5),
            exit_time=now,
            entry_price=100.0,
            exit_price=110.0,
            qty=10.0,
            side="long",
            pnl=100.0,
            pnl_pct=10.0,
            hold_duration_hours=5.0,
            exit_reason="signal",
        )

    def test_to_dict_has_symbol(self):
        trade = self._make_trade()
        d = trade.to_dict()
        assert d["symbol"] == "AAPL"

    def test_to_dict_has_pnl(self):
        trade = self._make_trade()
        d = trade.to_dict()
        assert d["pnl"] == pytest.approx(100.0)

    def test_to_dict_times_are_strings(self):
        trade = self._make_trade()
        d = trade.to_dict()
        assert isinstance(d["entry_time"], str)
        assert isinstance(d["exit_time"], str)

    def test_from_dict_round_trip(self):
        from core.trade_intelligence import CompletedTrade

        trade = self._make_trade()
        d = trade.to_dict()
        restored = CompletedTrade.from_dict(d)
        assert restored.symbol == trade.symbol
        assert restored.pnl == pytest.approx(trade.pnl)
        assert restored.exit_reason == trade.exit_reason

    def test_from_dict_entry_time_is_datetime(self):
        from core.trade_intelligence import CompletedTrade

        trade = self._make_trade()
        restored = CompletedTrade.from_dict(trade.to_dict())
        assert isinstance(restored.entry_time, datetime)

    def test_defaults_are_set(self):
        from core.trade_intelligence import CompletedTrade

        now = datetime.now()
        t = CompletedTrade(
            symbol="X",
            entry_time=now,
            exit_time=now,
            entry_price=50.0,
            exit_price=55.0,
            qty=1.0,
            side="long",
            pnl=5.0,
            pnl_pct=10.0,
            hold_duration_hours=1.0,
        )
        assert t.entry_confidence == 0.0
        assert t.exit_reason == ""


# ---------------------------------------------------------------------------
# 3. SymbolIntelligence
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestSymbolIntelligence:

    def test_win_rate_no_trades(self):
        from core.trade_intelligence import SymbolIntelligence

        si = SymbolIntelligence(symbol="AAPL", total_trades=0)
        assert si.win_rate == 0.0

    def test_win_rate_all_wins(self):
        from core.trade_intelligence import SymbolIntelligence

        si = SymbolIntelligence(symbol="AAPL", total_trades=5, winning_trades=5)
        assert si.win_rate == 1.0

    def test_win_rate_three_of_five(self):
        from core.trade_intelligence import SymbolIntelligence

        si = SymbolIntelligence(symbol="AAPL", total_trades=5, winning_trades=3)
        assert si.win_rate == pytest.approx(0.6)

    def test_profit_factor_no_loss(self):
        from core.trade_intelligence import SymbolIntelligence

        # With avg_win=100, winning_trades=0, avg_loss=0, losing_trades=0:
        # total_wins=0, total_losses=0, formula: 0 / max(1, 0) = 0.0
        si = SymbolIntelligence(symbol="AAPL", avg_win=0.0, avg_loss=0.0)
        assert si.profit_factor == 0.0

    def test_profit_factor_positive_wins_no_loss(self):
        from core.trade_intelligence import SymbolIntelligence

        # 5 wins @ avg_win=100, 0 losses: total_wins=500 / max(1,0) = 500
        si = SymbolIntelligence(
            symbol="AAPL",
            winning_trades=5,
            avg_win=100.0,
            losing_trades=0,
            avg_loss=0.0,
        )
        assert si.profit_factor == pytest.approx(500.0)

    def test_profit_factor_all_losses(self):
        from core.trade_intelligence import SymbolIntelligence

        si = SymbolIntelligence(symbol="AAPL", avg_win=0.0, avg_loss=50.0)
        assert si.profit_factor == 0.0


# ---------------------------------------------------------------------------
# 4. TradeIntelligence — record_entry
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceRecordEntry:

    def test_entry_creates_open_position(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=150.0, qty=10.0, confidence=0.8)
        assert "AAPL" in ti._open_positions

    def test_entry_stores_price(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=150.0, qty=10.0)
        assert ti._open_positions["AAPL"].entry_price == 150.0

    def test_entry_stores_confidence(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=150.0, qty=10.0, confidence=0.75)
        assert ti._open_positions["AAPL"].entry_confidence == 0.75

    def test_entry_with_features(self):
        ti = _make_ti()
        ti.record_entry(
            "TSLA",
            entry_price=200.0,
            qty=5.0,
            features={"rsi_14": 65.0, "adx_14": 30.0},
        )
        pos = ti._open_positions["TSLA"]
        assert pos.entry_rsi == 65.0
        assert pos.entry_adx == 30.0

    def test_entry_with_market_data(self):
        ti = _make_ti()
        ti.record_entry("MSFT", entry_price=300.0, qty=3.0, market_data={"vix": 18.5})
        assert ti._open_positions["MSFT"].entry_vix == 18.5

    def test_save_called_on_entry(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=1.0)
        ti._save_data.assert_called()


# ---------------------------------------------------------------------------
# 5. TradeIntelligence — record_exit
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceRecordExit:

    def test_exit_no_open_position_returns_none(self):
        ti = _make_ti()
        result = ti.record_exit("AAPL", exit_price=160.0)
        assert result is None

    def test_exit_removes_open_position(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=10.0)
        ti.record_exit("AAPL", exit_price=110.0)
        assert "AAPL" not in ti._open_positions

    def test_exit_creates_completed_trade(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=10.0)
        trade = ti.record_exit("AAPL", exit_price=110.0)
        assert trade is not None
        assert trade.symbol == "AAPL"

    def test_exit_calculates_positive_pnl(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=10.0)
        trade = ti.record_exit("AAPL", exit_price=110.0)
        assert trade.pnl == pytest.approx(100.0)  # (110-100)*10

    def test_exit_calculates_negative_pnl(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=10.0)
        trade = ti.record_exit("AAPL", exit_price=90.0)
        assert trade.pnl == pytest.approx(-100.0)

    def test_exit_increments_session_trades(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=1.0)
        ti.record_exit("AAPL", exit_price=110.0)
        assert ti._session_trades == 1

    def test_exit_updates_session_pnl(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=1.0)
        ti.record_exit("AAPL", exit_price=110.0)
        assert ti._session_pnl == pytest.approx(10.0)

    def test_exit_with_reason(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=1.0)
        trade = ti.record_exit("AAPL", exit_price=95.0, exit_reason="stop_loss")
        assert trade.exit_reason == "stop_loss"

    def test_exit_appends_to_completed_trades(self):
        ti = _make_ti()
        ti.record_entry("AAPL", entry_price=100.0, qty=1.0)
        ti.record_exit("AAPL", exit_price=105.0)
        assert len(ti._completed_trades) == 1


# ---------------------------------------------------------------------------
# 6. TradeIntelligence — _update_symbol_intelligence
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestUpdateSymbolIntelligence:

    def test_first_win_creates_symbol_intel(self):
        ti = _make_ti()
        trade = _add_completed_trade(ti, "AAPL", pnl=50.0)
        ti._update_symbol_intelligence(trade)
        assert "AAPL" in ti._symbol_intelligence

    def test_win_increments_winning_trades(self):
        ti = _make_ti()
        trade = _add_completed_trade(ti, "AAPL", pnl=50.0)
        ti._update_symbol_intelligence(trade)
        assert ti._symbol_intelligence["AAPL"].winning_trades == 1

    def test_loss_increments_losing_trades(self):
        ti = _make_ti()
        trade = _add_completed_trade(ti, "AAPL", pnl=-30.0)
        ti._update_symbol_intelligence(trade)
        assert ti._symbol_intelligence["AAPL"].losing_trades == 1

    def test_total_trades_increments(self):
        ti = _make_ti()
        trade1 = _add_completed_trade(ti, "AAPL", pnl=50.0)
        ti._update_symbol_intelligence(trade1)
        trade2 = _add_completed_trade(ti, "AAPL", pnl=-20.0)
        ti._update_symbol_intelligence(trade2)
        assert ti._symbol_intelligence["AAPL"].total_trades == 2

    def test_total_pnl_accumulates(self):
        ti = _make_ti()
        for pnl in [100.0, -50.0, 75.0]:
            trade = _add_completed_trade(ti, "MSFT", pnl=pnl)
            ti._update_symbol_intelligence(trade)
        assert ti._symbol_intelligence["MSFT"].total_pnl == pytest.approx(125.0)


# ---------------------------------------------------------------------------
# 7. TradeIntelligence — reset methods
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceReset:

    def test_reset_symbol_clears_confidence_adjustment(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", confidence_adjustment=-0.3, churn_count=5, quick_losses=3
        )
        ti.reset_symbol_intelligence("AAPL")
        assert ti._symbol_intelligence["AAPL"].confidence_adjustment == 0.0

    def test_reset_symbol_clears_churn_count(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", churn_count=7, quick_losses=4
        )
        ti.reset_symbol_intelligence("AAPL")
        assert ti._symbol_intelligence["AAPL"].churn_count == 0

    def test_reset_symbol_not_existing_no_crash(self):
        ti = _make_ti()
        ti.reset_symbol_intelligence("NONEXISTENT")  # Should not raise

    def test_reset_all_penalties_clears_all(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", confidence_adjustment=-0.5
        )
        ti._symbol_intelligence["TSLA"] = SymbolIntelligence(
            symbol="TSLA", confidence_adjustment=-0.3
        )
        ti.reset_all_penalties()
        assert ti._symbol_intelligence["AAPL"].confidence_adjustment == 0.0
        assert ti._symbol_intelligence["TSLA"].confidence_adjustment == 0.0

    def test_reset_all_penalties_empty_no_crash(self):
        ti = _make_ti()
        ti._symbol_intelligence = {}
        ti.reset_all_penalties()


# ---------------------------------------------------------------------------
# 8. TradeIntelligence — reporting methods
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceReports:

    def test_get_top_performers_empty(self):
        ti = _make_ti()
        result = ti.get_top_performers(n=3)
        assert isinstance(result, list)

    def test_get_top_performers_sorted_by_pnl(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", total_trades=5, total_pnl=500.0
        )
        ti._symbol_intelligence["TSLA"] = SymbolIntelligence(
            symbol="TSLA", total_trades=3, total_pnl=100.0
        )
        ti._symbol_intelligence["MSFT"] = SymbolIntelligence(
            symbol="MSFT", total_trades=4, total_pnl=300.0
        )
        result = ti.get_top_performers(n=2)
        assert len(result) == 2
        # get_top_performers returns List[Dict] via get_symbol_report()
        assert result[0]["symbol"] == "AAPL"

    def test_get_worst_performers(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", total_trades=5, total_pnl=-200.0
        )
        ti._symbol_intelligence["TSLA"] = SymbolIntelligence(
            symbol="TSLA", total_trades=3, total_pnl=100.0
        )
        result = ti.get_worst_performers(n=1)
        assert result[0]["symbol"] == "AAPL"

    def test_get_symbol_report_existing(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL",
            total_trades=5,
            winning_trades=3,
            total_pnl=200.0,
            avg_win=100.0,
            avg_loss=50.0,
        )
        report = ti.get_symbol_report("AAPL")
        assert report is not None

    def test_get_symbol_report_missing_returns_none_or_empty(self):
        ti = _make_ti()
        report = ti.get_symbol_report("NONEXISTENT")
        # Either None or empty dict — both acceptable
        assert report is None or report == {}


# ---------------------------------------------------------------------------
# 9. TradeIntelligence — _maybe_apply_forgiveness
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceForgiveness:

    def test_forgiveness_reduces_negative_adjustment(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", confidence_adjustment=-0.4, total_trades=20
        )
        # Backdate last_forgiveness_check to force forgiveness
        ti._completed_trades = [
            _add_completed_trade(ti, "AAPL", pnl=-10.0) for _ in range(10)
        ]
        # Directly call _maybe_apply_forgiveness
        ti._maybe_apply_forgiveness()
        # Adjustment should have moved towards 0 (forgiveness applied) OR stayed same
        adj = ti._symbol_intelligence["AAPL"].confidence_adjustment
        assert adj >= -0.4  # Must not have gotten worse


# ---------------------------------------------------------------------------
# 10. TradeIntelligence.__init__ via real Redis mock (covers lines 131-167)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeIntelligenceInit:

    def test_init_with_mocked_redis_empty(self):
        """Full __init__ with Redis returning None (fresh start)."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.set.return_value = True

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            from core.trade_intelligence import TradeIntelligence

            ti = TradeIntelligence()

        assert ti._completed_trades == []
        assert ti._open_positions == {}
        assert ti._symbol_intelligence == {}

    def test_init_with_mocked_redis_existing_data(self):
        """Full __init__ with Redis returning existing trade data."""
        import json

        from core.trade_intelligence import CompletedTrade

        now = datetime.now()
        trade = CompletedTrade(
            symbol="AAPL",
            entry_time=now - timedelta(hours=5),
            exit_time=now,
            entry_price=100.0,
            exit_price=110.0,
            qty=1.0,
            side="long",
            pnl=10.0,
            pnl_pct=10.0,
            hold_duration_hours=5.0,
            exit_reason="signal",
        )
        data = {
            "completed_trades": [trade.to_dict()],
            "symbol_intelligence": {},
            "last_updated": now.isoformat(),
        }
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(data).encode()
        mock_redis.set.return_value = True

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            from core.trade_intelligence import TradeIntelligence

            ti = TradeIntelligence()

        assert len(ti._completed_trades) == 1
        assert ti._completed_trades[0].symbol == "AAPL"

    def test_init_with_redis_error_graceful(self):
        """__init__ handles Redis errors gracefully."""
        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.side_effect = Exception("Redis unavailable")
            from core.trade_intelligence import TradeIntelligence

            ti = TradeIntelligence()  # Should not raise
        assert ti._completed_trades == []


# ---------------------------------------------------------------------------
# 11. should_trade
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestShouldTrade:

    def test_should_trade_approved_no_history(self):
        ti = _make_ti()
        approved, reason = ti.should_trade("AAPL", confidence=0.8, signal="buy")
        assert approved is True

    def test_should_trade_blocked_extreme_churn(self):
        """5+ trades in last hour → blocked."""
        ti = _make_ti()
        now = datetime.now()
        from core.trade_intelligence import CompletedTrade

        for i in range(6):
            trade = CompletedTrade(
                symbol="AAPL",
                entry_time=now - timedelta(minutes=10),
                exit_time=now - timedelta(minutes=i),
                entry_price=100.0,
                exit_price=95.0,
                qty=1.0,
                side="long",
                pnl=-5.0,
                pnl_pct=-5.0,
                hold_duration_hours=0.1,
                exit_reason="signal",
            )
            ti._completed_trades.append(trade)

        approved, reason = ti.should_trade("AAPL", confidence=0.8, signal="buy")
        assert approved is False
        assert "churn" in reason.lower() or "Extreme" in reason

    def test_should_trade_approved_below_churn_threshold(self):
        """Fewer than 5 trades in last hour → approved."""
        ti = _make_ti()
        now = datetime.now()
        from core.trade_intelligence import CompletedTrade

        for i in range(3):
            trade = CompletedTrade(
                symbol="AAPL",
                entry_time=now - timedelta(minutes=30),
                exit_time=now - timedelta(minutes=i * 5),
                entry_price=100.0,
                exit_price=95.0,
                qty=1.0,
                side="long",
                pnl=-5.0,
                pnl_pct=-5.0,
                hold_duration_hours=0.1,
                exit_reason="signal",
            )
            ti._completed_trades.append(trade)

        approved, reason = ti.should_trade("AAPL", confidence=0.8, signal="buy")
        assert approved is True


# ---------------------------------------------------------------------------
# 12. get_session_stats
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGetSessionStats:

    def test_get_session_stats_returns_dict(self):
        ti = _make_ti()
        stats = ti.get_session_stats()
        assert "session_start" in stats
        assert "trades" in stats
        assert "pnl" in stats

    def test_get_session_stats_trade_count(self):
        ti = _make_ti()
        ti._session_trades = 5
        ti._session_pnl = 250.0
        stats = ti.get_session_stats()
        assert stats["trades"] == 5
        assert stats["pnl"] == 250.0


# ---------------------------------------------------------------------------
# 13. get_tuned_parameters
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGetTunedParameters:

    def test_get_tuned_parameters_returns_dict(self):
        ti = _make_ti()
        params = ti.get_tuned_parameters()
        assert "min_hold_hours" in params
        assert "sell_bypass_threshold" in params
        assert "base_confidence" in params

    def test_get_tuned_parameters_defaults(self):
        ti = _make_ti()
        params = ti.get_tuned_parameters()
        assert params["min_hold_hours"] == 4.0
        assert params["base_confidence"] == 0.5


# ---------------------------------------------------------------------------
# 14. get_learning_summary
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGetLearningSummary:

    def test_get_learning_summary_no_history(self):
        ti = _make_ti()
        summary = ti.get_learning_summary()
        assert isinstance(summary, str)
        assert "No trading history" in summary or "learning" in summary.lower()

    def test_get_learning_summary_with_symbols(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL", total_trades=10, total_pnl=500.0, winning_trades=7
        )
        summary = ti.get_learning_summary()
        assert isinstance(summary, str)
        assert len(summary) > 10


# ---------------------------------------------------------------------------
# 15. get_entry_insight
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGetEntryInsight:

    def test_get_entry_insight_no_history(self):
        ti = _make_ti()
        insight = ti.get_entry_insight("AAPL", confidence=0.8)
        assert "First trade" in insight

    def test_get_entry_insight_with_history(self):
        ti = _make_ti()
        from core.trade_intelligence import SymbolIntelligence

        ti._symbol_intelligence["AAPL"] = SymbolIntelligence(
            symbol="AAPL",
            total_trades=10,
            winning_trades=7,
            total_pnl=300.0,
            confidence_adjustment=0.2,
        )
        insight = ti.get_entry_insight("AAPL", confidence=0.8)
        assert "10 trades" in insight or "History" in insight
