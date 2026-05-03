"""
core/database/session.py
SQLAlchemy async engine + session factory for Cloud SQL.

Connection strategy (priority order):
  1. Cloud SQL Python Connector (IAM-based, no IP whitelist needed)
     → used when CLOUD_SQL_CONNECTION_NAME env var is set (Cloud Run production)
     → credentials parsed from DATABASE_URL (already injected via Secret Manager)
  2. Direct asyncpg URL (DATABASE_URL)
     → used for local development / devcontainer (no CLOUD_SQL_CONNECTION_NAME)

This design requires no new secrets: DATABASE_URL (from Secret Manager via
--set-secrets in cloudbuild.yaml) contains user:password@host:port/db and is
parsed at runtime. The connector uses those credentials via the IAM-proxied
encrypted socket, bypassing the Cloud SQL IP authorized-networks check entirely.

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


def _create_engine():
    """
    Create the appropriate SQLAlchemy AsyncEngine based on environment.
    """
    if _CONN_NAME:
        # ── Cloud Run path: Cloud SQL Python Connector ────────────────────────
        # Uses IAM-authenticated socket; no public IP / authorized-network needed.
        try:
            from google.cloud.sql.connector import create_async_connector  # type: ignore[import]

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

    # ── Local dev / fallback path: direct asyncpg URL ────────────────────────
    url = _DATABASE_URL
    if not url or "dummy" in url:
        url = "postgresql+asyncpg://dummy:dummy@localhost:5432/dummy"
        logging.warning(
            "DATABASE_URL environment variable is not set or is a dummy value. "
            "Cloud SQL connections will fail. Set DATABASE_URL for a real database."
        )
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        future=True,
    )

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
