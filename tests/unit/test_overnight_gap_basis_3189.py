# flake8: noqa
"""#3189 (03.09., owner): "der P&L wird auf Basis des Startkurses pro Börsentag gerechnet, der ist
aber oft anders als der Schlusskurs des Vortages" — and the weekly figure in the chart differed
from the P&L tile.

Both were right. /portfolio-intraday built the curve from `timestamp` + `equity` only, so its
first point is the session's FIRST BAR: the overnight gap between yesterday's close and today's
open fell out of the measurement. For an account that holds positions overnight that gap is often
the bulk of the move. Meanwhile the tile computes `equity - lastEquity` against the previous daily
CLOSE (DailyUpdatesCard.tsx) — same quantity, other base, hence two numbers.

Alpaca ships the correct anchor in the same response: `base_value`, "Basis in dollar of the profit
loss calculation". It was discarded. Now it is prepended as the curve's first point, one bar-width
before the first bar, so the chart measures close -> now and shares the tile's base.

Only the window's LEFT edge was affected: the overnight jumps INSIDE a week already sat between
two consecutive hourly bars and were counted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Hist:
    def __init__(self, ts, eq, base_value=None):
        self.timestamp = ts
        self.equity = eq
        self.base_value = base_value


def _epoch(month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(
        datetime(2026, month, day, hour, minute, tzinfo=timezone.utc).timestamp()
    )


async def _call(hist, range_="1D", baseline=None, cashflows=None):
    from fastapi import Request

    api_client = MagicMock()
    api_client.get_portfolio_history.return_value = hist
    req = MagicMock(spec=Request)
    with patch(
        "core.engine.api_routes._resolve_orders_client",
        AsyncMock(return_value=api_client),
    ), patch(
        "core.engine.api_routes._get_live_cashflows", return_value=(cashflows or {})
    ), patch(
        "core.engine.api_routes._get_performance_baseline",
        AsyncMock(return_value=baseline),
    ):
        from core.engine.api_routes import get_portfolio_intraday

        return await get_portfolio_intraday(req, range_=range_)


# The real shape: yesterday closed at 11,800.00, today opened GAPPED UP at 11,880.00.
SESSION = _Hist(
    [_epoch(9, 3, 13, 30), _epoch(9, 3, 14, 0), _epoch(9, 3, 14, 30)],
    [11_880.00, 11_875.00, 11_890.00],
    base_value=11_800.00,
)


@pytest.mark.asyncio
async def test_curve_starts_at_the_previous_close_not_the_session_open():
    res = await _call(SESSION)
    assert res["points"][0]["equity"] == 11_800.00
    assert res["points"][1]["equity"] == 11_880.00  # the gap is now a measurable step


@pytest.mark.asyncio
async def test_the_anchor_sits_one_bar_before_the_first_bar():
    """It must precede the session strictly, so the curve stays chronological and the gap
    renders as a step at the left edge instead of being averaged into the first bar."""
    res = await _call(SESSION)
    first, second = res["points"][0]["date"], res["points"][1]["date"]
    assert first < second
    # 1D is served at 5-minute resolution -> one bar width earlier.
    assert first == datetime(2026, 9, 3, 13, 25, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_day_pl_now_equals_close_to_now_the_tile_s_own_basis():
    """The point of the fix: last - first is the figure the P&L tile shows
    (equity - previous close), so chart and tile cannot disagree."""
    res = await _call(SESSION)
    pts = res["points"]
    assert pts[-1]["equity"] - pts[0]["equity"] == pytest.approx(
        90.00
    )  # 11,890 - 11,800


@pytest.mark.asyncio
async def test_weekly_range_anchors_one_hour_before_the_week_s_first_bar():
    week = _Hist(
        [_epoch(8, 31, 13, 30), _epoch(9, 1, 13, 30), _epoch(9, 2, 13, 30)],
        [11_500.0, 11_700.0, 11_890.0],
        base_value=11_400.0,
    )
    res = await _call(week, range_="1W")
    assert res["points"][0]["equity"] == 11_400.0
    assert (
        res["points"][0]["date"]
        == datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc).isoformat()
    )


@pytest.mark.asyncio
async def test_missing_or_useless_base_value_changes_nothing():
    """Fail-soft: no anchor is invented. An absent, zero or negative base_value leaves the
    curve exactly as before (older API responses, a never-funded account)."""
    for bv in (None, 0.0, -5.0):
        res = await _call(
            _Hist(
                [_epoch(9, 3, 13, 30), _epoch(9, 3, 14, 0)],
                [11_880.0, 11_890.0],
                base_value=bv,
            )
        )
        assert [p["equity"] for p in res["points"]] == [11_880.0, 11_890.0], bv


@pytest.mark.asyncio
async def test_anchor_survives_the_unfunded_trim_and_does_not_shift_a_deposit():
    """Order matters: the anchor is prepended BEFORE the cash-flow alignment and before the
    pre-funding trim, so a deposit still lands on the equity JUMP and the anchor is not
    trimmed away as a leading point."""
    hist = _Hist(
        [_epoch(9, 3, 13, 30), _epoch(9, 3, 14, 0), _epoch(9, 3, 14, 30)],
        [6_143.00, 6_143.20, 11_928.25],
        base_value=6_142.65,
    )
    res = await _call(hist, cashflows={"2026-09-03": 5_785.60})
    pts = res["points"]
    assert pts[0]["equity"] == 6_142.65  # anchor kept
    # the deposit is attached to the settle jump, not to the anchor or the flat bars
    carriers = [i for i, p in enumerate(pts) if p.get("cashflow")]
    assert carriers == [len(pts) - 1], pts


@pytest.mark.asyncio
async def test_empty_history_stays_empty():
    res = await _call(_Hist([], [], base_value=11_800.0))
    assert res["points"] == []
