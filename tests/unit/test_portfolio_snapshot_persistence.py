# tests/unit/test_portfolio_snapshot_persistence.py

import json
import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.database.models import PortfolioSnapshot


@pytest.mark.asyncio
async def test_api_route_fallback_reconstruction_sqlite():
    """Test get_benchmark_equity fallback when Redis is cold under SQLite dialect."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Cold cache

    # Setup database snapshots
    snap1 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=10000.0,
        cash=5000.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    snap2 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=10500.0,
        cash=4500.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [snap1, snap2]
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars

    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Mock historical SPY prices
    import pandas as pd

    spy_data = {"close": [400.0, 420.0]}
    spy_df = pd.DataFrame(
        spy_data,
        index=[
            datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        ],
    )

    mock_engine = MagicMock()
    mock_engine.api = MagicMock()
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):

        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

        assert res["initial_capital"] == 10000.0
        assert len(res["points"]) == 2
        assert res["points"][0]["equity"] == 10000.0
        assert res["points"][1]["equity"] == 10500.0

        assert len(res["spy_points"]) == 2
        assert res["spy_points"][0]["equity"] == 10000.0
        assert res["spy_points"][1]["equity"] == 10500.0

        # Verify redis cache set was called
        mock_redis.set.assert_called_once()
        set_val = json.loads(mock_redis.set.call_args[0][1])
        assert set_val["initial_capital"] == 10000.0
        assert len(set_val["points"]) == 2


@pytest.mark.asyncio
async def test_api_route_fallback_reconstruction_postgres():
    """Test get_benchmark_equity fallback when Redis is cold under PostgreSQL dialect."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Cold cache

    # Setup database snapshots (distinct ordered by date, latest day first then sorted)
    snap1 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=10000.0,
        cash=5000.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    snap2 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=11000.0,
        cash=4000.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [snap2, snap1]
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars

    mock_session = MagicMock()
    mock_session.bind.dialect.name = "postgresql"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Mock historical SPY prices
    import pandas as pd

    spy_data = {"close": [500.0, 550.0]}
    spy_df = pd.DataFrame(
        spy_data,
        index=[
            datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        ],
    )

    mock_engine = MagicMock()
    mock_engine.api = MagicMock()
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):

        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

        # Verify it got sorted ascending by timestamp
        assert res["points"][0]["equity"] == 10000.0
        assert res["points"][1]["equity"] == 11000.0


def test_append_live_equity_to_benchmark_triggers_snapshot():
    """_append_live_equity_to_benchmark must reach log_portfolio_snapshot via the REAL
    local-mode sync facade.

    BORA desktop regression: the local-mode get_sync_redis() backend (LocalStateClient)
    has async get/set, so without _SyncLocalStateFacade the writer threw on
    json.loads(<coroutine>) and the snapshot was silently swallowed — portfolio_snapshots
    stayed empty forever on desktop. We INJECT the real facade here (bypassing the global
    fakeredis autouse mock) so this genuinely guards the bug by default."""
    from core.redis_client import (
        RedisClient,
        _get_local_state_client,
        _SyncLocalStateFacade,
    )

    real = _SyncLocalStateFacade(_get_local_state_client())
    real.delete("benchmark_equity_data")  # deterministic cold cache

    mock_acc = MagicMock()
    mock_acc.equity = 150000.0
    mock_acc.cash = 50000.0

    mock_pos = MagicMock()
    mock_pos.symbol = "AAPL"
    mock_pos.qty = 100.0

    mock_api = MagicMock()
    mock_api.get_account.return_value = mock_acc
    mock_api.get_all_positions.return_value = [mock_pos]

    mock_logger = MagicMock()

    from core.engine.base import BotEngine

    engine_instance = object.__new__(BotEngine)
    engine_instance.api = mock_api
    engine_instance.is_simulation = False

    # Inject the REAL sync facade: the global fakeredis autouse mock would otherwise
    # hand base.py a synchronous fakeredis and hide the desktop bug entirely.
    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=real
    ), patch("core.engine.base.get_cloud_logger", return_value=mock_logger):
        BotEngine._append_live_equity_to_benchmark(engine_instance)

    # log_portfolio_snapshot must have been reached with the correct structure
    mock_logger.log_portfolio_snapshot.assert_called_once()
    snapshot = mock_logger.log_portfolio_snapshot.call_args[0][0]
    assert snapshot["total_equity"] == 150000.0
    assert snapshot["cash"] == 50000.0
    assert snapshot["positions_json"] == [{"symbol": "AAPL", "qty": 100.0}]


@pytest.mark.asyncio
async def test_benchmark_equity_recomputes_frozen_empty_spy_points():
    """Regression (S&P line missing): a WARM benchmark cache whose spy_points is empty must self-heal.

    spy_points was reconstructed only on a COLD cache; on a warm cache the handler extended
    spy_points only ``if spy_points`` was already non-empty. So an early empty spy_points (the
    first reconstruction ran before SPY data was available / a transient miss) stayed empty
    forever -> the S&P benchmark line never appeared even though SPY data is now available.
    The handler must recompute spy_points from the cached points when the cached list is empty.

    BORA: driven purely through the RedisClient sync abstraction (the same handle in desktop
    LocalState and cloud Redis) + engine.data_provider, so the self-heal works in every edition.
    """
    import pandas as pd

    cached = {
        "points": [
            {"date": "2026-06-01", "equity": 10000.0},
            {"date": "2026-06-02", "equity": 10500.0},
        ],
        "spy_points": [],  # frozen empty -> the bug
        "initial_capital": 10000.0,
        "spy_first_close": None,
        "start_date": "2026-06-01",
        "end_date": "2026-06-02",
        "strategy": "RLAgent",
        "final_equity": 10500.0,
    }
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(cached)  # WARM cache

    spy_df = pd.DataFrame(
        {"close": [400.0, 420.0]},
        index=[
            datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        ],
    )
    mock_engine = MagicMock()
    mock_engine.api = None  # skip the live-equity append -> deterministic 2 points
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch("core.engine.api_routes.engine", mock_engine):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    # spy_points recomputed from the cached points (was [] in the cache)
    assert len(res["spy_points"]) == 2, res["spy_points"]
    # normalized so the first SPY point == initial_capital, then scaled by SPY return
    assert res["spy_points"][0]["equity"] == 10000.0
    assert res["spy_points"][1]["equity"] == 10500.0  # 10000 * 420/400
    # the healed series is written back so it does not recompute on every poll
    mock_redis.set.assert_called()


@pytest.mark.asyncio
async def test_benchmark_equity_rebuilds_from_db_when_cache_is_thin():
    """Regression (no S&P + truncated line after restart): a THIN cache written only by
    _append_live_equity_to_benchmark (base.py) — ``points`` but no initial_capital / spy_points
    — must trigger a full DB rebuild, not be returned as-is.

    base.py warms this key every engine cycle with points only; when that happens before the
    first dashboard poll it pre-empts the full reconstruction, so the handler returned a
    points-only cache forever: no S&P line and a history truncated to base.py's per-cycle
    appends. A missing initial_capital now marks the cache as thin -> full rebuild from the DB.
    """
    import pandas as pd

    thin = {
        "points": [{"date": "2026-06-02", "equity": 10500.0}]
    }  # base.py-warmed: no initial_capital/spy_points
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(thin)

    snap1 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=10000.0,
        cash=5000.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    snap2 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=10500.0,
        cash=4500.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [snap1, snap2]
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    spy_df = pd.DataFrame(
        {"close": [400.0, 420.0]},
        index=[
            datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        ],
    )
    mock_engine = MagicMock()
    mock_engine.api = MagicMock()
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    # full history rebuilt from the DB (not the thin cache's single point)
    assert res["initial_capital"] == 10000.0
    assert len(res["points"]) == 2
    # S&P benchmark now present
    assert len(res["spy_points"]) == 2
    assert res["spy_points"][0]["equity"] == 10000.0
    assert res["spy_points"][1]["equity"] == 10500.0


# --- #1782: "since inception" from the real Alpaca full portfolio-history -----------------


def _mk_portfolio_history(timestamps, equities):
    """Minimal stand-in for alpaca-py PortfolioHistory (only .timestamp/.equity are read)."""
    from types import SimpleNamespace

    return SimpleNamespace(timestamp=list(timestamps), equity=list(equities))


def _epoch(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def test_get_inception_equity_from_portfolio_history():
    """_get_inception_equity returns the first non-zero equity day from Alpaca's full history."""
    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.return_value = _mk_portfolio_history(
        [_epoch(2026, 2, 1), _epoch(2026, 2, 2), _epoch(2026, 2, 3)],
        [100000.0, 100500.0, 101000.0],
    )
    with patch("core.engine.api_routes.engine", mock_engine):
        from core.engine.api_routes import _get_inception_equity

        res = _get_inception_equity()
    assert res is not None
    inception_date, inception_equity, backfill = res
    assert inception_date == "2026-02-01"
    assert inception_equity == 100000.0
    assert [p["date"] for p in backfill] == ["2026-02-01", "2026-02-02", "2026-02-03"]
    assert backfill[0]["equity"] == 100000.0


def test_get_inception_equity_skips_leading_zero_equity():
    """Pre-deposit $0 days are skipped; inception = the first FUNDED day (created_at != funding)."""
    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.return_value = _mk_portfolio_history(
        [_epoch(2026, 2, 1), _epoch(2026, 2, 2), _epoch(2026, 2, 3)],
        [0.0, 0.0, 100000.0],
    )
    with patch("core.engine.api_routes.engine", mock_engine):
        from core.engine.api_routes import _get_inception_equity

        res = _get_inception_equity()
    assert res is not None
    inception_date, inception_equity, _ = res
    assert inception_date == "2026-02-03"
    assert inception_equity == 100000.0


def test_get_inception_equity_fallback_on_error():
    """Any Alpaca error -> None (fail-soft) so the caller falls back to records[0]."""
    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.side_effect = RuntimeError("alpaca down")
    with patch("core.engine.api_routes.engine", mock_engine):
        from core.engine.api_routes import _get_inception_equity

        assert _get_inception_equity() is None


@pytest.mark.asyncio
async def test_benchmark_equity_uses_alpaca_inception():
    """get_benchmark_equity uses the true Alpaca inception ($100k, 2026-02-01) as initial_capital
    and prepends the pre-snapshot daily equity — not records[0] (the first captured snapshot).
    """
    import pandas as pd

    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # cold cache

    # DB records only start at engine boot (2026-06-01) — NOT the true account inception.
    snap1 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=158000.0,
        cash=0.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    snap2 = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        timestamp=datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
        total_equity=155000.0,
        cash=0.0,
        positions_json=[],
        strategy_name="RLAgent",
        is_simulation=False,
    )
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [snap1, snap2]
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    spy_df = pd.DataFrame(
        {"close": [400.0]}, index=[datetime(2026, 2, 1, tzinfo=timezone.utc)]
    )

    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.return_value = _mk_portfolio_history(
        [_epoch(2026, 2, 1), _epoch(2026, 2, 2)], [100000.0, 101000.0]
    )
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    # inception = the true Alpaca start ($100k / 2026-02-01), NOT records[0] (158000)
    assert res["initial_capital"] == 100000.0
    dates = [p["date"] for p in res["points"]]
    assert dates[0] == "2026-02-01"  # prepended pre-snapshot inception day
    assert "2026-06-01" in dates and "2026-06-02" in dates  # DB records preserved


@pytest.mark.asyncio
async def test_benchmark_equity_empty_db_backfills_from_alpaca():
    """A fresh engine (no DB snapshots) still returns the account's FULL Alpaca history
    (period=all), so the Console/demo render the inception curve on day one — not an empty chart.
    """
    import pandas as pd

    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # cold cache

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []  # EMPTY DB — no recorded snapshots yet
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    spy_df = pd.DataFrame(
        {"close": [400.0]}, index=[datetime(2026, 2, 1, tzinfo=timezone.utc)]
    )

    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.return_value = _mk_portfolio_history(
        [_epoch(2026, 2, 1), _epoch(2026, 2, 2), _epoch(2026, 2, 3)],
        [100000.0, 100800.0, 101500.0],
    )
    mock_engine.data_provider.get_data.return_value = spy_df

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    assert res.get("message") is None  # NOT the "no run yet" placeholder
    assert res["initial_capital"] == 100000.0
    dates = [p["date"] for p in res["points"]]
    assert dates == ["2026-02-01", "2026-02-02", "2026-02-03"]
    assert len(res["spy_points"]) >= 1  # S&P reconstructed from the Alpaca curve
    mock_redis.set.assert_called_once()  # result cached


@pytest.mark.asyncio
async def test_benchmark_equity_empty_db_and_no_alpaca_returns_placeholder():
    """Empty DB AND no Alpaca history -> the honest 'no run yet' placeholder (never fabricate)."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_engine = MagicMock()
    mock_engine.api.get_portfolio_history.side_effect = RuntimeError("no history")

    with patch(
        "core.redis_client.RedisClient.get_sync_redis", return_value=mock_redis
    ), patch(
        "core.database.session.AsyncSessionLocal",
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session)),
    ), patch(
        "core.engine.api_routes.engine", mock_engine
    ):
        from core.engine.api_routes import get_benchmark_equity

        res = await get_benchmark_equity()

    assert res["points"] == []
    assert res["message"] == "No benchmark run yet."
    assert res["initial_capital"] is None
