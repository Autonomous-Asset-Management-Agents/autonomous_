"""
Unit tests for user_wallet_store.py ORM rewrite (OSS-4 / #1085).

Tests that UserWalletStore works with SQLite via SQLAlchemy ORM
instead of asyncpg raw queries.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

try:
    from core.database.models import Base
    from core.user_wallet_store import UserWalletStore

    MODELS_AVAILABLE = True
except ImportError:
    MODELS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MODELS_AVAILABLE, reason="ORM models not available")


@pytest.fixture
async def wallet_store(tmp_path):
    """Create a UserWalletStore with a temporary SQLite backend."""
    db_path = tmp_path / "wallets_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    store = UserWalletStore()
    store._session_factory = session_factory
    store._engine = engine
    store.is_connected = True
    yield store

    await engine.dispose()


class TestUserWalletStoreORM:
    """Verify ORM-based CRUD operations on SQLite."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_wallet(self, wallet_store):
        """Upsert creates a wallet, get retrieves it."""
        await wallet_store.upsert_wallet("user1", "broker-123", "secret-ref-1")

        wallet = await wallet_store.get_wallet("user1")
        assert wallet is not None
        assert wallet["user_id"] == "user1"
        assert wallet["broker_account_id"] == "broker-123"
        assert wallet["secret_manager_id"] == "secret-ref-1"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, wallet_store):
        """Upsert on existing wallet updates the record."""
        await wallet_store.upsert_wallet("user1", "broker-old", "secret-old")
        await wallet_store.upsert_wallet("user1", "broker-new", "secret-new")

        wallet = await wallet_store.get_wallet("user1")
        assert wallet["broker_account_id"] == "broker-new"
        assert wallet["secret_manager_id"] == "secret-new"

    @pytest.mark.asyncio
    async def test_get_nonexistent_wallet_returns_none(self, wallet_store):
        """Getting a non-existent wallet returns None."""
        result = await wallet_store.get_wallet("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_active_wallets(self, wallet_store):
        """get_active_wallets returns only wallets with status='active'."""
        await wallet_store.upsert_wallet("user1", "b1", "s1")
        await wallet_store.upsert_wallet("user2", "b2", "s2")

        await wallet_store.update_status("user1", "active")
        # user2 remains 'inactive' (default)

        active = await wallet_store.get_active_wallets()
        assert len(active) == 1
        assert active[0]["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_update_risk_limits(self, wallet_store):
        """Risk limits can be updated as JSON."""
        await wallet_store.upsert_wallet("user1", "b1", "s1")

        limits = {"max_position_pct": 0.1, "max_drawdown": 0.05}
        result = await wallet_store.update_risk_limits("user1", limits)
        assert result is True

        wallet = await wallet_store.get_wallet("user1")
        assert wallet["risk_limits"]["max_position_pct"] == 0.1

    @pytest.mark.asyncio
    async def test_update_status(self, wallet_store):
        """Status can be updated to valid values."""
        await wallet_store.upsert_wallet("user1", "b1", "s1")

        assert await wallet_store.update_status("user1", "active") is True
        wallet = await wallet_store.get_wallet("user1")
        assert wallet["status"] == "active"

        assert await wallet_store.update_status("user1", "halted") is True
        wallet = await wallet_store.get_wallet("user1")
        assert wallet["status"] == "halted"

    @pytest.mark.asyncio
    async def test_update_status_invalid_raises(self, wallet_store):
        """Invalid status value raises ValueError."""
        await wallet_store.upsert_wallet("user1", "b1", "s1")

        with pytest.raises(ValueError, match="Invalid status"):
            await wallet_store.update_status("user1", "bogus")

    @pytest.mark.asyncio
    async def test_update_alpaca_keys_raises(self, wallet_store):
        """update_alpaca_keys is disabled in OSS (NotImplementedError)."""
        with pytest.raises(NotImplementedError):
            await wallet_store.update_alpaca_keys("user1", "key", "secret")
