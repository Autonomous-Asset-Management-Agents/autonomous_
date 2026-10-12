"""
Unit tests for core/database/bootstrap.py (OSS-4 / #1085).

Tests the local SQLite database initialization, schema version tracking,
backup+rebuild on schema upgrade, and :memory: safety.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.database.bootstrap import (
    CURRENT_SCHEMA_VERSION,
    _backup_db_file,
    _get_schema_version,
    _set_schema_version,
    init_local_db,
)


@pytest.fixture
async def sqlite_engine(tmp_path):
    """Create a temporary SQLite engine for testing."""
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def memory_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await engine.dispose()


class TestSchemaVersionTracking:
    """Test _get_schema_version and _set_schema_version."""

    @pytest.mark.asyncio
    async def test_fresh_db_returns_none(self, memory_engine):
        """Fresh database with no _schema_meta table returns None."""
        version = await _get_schema_version(memory_engine)
        assert version is None

    @pytest.mark.asyncio
    async def test_set_and_get_version(self, memory_engine):
        """Setting schema version can be read back."""
        await _set_schema_version(memory_engine, 1)
        version = await _get_schema_version(memory_engine)
        assert version == 1

    @pytest.mark.asyncio
    async def test_version_can_be_updated(self, memory_engine):
        """Schema version can be updated (upsert)."""
        await _set_schema_version(memory_engine, 1)
        await _set_schema_version(memory_engine, 2)
        version = await _get_schema_version(memory_engine)
        assert version == 2


class TestBackupDbFile:
    """Test _backup_db_file safety."""

    def test_backup_physical_file(self, tmp_path):
        """Physical DB file gets backed up with timestamp suffix."""
        db_file = tmp_path / "test.db"
        db_file.write_text("fake db content")

        backup = _backup_db_file(db_file)
        assert backup is not None
        assert backup.exists()
        assert ".bak." in backup.name

    def test_backup_nonexistent_file_returns_none(self, tmp_path):
        """Non-existent file returns None (no crash)."""
        db_file = tmp_path / "nonexistent.db"
        result = _backup_db_file(db_file)
        assert result is None

    def test_backup_memory_path_returns_none(self):
        """':memory:' path returns None (BORA-CRASH-01 fix)."""
        result = _backup_db_file(Path(":memory:"))
        assert result is None

    def test_backup_directory_returns_none(self, tmp_path):
        """Directory path returns None (is_file check)."""
        result = _backup_db_file(tmp_path)
        assert result is None


class TestInitLocalDb:
    """Test init_local_db — the main bootstrap entry point."""

    @pytest.mark.asyncio
    async def test_skips_non_sqlite_engine(self):
        """Non-SQLite engines are skipped (no-op)."""
        from unittest.mock import MagicMock

        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://localhost/db"

        await init_local_db(mock_engine)
        mock_engine.begin.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_db_creates_schema(self, memory_engine):
        """Fresh SQLite DB gets tables created + schema version set."""
        await init_local_db(memory_engine)

        version = await _get_schema_version(memory_engine)
        assert version == CURRENT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_current_schema_is_noop(self, memory_engine):
        """DB at current schema version is a no-op."""
        # First init
        await init_local_db(memory_engine)

        # Second init — should be no-op
        await init_local_db(memory_engine)

        version = await _get_schema_version(memory_engine)
        assert version == CURRENT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_outdated_schema_triggers_rebuild(self, memory_engine):
        """DB with old schema version triggers backup + rebuild."""
        # Simulate old schema
        await _set_schema_version(memory_engine, 0)

        # init_local_db should detect version mismatch and rebuild
        await init_local_db(memory_engine)

        version = await _get_schema_version(memory_engine)
        assert version == CURRENT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_adds_paper_trading_column_without_dropping_rows(self, sqlite_engine):
        """PR-3 Inc1: an existing SQLite DB whose portfolio_snapshots table predates the
        paper_trading discriminator must gain the column IN PLACE — additive ALTER, no
        drop/rebuild -> existing snapshot rows survive.

        create_all() only CREATES missing tables; it never ALTERs an existing one, so a DB
        already at CURRENT_SCHEMA_VERSION would otherwise stay column-less forever. We must NOT
        bump CURRENT_SCHEMA_VERSION for this (that drop_all's the WORM audit / round-table
        tables = data loss).
        """
        # Pre-existing DB at the CURRENT schema version, but portfolio_snapshots has NO
        # paper_trading column (predates PR-3), and it already holds a row.
        async with sqlite_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE portfolio_snapshots ("
                    "id TEXT PRIMARY KEY, timestamp DATETIME, total_equity FLOAT, "
                    "cash FLOAT, positions_json JSON, strategy_name TEXT, "
                    "is_simulation BOOLEAN)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO portfolio_snapshots "
                    "(id, total_equity, strategy_name, is_simulation) "
                    "VALUES ('row1', 12345.0, 'RLAgent', 0)"
                )
            )
        await _set_schema_version(sqlite_engine, CURRENT_SCHEMA_VERSION)

        # Pre-condition: the column is absent.
        async with sqlite_engine.begin() as conn:
            before = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(portfolio_snapshots)"))
                ).fetchall()
            }
        assert "paper_trading" not in before

        await init_local_db(sqlite_engine)

        # Post-condition: column added, and the pre-existing row was NOT dropped.
        async with sqlite_engine.begin() as conn:
            after = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(portfolio_snapshots)"))
                ).fetchall()
            }
            rows = (
                await conn.execute(
                    text("SELECT id, total_equity FROM portfolio_snapshots")
                )
            ).fetchall()
        assert "paper_trading" in after
        assert len(rows) == 1
        assert rows[0][0] == "row1"
        assert rows[0][1] == 12345.0

    @pytest.mark.asyncio
    async def test_paper_trading_column_ensure_is_idempotent(self, sqlite_engine):
        """PR-3 Inc1: running init_local_db twice must not fail (duplicate ALTER) — the
        column-ensure is idempotent (PRAGMA table_info guard before ADD COLUMN)."""
        await init_local_db(
            sqlite_engine
        )  # fresh -> create_all (column present via model)
        await init_local_db(sqlite_engine)  # second run must be a clean no-op

        async with sqlite_engine.begin() as conn:
            cols = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(portfolio_snapshots)"))
                ).fetchall()
            }
        assert "paper_trading" in cols

    @pytest.mark.asyncio
    async def test_adds_is_sim_day_columns_in_place(self, sqlite_engine):
        """#2588: an existing SQLite DB whose tables predate the ``is_sim_day`` column
        (#2544 added it to the PortfolioSnapshot + RoundTableSession models WITHOUT a
        bootstrap column-ensure) must gain the column IN PLACE on the next init.

        Without this, EVERY ORM SELECT of PortfolioSnapshot fails with
        ``sqlite3.OperationalError: no such column: portfolio_snapshots.is_sim_day`` —
        which is exactly how /benchmark-equity went dark (empty points ->
        "Collecting equity history…" + Since-inception/Started/MaxDD all "—").
        """
        import sqlalchemy as sa
        from sqlalchemy.ext.asyncio import AsyncSession

        from core.database.models import PortfolioSnapshot

        # Pre-existing DB at the CURRENT schema version whose tables predate #2544:
        # portfolio_snapshots HAS paper_trading but NOT is_sim_day (the real drift
        # observed on a 0.3.10 desktop install), round_table_sessions likewise.
        async with sqlite_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE portfolio_snapshots ("
                    "id TEXT PRIMARY KEY, timestamp DATETIME, total_equity FLOAT, "
                    "cash FLOAT, positions_json JSON, strategy_name TEXT, "
                    "is_simulation BOOLEAN, paper_trading BOOLEAN)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO portfolio_snapshots "
                    "(id, total_equity, strategy_name, is_simulation) "
                    "VALUES ('row1', 12345.0, 'RLAgent', 0)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE round_table_sessions ("
                    "session_id TEXT PRIMARY KEY, session_time DATETIME NOT NULL, "
                    "symbol TEXT NOT NULL, consensus_score FLOAT NOT NULL, "
                    "signal_action TEXT, gatekeeper_approved BOOLEAN NOT NULL, "
                    "gatekeeper_reason TEXT, vote_count INTEGER, votes_json JSON, "
                    "is_simulation BOOLEAN NOT NULL DEFAULT 0)"
                )
            )
        await _set_schema_version(sqlite_engine, CURRENT_SCHEMA_VERSION)

        await init_local_db(sqlite_engine)

        async with sqlite_engine.begin() as conn:
            snap_cols = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(portfolio_snapshots)"))
                ).fetchall()
            }
            rt_cols = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(round_table_sessions)"))
                ).fetchall()
            }
        assert "is_sim_day" in snap_cols
        assert "is_sim_day" in rt_cols

        # The #2588 failure mode itself: a full ORM SELECT must work again, and the
        # pre-existing row must have survived (additive ALTER, no rebuild).
        async with AsyncSession(sqlite_engine) as session:
            rows = (await session.execute(sa.select(PortfolioSnapshot))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == "row1"
        assert rows[0].total_equity == 12345.0

    @pytest.mark.asyncio
    async def test_is_sim_day_column_ensure_is_idempotent(self, sqlite_engine):
        """#2588: running init_local_db twice must not fail (duplicate ALTER) — the
        is_sim_day column-ensure is PRAGMA-guarded like the paper_trading one."""
        await init_local_db(sqlite_engine)  # fresh -> create_all (columns via model)
        await init_local_db(sqlite_engine)  # second run must be a clean no-op

        async with sqlite_engine.begin() as conn:
            snap_cols = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(portfolio_snapshots)"))
                ).fetchall()
            }
            rt_cols = {
                r[1]
                for r in (
                    await conn.execute(text("PRAGMA table_info(round_table_sessions)"))
                ).fetchall()
            }
        assert "is_sim_day" in snap_cols
        assert "is_sim_day" in rt_cols

    @pytest.mark.asyncio
    async def test_creates_missing_table_on_current_schema_without_rebuild(
        self, sqlite_engine
    ):
        """Bootstrap-gap fix: a DB already at CURRENT_SCHEMA_VERSION that is MISSING a table
        added to the models since it was last built (e.g. ``decision_outcomes``) must gain that
        table on the next init — WITHOUT a destructive backup+rebuild.

        The "schema current" branch was a pure no-op, so ``create_all()`` never ran for an
        existing DB. A model that gained a table without a CURRENT_SCHEMA_VERSION bump left every
        existing install permanently missing that table -> ``no such table: decision_outcomes``
        for every capture/outcome writer. The additive ``create_all(checkfirst=True)`` creates
        ONLY the missing table and never drops/alters an existing one, so the WORM audit /
        round-table history is preserved (no version bump, no drop_all, no backup).
        """
        from core.database.models import Base

        # Pre-existing DB at the CURRENT schema version with the full model schema…
        async with sqlite_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _set_schema_version(sqlite_engine, CURRENT_SCHEMA_VERSION)

        # …but ``decision_outcomes`` was dropped (simulating an install whose DB reached the
        # current version BEFORE the table was added to the models).
        async with sqlite_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS decision_outcomes"))

        # Pre-condition: the table is absent.
        async with sqlite_engine.begin() as conn:
            present_before = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='decision_outcomes'"
                    )
                )
            ).fetchall()
        assert present_before == []

        # A rebuild would call _backup_db_file — spy on it to prove we take the ADDITIVE path.
        with patch("core.database.bootstrap._backup_db_file") as backup_spy:
            await init_local_db(sqlite_engine)

        # Post-condition: table recreated, and NO backup/rebuild happened (additive only).
        async with sqlite_engine.begin() as conn:
            present_after = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='decision_outcomes'"
                    )
                )
            ).fetchall()
        assert len(present_after) == 1, "decision_outcomes must be (re)created on init"
        backup_spy.assert_not_called()  # additive create_all, never a drop_all rebuild
        assert await _get_schema_version(sqlite_engine) == CURRENT_SCHEMA_VERSION
