# flake8: noqa
# tests/unit/test_performance_baseline_3071.py
# #3071 — selectable performance baseline date (plan-approved, docs/3071-performance-baseline-date/).
#
# The five Gherkin scenarios from the implementation plan, on the REAL #3068 live-curve
# fixture. Slicing happens AFTER align_cashflows_to_points (#3068) so a boundary deposit
# attaches to its jump exactly once, and flows before the baseline fall into the base.

from __future__ import annotations

import pytest

from core.engine.perf_metrics import (
    align_cashflows_to_points,
    compute_cashflow_adjusted_returns,
    slice_points_to_baseline,
)
from tests.unit.test_cashflow_jump_alignment_3068 import (
    REAL_DATES,
    REAL_EQUITY,
    REAL_FLOWS,
)


def _aligned_points():
    cf = align_cashflows_to_points(REAL_DATES, REAL_EQUITY, REAL_FLOWS)
    return [
        {"date": d, "equity": e, **({"cashflow": c} if c else {})}
        for d, e, c in zip(REAL_DATES, REAL_EQUITY, cf)
    ]


# ---------------------------------------------------------------------------
# Scenario: Default is true inception (baseline None → unchanged)
# ---------------------------------------------------------------------------


def test_no_baseline_returns_the_full_curve_unchanged():
    pts = _aligned_points()
    out = slice_points_to_baseline(pts, None)
    assert out == pts  # byte-identical — today's behaviour


# ---------------------------------------------------------------------------
# Scenario: KPIs re-anchor to the chosen baseline
# ---------------------------------------------------------------------------


def test_kpis_reanchor_to_the_chosen_baseline():
    out = slice_points_to_baseline(_aligned_points(), "2026-08-10")
    assert out[0]["date"] == "2026-08-10"
    dates = [p["date"] for p in out]
    equity = [p["equity"] for p in out]
    cashflows = [p.get("cashflow", 0.0) for p in out]
    r = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    # the 114.13 deposit (aligned to 2026-08-09) is OUTSIDE the window → base, not a deposit
    assert r["net_deposits"] == pytest.approx(5793.00)
    # chain 342.31 → … → 6145.06 with the 5793 stripped at its jump ≈ +2.4 %
    assert r["twr_pct"] == pytest.approx(2.38, abs=0.3)


# ---------------------------------------------------------------------------
# Scenario: Deposit at the boundary is not double-counted (align FIRST, then slice)
# ---------------------------------------------------------------------------


def test_boundary_deposit_attaches_to_its_jump_exactly_once():
    """Activity date 2 days BEFORE the baseline, equity jump 1 day AFTER it: because the
    matcher runs on the FULL curve first, the flow lands on the in-window jump — once.
    """
    dates = ["2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12"]
    equity = [200.0, 201.0, 202.0, 5205.0, 5206.0]
    flows = {"2026-08-08": 5000.0}  # jump is 08-11
    cf = align_cashflows_to_points(dates, equity, flows)
    pts = [
        {"date": d, "equity": e, **({"cashflow": c} if c else {})}
        for d, e, c in zip(dates, equity, cf)
    ]
    out = slice_points_to_baseline(pts, "2026-08-10")
    window_flows = [p.get("cashflow", 0.0) for p in out]
    assert sum(window_flows) == pytest.approx(5000.0)  # counted exactly once, in-window
    r = compute_cashflow_adjusted_returns(
        [p["date"] for p in out], [p["equity"] for p in out], window_flows
    )
    assert abs(r["twr_pct"]) < 5.0  # flat market → no fabricated ±2000 %


# ---------------------------------------------------------------------------
# Scenario: Baseline before inception is clamped
# ---------------------------------------------------------------------------


def test_baseline_before_first_point_is_clamped_to_the_full_curve():
    pts = _aligned_points()
    out = slice_points_to_baseline(pts, "2026-01-01")
    assert out == pts  # effective baseline = inception


def test_baseline_after_last_point_keeps_a_minimal_tail():
    """Degenerate input (baseline in the future) must not return an empty curve — the
    endpoint would divide by nothing. Keep at least the last two points (fail-soft)."""
    pts = _aligned_points()
    out = slice_points_to_baseline(pts, "2027-01-01")
    assert len(out) >= 2


# ---------------------------------------------------------------------------
# Scenario: on a non-point day, the window starts at the first point ON/AFTER it
# ---------------------------------------------------------------------------


def test_baseline_on_a_gap_day_starts_at_the_next_point():
    out = slice_points_to_baseline(_aligned_points(), "2026-08-15")  # 15th has no point
    assert out[0]["date"] == "2026-08-17"
