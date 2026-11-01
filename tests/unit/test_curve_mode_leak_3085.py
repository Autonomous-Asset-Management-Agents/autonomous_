# flake8: noqa
"""rc5 live findings (01.09.) — two defects behind the "-$99,062.91 / -89.34% past week"
screenshot taken right after an app restart on a second machine.

R5 (this file, backend): ``_append_live_equity_to_benchmark`` warms the Redis benchmark
cache every engine cycle. It read the cached ``points`` WITHOUT looking at the payload's
``paper_trading`` tag, appended today's equity of the CURRENT account, and then re-tagged
the whole payload with the CURRENT mode. After a paper<->live restart that produces one
array holding the other account's history (~110,875 paper) plus the new account's point
(11,812 live) — and, because the writer overwrote the tag, ``/benchmark-equity``'s PR-3
staleness guard (``_cache_mode_stale``) no longer fires: the mixed curve is served as
authentic. Measured against the screenshot: start 110,875.34 -> end 11,812.43 with no
cash-flow reproduces both the -$99,062.91 and the -89.34% to the cent.

R7 (perf_metrics): Alpaca's ``period=1W`` portfolio history covers days on which the
account did not exist yet and reports them as ZERO equity. Those leading points make the
1W chart a flat line at 0 with a cliff, and they are the base of the hero's money delta
(the "+$11,776.22 next to -0.53%" screenshot). They carry no information -> trim them.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _run_append(cached_payload, paper_flag, equity=11_812.43):
    mock_redis = MagicMock()
    mock_redis.get.return_value = (
        json.dumps(cached_payload) if cached_payload is not None else None
    )

    mock_acc = MagicMock()
    mock_acc.equity = equity
    mock_acc.cash = 0.0

    mock_api = MagicMock()
    mock_api.get_account.return_value = mock_acc
    mock_api.get_all_positions.return_value = []

    from core.engine.base import BotEngine

    engine_instance = object.__new__(BotEngine)
    engine_instance.api = mock_api
    engine_instance.is_simulation = False

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch("core.engine.base.get_cloud_logger", return_value=MagicMock()), patch(
        "config.PAPER_TRADING", paper_flag
    ):
        BotEngine._append_live_equity_to_benchmark(engine_instance)

    mock_redis.set.assert_called_once()
    return json.loads(mock_redis.set.call_args[0][1])


PAPER_ERA = {
    "points": [
        {"date": "2026-08-25", "equity": 110_875.34},
        {"date": "2026-08-26", "equity": 110_875.34},
    ],
    "paper_trading": True,
    "initial_capital": 100_000.0,
    "spy_points": [{"date": "2026-08-25", "equity": 100_000.0}],
}


def test_r5_wrong_mode_cache_is_discarded_not_appended_to():
    """The live engine must NOT append its equity onto the paper-era curve."""
    written = _run_append(PAPER_ERA, paper_flag=False)
    equities = [p["equity"] for p in written["points"]]
    assert 110_875.34 not in equities, "paper history leaked into the live curve"
    assert equities == [11_812.43]
    assert written["paper_trading"] is False


def test_r5_discarded_cache_also_drops_the_stale_rebuild_markers():
    """Dropping only ``points`` would keep ``initial_capital``/``spy_points`` of the other
    account — /benchmark-equity treats a payload WITH initial_capital as complete and skips
    the DB rebuild, so the S&P line and the rebase base would stay paper-era."""
    written = _run_append(PAPER_ERA, paper_flag=False)
    assert not written.get("initial_capital")
    assert not written.get("spy_points")


def test_r5_same_mode_cache_is_still_appended_to():
    """Regression guard: the normal warm-append path must keep working."""
    same_mode = {
        "points": [{"date": "2026-08-31", "equity": 11_800.00}],
        "paper_trading": False,
        "initial_capital": 36.21,
    }
    written = _run_append(same_mode, paper_flag=False)
    assert [p["equity"] for p in written["points"]] == [11_800.00, 11_812.43]
    assert written["initial_capital"] == 36.21


def test_r5_legacy_untagged_cache_counts_as_paper():
    """A legacy payload without the tag defaults to paper (same convention as the reader in
    api_routes) — in live that is a mismatch and must be discarded."""
    legacy = {"points": [{"date": "2026-08-26", "equity": 110_875.34}]}
    written = _run_append(legacy, paper_flag=False)
    assert [p["equity"] for p in written["points"]] == [11_812.43]


def test_r5_malformed_cache_is_survivable():
    """Fail-soft: a corrupt payload must not kill the engine cycle — it starts a fresh curve."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = "{not json"

    mock_acc = MagicMock()
    mock_acc.equity = 11_812.43
    mock_acc.cash = 0.0
    mock_api = MagicMock()
    mock_api.get_account.return_value = mock_acc
    mock_api.get_all_positions.return_value = []

    from core.engine.base import BotEngine

    engine_instance = object.__new__(BotEngine)
    engine_instance.api = mock_api
    engine_instance.is_simulation = False

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch("core.engine.base.get_cloud_logger", return_value=MagicMock()), patch(
        "config.PAPER_TRADING", False
    ):
        BotEngine._append_live_equity_to_benchmark(engine_instance)

    mock_redis.set.assert_called_once()
    written = json.loads(mock_redis.set.call_args[0][1])
    assert [p["equity"] for p in written["points"]] == [11_812.43]


# --- R7: pre-existence zero points -------------------------------------------------------


def test_r7_trims_leading_zero_equity_points():
    from core.engine.perf_metrics import trim_leading_unfunded

    pts = [
        {"date": "2026-08-25T13:00:00+00:00", "equity": 0.0},
        {"date": "2026-08-26T13:00:00+00:00", "equity": 0.0},
        {"date": "2026-08-27T13:00:00+00:00", "equity": 6_142.65},
        {"date": "2026-08-28T13:00:00+00:00", "equity": 11_812.43},
    ]
    out = trim_leading_unfunded(pts)
    assert [p["equity"] for p in out] == [6_142.65, 11_812.43]


def test_r7_keeps_interior_zeros_and_never_reorders():
    """Only the LEADING run is pre-existence; an interior zero is real data (a fully
    liquidated account) and must survive."""
    from core.engine.perf_metrics import trim_leading_unfunded

    pts = [
        {"date": "d1", "equity": 0.0},
        {"date": "d2", "equity": 100.0},
        {"date": "d3", "equity": 0.0},
        {"date": "d4", "equity": 50.0},
    ]
    assert [p["equity"] for p in trim_leading_unfunded(pts)] == [100.0, 0.0, 50.0]


def test_r7_fail_soft_when_trimming_would_leave_less_than_two_points():
    from core.engine.perf_metrics import trim_leading_unfunded

    pts = [{"date": "d1", "equity": 0.0}, {"date": "d2", "equity": 11_812.43}]
    assert len(trim_leading_unfunded(pts)) == 2  # unchanged: the chart needs two points
    allzero = [{"date": "d1", "equity": 0.0}, {"date": "d2", "equity": 0.0}]
    assert trim_leading_unfunded(allzero) == allzero
    assert trim_leading_unfunded([]) == []


def test_r7_tolerates_missing_or_bad_equity_keys():
    from core.engine.perf_metrics import trim_leading_unfunded

    pts = [
        {"date": "d1"},
        {"date": "d2", "equity": None},
        {"date": "d3", "equity": 5.0},
        {"date": "d4", "equity": 6.0},
    ]
    assert [p["date"] for p in trim_leading_unfunded(pts)] == ["d3", "d4"]


# --- R6 (engine side): the settlement-lag guard must not need a NEGATIVE remainder --------


def test_r6_engine_lag_guard_catches_a_positive_remainder():
    """The frontend chain-link and ``compute_cashflow_adjusted_returns`` must agree, so the
    engine gets the same rule: decide by the market move each reading implies.

    Real 28.08. shape: prev 6,142.65, a 5,785.60 deposit dated one point EARLY, equity still
    6,143.00, then the settled 11,928.25. The old guard only deferred when equity-flow went
    negative — here the remainder is a positive 357.40, so the sub-period read -94.2 % and the
    next +94 % (the "-94.27 % today" tile). Both sub-periods are ~flat in truth.
    """
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    res = compute_cashflow_adjusted_returns(
        dates=["2026-08-26", "2026-08-27", "2026-08-28"],
        equity=[6_142.65, 6_143.00, 11_928.25],
        cashflows=[0.0, 5_785.60, 0.0],
    )
    assert abs(res["twr_pct"]) < 1.0, res["twr_pct"]
    assert res["net_deposits"] == pytest.approx(5_785.60)
    assert abs(res["pnl_net_of_deposits"]) < 50.0


def test_r6_engine_still_strips_a_contained_deposit():
    """Regression guard: a deposit the equity DOES contain is stripped, not deferred."""
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    res = compute_cashflow_adjusted_returns(
        dates=["2026-08-01", "2026-08-02", "2026-08-03"],
        equity=[10_000.0, 11_000.0, 22_000.0],
        cashflows=[0.0, 0.0, 9_000.0],
    )
    assert res["twr_pct"] == pytest.approx(30.0, abs=1e-6)


def test_r6_engine_reports_a_real_loss_on_a_deposit_day():
    """A genuine market loss on the deposit day must survive the guard (no muting)."""
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    res = compute_cashflow_adjusted_returns(
        dates=["2026-08-01", "2026-08-02"],
        equity=[10_000.0, 14_800.0],  # +5,000 deposit, market -200
        cashflows=[0.0, 5_000.0],
    )
    assert res["twr_pct"] == pytest.approx(-2.0, abs=1e-6)
