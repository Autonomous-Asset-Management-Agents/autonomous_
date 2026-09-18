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


# Additive column registry (#2588): every column added to an EXISTING table after its
# create_all() birth MUST be listed here. ``Base.metadata.create_all()`` only CREATES
# missing tables — it never ALTERs an existing one — so a model column added without a
# CURRENT_SCHEMA_VERSION bump silently drifts on every existing desktop install, and the
# FIRST full ORM SELECT then dies with ``sqlite3.OperationalError: no such column``.
# That is exactly how #2544's ``is_sim_day`` killed /benchmark-equity (empty points ->
# "Collecting equity history…" + dead Since-inception/Started/MaxDD KPIs, #2588) AND the
# daily snapshot writer (base.py, swallowed at WARNING). A version bump is NOT the fix:
# it drop_all's the WORM audit / round-table history.
_ADDITIVE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "portfolio_snapshots": (
        ("paper_trading", "BOOLEAN"),  # PR-3 paper-vs-live discriminator
        ("is_sim_day", "BOOLEAN"),  # #2544 sim-day marker (ensure added for #2588)
    ),
    "round_table_sessions": (
        ("is_sim_day", "BOOLEAN"),  # #2544 sim-day marker (ensure added for #2588)
    ),
}


async def _ensure_additive_columns(engine: AsyncEngine) -> None:
    """Additive, idempotent: ensure every ``_ADDITIVE_COLUMNS`` column exists (PR-3, #2588).

    Adds each missing column IN PLACE (``ALTER TABLE … ADD COLUMN``) guarded by a
    ``PRAGMA table_info`` lookup, so each ALTER runs at most once and the whole pass is
    a clean no-op afterwards. Existing rows are preserved — never a drop/rebuild.
    """
    async with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            info = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in info.fetchall()}
            if not existing:
                # Table absent (should not happen after create_all) — nothing to ALTER.
                continue
            for column, ddl_type in columns:
                if column not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
                    )
                    # WARNING, not DEBUG: this heals real schema drift the operator
                    # should see once per upgrade (#2588 went dark silently).
                    logger.warning(
                        "init_local_db: added %s.%s column in place (additive "
                        "migration — no rebuild, existing rows preserved).",
                        table,
                        column,
                    )


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

    # Additive TABLE-ensure — runs on EVERY init (idempotent) so an existing DB already at
    # CURRENT_SCHEMA_VERSION still gains any table added to the models since it was last built
    # (e.g. ``decision_outcomes``). The "schema current" branch above is a no-op, so a table
    # added WITHOUT a version bump would otherwise be missing forever on existing installs ->
    # ``no such table`` for every capture/outcome writer. ``create_all`` with the default
    # ``checkfirst=True`` creates ONLY missing tables — it never drops or ALTERs an existing one
    # — so the WORM audit / round-table history is fully preserved (no backup, no drop_all). On a
    # fresh / rebuilt DB every table already exists here -> a clean no-op.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # PR-3/#2588: additive column-ensure — runs on EVERY init (idempotent) so an existing DB
    # at the current schema version still gains every column added to the models since it was
    # built (paper_trading, is_sim_day, …) without a destructive rebuild. On a fresh / rebuilt
    # DB create_all() already added them -> a no-op here.
    await _ensure_additive_columns(engine)
