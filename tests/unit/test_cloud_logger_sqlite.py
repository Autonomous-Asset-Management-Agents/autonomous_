"""
Unit tests for cloud_logger.py dialect-aware insert strategy (OSS-4 / #1085).

Tests that _dialect_insert_ignore() produces correct SQL for both
SQLite (INSERT OR IGNORE) and PostgreSQL (ON CONFLICT DO NOTHING),
and that batch inserts work end-to-end on an in-memory SQLite DB.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Import DB_AVAILABLE guard — tests skip if ORM models unavailable
try:
    from core.cloud_logger import _dialect_insert_ignore
    from core.database.models import (
        AIThought,
        Base,
        Decision,
        MifidDecisionLog,
        RiskEvent,
        RoundTableSession,
        Trade,
    )

    MODELS_AVAILABLE = True
except ImportError:
    MODELS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MODELS_AVAILABLE, reason="ORM models not available")


@pytest.fixture
async def sqlite_session():
    """Create an async in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session

    await engine.dispose()


class TestDialectInsertIgnoreHelper:
    """Unit tests for the _dialect_insert_ignore() helper function."""

    def test_sqlite_produces_or_ignore(self):
        """SQLite dialect produces INSERT OR IGNORE prefix."""
        values = {
            "decision_id": "test-id",
            "symbol": "AAPL",
            "action": "BUY",
            "decision_time": datetime.now(timezone.utc),
        }
        stmt = _dialect_insert_ignore(Decision, values, "decision_id", "sqlite")
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "OR IGNORE" in compiled

    def test_postgresql_produces_on_conflict(self):
        """PostgreSQL dialect produces ON CONFLICT DO NOTHING."""
        values = {
            "decision_id": "test-id",
            "symbol": "AAPL",
            "action": "BUY",
            "decision_time": datetime.now(timezone.utc),
        }
        stmt = _dialect_insert_ignore(Decision, values, "decision_id", "postgresql")
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "ON CONFLICT" in compiled


class TestDialectInsertIgnoreSqliteE2E:
    """End-to-end tests on actual SQLite database."""

    @pytest.mark.asyncio
    async def test_decision_insert_sqlite(self, sqlite_session):
        """Decision insert with OR IGNORE works on SQLite."""
        values = {
            "decision_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "action": "BUY",
            "decision_time": datetime.now(timezone.utc),
            "model_version_id": "test",
            "conviction_score": 0.85,
            "current_price": 150.0,
            "reasoning_summary": "Test decision",
        }
        stmt = _dialect_insert_ignore(Decision, values, "decision_id", "sqlite")
        await sqlite_session.execute(stmt)
        await sqlite_session.commit()

        result = await sqlite_session.execute(
            select(Decision).where(Decision.decision_id == values["decision_id"])
        )
        row = result.scalars().first()
        assert row is not None
        assert row.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_duplicate_insert_ignored(self, sqlite_session):
        """Duplicate insert is silently ignored (OR IGNORE)."""
        decision_id = str(uuid.uuid4())
        values = {
            "decision_id": decision_id,
            "symbol": "AAPL",
            "action": "BUY",
            "decision_time": datetime.now(timezone.utc),
            "model_version_id": "test",
            "conviction_score": 0.85,
            "current_price": 150.0,
            "reasoning_summary": "First insert",
        }
        stmt = _dialect_insert_ignore(Decision, values, "decision_id", "sqlite")
        await sqlite_session.execute(stmt)
        await sqlite_session.commit()

        # Second insert with same ID — should be ignored
        values2 = {**values, "symbol": "MSFT", "reasoning_summary": "Duplicate"}
        stmt2 = _dialect_insert_ignore(Decision, values2, "decision_id", "sqlite")
        await sqlite_session.execute(stmt2)
        await sqlite_session.commit()

        result = await sqlite_session.execute(
            select(Decision).where(Decision.decision_id == decision_id)
        )
        row = result.scalars().first()
        assert row.symbol == "AAPL"  # Original, not overwritten

    @pytest.mark.asyncio
    async def test_trade_insert_sqlite(self, sqlite_session):
        """Trade insert with OR IGNORE works on SQLite."""
        values = {
            "trade_id": str(uuid.uuid4()),
            "symbol": "TSLA",
            "side": "buy",
            "qty": 10.0,
            "price": 200.0,
            "total_value": 2000.0,
            "executed_at": datetime.now(timezone.utc),
            "strategy_name": "RLAgent",
        }
        stmt = _dialect_insert_ignore(Trade, values, "trade_id", "sqlite")
        await sqlite_session.execute(stmt)
        await sqlite_session.commit()

        result = await sqlite_session.execute(
            select(Trade).where(Trade.trade_id == values["trade_id"])
        )
        row = result.scalars().first()
        assert row is not None
        assert row.symbol == "TSLA"

    @pytest.mark.asyncio
    async def test_risk_event_insert_sqlite(self, sqlite_session):
        """RiskEvent insert with OR IGNORE works on SQLite."""
        values = {
            "id": str(uuid.uuid4()),
            "event_time": datetime.now(timezone.utc),
            "event_type": "max_drawdown",
            "severity": "warning",
            "message": "Test risk event",
        }
        stmt = _dialect_insert_ignore(RiskEvent, values, "id", "sqlite")
        await sqlite_session.execute(stmt)
        await sqlite_session.commit()

        result = await sqlite_session.execute(
            select(RiskEvent).where(RiskEvent.id == values["id"])
        )
        row = result.scalars().first()
        assert row is not None
        assert row.event_type == "max_drawdown"
