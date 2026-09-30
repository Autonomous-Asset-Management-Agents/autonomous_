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
from sqlalchemy.schema import CreateColumn

logger = logging.getLogger(__name__)

# Increment this when ORM models change. Triggers backup + rebuild
# for existing local SQLite databases.
CURRENT_SCHEMA_VERSION = 3  # +sizing_mode +sizing_target_weight (#3284)

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


# Additive column-ensure (#2588, #3373).
#
# ``Base.metadata.create_all()`` only CREATES missing tables — it never ALTERs an existing
# one. A model column added after a table's birth therefore drifts silently on every
# existing desktop install: the first ORM statement touching it dies with
# ``sqlite3.OperationalError: no such column`` / ``has no column named``. A version bump is
# NOT the fix — it drop_all's the WORM audit / round-table history.
#
# Until #3373 the columns to add lived in a hand-maintained registry here. It was forgotten
# three times: #2544 (``is_sim_day`` killed /benchmark-equity), #2588, and #3303 —
# ``decisions.sizing_mode`` / ``sizing_target_weight`` made EVERY ``decisions`` insert fail
# from 2026-09-11 on; ``decision_outcomes`` (FK -> decisions) fell with it, and the capture
# sat silent for seven days while the engine kept trading. A list one can forget gets
# forgotten, so the columns are now DERIVED from the models themselves.


def _sql_literal(value) -> str | None:
    """Render a constant column default as a SQLite literal; ``None`` if it is not one."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _additive_ddl(column, dialect) -> str | None:
    """The column spec for ``ALTER TABLE … ADD COLUMN`` — or ``None`` if SQLite cannot add it.

    Rendered by SQLAlchemy's own DDL compiler, so a healed column is byte-identical to
    the one ``create_all()`` gives a fresh install (type, ``server_default``, NOT NULL).

    SQLite cannot add a PRIMARY KEY / UNIQUE column, nor a NOT NULL column without a
    constant default. Those cannot be healed in place; the caller reports them.
    """
    if column.primary_key or column.unique:
        return None
    spec = str(CreateColumn(column).compile(dialect=dialect)).strip()
    if column.nullable or column.server_default is not None:
        return spec
    default = column.default
    literal = (
        _sql_literal(default.arg)
        if default is not None and getattr(default, "is_scalar", False)
        else None
    )
    if literal is None:
        return None
    return f"{spec} DEFAULT {literal}"


async def _ensure_additive_columns(engine: AsyncEngine, metadata=None) -> None:
    """Additive, idempotent: every model column exists on every existing table (#3373).

    Adds each missing column IN PLACE (``ALTER TABLE … ADD COLUMN``) guarded by a
    ``PRAGMA table_info`` lookup, so each ALTER runs at most once and the whole pass is a
    clean no-op afterwards. Existing rows are preserved — never a drop/rebuild.

    A column that cannot be added in place is reported at ERROR and skipped: a schema
    finding must never keep the engine from starting.
    """
    if metadata is None:
        from core.database.models import Base

        metadata = Base.metadata

    async with engine.begin() as conn:
        for table in metadata.sorted_tables:
            info = await conn.execute(text(f'PRAGMA table_info("{table.name}")'))
            existing = {row[1] for row in info.fetchall()}
            if not existing:
                # Table absent (should not happen after create_all) — nothing to ALTER.
                continue
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = _additive_ddl(column, engine.dialect)
                if ddl is None:
                    logger.error(
                        "init_local_db: %s.%s is missing and cannot be added in place "
                        "(PRIMARY KEY/UNIQUE, or NOT NULL without a constant default). "
                        "Writes to this table will fail until it is migrated (#3373).",
                        table.name,
                        column.name,
                    )
                    continue
                await conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
                # WARNING, not DEBUG: this heals real schema drift the operator
                # should see once per upgrade (#2588 and #3373 went dark silently).
                logger.warning(
                    "init_local_db: added %s.%s column in place (additive "
                    "migration — no rebuild, existing rows preserved).",
                    table.name,
                    column.name,
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
