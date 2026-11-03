# flake8: noqa
# tests/unit/test_cashflow_jump_alignment_3068.py
# #3068 — date-based cash-flow attribution misassigns settlement-lagged deposits.
#
# Reproduced on the running LIVE instance (real DB, 0.4.9-rc1), quantitatively:
#   - today tile:  (6145.06 - 6143.35 - 5793) / 6143.35 = -94.27%
#     (the deposit that settled YESTERDAY was re-attached into today's 1D window)
#   - max drawdown: (224.78 - 114.13) / 224.78 = -50.77%
#     (the 114.13 deposit stripped one day BEFORE its equity jump)
#   - since inception: 0.4923 x 1.5077 x ... = -23.3% (permanent compound damage)
#
# The rc1 carry-forward guard only fires when equity - flow <= 0; every wrong-base case
# that stays POSITIVE sailed through. Root fix: align each flow to the EQUITY JUMP that
# matches it (same sign, step >= ~50% of the flow, within a +-lag-day window); no matching
# jump -> the flow is already inside the base -> drop.

from __future__ import annotations

import pytest

from core.engine.perf_metrics import (
    align_cashflows_to_points,
    bucket_cashflows_to_points_intraday,
    compute_cashflow_adjusted_returns,
)

# Andre's REAL live daily curve (last snapshot per day, aaagents.db, 43 rows collapsed).
REAL_DATES = [
    "2026-07-24",
    "2026-07-25",
    "2026-08-09",
    "2026-08-10",
    "2026-08-11",
    "2026-08-12",
    "2026-08-13",
    "2026-08-14",
    "2026-08-17",
    "2026-08-18",
    "2026-08-19",
    "2026-08-23",
    "2026-08-24",
    "2026-08-25",
    "2026-08-26",
]
REAL_EQUITY = [
    224.78,
    224.78,
    338.91,
    342.31,
    344.05,
    343.67,
    346.15,
    346.66,
    346.74,
    345.20,
    345.55,
    350.48,
    349.94,
    6143.35,
    6145.06,
]
# Real flows by ACTIVITY date (Alpaca): the 114.13 deposit posted ~2 days before its
# equity jump (08-09); the 5,833-40=5,793 net deposit is dated 08-24, settles 08-25.
REAL_FLOWS = {"2026-08-07": 114.13, "2026-08-24": 5793.00}


# ---------------------------------------------------------------------------
# align_cashflows_to_points — the shared matcher
# ---------------------------------------------------------------------------


def test_flow_lands_on_its_matching_equity_jump_not_its_activity_date():
    out = align_cashflows_to_points(REAL_DATES, REAL_EQUITY, REAL_FLOWS)
    by_date = dict(zip(REAL_DATES, out))
    assert by_date["2026-08-09"] == pytest.approx(114.13)  # jump 224.78 -> 338.91
    assert by_date["2026-08-25"] == pytest.approx(5793.00)  # jump 349.94 -> 6143.35
    assert sum(1 for v in out if v) == 2  # nothing attached anywhere else


def test_no_matching_jump_in_window_drops_the_flow():
    """A flow whose jump is NOT in the curve (already inside the base) must be dropped,
    never strapped onto a tiny unrelated step (the -94.27% mechanism)."""
    dates = ["2026-08-26"] * 3
    eq = [6143.9, 6144.5, 6145.06]  # only tiny intraday steps
    out = align_cashflows_to_points(dates, eq, {"2026-08-24": 5793.0})
    assert out == [0.0, 0.0, 0.0]


def test_flow_older_than_the_lag_window_is_dropped():
    out = align_cashflows_to_points(
        ["2026-08-25", "2026-08-26"], [6143.0, 6144.0], {"2026-08-10": 5793.0}
    )
    assert out == [0.0, 0.0]


def test_withdrawal_matches_a_negative_jump():
    out = align_cashflows_to_points(
        ["2026-08-20", "2026-08-21", "2026-08-22"],
        [1000.0, 1002.0, 502.0],
        {"2026-08-20": -500.0},
    )
    assert out == [0.0, 0.0, -500.0]


def test_tiny_step_never_absorbs_a_large_flow():
    """Step must be >= ~half the flow: a +2 step cannot 'be' a +5793 deposit."""
    out = align_cashflows_to_points(
        ["2026-08-24", "2026-08-25"], [6141.0, 6143.0], {"2026-08-24": 5793.0}
    )
    assert out == [0.0, 0.0]


# ---------------------------------------------------------------------------
# End-to-end on the REAL curve: the three broken tiles get their Soll values
# ---------------------------------------------------------------------------


def test_real_curve_inception_twr_is_plus_3_4_pct_not_minus_23():
    cf = align_cashflows_to_points(REAL_DATES, REAL_EQUITY, REAL_FLOWS)
    r = compute_cashflow_adjusted_returns(REAL_DATES, REAL_EQUITY, cf)
    assert r["twr_pct"] == pytest.approx(3.40, abs=0.3)
    assert r["net_deposits"] == pytest.approx(5907.13)


def test_real_curve_has_no_fabricated_50_pct_drawdown():
    """Deposit-neutral adjusted curve must never dip ~-50%: with jump-aligned flows the
    worst deposit-neutral dip on the real curve is under 1%."""
    cf = align_cashflows_to_points(REAL_DATES, REAL_EQUITY, REAL_FLOWS)
    cum, peak, max_dd = 0.0, None, 0.0
    for e, c in zip(REAL_EQUITY, cf):
        cum += c
        adj = e - cum
        peak = adj if peak is None else max(peak, adj)
        if peak > 0:
            max_dd = min(max_dd, (adj - peak) / peak * 100.0)
    assert max_dd > -1.5, f"fabricated drawdown: {max_dd}"


# ---------------------------------------------------------------------------
# Intraday (1D) — yesterday's settled deposit must NOT enter today's window
# ---------------------------------------------------------------------------


def test_intraday_yesterdays_settled_deposit_is_dropped_from_todays_window():
    """THE -94.27% bug: today's window only has tiny steps; the 5,793 deposit settled
    yesterday and is inside the opening equity. It must be dropped, not attached to a
    +0.6 step."""
    dates = [
        "2026-08-26T13:30:00+00:00",
        "2026-08-26T14:00:00+00:00",
        "2026-08-26T14:30:00+00:00",
    ]
    out = bucket_cashflows_to_points_intraday(
        dates, [6143.9, 6144.5, 6145.06], {"2026-08-24": 5793.0}
    )
    assert out == [0.0, 0.0, 0.0]


def test_intraday_todays_actual_settlement_jump_still_matches():
    """Regression (#3049 case): the deposit's jump IS inside today's window -> attach there."""
    dates = [
        "2026-08-25T13:30:00+00:00",
        "2026-08-25T14:00:00+00:00",
        "2026-08-25T14:30:00+00:00",
    ]
    out = bucket_cashflows_to_points_intraday(
        dates, [348.37, 349.0, 6142.36], {"2026-08-24": 5833.0}
    )
    assert out == [0.0, 0.0, 5833.0]
