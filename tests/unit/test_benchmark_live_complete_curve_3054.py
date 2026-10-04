"""#3054 — the LIVE daily benchmark curve is built from Alpaca's COMPLETE portfolio_history  # noqa: E501
(period=all), not the sparse local snapshots that starve Sharpe/drawdown/TWR.

PR-3 deliberately gated the Alpaca backfill to paper (fear of paper-era pollution). For a  # noqa: E501
SEPARATE live Alpaca account that concern does not apply — its history is clean. Owner sign-off  # noqa: E501
2026-08-26 (separate account confirmed) authorised opening the gate for live.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.database.models import PortfolioSnapshot
from core.engine import api_routes


def _snap(ts, equity):
    return PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=ts,
        total_equity=equity,
        cash=0.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
        paper_trading=False,  # LIVE
    )


def _complete_backfill(n=25, start_equity=224.78):
    """A gap-free daily Alpaca curve of n days (what _get_inception_equity returns)."""  # noqa: E501
    d0 = datetime(2026, 7, 24, tzinfo=timezone.utc)
    pts = []
    for i in range(n):
        day = d0.strftime("%Y-%m-%d")
        d0 = d0.fromordinal(d0.toordinal() + 1).replace(tzinfo=timezone.utc)
        pts.append(
            {
                "date": day,
                "equity": round(start_equity + i * 0.1, 2),
                "cashflow": 0.0,
            }
        )
    return pts


async def _call_live(records, backfill, csd_rows=None):
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # cold cache -> rebuild
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = records
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_engine = MagicMock()

    def _get(path, *a, **k):
        if path.endswith("/CSD"):
            return csd_rows or []
        return []

    mock_engine.api.get.side_effect = _get
    mock_engine.data_provider.get_data.return_value = pd.DataFrame(
        {"close": [400.0] * max(len(backfill or []), 1)}
    )

    # fmt: off
    inc = (
        None if backfill is None
        else ("2026-07-24", backfill[0]["equity"], backfill)
    )
    # fmt: on  # noqa: E501

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_session)
        ),  # noqa: E501
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ), patch(
        "core.engine.api_routes._get_inception_equity", return_value=inc
    ), patch(
        "core.engine.api_routes._reconstruct_spy_points",
        return_value=([], None),
    ), patch.object(
        api_routes.config, "PAPER_TRADING", False
    ):
        from core.engine.api_routes import get_benchmark_equity

        return await get_benchmark_equity()


@pytest.mark.asyncio
async def test_live_uses_complete_alpaca_curve_over_sparse_snapshots():
    """Two sparse live snapshots, but Alpaca has 25 gap-free days -> the response must carry the  # noqa: E501
    COMPLETE 25-day curve (so Sharpe's >=20-point gate is met), not the 2 snapshots."""  # noqa: E501
    d1 = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    d2 = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    records = [_snap(d1, 349.94), _snap(d2, 6142.36)]
    backfill = _complete_backfill(25)
    res = await _call_live(records, backfill)
    assert res.get("message") != "no_live_history"
    assert len(res["points"]) == 25
    assert res["initial_capital"] == pytest.approx(backfill[0]["equity"])


@pytest.mark.asyncio
async def test_live_with_zero_snapshots_still_builds_from_alpaca():
    """Zero live snapshots must no longer return no_live_history when Alpaca has history."""  # noqa: E501
    res = await _call_live([], _complete_backfill(22))
    assert res.get("message") != "no_live_history"
    assert len(res["points"]) == 22


@pytest.mark.asyncio
async def test_live_deposit_still_neutralised_on_the_alpaca_curve():
    """A deposit inside the Alpaca curve is still stripped (cash-flow attachment runs)."""  # noqa: E501
    backfill = _complete_backfill(21)
    backfill[-1]["equity"] = round(backfill[-2]["equity"] + 5000.0, 2)
    res = await _call_live(
        [],
        backfill,
        csd_rows=[
            {
                "activity_type": "CSD",
                "date": backfill[-1]["date"],
                "net_amount": "5000",
            }
        ],
    )
    assert res["net_deposits"] == pytest.approx(5000.0)
    assert abs(res["twr_pct"]) < 50.0  # not the +2000%-style deposit inflation


@pytest.mark.asyncio
async def test_live_fails_soft_to_snapshots_when_alpaca_unavailable():
    """No Alpaca history -> keep today's snapshot behaviour (no regression)."""
    d1 = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    d2 = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    res = await _call_live([_snap(d1, 349.94), _snap(d2, 6142.36)], None)
    assert [p["equity"] for p in res["points"]] == [349.94, 6142.36]
