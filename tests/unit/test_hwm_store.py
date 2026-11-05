# tests/unit/test_hwm_store.py
# #2557 — the trailing-stop high-water-mark map must SURVIVE the daily desktop restart. Today
# _position_high_water_marks (core/engine/trading_loop.py) is in-memory only, so every reboot
# reseeds the peak from the current price → the trail measures drawdown only from boot and the
# pre-loop position_stop path can never fire a tier-trail. This store persists the map to a
# SystemConfig row (mirroring core/hitl_policy_store.py) so the peak is remembered. TDD Red→Green.
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.database.models import Base, SystemConfig
from core.engine.hwm_store import (
    POSITION_HWM_CONFIG_KEY,
    load_position_hwm,
    save_position_hwm,
)


async def _make_sqlite_factory():
    """In-memory SQLite engine + session factory with the ORM schema created.

    StaticPool keeps a single shared connection so the :memory: DB survives across sessions
    within one event loop (mirrors test_entitlement_webhook.py)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_upsert_then_read_round_trip():
    """save_position_hwm → load_position_hwm returns the same map (values as floats)."""
    engine, factory = await _make_sqlite_factory()
    marks = {"DDOG": 195.5, "AAPL": 231.25}
    with patch("core.database.session.AsyncSessionLocal", factory):
        await save_position_hwm(marks)
        # second save overwrites (upsert, not duplicate-insert) and prunes a dropped symbol
        await save_position_hwm({"DDOG": 200.0})
        got = await load_position_hwm()
    assert got == {"DDOG": 200.0}
    await engine.dispose()


async def test_empty_store_returns_empty_dict():
    """No persisted row → {} (the safe/byte-identical seed)."""
    engine, factory = await _make_sqlite_factory()
    with patch("core.database.session.AsyncSessionLocal", factory):
        got = await load_position_hwm()
    assert got == {}
    await engine.dispose()


async def test_corrupt_stored_value_returns_empty_dict_no_throw():
    """A corrupt (non-dict) config_value must degrade to {} without raising."""
    engine, factory = await _make_sqlite_factory()
    async with factory() as session:
        session.add(
            SystemConfig(
                config_key=POSITION_HWM_CONFIG_KEY,
                config_value=["not", "a", "dict"],
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    with patch("core.database.session.AsyncSessionLocal", factory):
        got = await load_position_hwm()  # must not throw
    assert got == {}
    await engine.dispose()


async def test_corrupt_entry_value_is_dropped():
    """A non-numeric per-symbol value is dropped; the rest survive (no throw)."""
    engine, factory = await _make_sqlite_factory()
    async with factory() as session:
        session.add(
            SystemConfig(
                config_key=POSITION_HWM_CONFIG_KEY,
                config_value={"DDOG": 200.0, "AAPL": "corrupt"},
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    with patch("core.database.session.AsyncSessionLocal", factory):
        got = await load_position_hwm()
    assert got == {"DDOG": 200.0}
    await engine.dispose()


async def test_none_value_returns_empty_dict():
    """A None value from the DB layer → {} (isinstance guard, no throw)."""
    with patch(
        "core.engine.hwm_store._read_system_config",
        AsyncMock(return_value=None),
    ):
        got = await load_position_hwm()
    assert got == {}


async def test_load_is_fail_safe_when_read_raises():
    """A DB error on read degrades to {} + a WARNING — never propagates (§5.6)."""
    with patch(
        "core.engine.hwm_store._read_system_config",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        got = await load_position_hwm()  # must not raise
    assert got == {}


async def test_save_is_fail_safe_when_upsert_raises():
    """A DB error on write is swallowed (no-op) + a WARNING — never breaks the loop (§5.6)."""
    with patch(
        "core.engine.hwm_store._upsert_system_config",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await save_position_hwm({"DDOG": 200.0})  # must not raise
