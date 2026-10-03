"""Endpoint-level proof that a deposit does not inflate the reported return (#1957).

LIVE mode builds the curve from DB ``PortfolioSnapshot`` rows (no cash-flow column) and fetches the
live account's CSD/CSW flows separately via ``_get_live_cashflows``. A +5000 deposit on a flat day
must leave ``twr_pct`` at 0 while ``net_deposits`` reflects the 5000. Reuses the cold-Redis + SQLite
scaffolding from test_portfolio_snapshot_persistence.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.database.models import PortfolioSnapshot
from core.engine import api_routes

_D1 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_D2 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
_TS_D1 = int(datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
_TS_D2 = int(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc).timestamp())


def _activities_get(csd_rows, csw_rows=None):
    """Side-effect for mock ``engine.api.get`` — returns the CSD/CSW activity lists the fix reads
    from ``GET /v2/account/activities/{CSD,CSW}``."""
    csw_rows = csw_rows or []

    def _get(path, *a, **k):
        if path.endswith("/CSD"):
            return csd_rows
        if path.endswith("/CSW"):
            return csw_rows
        return []

    return _get


def _snap(ts, equity):
    return PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=ts,
        total_equity=equity,
        cash=0.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
        paper_trading=False,  # LIVE snapshots
    )


@pytest.mark.asyncio
async def test_live_deposit_does_not_inflate_twr():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # cold cache -> full rebuild

    # Equity jumps 10000 -> 15000 purely because of a 5000 deposit on day 2 (price flat).
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [_snap(_D1, 10_000.0), _snap(_D2, 15_000.0)]
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    spy_df = pd.DataFrame({"close": [400.0, 420.0]}, index=[_D1, _D2])

    # #2717: _get_live_cashflows() reads the ACCOUNT-ACTIVITIES API (portfolio-history.cashflow is
    # empty in reality). A 5000 CSD deposit on day 2.
    mock_engine = MagicMock()
    mock_engine.api.get.side_effect = _activities_get(
        [{"activity_type": "CSD", "date": "2026-06-02", "net_amount": "5000"}]
    )
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ), patch.object(
        api_routes.config, "PAPER_TRADING", False
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    # Curve still shows the real equity (incl. the deposit) …
    assert res["initial_capital"] == 10_000.0
    assert [p["equity"] for p in res["points"]] == [10_000.0, 15_000.0]
    # … but the RETURN is deposit-neutral.
    assert res["twr_pct"] == pytest.approx(0.0, abs=1e-6)
    assert res["net_deposits"] == pytest.approx(5_000.0)
    # day-2 point carries the deposit marker
    assert res["points"][1]["cashflow"] == pytest.approx(5_000.0)


@pytest.mark.asyncio
async def test_live_deposit_in_snapshot_gap_still_neutralised():
    # #2717 regression: the deposit lands on a day with NO snapshot (engine was off). Snapshots
    # exist only on 06-01 and 06-03; the CSD is on 06-02. The old exact-date alignment dropped it
    # → TWR stayed the deposit-inflated +50 %. Bucketing must attribute it to the 06-01→06-03
    # sub-period (first point on/after 06-02 = 06-03) so TWR reads 0 %.
    d3 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)

    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [
        _snap(_D1, 10_000.0),
        _snap(d3, 15_000.0),
    ]  # no 06-02 point
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    spy_df = pd.DataFrame({"close": [400.0, 420.0]}, index=[_D1, d3])

    # Account-activities reports the deposit on the GAP day 06-02 (no snapshot that day).
    mock_engine = MagicMock()
    mock_engine.api.get.side_effect = _activities_get(
        [{"activity_type": "CSD", "date": "2026-06-02", "net_amount": "5000"}]
    )
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ), patch.object(
        api_routes.config, "PAPER_TRADING", False
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    assert [p["equity"] for p in res["points"]] == [10_000.0, 15_000.0]
    assert res["twr_pct"] == pytest.approx(0.0, abs=1e-6)  # was +50 % before the fix
    assert res["net_deposits"] == pytest.approx(5_000.0)
    # the gap deposit is attributed to the spanning sub-period's end point (06-03)
    assert res["points"][1]["cashflow"] == pytest.approx(5_000.0)
