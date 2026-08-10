# tests/unit/test_smart_exit_trailing_policy.py
"""TDD for RTR-5/B2 (#2401) — Trailing-Exit-Policy (dark, flag-gated).

Plan §7 Gherkin (issue #2401):
    Given EXIT_POLICY_TRAILING_ENABLED=True und eine Position 40% über Entry mit HWM
    When der Kurs X% unter den Positions-HWM fällt
    Then wird ein auditierter Exit-Vorschlag erzeugt (HITL-konform),
         Grund "trailing_from_peak"

    Given beide Flags off
    Then heutiges Verhalten exakt erhalten (byte-identisch) —
         hier als PINNED regression battery: exact (action, reason) literals.

The 159k gap (#2401 §2): static take-profit exists (smart_exit.py:79) but no
trailing-from-position-HWM policy fired at the account peak. B2 adds the policy
INSIDE the existing ``should_sell_smart`` contract (an ExitDecision PROPOSAL —
no new autonomous SELL path; risk manager / HITL / compliance gates downstream
are untouched). Churn-safe: min-hold wins (the trail min-hold gate here, and
PortfolioManager.can_sell_position at execution).

V-2 (HWM source): session HWM maps live in-memory (lstm_strategy.py:107/:635,
rl_execution.py:198) and die on restart. Restart-proof derivation without a new
DB table: reconciled entry_time (#1994 entry_time_reconcile pattern) + bar highs
since entry -> ``derive_position_hwm``. The pure derivation ships here; the
broker bar-fetch wiring is B3/follow-up (documented limitation).
"""

from datetime import datetime

import pytest

from core.smart_exit import derive_position_hwm, should_sell_smart

DAY = 24.0  # hours


def _kwargs(**over):
    """Baseline: healthy in-top-N position so only the rule under test is in play."""
    base = {
        "symbol": "HOOD",
        "entry_price": 100.0,
        "current_price": 100.0,
        "high_water_mark": 100.0,
        "hours_held": 2.0,
        "in_top_n": True,
        "lstm_rank": 3,
        "top_n_size": 10,
        "smart_take_profit": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# (a) Flag OFF = byte-identical — PINNED regression battery (plan §7 Gherkin 3).
# Exact (action, reason) literals of today's behaviour. If ANY of these change,
# the flag-off guarantee is broken.
# ---------------------------------------------------------------------------
FLAG_OFF_BATTERY = [
    # rule 1 — rotation sell after min-hold
    (
        {"in_top_n": False, "lstm_rank": 12, "hours_held": 5 * DAY},
        ("SELL", "Dropped from LSTM top 10 (rank 12) - rebalancing"),
    ),
    # rule 2 — stop-loss
    (
        {"current_price": 92.0, "high_water_mark": 100.0},
        ("SELL", "Stop-loss: -8.0% below entry (limit -7.0%)"),
    ),
    # rule 3 — take-profit (static, smart off)
    (
        {"current_price": 130.0, "high_water_mark": 130.0},
        ("SELL", "Take-profit: +30.0% (target +25.0%)"),
    ),
    # rule 4 — legacy trailing stop
    (
        {"current_price": 106.0, "high_water_mark": 110.0},
        ("SELL", "Trailing stop: 3.6% down from high (trail 3.0%)"),
    ),
    # no trigger
    (
        {"current_price": 101.0, "high_water_mark": 101.5, "hours_held": 0.5},
        ("HOLD", "No exit trigger"),
    ),
    # invalid prices
    (
        {"entry_price": 0.0},
        ("HOLD", "Invalid prices"),
    ),
]


@pytest.mark.parametrize("over,expected", FLAG_OFF_BATTERY)
def test_flag_off_byte_identical_pinned(over, expected):
    """Default config (EXIT_POLICY_TRAILING_ENABLED off) -> exact legacy output."""
    d = should_sell_smart(**_kwargs(**over))
    assert (d.action, d.reason) == expected


@pytest.mark.parametrize("over,expected", FLAG_OFF_BATTERY)
def test_flag_on_existing_rules_unchanged(monkeypatch, over, expected):
    """Flag ON must not disturb ANY existing rule's action or reason literal."""
    import config as _config

    monkeypatch.setattr(_config, "EXIT_POLICY_TRAILING_ENABLED", True, raising=False)
    d = should_sell_smart(**_kwargs(**over))
    assert (d.action, d.reason) == expected


def test_flag_off_explicit_param_matches_default():
    """Explicit trailing_from_peak_enabled=False == default OFF resolution."""
    d1 = should_sell_smart(**_kwargs(current_price=119.0, high_water_mark=140.0))
    d2 = should_sell_smart(
        **_kwargs(current_price=119.0, high_water_mark=140.0),
        trailing_from_peak_enabled=False,
    )
    assert (d1.action, d1.reason) == (d2.action, d2.reason)


# ---------------------------------------------------------------------------
# (b) Flag ON — the 159k scenario (plan §7 Gherkin 2)
# ---------------------------------------------------------------------------
def test_trailing_from_peak_fires_on_drawdown_from_hwm():
    """+40% HWM, price 15% below peak (still +19% vs entry) -> exit PROPOSAL
    with reason 'trailing_from_peak' (default TRAILING_FROM_PEAK_PCT=10)."""
    d = should_sell_smart(
        **_kwargs(
            current_price=119.0,
            high_water_mark=140.0,
            hours_held=5 * DAY,
            take_profit_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
    )
    assert d.action == "SELL"
    assert "trailing_from_peak" in d.reason


def test_trailing_from_peak_boundary_exact_threshold_fires():
    """Drawdown exactly == TRAILING_FROM_PEAK_PCT (>=) -> fires."""
    d = should_sell_smart(
        **_kwargs(
            current_price=126.0,  # (140-126)/140 = 10.0%
            high_water_mark=140.0,
            hours_held=5 * DAY,
            take_profit_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
        trailing_from_peak_pct=10.0,
    )
    assert d.action == "SELL"
    assert "trailing_from_peak" in d.reason


def test_trailing_from_peak_below_threshold_holds():
    """Drawdown below the tunable -> no proposal (legacy rules isolated away)."""
    d = should_sell_smart(
        **_kwargs(
            current_price=128.8,  # (140-128.8)/140 = 8.0% < 10%
            high_water_mark=140.0,
            hours_held=5 * DAY,
            take_profit_pct=99.0,
            trailing_stop_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
        trailing_from_peak_pct=10.0,
    )
    assert d.action == "HOLD"


def test_trailing_from_peak_min_hold_wins():
    """Churn-safe (#2401): within the trail min-hold window the policy does NOT
    propose — min-hold wins. (Execution-level can_sell_position stays binding.)"""
    d = should_sell_smart(
        **_kwargs(
            current_price=119.0,
            high_water_mark=140.0,
            hours_held=0.5,  # < MIN_HOLD_HOURS_BEFORE_TRAIL (1.0)
            take_profit_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
    )
    assert d.action == "HOLD"


def test_trailing_from_peak_requires_profit():
    """Losing position -> the profit-taking policy never fires (losses are the
    stop-loss rule's jurisdiction, not trailing-from-peak)."""
    d = should_sell_smart(
        **_kwargs(
            current_price=90.0,  # pnl -10%
            high_water_mark=105.0,  # dd 14.3% >= 10%
            hours_held=5 * DAY,
            stop_loss_pct=50.0,
            take_profit_pct=99.0,
            trailing_stop_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
        trailing_from_peak_pct=10.0,
    )
    assert d.action == "HOLD"


def test_trailing_from_peak_resolves_flag_from_config(monkeypatch):
    """No explicit param: the flag + tunable resolve from config at call time."""
    import config as _config

    monkeypatch.setattr(_config, "EXIT_POLICY_TRAILING_ENABLED", True, raising=False)
    d = should_sell_smart(
        **_kwargs(
            current_price=119.0,
            high_water_mark=140.0,
            hours_held=5 * DAY,
            take_profit_pct=99.0,
        )
    )
    assert d.action == "SELL"
    assert "trailing_from_peak" in d.reason


def test_trailing_from_peak_is_a_proposal_not_an_order():
    """HITL contract: the policy returns an ExitDecision (action+reason) through
    the EXISTING should_sell_smart contract — no side effects, no order objects."""
    d = should_sell_smart(
        **_kwargs(
            current_price=119.0,
            high_water_mark=140.0,
            hours_held=5 * DAY,
            take_profit_pct=99.0,
        ),
        trailing_from_peak_enabled=True,
    )
    assert set(vars(d).keys()) == {"action", "reason"}


# ---------------------------------------------------------------------------
# (c) V-2 — honest position-HWM derivation (restart-proof via #1994 pattern)
# ---------------------------------------------------------------------------
def test_derive_hwm_from_bar_highs_is_restart_proof():
    """Bar highs since the reconciled entry_time dominate: max survives restart."""
    assert (
        derive_position_hwm(
            100.0,
            session_hwm=None,
            bar_highs=[101.0, 140.0, 120.0],
            current_price=105.0,
        )
        == 140.0
    )


def test_derive_hwm_max_of_all_sources():
    assert (
        derive_position_hwm(
            100.0, session_hwm=120.0, bar_highs=[110.0, 130.5], current_price=125.0
        )
        == 130.5
    )


def test_derive_hwm_session_only_falls_back():
    """No bar history wired (pre-B3): honest session-HWM (documented limitation)."""
    assert derive_position_hwm(100.0, session_hwm=112.0) == 112.0


def test_derive_hwm_entry_price_floor():
    assert derive_position_hwm(100.0) == 100.0


def test_derive_hwm_skips_malformed_bars_never_raises():
    assert derive_position_hwm(100.0, bar_highs=[None, "x", 115.0]) == 115.0
    assert derive_position_hwm(0.0) == 0.0
