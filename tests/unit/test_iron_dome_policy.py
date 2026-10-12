# ADR-SEC-06 (#1583) · sub-issue #1594 — Policy store + loader. TDD RED first.
# Covers the DoD: clamp-to-floor + fail-closed. The loader fills each missing/invalid
# field with the strict (tightest-safe) default and clamps every provided value to the
# immutable hard-floor caps, so the AI/agents can never widen a control past the floor.

import dataclasses

import pytest

from core.governance.iron_dome_policy import STRICT_DEFAULT, load_policy


def test_none_source_fails_closed_to_strict_default():
    # No stored policy (DB empty/unreadable) -> tightest safe defaults, never disabled.
    assert load_policy(None) == STRICT_DEFAULT


def test_malformed_source_fails_closed_to_strict_default():
    # A non-dict / garbage value must not crash and must fail closed.
    assert load_policy("garbage") == STRICT_DEFAULT


def test_daily_trades_above_ceiling_clamps_to_50():
    # ADR-C04 hard-floor: MAX_POSITIONS(10) x MAX_TRADES_PER_SYMBOL_PER_DAY(5) = 50.
    assert load_policy({"max_daily_trades": 999}).max_daily_trades == 50


def test_wash_window_below_minimum_clamps_to_30():
    # ADR-C03 hard-floor: < 30 s would wrongly block legitimate correction trades.
    assert load_policy({"wash_trade_window_seconds": 5}).wash_trade_window_seconds == 30


def test_valid_in_range_policy_is_preserved():
    p = load_policy(
        {
            "max_daily_trades": 25,
            "wash_trade_window_seconds": 90,
            "max_order_value": 5000.0,
        }
    )
    assert p.max_daily_trades == 25
    assert p.wash_trade_window_seconds == 90
    assert p.max_order_value == 5000.0


def test_policy_is_immutable():
    # The effective policy must not be mutable in place by any caller (agents included).
    with pytest.raises(dataclasses.FrozenInstanceError):
        STRICT_DEFAULT.max_daily_trades = 999  # type: ignore[misc]


def test_nan_values_fail_closed_to_strict_default():
    # SEC-01: float("nan") is a valid float; min(nan, ceiling) == nan, and every
    # downstream comparison (order_value > max_order_value) with nan is False — which
    # would silently bypass the hard limits. NaN must fail closed to the strict default.
    p = load_policy(
        {
            "max_order_value": float("nan"),
            "portfolio_stop_loss_pct": float("nan"),
            "daily_drawdown_pct": float("nan"),
        }
    )
    assert p.max_order_value == STRICT_DEFAULT.max_order_value
    assert p.portfolio_stop_loss_pct == STRICT_DEFAULT.portfolio_stop_loss_pct
    assert p.daily_drawdown_pct == STRICT_DEFAULT.daily_drawdown_pct


def test_max_order_value_above_ceiling_clamps():
    assert load_policy({"max_order_value": 999_999.0}).max_order_value == 100_000.0


def test_portfolio_stop_loss_pct_above_ceiling_clamps():
    assert (
        load_policy({"portfolio_stop_loss_pct": 0.50}).portfolio_stop_loss_pct == 0.10
    )


def test_daily_drawdown_pct_above_ceiling_clamps():
    assert load_policy({"daily_drawdown_pct": 0.50}).daily_drawdown_pct == 0.20
