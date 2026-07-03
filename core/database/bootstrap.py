"""
core/database/bootstrap.py — Local SQLite Database Initialization (OSS-4 / #1085)

Provides `init_local_db()` for desktop mode:
  - Creates the data directory if missing
  - Runs `Base.metadata.create_all()` for fresh databases
  - Schema version check with backup + rebuild on upgrade
  - Enforces WAL journal mode (also handled in session.py connect event)

Enterprise mode (PostgreSQL) uses Alembic migrations instead — this module
is a no-op when DATABASE_URL points to PostgreSQL.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Increment this when ORM models change. Triggers backup + rebuild
# for existing local SQLite databases.
CURRENT_SCHEMA_VERSION = (
    2  # +iron_dome_policy_audit +pending_policy_change (#1634/#1635)
)

# Meta table to track schema version inside the SQLite database.
_META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


async def _get_schema_version(engine: AsyncEngine) -> int | None:
    """Read the schema version from the _schema_meta table.

    Returns None if the table or key doesn't exist (fresh database).
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_META_TABLE_DDL))
            result = await conn.execute(
                text("SELECT value FROM _schema_meta WHERE key = 'schema_version'")
            )
            row = result.fetchone()
            return int(row[0]) if row else None
    except Exception:
        return None


async def _set_schema_version(engine: AsyncEngine, version: int) -> None:
    """Write (upsert) the schema version into _schema_meta."""
    async with engine.begin() as conn:
        await conn.execute(text(_META_TABLE_DDL))
        await conn.execute(
            text(
                "INSERT OR REPLACE INTO _schema_meta (key, value) "
                "VALUES ('schema_version', :version)"
            ),
            {"version": str(version)},
        )


def _backup_db_file(db_path: Path) -> Path | None:
    """Create a timestamped backup of the SQLite database file.

    Returns the path to the backup file, or None if the source is not
    a physical file (e.g. :memory: databases).
    """
    if not db_path.exists() or not db_path.is_file():
        logger.info(
            "Skipping database file backup: '%s' is not a physical file.", db_path
        )
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".bak.{timestamp}")
    try:
        shutil.copy2(db_path, backup_path)
        logger.warning("Database backed up to: %s", backup_path)
        return backup_path
    except OSError as exc:
        # BORA-03: On Windows, another process may hold the file open.
        # A failed backup must not block engine startup.
        logger.warning("Database backup failed (non-fatal, continuing): %s", exc)
        return None


async def init_local_db(engine: AsyncEngine) -> None:
    """Initialize or upgrade the local SQLite database.

    Called at engine startup when `config.is_local_mode` is True.
    Enterprise mode (PostgreSQL) uses Alembic migrations — this is a no-op
    for non-SQLite engines.

    Strategy:
      - New database → create_all() + set schema version
      - Outdated schema → backup + drop all + create_all() + set schema version
      - Current schema → no-op
    """
    from core.database.models import Base

    url_str = str(engine.url)
    if not url_str.startswith("sqlite"):
        logger.debug("init_local_db() skipped: non-SQLite engine (%s)", url_str)
        return

    # P2-01: Ensure the parent directory exists for custom DATABASE_URL paths
    # (e.g. sqlite+aiosqlite:///C:/my/custom/path/db.sqlite)
    db_path_str = url_str.split("///", 1)[-1] if "///" in url_str else ""
    if db_path_str and db_path_str != ":memory:":
        Path(db_path_str).parent.mkdir(parents=True, exist_ok=True)

    db_version = await _get_schema_version(engine)

    if db_version is None:
        # Fresh database — create all tables
        logger.info(
            "Fresh SQLite database detected. Creating schema v%d...",
            CURRENT_SCHEMA_VERSION,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _set_schema_version(engine, CURRENT_SCHEMA_VERSION)
        logger.info("Schema v%d created successfully.", CURRENT_SCHEMA_VERSION)

    elif db_version < CURRENT_SCHEMA_VERSION:
        # Outdated schema — backup and rebuild
        logger.warning(
            "Schema v%d → v%d: backing up and recreating database.",
            db_version,
            CURRENT_SCHEMA_VERSION,
        )
        # Extract file path from SQLite URL (sqlite+aiosqlite:///path/to/db)
        db_path_str = url_str.split("///", 1)[-1] if "///" in url_str else ""
        if db_path_str:
            _backup_db_file(Path(db_path_str))

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await _set_schema_version(engine, CURRENT_SCHEMA_VERSION)
        logger.info(
            "Schema rebuilt to v%d. Previous data backed up.",
            CURRENT_SCHEMA_VERSION,
        )
    else:
        logger.debug("Schema v%d is current. No migration needed.", db_version)
