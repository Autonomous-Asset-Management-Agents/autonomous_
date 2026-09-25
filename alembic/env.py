"""
Alembic environment configuration for GCP Cloud SQL PostgreSQL.

- Database URL is read from the DATABASE_URL environment variable (set via GCP Secret Manager in production).
- For local development, DATABASE_URL can be set in the .env file.
- Supports both synchronous (for offline migrations) and asynchronous (for online migrations with asyncpg) modes.
"""

import asyncio
import os
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


from core.database.utils import get_database_url


def compare_type_custom(
    context, inspected_column, metadata_column, inspected_type, metadata_type
):
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.types import JSON

    inspected_class = inspected_type.__class__
    metadata_class = metadata_type.__class__

    if inspected_class in (JSON, JSONB) and metadata_class in (JSON, JSONB):
        return False

    return None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no DB connection)."""
    url = get_database_url()
    is_sqlite = url.startswith("sqlite")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_type_custom,
        render_as_batch=is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=compare_type_custom,
        render_as_batch=is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using async engine (asyncpg)."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

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
