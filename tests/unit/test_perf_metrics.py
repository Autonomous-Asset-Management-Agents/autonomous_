"""Cash-flow-adjusted return metrics (#1957 follow-up — deposit contaminates KPIs).

A deposit/withdrawal must NOT move the reported return. TWR (time-weighted) is the headline
metric: it breaks the equity series at each cash-flow so external money neither inflates nor
deflates performance, and it is the only measure commensurable with the (cash-flow-free) SPY
benchmark. ``net_deposits`` and ``pnl_net_of_deposits`` give the € view alongside it.

Timing convention: ``cashflows[i]`` is the NET external flow that landed ON day ``i`` (deposit
positive, withdrawal negative) and is already reflected in ``equity[i]`` (end-of-day). The
market return of the sub-period i-1 -> i is therefore ``(equity[i] - cashflows[i]) / equity[i-1] - 1``.
Day 0 is inception: ``equity[0]`` is the starting capital, ``cashflows[0]`` is normally 0.
"""

import pytest

from core.engine.perf_metrics import (
    bucket_cashflows_to_points,
    compute_cashflow_adjusted_returns,
    net_activities_by_date,
    net_cashflow_by_date,
)


def test_net_activities_by_date_real_alpaca_shape():
    # REAL shape from GET /v2/account/activities/CSD (verified against a live account): a deposit
    # is {activity_type:"CSD", date, net_amount:"115.66"}. This is the source that actually returns
    # deposits — portfolio-history.cashflow comes back empty (the #2718/#2735 bug).
    activities = [
        {"activity_type": "CSD", "date": "2026-08-07", "net_amount": "115.66"},
        {"activity_type": "CSD", "date": "2026-07-22", "net_amount": "228.20"},
    ]
    assert net_activities_by_date(activities) == {
        "2026-08-07": 115.66,
        "2026-07-22": 228.20,
    }


def test_net_activities_withdrawal_is_negative_regardless_of_alpaca_sign():
    # CSW subtracts — enforced from activity_type so Alpaca's own sign can't flip it (whether it
    # returns net_amount "500" or "-500").
    assert net_activities_by_date(
        [{"activity_type": "CSW", "date": "2026-08-01", "net_amount": "500"}]
    ) == {"2026-08-01": -500.0}
    assert net_activities_by_date(
        [{"activity_type": "CSW", "date": "2026-08-01", "net_amount": "-500"}]
    ) == {"2026-08-01": -500.0}


def test_net_activities_sums_same_day_and_skips_malformed():
    activities = [
        {"activity_type": "CSD", "date": "2026-08-07", "net_amount": "100"},
        {"activity_type": "CSD", "date": "2026-08-07", "net_amount": "15.66"},
        {"activity_type": "CSD", "date": None, "net_amount": "9"},  # skipped (no date)
        {
            "activity_type": "CSD",
            "date": "2026-08-08",
            "net_amount": "oops",
        },  # skipped (bad amount)
    ]
    assert net_activities_by_date(activities) == {"2026-08-07": 115.66}
    assert net_activities_by_date([]) == {}


def test_users_real_case_end_to_end():
    # The exact reported live account: base 224.78, a +115.66 deposit on 08-07 that landed in the
    # snapshot gap (07-25 -> 08-09), trading ~flat. The 228.20 initial funding (07-22) predates the
    # first tracked point (07-24) → dropped as inception base. Return must read ~0 %, net_deposits 115.66.
    dates = ["2026-07-24", "2026-07-25", "2026-08-09", "2026-08-10"]
    equity = [224.78, 224.78, 338.91, 339.62]
    activities = [
        {"activity_type": "CSD", "date": "2026-08-07", "net_amount": "115.66"},
        {"activity_type": "CSD", "date": "2026-07-22", "net_amount": "228.20"},
    ]
    cf_by_date = net_activities_by_date(activities)
    cashflows = bucket_cashflows_to_points(dates, cf_by_date)
    r = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    assert r["twr_pct"] == pytest.approx(0.0, abs=0.5)  # NOT ~50 %
    assert r["net_deposits"] == pytest.approx(
        115.66
    )  # 228.20 dropped (pre-inception base)


# Two UTC days (2026-01-02, 2026-01-03) as epoch seconds.
_TS_D1 = 1767312000  # 2026-01-02 00:00:00 UTC
_TS_D2 = 1767398400  # 2026-01-03 00:00:00 UTC


def test_bucket_gap_deposit_lands_in_spanning_subperiod():
    # THE REAL BUG (#2717): a deposit on a day with NO snapshot must still be attributed to the
    # sub-period that spans it — the first point on/after the deposit date — not dropped.
    # Snapshots on 07-24, 07-25, 08-09, 08-10; deposit on 08-01 (engine was off, no snapshot).
    dates = ["2026-07-24", "2026-07-25", "2026-08-09", "2026-08-10"]
    buckets = bucket_cashflows_to_points(dates, {"2026-08-01": 114.13})
    assert buckets == [
        0.0,
        0.0,
        114.13,
        0.0,
    ]  # attributed to 08-09, the first point >= 08-01


def test_bucket_exact_date_match():
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert bucket_cashflows_to_points(dates, {"2026-01-02": 5_000.0}) == [
        0.0,
        5_000.0,
        0.0,
    ]


def test_bucket_flow_on_or_before_inception_is_dropped():
    # A flow on/before the first tracked point is part of the inception base, not a tracked deposit.
    dates = ["2026-01-05", "2026-01-06"]
    assert bucket_cashflows_to_points(
        dates, {"2026-01-05": 9_000.0, "2026-01-01": 1_000.0}
    ) == [
        0.0,
        0.0,
    ]


def test_bucket_flow_after_last_point_attaches_to_last():
    dates = ["2026-01-01", "2026-01-02"]
    assert bucket_cashflows_to_points(dates, {"2026-01-09": 5_000.0}) == [0.0, 5_000.0]


def test_bucket_multiple_flows_and_empty():
    dates = ["2026-01-01", "2026-01-04", "2026-01-08"]
    got = bucket_cashflows_to_points(
        dates, {"2026-01-02": 100.0, "2026-01-03": 50.0, "2026-01-06": 200.0}
    )
    assert got == [
        0.0,
        150.0,
        200.0,
    ]  # 01-02 & 01-03 -> first point >= them (01-04); 01-06 -> 01-08
    assert bucket_cashflows_to_points(dates, {}) == [0.0, 0.0, 0.0]
    assert bucket_cashflows_to_points([], {"2026-01-02": 1.0}) == []


def test_gap_deposit_end_to_end_twr_is_neutral():
    # Reproduces the user's live account: 224.78 base, +114.13 deposit in the snapshot gap,
    # trading essentially flat. TWR must read ~0 %, not the deposit-inflated +51 %.
    dates = ["2026-07-24", "2026-07-25", "2026-08-09", "2026-08-10"]
    equity = [224.78, 224.78, 338.91, 339.62]
    cashflows = bucket_cashflows_to_points(dates, {"2026-08-01": 114.13})
    r = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    assert r["twr_pct"] == pytest.approx(0.0, abs=0.5)  # ~0 %, NOT 51 %
    assert r["net_deposits"] == pytest.approx(114.13)


def test_net_cashflow_parses_csd_and_csw_signed():
    # CSD (deposit +) on day1, CSW (withdrawal −) on day2.
    out = net_cashflow_by_date(
        [_TS_D1, _TS_D2],
        {"CSD": [5_000.0, 0.0], "CSW": [0.0, -2_000.0]},
    )
    assert out == {"2026-01-02": 5_000.0, "2026-01-03": -2_000.0}


def test_net_cashflow_sums_same_day_multiple_types():
    # Deposit and withdrawal both on the same timestamp -> netted.
    out = net_cashflow_by_date([_TS_D1], {"CSD": [5_000.0], "CSW": [-1_500.0]})
    assert out == {"2026-01-02": 3_500.0}


def test_net_cashflow_empty_is_safe():
    assert net_cashflow_by_date([], {}) == {}
    assert net_cashflow_by_date([_TS_D1], {}) == {}
    # Ragged amounts list shorter than timestamps -> missing index treated as no flow.
    assert net_cashflow_by_date([_TS_D1, _TS_D2], {"CSD": [5_000.0]}) == {
        "2026-01-02": 5_000.0,
        "2026-01-03": 0.0,
    }


def test_deposit_does_not_move_return():
    # Gherkin 1: +5000 deposit, price flat -> TWR stays 0, net_deposits = 5000.
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02"],
        equity=[10_000.0, 15_000.0],
        cashflows=[0.0, 5_000.0],
    )
    assert r["twr_pct"] == pytest.approx(0.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(5_000.0)


def test_real_gain_without_cashflow():
    # Gherkin 2: 10000 -> 11000, no cash-flow -> 10 %.
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02"],
        equity=[10_000.0, 11_000.0],
        cashflows=[0.0, 0.0],
    )
    assert r["twr_pct"] == pytest.approx(10.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(0.0)
    assert r["pnl_net_of_deposits"] == pytest.approx(1_000.0)


def test_gain_after_deposit_is_attributed_correctly():
    # Gherkin 3: 10000 -> +5000 deposit (15000) -> 16500 (+10 % on 15000). TWR ~10 %, NOT 65 %.
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02", "2026-01-03"],
        equity=[10_000.0, 15_000.0, 16_500.0],
        cashflows=[0.0, 5_000.0, 0.0],
    )
    assert r["twr_pct"] == pytest.approx(10.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(5_000.0)
    assert r["pnl_net_of_deposits"] == pytest.approx(1_500.0)


def test_withdrawal_does_not_lower_return():
    # Gherkin 4: 15000 -> -5000 withdrawal (10000), price flat -> TWR 0, net_deposits -5000.
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02"],
        equity=[15_000.0, 10_000.0],
        cashflows=[0.0, -5_000.0],
    )
    assert r["twr_pct"] == pytest.approx(0.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(-5_000.0)


def test_multiple_deposits_chain_linked():
    # Two deposits interleaved with real moves; TWR must chain sub-period returns.
    # d0 10000 -> d1 11000 (+10 %) -> d2 deposit +5000 (16000, flat) -> d3 17600 (+10 %).
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        equity=[10_000.0, 11_000.0, 16_000.0, 17_600.0],
        cashflows=[0.0, 0.0, 5_000.0, 0.0],
    )
    # (1.10)(1.00)(1.10) - 1 = 0.21
    assert r["twr_pct"] == pytest.approx(21.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(5_000.0)
    assert r["pnl_net_of_deposits"] == pytest.approx(17_600.0 - 10_000.0 - 5_000.0)


def test_zero_start_equity_subperiod_is_guarded():
    # Funding a $0 account: day0 equity 0, day1 funded 5000 via deposit. The 0->5000 sub-period
    # has no valid base (division by zero) -> skipped; TWR measured only over funded sub-periods.
    r = compute_cashflow_adjusted_returns(
        dates=["2026-01-01", "2026-01-02", "2026-01-03"],
        equity=[0.0, 5_000.0, 5_500.0],
        cashflows=[0.0, 5_000.0, 0.0],
    )
    # only 5000 -> 5500 counts: +10 %
    assert r["twr_pct"] == pytest.approx(10.0, abs=1e-6)
    assert r["net_deposits"] == pytest.approx(5_000.0)


def test_empty_and_single_point_are_safe():
    empty = compute_cashflow_adjusted_returns(dates=[], equity=[], cashflows=[])
    assert empty["twr_pct"] == pytest.approx(0.0)
    assert empty["net_deposits"] == pytest.approx(0.0)
    single = compute_cashflow_adjusted_returns(
        dates=["2026-01-01"], equity=[10_000.0], cashflows=[0.0]
    )
    assert single["twr_pct"] == pytest.approx(0.0)
    assert single["pnl_net_of_deposits"] == pytest.approx(0.0)
