# flake8: noqa
# tests/unit/test_deposit_kpi_fee_align_3049.py
# #3049 — a funded, fee-bearing, settlement-lagged international deposit misreports KPIs.
#
# Live evidence (2026-08-25): a ~$5,833 intl. deposit (Currencycloud fee 1.5% capped $40) →
#   net_deposits 5,948.66 · pnl_net_of_deposits -30.88 · twr_pct -28,304% · realized +1.72.
# The ~$42 gap is the transfer FEE, NOT trading. Two roots:
#   A) a FEE activity reduces equity but is never classified → it lands in pnl/TWR/drawdown as
#      an investment loss (the -17.29% Max-Drawdown and the -9.19% "Since inception").
#   B) the CSD `date` (08-24) precedes the day the equity reflects it (08-25) → a sub-period
#      subtracts the deposit from PRE-deposit equity → twr_pct explodes.

from __future__ import annotations

import pytest

from core.engine.perf_metrics import (  # noqa: E402
    FUNDING_ACTIVITY_TYPES,
    compute_cashflow_adjusted_returns,
    net_activities_by_date,
)

# ---------------------------------------------------------------------------
# Root A — funding fees are classified (fetched + signed negative)
# ---------------------------------------------------------------------------


def test_fee_is_a_fetched_funding_activity_type():
    """The fee is a real cash movement; `_get_live_cashflows` must fetch it."""
    assert "FEE" in FUNDING_ACTIVITY_TYPES
    assert "CSD" in FUNDING_ACTIVITY_TYPES and "CSW" in FUNDING_ACTIVITY_TYPES


def test_fee_activity_is_a_negative_cash_flow():
    """A FEE debit reduces external capital → negative flow, so it is stripped from the
    return/drawdown instead of read as a strategy loss."""
    out = net_activities_by_date(
        [{"activity_type": "FEE", "date": "2026-08-25", "net_amount": "40.00"}]
    )
    assert out == {"2026-08-25": -40.0}


def test_deposit_and_fee_net_on_the_same_day():
    out = net_activities_by_date(
        [
            {"activity_type": "CSD", "date": "2026-08-25", "net_amount": "5833.00"},
            {"activity_type": "FEE", "date": "2026-08-25", "net_amount": "40.00"},
        ]
    )
    assert out == {"2026-08-25": pytest.approx(5793.0)}


def test_unknown_activity_type_is_skipped_not_misclassified():
    """An unrecognised type must not be silently signed negative (today's `else -1` guessed)."""
    out = net_activities_by_date(
        [{"activity_type": "MYSTERY", "date": "2026-08-25", "net_amount": "10.00"}]
    )
    assert out == {}


# ---------------------------------------------------------------------------
# Root B — TWR survives a deposit dated the day before the equity reflects it
# ---------------------------------------------------------------------------


def test_twr_survives_settlement_lagged_deposit():
    """The live -28,304% case: cash-flow on day D (pre-deposit equity 349.94), equity only
    rises on D+1. TWR must stay bounded; net_deposits/pnl unchanged."""
    dates = ["2026-08-23", "2026-08-24", "2026-08-25"]
    equity = [300.0, 349.94, 6142.36]
    cashflows = [0.0, 5833.0, 0.0]  # flow dated D=08-24, equity reflects it 08-25
    r = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    assert abs(r["twr_pct"]) < 100.0, f"twr blew up: {r['twr_pct']}"
    assert r["net_deposits"] == pytest.approx(5833.0)
    assert r["pnl_net_of_deposits"] == pytest.approx(6142.36 - 300.0 - 5833.0)


# ---------------------------------------------------------------------------
# Root C — the 1D intraday deposit marker anchors to the equity jump
# ---------------------------------------------------------------------------


def test_intraday_lagged_deposit_anchors_to_the_equity_jump():
    """The 1D window is all on 08-25; the deposit's activity date is 08-24 (settlement lag) but
    the cash settles today — equity jumps 349 -> 6142 at the 3rd point. The marker MUST land on
    the jump (index 2), so the frontend strips it forward: NOT dropped (the -"+1663% today" bug),
    and NOT on the pre-jump point 0 (which would strip from a base that goes negative).
    """
    from core.engine.perf_metrics import bucket_cashflows_to_points_intraday

    dates = [
        "2026-08-25T13:30:00+00:00",
        "2026-08-25T14:00:00+00:00",
        "2026-08-25T14:30:00+00:00",
    ]
    equities = [348.37, 349.0, 6142.36]
    out = bucket_cashflows_to_points_intraday(dates, equities, {"2026-08-24": 5833.0})
    assert out == [0.0, 0.0, 5833.0]


def test_intraday_old_deposit_is_dropped_as_baked_into_base():
    """A deposit dated well before the 1D window is already inside the opening equity → dropping
    it is correct (attaching it would double-strip)."""
    from core.engine.perf_metrics import bucket_cashflows_to_points_intraday

    dates = ["2026-08-25T13:30:00+00:00", "2026-08-25T14:30:00+00:00"]
    out = bucket_cashflows_to_points_intraday(
        dates, [6142.0, 6143.0], {"2026-08-15": 5833.0}
    )
    assert out == [0.0, 0.0]


def test_intraday_in_window_flow_attaches_by_date():
    """A flow dated inside the window keeps the daily behaviour (first point on/after its date)."""
    from core.engine.perf_metrics import bucket_cashflows_to_points_intraday

    dates = ["2026-08-25T13:30:00+00:00", "2026-08-26T13:30:00+00:00"]
    out = bucket_cashflows_to_points_intraday(
        dates, [100.0, 6000.0], {"2026-08-26": 5833.0}
    )
    assert out == [0.0, 5833.0]


def test_aligned_deposit_is_unchanged_by_the_guard():
    """Regression: a normally-aligned deposit (flow on the day its equity includes it) is
    byte-identical — the carry-forward only fires when equity[i]-cf[i] would go non-positive.
    """
    dates = ["2026-07-24", "2026-07-25", "2026-08-09", "2026-08-10"]
    equity = [224.78, 224.78, 338.91, 339.62]
    cashflows = [0.0, 0.0, 114.13, 0.0]  # 338.91 - 114.13 = 224.78 > 0 → no carry
    r = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    assert r["twr_pct"] == pytest.approx(0.0, abs=0.5)
    assert r["net_deposits"] == pytest.approx(114.13)
