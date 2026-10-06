"""Dashboard consistency harness (Overview page).

Encodes the invariants that every Overview figure must satisfy — BOTH lenses:
  (A) cross-widget: figures for the same quantity agree;
  (B) self-consistency: each figure matches its OWN label/definition/anchor.

The checkers are pure functions over a "dashboard payload" dict (the shape the
Overview consumes from /portfolio-summary + /benchmark-equity), so they run in CI
without the engine and can also be pointed at a live payload.

The known-open bug #3911 is captured as a strict xfail against the REAL values
read live from /benchmark-equity (localhost:8001): with zero net cashflows the
curve returns +42.25% since inception (2026-02-20, 100k->142k), but inception_twr_pct
reports -1.08% (sourced from the sparse install-window metrics report, 2026-09-25->now),
contradicting the endpoint's own points series. The anchor itself is CORRECT on the
live path; the defect is the twr VALUE. The xfail flips to green when the backend
computes twr from the full inception series.

Ref: docs/dashboard-consistency/implementation_plan.md ; issue #3911
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

EPS = 0.01  # money rounding tolerance (currency units)
TOL_PCT = 0.5  # percent-point tolerance for return reconciliation
SHARPE_MIN_POINTS = (
    20  # fewer points than this => Sharpe is not meaningful (must be None)
)


# ─────────────────────────── invariant checkers ───────────────────────────
def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00").split("+")[0]).date()


def _n_points(payload: dict) -> int:
    return int(payload.get("n_points", len(payload["equity_series"])))


def inv_anchor_equals_base(payload: dict) -> tuple[bool, str]:
    """INV-1 (B): the since-inception LABEL anchor (metrics.start_date) must equal the
    DATA base (timestamp of the first equity point). On the live /benchmark-equity path
    this HOLDS (inception_date == points[0] == 2026-02-20)."""
    start = _as_date(payload["metrics"]["start_date"])
    data_start = _as_date(payload["equity_series"][0]["t"])
    ok = start == data_start
    return ok, f"start_date={start} vs first equity point={data_start}"


def inv_twr_reconciles_series(payload: dict) -> tuple[bool, str]:
    """INV-6 (A/B, #3911): with net cashflows ~0, the reported since-inception TWR must
    equal the return of the endpoint's OWN equity series (final/initial - 1). A -1% TWR
    next to a +42% curve means the TWR is sourced from a different (sparse) window."""
    m = payload["metrics"]
    flows = payload.get("net_cashflow_in_window") or 0.0
    series_ret = (m["final_equity"] / m["initial_capital"] - 1.0) * 100
    reported = m.get("inception_twr_pct", m.get("twr_pct"))
    if abs(flows) > EPS:
        return (
            True,
            f"cashflows present ({flows}); TWR need not equal raw series — skip",
        )
    ok = abs(reported - series_ret) <= TOL_PCT
    return (
        ok,
        f"inception_twr_pct={reported:.4f} vs series return={series_ret:.4f} (flows=0)",
    )


def inv_balances_sum(payload: dict) -> tuple[bool, str]:
    """INV-2 (A): sum(position.market_value) + cash == equity."""
    s = payload["summary"]
    lhs = sum(p["market_value"] for p in s["positions"]) + s["cash"]
    ok = abs(lhs - s["equity"]) <= EPS
    return (
        ok,
        f"sum(mv)+cash={lhs:.2f} vs equity={s['equity']:.2f} (delta={lhs - s['equity']:.2f})",
    )


def inv_hero_equals_chart(payload: dict) -> tuple[bool, str]:
    """INV-3 (A/B): hero change == chart(last) - chart(first) in the SAME basis (B1)."""
    series = payload["equity_series"]
    chart_delta = series[-1]["eur"] - series[0]["eur"]
    hero = payload["hero_change_abs"]
    ok = abs(hero - chart_delta) <= EPS
    return ok, f"hero_change_abs={hero:.2f} vs chart(end-start)={chart_delta:.2f}"


def inv_twr_not_pointwise(payload: dict) -> tuple[bool, str]:
    """INV-4 (B): when cashflows landed in the window, twr_pct must NOT equal the raw
    point-to-point (final-initial)/initial. Skipped when there are no flows."""
    m = payload["metrics"]
    flows = payload.get("net_cashflow_in_window")
    raw = (m["final_equity"] - m["initial_capital"]) / m["initial_capital"] * 100
    if not flows:
        return True, f"no cashflows (twr={m['twr_pct']:.4f}, raw={raw:.4f}) — skip"
    ok = abs(m["twr_pct"] - raw) > 1e-6
    return ok, f"twr_pct={m['twr_pct']:.4f} vs raw={raw:.4f}, flows={flows}"


def inv_sharpe_requires_history(payload: dict) -> tuple[bool, str]:
    """INV-5 (B): Sharpe must be None when the history is too short, regardless of paper/live."""
    n = _n_points(payload)
    sharpe = payload["summary"].get("sharpe")
    ok = not (n < SHARPE_MIN_POINTS and sharpe is not None)
    return ok, f"n_points={n} (min={SHARPE_MIN_POINTS}), sharpe={sharpe}"


CHECKERS = {
    "INV-1 anchor==base": inv_anchor_equals_base,
    "INV-2 balances sum": inv_balances_sum,
    "INV-3 hero==chart": inv_hero_equals_chart,
    "INV-4 twr not point-to-point": inv_twr_not_pointwise,
    "INV-5 sharpe needs history": inv_sharpe_requires_history,
    "INV-6 twr reconciles series (#3911)": inv_twr_reconciles_series,
}


# ─────────────────────────── synthetic fixtures ───────────────────────────
def _good_payload() -> dict:
    return {
        "metrics": {
            "start_date": "2026-02-20",
            "initial_capital": 100000.0,
            "final_equity": 142294.88,
            "twr_pct": 42.29,
            "inception_twr_pct": 42.29,
        },
        "equity_series": [
            {"t": "2026-02-20", "eur": 100000.0},
            {"t": "2026-10-02", "eur": 142294.88},
        ],
        "n_points": 158,
        "hero_change_abs": 42294.88,
        "net_cashflow_in_window": 0.0,
        "summary": {
            "equity": 142294.88,
            "cash": 92074.49,
            "positions": [{"symbol": "AAA", "market_value": 50220.39}],
            "sharpe": 1.1,
        },
    }


# Real values read live from /benchmark-equity + /portfolio-summary (localhost:8001) on 2026-10-02.
def _live_payload() -> dict:
    return {
        "metrics": {
            "start_date": "2026-02-20",  # inception_date — CORRECT on the live path
            "initial_capital": 100000.0,  # points[0].equity — CORRECT
            "final_equity": 142294.88,
            "twr_pct": -1.3208291435,  # WRONG: curve returns +42.25% with 0 flows
            "inception_twr_pct": -1.0777530467,  # == stale portfolio_metrics_reports (Sep-25 window)
        },
        "equity_series": [  # 158 real points, Feb-20 -> Oct-02
            {"t": "2026-02-20", "eur": 100000.0},
            {"t": "2026-10-02", "eur": 142254.65},
        ],
        "n_points": 158,
        "hero_change_abs": 42254.65,
        "net_cashflow_in_window": 0.0,  # effective_cashflows all 0, net_deposits 0, /activities 0
        "summary": {
            "equity": 142588.49,
            "cash": 92074.49,
            "positions": [
                {"symbol": "AGG", "market_value": 50514.00}
            ],  # Σmv == equity-cash
            "sharpe": 1.0,  # 158 points -> history adequate
        },
    }


# ─────────────────────────── tests: checker logic ───────────────────────────
def test_checkers_pass_on_consistent_payload():
    payload = _good_payload()
    failures = {
        name: fn(payload)[1] for name, fn in CHECKERS.items() if not fn(payload)[0]
    }
    assert (
        not failures
    ), f"consistent payload should pass all invariants, got: {failures}"


def test_checkers_detect_injected_inconsistencies():
    p = _good_payload()
    p["metrics"]["start_date"] = "2026-09-25"  # break anchor
    p["summary"]["cash"] = 999.0  # break balances
    p["hero_change_abs"] = 123.0  # break hero==chart
    p["metrics"]["inception_twr_pct"] = -1.08  # break twr-reconcile
    assert not inv_anchor_equals_base(p)[0]
    assert not inv_balances_sum(p)[0]
    assert not inv_hero_equals_chart(p)[0]
    assert not inv_twr_reconciles_series(p)[0]


# ─────────────────────────── tests: live regression (#3911) ───────────────────────────
def test_live_anchor_is_correct():
    """On the live /benchmark-equity path the anchor IS correct (inception 2026-02-20)."""
    ok, msg = inv_anchor_equals_base(_live_payload())
    assert ok, msg


@pytest.mark.xfail(
    strict=True,
    reason="#3911: inception_twr_pct=-1.08% (install-window) vs +42.25% series return (0 flows)",
)
def test_live_twr_reconciles_with_curve():
    """RED until #3911 is fixed (twr computed from the full inception series), then flips green."""
    ok, msg = inv_twr_reconciles_series(_live_payload())
    assert ok, msg
