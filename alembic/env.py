"""
Alembic environment configuration for GCP Cloud SQL PostgreSQL.

- Database URL is read from the DATABASE_URL environment variable (set via GCP Secret Manager in production).
- For local development, DATABASE_URL can be set in the .env file.
- Supports both synchronous (for offline migrations) and asynchronous (for online migrations with asyncpg) modes.
"""

import os
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import the SQLAlchemy Base and all models so Alembic can auto-detect the schema
from core.database.models import Base  # noqa: F401

# This is the Alembic Config object, which provides access to .ini file values.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The MetaData object for auto-generating migrations
target_metadata = Base.metadata


def _get_database_url() -> str:
    """
    Resolve the DATABASE_URL from environment (GCP Secret Manager injects this at runtime).
    For local development, it's loaded from .env.

    Falls back to a safe default that will raise a clear error if not configured.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "In production, this is injected by GCP Secret Manager. "
            "For local use: export DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname"
        )

    # Normalise Cloud SQL / legacy postgres:// scheme → asyncpg
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no DB connection)."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using async engine (asyncpg)."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations — runs the async function."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
