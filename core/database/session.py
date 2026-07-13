"""
core/database/session.py
SQLAlchemy async engine + session factory (BORA dual-mode).

Connection strategy (priority order):
  1. Cloud SQL Python Connector (IAM-based, no IP whitelist needed)
     → used when CLOUD_SQL_CONNECTION_NAME env var is set (Cloud Run production)
     → credentials parsed from DATABASE_URL (already injected via Secret Manager)
  2. Direct asyncpg URL (DATABASE_URL starting with 'postgresql')
     → used for local development with Docker PostgreSQL
  3. SQLite via aiosqlite (DATABASE_URL starting with 'sqlite' or empty)  [OSS-4]
     → used for local-first desktop mode (no Docker, no PostgreSQL)
     → WAL mode enforced on every connection for async safety
     → Falls back to ./data/aaagents.db when DATABASE_URL is unset

IAM requirement: trading-bot-sa must have roles/cloudsql.client (granted 2026-04-02).
"""

import asyncio
import logging
import os
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

_CONN_NAME = os.environ.get("CLOUD_SQL_CONNECTION_NAME", "").strip()
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_ENGINE_CLEANUPS = {}


def _parse_credentials(database_url: str) -> tuple[str, str, str]:
    """
    Parse user, password, and database name from a DATABASE_URL string.

    Supports:
      postgresql+asyncpg://user:password@host:port/dbname
      postgresql://user:password@host:port/dbname

    Returns:
        (user, password, dbname) — empty strings on parse failure.
    """
    try:
        parsed = urlparse(database_url)
        user = parsed.username or ""
        password = parsed.password or ""
        dbname = parsed.path.lstrip("/")
        return user, password, dbname
    except Exception as exc:
        logging.warning("Failed to parse DATABASE_URL for credentials: %s", exc)
        return "", "", ""


def _guard_cloud_sqlite_fallback() -> None:
    """Refuse the ephemeral-SQLite fallback on Cloud Run (G0a, PR-review P0-2).

    ``K_SERVICE`` is set by Cloud Run. Booting there without DB config used to
    crash on the first write (accidental fail-closed); with the G0a bootstrap
    it would instead get WORKING ephemeral tables and silently lose every
    audit record on container recycle. Fail loud and early instead.

    NOTE: defined ABOVE _create_engine — it is called during the module-level
    ``engine = _create_engine()`` evaluation at import time.
    """
    if os.environ.get("K_SERVICE", "").strip():
        raise RuntimeError(
            "Cloud SQL not configured (no CLOUD_SQL_CONNECTION_NAME / "
            "DATABASE_URL) but K_SERVICE is set — refusing the ephemeral "
            "SQLite fallback on Cloud Run to prevent silent audit-data loss."
        )


def _create_engine():
    """
    Create the appropriate SQLAlchemy AsyncEngine based on environment.
    """
    if _CONN_NAME:
        # ── Cloud Run path: Cloud SQL Python Connector ────────────────────────
        # Uses IAM-authenticated socket; no public IP / authorized-network needed.
        try:
            from google.cloud.sql.connector import (
                create_async_connector,  # type: ignore[import]
            )

            user, password, dbname = _parse_credentials(_DATABASE_URL)
            if not all([user, dbname]):
                logging.error(
                    "CLOUD_SQL_CONNECTION_NAME is set but DATABASE_URL is missing or "
                    "unparseable (user=%r, db=%r). Falling back to direct URL.",
                    user,
                    dbname,
                )
                raise ValueError("Unparseable DATABASE_URL")

            logging.info(
                "Cloud SQL Connector: connecting to %s (db=%s, user=%s)",
                _CONN_NAME,
                dbname,
                user,
            )

            connector_instance = None
            connector_lock = None

            async def _get_conn():
                """Async connection factory for SQLAlchemy's async_creator."""
                nonlocal connector_instance, connector_lock
                if connector_lock is None:
                    connector_lock = asyncio.Lock()

                async with connector_lock:
                    if connector_instance is None:
                        connector_instance = await create_async_connector()
                return await connector_instance.connect_async(
                    _CONN_NAME,
                    "asyncpg",
                    user=user,
                    password=password,
                    db=dbname,
                )

            engine = create_async_engine(
                "postgresql+asyncpg://",  # URL is a placeholder; real conn via async_creator
                async_creator=_get_conn,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                future=True,
            )

            async def cleanup_connector():
                if connector_instance is not None:
                    await connector_instance.close_async()

            _ENGINE_CLEANUPS[id(engine)] = cleanup_connector
            return engine

        except ImportError:
            logging.error(
                "google-cloud-sql-connector not installed. "
                "Install with: pip install 'cloud-sql-python-connector[asyncpg]'. "
                "Falling back to direct DATABASE_URL connection."
            )
        except Exception as exc:
            logging.error(
                "Cloud SQL Connector setup failed (%s). Falling back to direct URL.",
                exc,
            )

    # ── Local dev / fallback path: direct URL ───────────────────────────────
    url = _DATABASE_URL
    if not url or "dummy" in url:
        # G0a (#1050, PR-review P0-2): on Cloud Run an ephemeral-SQLite fallback
        # would be SILENT DATA LOSS (audit records vanish on container recycle).
        # Fail closed there; the fallback is desktop/dev-only.
        _guard_cloud_sqlite_fallback()
        # OSS-4: Default to SQLite for local-first desktop mode.
        import pathlib

        # session.py is at ai_trading_bot/core/database/session.py
        # → .parent.parent.parent reaches ai_trading_bot/ (project root)
        # Account-state DB under USER_DATA_DIR (per-user; AAA_USER_DATA_DIR or project data/).
        _data_dir = pathlib.Path(
            os.environ.get("AAA_USER_DATA_DIR")
            or (pathlib.Path(__file__).resolve().parent.parent.parent / "data")
        )
        _data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{(_data_dir / 'aaagents.db').as_posix()}"
        logging.warning(
            "DATABASE_URL not set or is a dummy value. "
            "Falling back to local SQLite: %s",
            url,
        )

    kwargs = {
        "echo": False,
        "pool_pre_ping": True,
        "future": True,
    }

    if url.startswith("sqlite"):
        # SQLite: no connection pool (single-writer), enable WAL mode
        # via connect event for async lock-contention safety.
        from sqlalchemy import event, text

        engine = create_async_engine(url, **kwargs)

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            """Enforce WAL journal mode on every SQLite connection.

            WAL is persisted in the DB file, but re-issuing PRAGMA at
            every connect is idempotent and guards against files copied
            from non-WAL environments.
            """
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    else:
        # PostgreSQL: connection pooling
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        engine = create_async_engine(url, **kwargs)

    async def dummy_cleanup():
        pass

    _ENGINE_CLEANUPS[id(engine)] = dummy_cleanup
    return engine


async def cleanup_engine_connector(eng):
    """Safely invoke the cleanup function associated with a specific engine."""
    cleanup_func = _ENGINE_CLEANUPS.get(id(eng))
    if cleanup_func:
        await cleanup_func()


# Module-level singletons — imported by cloud_logger.py and ORM models
engine = _create_engine()

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# OSS-4: Lazy local DB initializer — must be called explicitly by
# application entrypoints (e.g. engine startup), NOT at import time.
# This avoids filesystem side effects during test collection or linting.
_local_db_initialized = False
# G0a (PR-review P1-1): per-event-loop locks for the check-then-act below —
# same pattern as core/ml/model_registry (an asyncio.Lock is loop-bound, so a
# single module-level Lock would break under multiple loops, e.g. in tests).
_init_locks: dict = {}


async def ensure_local_db_ready() -> None:
    """Initialize local SQLite database if not already done.

    Safe to call multiple times (idempotent) AND safe under concurrent calls
    on the same event loop (double-checked per-loop lock — PR-review P1-1).
    No-op for PostgreSQL engines. Called by engine startup code, NOT at
    module import time.
    """
    global _local_db_initialized
    if _local_db_initialized:
        return
    if not str(engine.url).startswith("sqlite"):
        _local_db_initialized = True
        return

    loop_id = id(asyncio.get_running_loop())
    lock = _init_locks.setdefault(loop_id, asyncio.Lock())
    async with lock:
        if _local_db_initialized:  # double-check inside the lock
            return
        from core.database.bootstrap import init_local_db

        await init_local_db(engine)
        _local_db_initialized = True
