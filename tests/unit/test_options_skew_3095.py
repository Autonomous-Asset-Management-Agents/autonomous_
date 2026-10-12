"""#3095 (b) — pure Risk-Reversal core (25Δ selection, RR sign, percentile rank)."""

import pytest

from core.options_skew import risk_reversal_from_chain, rr_percentile, select_25delta


def _chain():
    # calls (positive delta) + puts (negative delta), varied strikes
    return [
        {"right": "C", "delta": 0.50, "iv": 0.30},
        {"right": "C", "delta": 0.26, "iv": 0.34},  # ~25Δ call
        {"right": "C", "delta": 0.10, "iv": 0.40},
        {"right": "P", "delta": -0.48, "iv": 0.31},
        {"right": "P", "delta": -0.24, "iv": 0.28},  # ~25Δ put
        {"right": "P", "delta": -0.08, "iv": 0.45},
    ]


def test_select_25delta_picks_nearest_wings():
    call_iv, put_iv = select_25delta(_chain())
    assert call_iv == pytest.approx(0.34)
    assert put_iv == pytest.approx(0.28)


def test_risk_reversal_sign_is_call_minus_put():
    rr = risk_reversal_from_chain(_chain())
    assert rr == pytest.approx(0.34 - 0.28)
    assert rr > 0  # bullish skew (calls richer)


def test_missing_side_returns_none():
    calls_only = [{"right": "C", "delta": 0.25, "iv": 0.3}]
    assert select_25delta(calls_only) is None
    assert risk_reversal_from_chain(calls_only) is None


def test_unusable_contracts_ignored():
    chain = [
        {"right": "C", "delta": 0.25, "iv": None},
        {"right": "C", "delta": 0.27, "iv": 0.33},
        {"right": "P", "delta": -0.26, "iv": 0.30},
        {"right": "P", "delta": float("nan"), "iv": 0.30},
    ]
    call_iv, put_iv = select_25delta(chain)
    assert call_iv == pytest.approx(0.33)
    assert put_iv == pytest.approx(0.30)


def test_rr_percentile_ranks_and_needs_distinct_reference():
    ref = [-0.05, -0.02, 0.00, 0.03, 0.06]
    assert rr_percentile(0.04, ref) == pytest.approx(4 / 5)
    assert rr_percentile(-0.10, ref) == pytest.approx(0.0)
    assert rr_percentile(0.0, [1.0]) is None  # < 2 values
    assert rr_percentile(0.0, [2.0, 2.0, 2.0]) is None  # not distinct


def test_higher_rr_ranks_higher_bullish():
    ref = [-0.03, 0.00, 0.02, 0.05]
    assert rr_percentile(0.06, ref) > rr_percentile(-0.02, ref)
