"""
Unit tests for config.py local-mode detection (OSS-4 / #1085).

Tests the `is_local_mode` property and REDIS_URL optionality that enable
the SQLite + In-Memory State dual-mode for desktop installations.
"""

import os
from unittest.mock import patch

import pytest


class TestConfigLocalMode:
    """Verify config.is_local_mode correctly detects SQLite vs PostgreSQL."""

    def test_sqlite_url_is_local_mode(self):
        """DATABASE_URL starting with 'sqlite' → is_local_mode = True."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite+aiosqlite:///./data/aaagents.db",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            # Force re-import to pick up new env vars
            from config import RuntimeConfigState

            cfg = RuntimeConfigState()
            assert cfg.is_local_mode is True

    def test_postgres_url_is_not_local_mode(self):
        """DATABASE_URL starting with 'postgresql' → is_local_mode = False."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
                "REDIS_URL": "redis://localhost:6379/0",
            },
            clear=False,
        ):
            from config import RuntimeConfigState

            cfg = RuntimeConfigState()
            assert cfg.is_local_mode is False

    def test_empty_database_url_defaults_to_local_mode(self):
        """No DATABASE_URL → should default to SQLite local mode."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            from config import RuntimeConfigState

            cfg = RuntimeConfigState()
            assert cfg.is_local_mode is True

    def test_redis_url_optional_in_local_mode(self):
        """REDIS_URL should be optional (empty string) in local mode."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite+aiosqlite:///./data/aaagents.db",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            from config import RuntimeConfigState

            cfg = RuntimeConfigState()
            assert cfg.REDIS_URL == ""
            assert cfg.is_local_mode is True

    def test_redis_url_present_in_enterprise_mode(self):
        """REDIS_URL with a value → enterprise mode with Redis."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
                "REDIS_URL": "redis://:secret@redis:6379/0",
            },
            clear=False,
        ):
            from config import RuntimeConfigState

            cfg = RuntimeConfigState()
            assert cfg.REDIS_URL == "redis://:secret@redis:6379/0"
            assert cfg.is_local_mode is False
