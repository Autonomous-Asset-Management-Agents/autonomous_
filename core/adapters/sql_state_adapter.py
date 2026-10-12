import contextlib
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import AsyncSessionLocal
from core.ports.state_port import StatePort


class SqlStateAdapter(StatePort):
    """
    Adapter that uses the existing database sessionmaker.
    Later, this can be split into CloudSqlStateAdapter and LocalSqliteStateAdapter.
    """

    @contextlib.asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionLocal() as session:  # pragma: no cover
            yield session  # pragma: no cover
