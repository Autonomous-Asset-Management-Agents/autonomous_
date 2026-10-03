import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from core.database.models import Base


@pytest.mark.asyncio
async def test_sqlite_engine_creation_and_metadata():
    """
    Verifies that the database engine can be created for SQLite without pooling parameter errors,
    and that the declarative base models compile cleanly in SQLite (validating our JSON_TYPE abstraction).
    """
    db_url = "sqlite+aiosqlite:///:memory:"

    # We must replicate the session.py fallback behavior to verify the args
    kwargs = {
        "echo": False,
        "pool_pre_ping": True,
        "future": True,
    }
    if not db_url.startswith("sqlite"):
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20

    engine = create_async_engine(db_url, **kwargs)

    async with engine.begin() as conn:
        # If the Base contains non-SQLite compatible types (e.g., direct JSONB without variant),
        # run_sync(Base.metadata.create_all) will raise a CompileError.
        await conn.run_sync(Base.metadata.create_all)

    assert engine.url.get_backend_name() == "sqlite"
    # Ensure it successfully disposes
    await engine.dispose()
