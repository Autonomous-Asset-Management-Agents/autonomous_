# tests/unit/test_smart_exit_min_hold.py
"""TDD for #1952 — min-hold + hysteresis gate on smart_exit rule #1 (rebalance-sell).

The LSTM signal is a ~5-day-horizon signal. The shipped `should_sell_smart` rule #1
sold a name the moment it fell out of the LSTM top-N — every cycle — churning the
position daily and throwing away the horizon alpha (bake-off #1952: daily-naive net
Sharpe 0.758 vs ~5-day-hold 1.56, +0.80).

This gate keeps rule #1 (rebalance-sell) from firing during a minimum hold window
UNLESS the rank has genuinely collapsed (sustained adverse info, hysteresis). It
MUST NOT touch the risk exits: stop-loss / take-profit / trailing (rules 2-4) still
fire inside the min-hold window. Those are the safety-critical tests below.
"""
from datetime import datetime, timedelta

from core.smart_exit import resolve_hold_hours, should_sell_smart

DAY = 24.0  # hours


def _kwargs(**over):
    """A healthy position (pnl ~0, no stop/TP/trail trigger) so ONLY rule #1 is in play.

    Defaults: dropped out of the top-N (in_top_n=False, rank just outside), min_hold=5d,
    hysteresis factor 3 => collapse threshold = top_n_size * 3 = 30.
    """
    base = {
        "symbol": "AAPL",
        "entry_price": 100.0,
        "current_price": 100.0,
        "high_water_mark": 100.0,
        "hours_held": 1.0,
        "in_top_n": False,
        "lstm_rank": 12,
        "top_n_size": 10,
        "min_hold_days": 5.0,
        "exit_rank_hysteresis": 3.0,
        "smart_take_profit": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- (a) HOLD in-window
def test_a_dropped_within_min_hold_no_collapse_holds():
    """Fell out of top-N, held < min_hold, rank not collapsed -> HOLD (no daily churn)."""
    d = should_sell_smart(**_kwargs(hours_held=1 * DAY, lstm_rank=12))
    assert d.action == "HOLD", d.reason


def test_a2_dropped_just_under_min_hold_holds():
    """Boundary: held 4.99 days < 5 -> still HOLD."""
    d = should_sell_smart(**_kwargs(hours_held=4.99 * DAY, lstm_rank=15))
    assert d.action == "HOLD", d.reason


# ------------------------------------------------------------- (b) SELL after min-hold
def test_b_dropped_after_min_hold_sells():
    """Fell out of top-N and held >= min_hold -> normal rotation SELL."""
    d = should_sell_smart(**_kwargs(hours_held=5 * DAY, lstm_rank=12))
    assert d.action == "SELL"
    assert "rebalanc" in d.reason.lower()


def test_b2_min_hold_boundary_exact_sells():
    """Boundary: held exactly min_hold (>=) -> SELL."""
    d = should_sell_smart(**_kwargs(hours_held=5 * DAY, lstm_rank=11))
    assert d.action == "SELL"


# --------------------------------------------------- (c) early exit on rank collapse
def test_c_rank_collapse_within_min_hold_sells_early():
    """Held < min_hold BUT rank collapsed past hysteresis threshold -> early SELL."""
    d = should_sell_smart(**_kwargs(hours_held=1 * DAY, lstm_rank=35))  # 35 > 10*3=30
    assert d.action == "SELL"
    assert "rebalanc" in d.reason.lower()


def test_c2_rank_at_threshold_does_not_early_exit():
    """Boundary: rank == threshold (30) is NOT a collapse (strict >) -> HOLD in-window."""
    d = should_sell_smart(**_kwargs(hours_held=1 * DAY, lstm_rank=30))
    assert d.action == "HOLD", d.reason


# ----------------------------------------------------- (d) SAFETY: risk exits not gated
def test_d_stop_loss_fires_inside_min_hold():
    """CRITICAL: a position falling into stop-loss during min-hold MUST still sell."""
    d = should_sell_smart(
        **_kwargs(hours_held=1.0, current_price=90.0, lstm_rank=12)  # -10% <= -7%
    )
    assert d.action == "SELL"
    assert "stop-loss" in d.reason.lower()


def test_d2_take_profit_fires_inside_min_hold():
    """CRITICAL: take-profit still fires during min-hold (rule #1 gate must not swallow it)."""
    d = should_sell_smart(
        **_kwargs(hours_held=1.0, current_price=130.0, lstm_rank=12)  # +30% >= 25%
    )
    assert d.action == "SELL"
    assert "take-profit" in d.reason.lower()


def test_d3_trailing_stop_fires_inside_min_hold():
    """CRITICAL: trailing stop still fires during min-hold for a profitable, dropped name."""
    d = should_sell_smart(
        **_kwargs(
            hours_held=2.0,
            entry_price=100.0,
            current_price=104.0,  # +4% profit (> min_profit_for_trail 2%)
            high_water_mark=110.0,  # 5.45% down from high (> trail 3%)
            lstm_rank=12,
            trailing_stop_pct=3.0,
            min_hold_hours=1.0,
        )
    )
    assert d.action == "SELL"
    assert "trailing" in d.reason.lower()


# ---------------------------------------------------------- (e) regression: rules intact
def test_e_in_top_n_healthy_holds():
    """Still in top-N, healthy -> HOLD (rule #1 does not apply)."""
    d = should_sell_smart(**_kwargs(in_top_n=True, lstm_rank=3, hours_held=1.0))
    assert d.action == "HOLD", d.reason


def test_e2_none_rank_healthy_holds():
    """lstm_rank None (abstained) -> rule #1 never fires; healthy -> HOLD."""
    d = should_sell_smart(**_kwargs(lstm_rank=None, hours_held=10 * DAY))
    assert d.action == "HOLD", d.reason


def test_e3_rollback_min_hold_zero_restores_legacy_immediate_sell():
    """Rollback lever: min_hold_days=0 -> old behavior (immediate rebalance-sell)."""
    d = should_sell_smart(**_kwargs(hours_held=0.0, lstm_rank=12, min_hold_days=0.0))
    assert d.action == "SELL"
    assert "rebalanc" in d.reason.lower()


def test_e4_invalid_prices_hold():
    d = should_sell_smart(**_kwargs(entry_price=0.0))
    assert d.action == "HOLD"


# ------------------------------------------------------------------ (f) determinism
def test_f_deterministic():
    k = _kwargs(hours_held=1 * DAY, lstm_rank=35)
    r1 = should_sell_smart(**k)
    r2 = should_sell_smart(**k)
    assert (r1.action, r1.reason) == (r2.action, r2.reason)


# ------------------------------------------- (g) #1952 follow-up: fail-open unknown entry
# resolve_hold_hours() is the call-site helper used by lstm_strategy / rl_execution. The
# strategy's `_entry_time` map is in-memory only: after an engine restart a reconciled
# position has NO entry -> entry_time is None. Returning ~0h there would silently freeze
# rule #1 (rebalance-sell) for up to min_hold_days. It must FAIL OPEN instead (legacy
# sell-on-drop for unknown-age positions). Risk rules stay unaffected.
_NOW = datetime(2026, 7, 11, 12, 0, 0)


def test_g_unknown_entry_fails_open_and_rule1_sells():
    h = resolve_hold_hours(None, _NOW, min_hold_days=5.0)
    assert h > 5.0 * DAY  # strictly PAST the 5-day window (not ~0)
    d = should_sell_smart(**_kwargs(hours_held=h, lstm_rank=12))
    assert d.action == "SELL"  # dropped name is rotated, not silently held
    assert "rebalanc" in d.reason.lower()


def test_g2_known_entry_returns_real_age_and_holds_in_window():
    h = resolve_hold_hours(_NOW - timedelta(days=2), _NOW, min_hold_days=5.0)
    assert abs(h - 2 * DAY) < 1e-6
    d = should_sell_smart(**_kwargs(hours_held=h, lstm_rank=12))
    assert d.action == "HOLD", d.reason  # 2d < 5d, no collapse -> still held


def test_g3_none_entry_is_none_safe_with_default_min_hold():
    from core.smart_exit import SMART_EXIT_MIN_HOLD_DAYS

    h = resolve_hold_hours(None, _NOW)  # never raises (was a latent TypeError)
    assert h > SMART_EXIT_MIN_HOLD_DAYS * DAY


def test_f2_default_min_hold_is_active():
    """The behavior change ships active by default: default min_hold_days > 0 gates churn."""
    from core.smart_exit import SMART_EXIT_MIN_HOLD_DAYS

    assert SMART_EXIT_MIN_HOLD_DAYS >= 1.0
    # With defaults (no explicit min_hold_days), a fresh dropped name must NOT churn-sell.
    d = should_sell_smart(
        symbol="AAPL",
        entry_price=100.0,
        current_price=100.0,
        high_water_mark=100.0,
        hours_held=1.0,
        in_top_n=False,
        lstm_rank=12,
        top_n_size=10,
        smart_take_profit=False,
    )
    assert d.action == "HOLD", d.reason
