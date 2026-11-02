from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator, Callable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class StatePort(ABC):
    """
    Abstract port for providing database session state.
    Isolates the core from knowledge about SQLite vs Postgres/Redis.
    """

    @abstractmethod
    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provides an asynchronous database session context manager."""
        pass  # pragma: no cover
