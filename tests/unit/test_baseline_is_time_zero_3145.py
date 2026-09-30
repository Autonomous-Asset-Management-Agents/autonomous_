# flake8: noqa
"""#3145 bug 3 (02.09.): with the performance baseline set to 2026-09-01, the charts and KPIs
still showed older history. /benchmark-equity slices to the baseline (#3071) — but
/portfolio-intraday, which feeds the 1D/1W chart AND the Overview hero, never knew about it.
The baseline must be time zero for EVERY served curve.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Hist:
    def __init__(self, ts, eq):
        self.timestamp = ts
        self.equity = eq


def _epoch(month: int, day: int, hour: int = 14) -> int:
    from datetime import datetime, timezone

    return int(datetime(2026, month, day, hour, tzinfo=timezone.utc).timestamp())


async def _call_intraday(baseline):
    from fastapi import Request

    hist = _Hist(
        [_epoch(8, 29), _epoch(8, 30), _epoch(8, 31), _epoch(9, 1)],
        [6_143.0, 6_150.0, 11_900.0, 11_812.43],
    )
    api_client = MagicMock()
    api_client.get_portfolio_history.return_value = hist

    req = MagicMock(spec=Request)
    with patch(
        "core.engine.api_routes._resolve_orders_client",
        AsyncMock(return_value=api_client),
    ), patch("core.engine.api_routes._get_live_cashflows", return_value={}), patch(
        "core.engine.api_routes._get_performance_baseline",
        AsyncMock(return_value=baseline),
    ):
        from core.engine.api_routes import get_portfolio_intraday

        return await get_portfolio_intraday(req, range_="1W")


@pytest.mark.asyncio
async def test_intraday_starts_at_the_chosen_baseline():
    res = await _call_intraday("2026-08-31")
    days = [p["date"][:10] for p in res["points"]]
    assert days[0] >= "2026-08-31", days
    assert "2026-08-29" not in days and "2026-08-30" not in days


@pytest.mark.asyncio
async def test_intraday_without_a_baseline_is_unchanged():
    res = await _call_intraday(None)
    assert len(res["points"]) == 4


@pytest.mark.asyncio
async def test_intraday_echoes_the_baseline_so_the_console_can_label_it():
    res = await _call_intraday("2026-08-31")
    assert res["baseline_date"] == "2026-08-31"


@pytest.mark.asyncio
async def test_intraday_baseline_after_the_window_keeps_two_points():
    """Fail-soft, same rule as the daily path: never serve an empty/1-point curve."""
    res = await _call_intraday("2026-12-31")
    assert len(res["points"]) == 2


@pytest.mark.asyncio
async def test_intraday_baseline_read_failure_does_not_break_the_curve():
    from fastapi import Request

    hist = _Hist([_epoch(8, 29), _epoch(8, 30)], [6_143.0, 6_150.0])
    api_client = MagicMock()
    api_client.get_portfolio_history.return_value = hist
    req = MagicMock(spec=Request)
    with patch(
        "core.engine.api_routes._resolve_orders_client",
        AsyncMock(return_value=api_client),
    ), patch("core.engine.api_routes._get_live_cashflows", return_value={}), patch(
        "core.engine.api_routes._get_performance_baseline",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from core.engine.api_routes import get_portfolio_intraday

        res = await get_portfolio_intraday(req, range_="1W")
    assert len(res["points"]) == 2  # degrades to "no baseline", never to an empty chart


# --- #3145 bug 2: the same return, computed twice, must agree -----------------------------


def _parity_vector():
    import json
    import pathlib

    p = (
        pathlib.Path(__file__).resolve().parents[1]
        / "fixtures"
        / "twr_parity_3145.json"
    )
    return json.loads(p.read_text(encoding="utf-8"))


def test_engine_matches_the_shared_parity_vector():
    """The 'autonomous_' KPI card takes its return from here, the hero/chart computes the same
    number in the console. Both are pinned to ONE vector so neither can drift unnoticed —
    the console half asserts the identical value in
    src/console/live/__tests__/twrParity3145.test.ts.
    """
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    fx = _parity_vector()
    res = compute_cashflow_adjusted_returns(
        dates=fx["dates"], equity=fx["equity"], cashflows=fx["cashflows"]
    )
    assert res["twr_pct"] == pytest.approx(fx["expected_twr_pct"], abs=1e-9)
    assert res["net_deposits"] == pytest.approx(fx["net_deposits"], abs=1e-9)
    assert res["pnl_net_of_deposits"] == pytest.approx(
        fx["expected_market_pnl_engine"], abs=1e-9
    )


def test_parity_vector_money_and_percent_agree_in_sign():
    """Sanity the reader can redo by hand: 11,812.43 - 6,142.65 - 5,785.60 = -115.82, so the
    window is a loss — and the percentage must be negative too."""
    fx = _parity_vector()
    assert fx["expected_market_pnl_console"] == pytest.approx(-115.82, abs=1e-9)
    assert fx["expected_twr_pct"] < 0
