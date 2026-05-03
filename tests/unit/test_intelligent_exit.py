# tests/unit/test_intelligent_exit.py
# Epic 2.4 — Intelligent Exit System: TDD
# Iron Dome Coverage target: ≥40% for core/intelligent_exit.py
#
# Gherkin (Architect Blueprint):
#   Given: A position with known PnL, hours held, and LSTM prediction
#   When:  analyze_exit(PositionContext) is called
#   Then:  ExitAnalysis.should_sell reflects the 5-tier loss/winner rules
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.intelligent_exit import (
    ExitAnalysis,
    PositionContext,
    analyze_exit,
    get_dynamic_trailing_stop_pct,
    _calculate_loss_pressure,
    _calculate_trailing_stop_score,
    _calculate_momentum_fade,
    _calculate_news_pressure,
    _calculate_time_pressure,
    HARD_STOP_LOSS_PCT,
    TRAIL_PROFIT_TIERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    symbol: str = "TEST",
    entry: float = 100.0,
    current: float = 100.0,
    hwm: float = 100.0,
    hours: float = 5.0,
    lstm: float = 0.0,
    news: float = 0.0,
    momentum: list | None = None,
) -> PositionContext:
    return PositionContext(
        symbol=symbol,
        entry_price=entry,
        current_price=current,
        high_water_mark=hwm,
        hours_held=hours,
        entry_time=datetime.now() - timedelta(hours=hours),
        lstm_prediction=lstm,
        news_score=news,
        momentum_history=momentum or [],
    )


# ---------------------------------------------------------------------------
# 1. Hard Stop Loss — kein Override möglich
# ---------------------------------------------------------------------------


class TestHardStop:
    def test_hard_stop_triggers_regardless_of_hours(self):
        """HARD STOP bei -8% auch in Panic-Protection-Phase (< 2h)."""
        ctx = _ctx(entry=100.0, current=91.5, hwm=100.0, hours=0.5)
        result = analyze_exit(ctx)
        assert result.should_sell is True
        assert "HARD STOP" in result.reason

    def test_loss_pressure_100_at_hard_stop(self):
        score = _calculate_loss_pressure(-8.0, 1.0)
        assert score == 100.0

    def test_loss_pressure_100_beyond_hard_stop(self):
        score = _calculate_loss_pressure(-15.0, 1.0)
        assert score == 100.0


# ---------------------------------------------------------------------------
# 2. Panic Protection — keine Verkäufe in ersten 2h (außer Hard Stop)
# ---------------------------------------------------------------------------


class TestPanicProtection:
    def test_no_sell_in_first_2h_at_minus_3pct(self):
        """Position bei -3%, 1h gehalten → keine Panic-Sell."""
        ctx = _ctx(
            entry=100.0, current=97.0, hwm=100.0, hours=1.0, lstm=-0.5, news=-0.5
        )
        result = analyze_exit(ctx)
        assert result.should_sell is False

    def test_sell_allowed_after_2h(self):
        """-5% Verlust nach 30h + bearish LSTM → Score > 70."""
        ctx = _ctx(
            entry=100.0, current=95.0, hwm=100.0, hours=30.0, lstm=-0.4, news=-0.4
        )
        result = analyze_exit(ctx)
        assert result.total_score > 70


# ---------------------------------------------------------------------------
# 3. Loss Pressure Tiers
# ---------------------------------------------------------------------------


class TestLossPressure:
    @pytest.mark.parametrize(
        "pnl,hours,expected_min",
        [
            (-2.1, 5.0, 35.0),  # Tier 1: base_score ~40
            (-4.1, 5.0, 60.0),  # Tier 2: base_score 70
            (-6.1, 5.0, 80.0),  # Tier 3: base_score 90
        ],
    )
    def test_loss_tiers(self, pnl: float, hours: float, expected_min: float):
        score = _calculate_loss_pressure(pnl, hours)
        assert score >= expected_min

    def test_time_multiplier_increases_score(self):
        score_4h = _calculate_loss_pressure(-4.5, 4.0)
        score_24h = _calculate_loss_pressure(-4.5, 24.0)
        assert score_24h > score_4h

    def test_positive_pnl_returns_zero(self):
        assert _calculate_loss_pressure(5.0, 10.0) == 0.0


# ---------------------------------------------------------------------------
# 4. Trailing Stop — Gewinner laufen lassen
# ---------------------------------------------------------------------------


class TestTrailingStop:
    def test_no_trailing_for_losers(self):
        score = _calculate_trailing_stop_score(-5.0, 3.0, 5.0)
        assert score == 0.0

    def test_trailing_triggers_when_drawdown_exceeds_tier(self):
        """At +25% profit (tier: 6% trailing), a 8% drawdown should trigger."""
        score = _calculate_trailing_stop_score(25.0, 8.0, 12.0)
        assert score >= 85.0

    def test_no_trigger_below_trailing_threshold(self):
        """At +25% profit, a 3% drawdown (below 6% trailing) should not trigger."""
        score = _calculate_trailing_stop_score(25.0, 3.0, 12.0)
        assert score < 85.0

    def test_min_hold_prevents_trailing(self):
        """At +25% profit, held only 1h (tier requires 8h) → no trigger."""
        score = _calculate_trailing_stop_score(25.0, 8.0, 1.0)
        assert score == 0.0

    @pytest.mark.parametrize(
        "pnl,expected_trail",
        [
            (1.5, 1.5),  # below first tier → default 1.5%
            (2.0, 1.5),
            (5.0, 2.5),
            (10.0, 4.0),
            (20.0, 6.0),
            (35.0, 8.0),
            (50.0, 8.0),  # above last tier → max 8%
        ],
    )
    def test_get_dynamic_trailing_stop_pct(self, pnl: float, expected_trail: float):
        assert get_dynamic_trailing_stop_pct(pnl) == expected_trail


# ---------------------------------------------------------------------------
# 5. Momentum Fade
# ---------------------------------------------------------------------------


class TestMomentumFade:
    def test_bearish_lstm_on_winner_raises_score(self):
        score = _calculate_momentum_fade(-0.6, [], 10.0)
        assert score >= 60.0

    def test_bearish_lstm_on_loser_raises_score_less(self):
        """Bearish LSTM on loser still adds pressure but less than on winner."""
        score_winner = _calculate_momentum_fade(-0.6, [], 10.0)
        score_loser = _calculate_momentum_fade(-0.6, [], -5.0)
        assert score_winner > score_loser

    def test_falling_momentum_history_adds_25_points(self):
        score_no_history = _calculate_momentum_fade(-0.4, [], 3.0)
        score_with_history = _calculate_momentum_fade(-0.4, [0.3, 0.1, -0.1], 3.0)
        assert score_with_history > score_no_history

    def test_positive_lstm_no_score(self):
        score = _calculate_momentum_fade(0.5, [0.2, 0.3, 0.4], 10.0)
        assert score == 0.0


# ---------------------------------------------------------------------------
# 6. News Pressure
# ---------------------------------------------------------------------------


class TestNewsPressure:
    def test_positive_news_no_pressure(self):
        assert _calculate_news_pressure(0.5, -2.0) == 0.0

    def test_negative_news_adds_pressure(self):
        score = _calculate_news_pressure(-0.6, -2.0)
        assert score > 0.0

    def test_negative_news_amplified_on_loser(self):
        score_flat = _calculate_news_pressure(-0.5, 0.0)
        score_losing = _calculate_news_pressure(-0.5, -3.0)
        assert score_losing > score_flat


# ---------------------------------------------------------------------------
# 7. Time Pressure
# ---------------------------------------------------------------------------


class TestTimePressure:
    def test_no_time_pressure_for_winners(self):
        assert _calculate_time_pressure(5.0, 100.0) == 0.0

    def test_no_time_pressure_for_losers_under_4h(self):
        assert _calculate_time_pressure(-2.0, 3.0) == 0.0

    def test_time_pressure_grows_with_hours_for_losers(self):
        score_4h = _calculate_time_pressure(-3.0, 4.0)
        score_50h = _calculate_time_pressure(-3.0, 50.0)
        assert score_50h > score_4h


# ---------------------------------------------------------------------------
# 8. Full analyze_exit integration
# ---------------------------------------------------------------------------


class TestAnalyzeExitIntegration:
    def test_winner_holding_no_sell(self):
        """Position +17%, 10h, bullish LSTM, 2.5% drawdown from HWM → hold."""
        ctx = _ctx(
            entry=100.0,
            current=117.0,
            hwm=120.0,
            hours=10.0,
            lstm=0.3,
            momentum=[0.2, 0.3, 0.25],
        )
        result = analyze_exit(ctx)
        assert result.should_sell is False
        assert result.total_score < 50.0

    def test_momentum_fade_triggers_sell(self):
        """Position +10%, strongly bearish LSTM with falling history → sell."""
        ctx = _ctx(
            entry=100.0,
            current=110.0,
            hwm=112.0,
            hours=6.0,
            lstm=-0.6,
            news=-0.2,
            momentum=[0.5, 0.3, 0.1, -0.2, -0.4],
        )
        result = analyze_exit(ctx)
        assert result.should_sell is True

    def test_trailing_stop_triggers_sell(self):
        """Position entered at 100, peaked at 125 (+25%), now at 115 (8% dd) → sell."""
        ctx = _ctx(
            entry=100.0,
            current=115.0,
            hwm=125.0,
            hours=12.0,
            lstm=-0.2,
            momentum=[0.3, 0.1, -0.1],
        )
        result = analyze_exit(ctx)
        assert result.should_sell is True

    def test_exit_analysis_fields_populated(self):
        ctx = _ctx(entry=100.0, current=95.0, hwm=100.0, hours=30.0, lstm=-0.4)
        result = analyze_exit(ctx)
        assert isinstance(result, ExitAnalysis)
        assert result.symbol == "TEST"
        assert isinstance(result.pnl_pct, float)
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.reason, str)

    def test_constants_match_spec(self):
        """Leading Indicators check: Hard Stop at -8% (Epic 2.4 spec)."""
        assert HARD_STOP_LOSS_PCT == -8.0

    def test_trail_profit_tiers_structure(self):
        """5 tiers defined, each with (min_profit, trailing_stop, min_hold_hours)."""
        assert len(TRAIL_PROFIT_TIERS) == 5
        for tier in TRAIL_PROFIT_TIERS:
            assert len(tier) == 3
