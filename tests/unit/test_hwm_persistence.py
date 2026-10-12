# tests/unit/test_hwm_persistence.py
# #2557 — wire the persistent HWM store into TradingLoopMixin._ratchet_high_water_marks so the
# per-symbol trailing-stop peak survives the daily desktop restart. Guarded by BOTH
# POSITION_EXIT_HWM_TRAILING_ENABLED and POSITION_EXIT_HWM_PERSIST_ENABLED. Fail-safe: flag OFF,
# empty/None store, or a read error → prev = {} → byte-identical to today (a missing peak can only
# make the trail STRICTER — absence is conservative, it never manufactures a false exit). TDD Red→Green.
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.database.models import Base
from core.engine.hwm_store import load_position_hwm
from core.engine.trading_loop import TradingLoopMixin


def _sn_pos(symbol, current):
    return SimpleNamespace(
        symbol=symbol, qty=1.0, avg_entry_price=1.0, current_price=current
    )


async def _make_sqlite_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_flag_on_peak_survives_restart_and_prunes():
    """(a) Flag ON: the ratcheted peak is persisted; a fresh loop instance (simulated restart)
    seeds prev from the store so the peak SURVIVES; a no-longer-held symbol drops (prune).
    """
    engine, factory = await _make_sqlite_factory()
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=True,
        POSITION_EXIT_HWM_PERSIST_ENABLED=True,
    )
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.database.session.AsyncSessionLocal", factory
    ):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        m1 = await eng._ratchet_high_water_marks(
            [_sn_pos("DDOG", 200.0), _sn_pos("AAPL", 50.0)]
        )
        assert m1 == {"DDOG": 200.0, "AAPL": 50.0}

        # Simulated restart: a brand-new instance with NO in-memory map.
        eng2 = TradingLoopMixin.__new__(TradingLoopMixin)
        # DDOG rolled over to 190 (peak must be REMEMBERED at 200), AAPL no longer held.
        m2 = await eng2._ratchet_high_water_marks([_sn_pos("DDOG", 190.0)])
        assert m2 == {"DDOG": 200.0}, "peak must survive the restart via the store"

        persisted = await load_position_hwm()
    assert persisted == {
        "DDOG": 200.0
    }, "AAPL no longer held → pruned from the persisted row"
    await engine.dispose()


async def test_flag_off_no_store_access_byte_identical():
    """(b) Persist flag OFF → the store is NEVER touched; behaviour byte-identical to today."""
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=True,
        POSITION_EXIT_HWM_PERSIST_ENABLED=False,
    )
    fake_load = AsyncMock(return_value={"DDOG": 999.0})
    fake_save = AsyncMock()
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.engine.trading_loop.load_position_hwm", fake_load
    ), patch("core.engine.trading_loop.save_position_hwm", fake_save):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        m1 = await eng._ratchet_high_water_marks([_sn_pos("DDOG", 100.0)])
    assert m1 == {"DDOG": 100.0}  # seeded from {}, NOT from the store's 999
    fake_load.assert_not_awaited()
    fake_save.assert_not_awaited()


async def test_read_error_falls_back_to_empty_no_false_exit():
    """(c) Store raises on read → seed falls back to {} → NO exit forced (missing peak = current
    price → drawdown 0 → conservative). The loop must not raise."""
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=True,
        POSITION_EXIT_HWM_PERSIST_ENABLED=True,
    )
    fake_load = AsyncMock(side_effect=RuntimeError("db down"))
    fake_save = AsyncMock()
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.engine.trading_loop.load_position_hwm", fake_load
    ), patch("core.engine.trading_loop.save_position_hwm", fake_save):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        m1 = await eng._ratchet_high_water_marks([_sn_pos("DDOG", 100.0)])
    # prev fell back to {} → current is just the observed price (no inflated/false peak).
    assert m1 == {"DDOG": 100.0}


async def test_disabled_trailing_returns_none_no_store_access():
    """Trailing flag OFF → returns None (byte-identical) and never touches the store."""
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=False,
        POSITION_EXIT_HWM_PERSIST_ENABLED=True,
    )
    fake_load = AsyncMock()
    fake_save = AsyncMock()
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.engine.trading_loop.load_position_hwm", fake_load
    ), patch("core.engine.trading_loop.save_position_hwm", fake_save):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        assert await eng._ratchet_high_water_marks([_sn_pos("DDOG", 100.0)]) is None
    fake_load.assert_not_awaited()
    fake_save.assert_not_awaited()


async def test_save_error_does_not_kill_loop():
    """A write error is swallowed — the ratchet still returns the in-memory map (never loop-killing)."""
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=True,
        POSITION_EXIT_HWM_PERSIST_ENABLED=True,
    )
    fake_load = AsyncMock(return_value={})
    fake_save = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.engine.trading_loop.load_position_hwm", fake_load
    ), patch("core.engine.trading_loop.save_position_hwm", fake_save):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        m1 = await eng._ratchet_high_water_marks([_sn_pos("DDOG", 100.0)])
    assert m1 == {"DDOG": 100.0}
