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
