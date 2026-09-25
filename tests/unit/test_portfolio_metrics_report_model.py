"""
Unit tests for PortfolioMetricsReport model (ARC-E5.5 / #3406).
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from core.database.models import Base, PortfolioMetricsReport


@pytest.fixture
async def memory_db_session():
    """Create an in-memory SQLite backend and yield a session."""
    url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


class TestPortfolioMetricsReportModel:
    """Verify PortfolioMetricsReport model."""

    @pytest.mark.asyncio
    async def test_instantiate_and_persist(self, memory_db_session: AsyncSession):
        """Test instantiation and persistence."""
        now = datetime.now(timezone.utc)
        report = PortfolioMetricsReport(
            id="test-report-1",
            timestamp=now,
            start_date=now,
            end_date=now,
            baseline="1M",
            paper_trading=True,
            initial_capital=10000.0,
            final_equity=10500.0,
            twr_pct=5.0,
            max_drawdown_pct=2.5,
            win_rate_pct=55.0,
            decision_ids=["dec-1", "dec-2"],
            order_ids=["ord-1"],
        )

        memory_db_session.add(report)
        await memory_db_session.commit()

        # Read back
        stmt = select(PortfolioMetricsReport).where(
            PortfolioMetricsReport.id == "test-report-1"
        )
        result = await memory_db_session.execute(stmt)
        saved_report = result.scalar_one_or_none()

        assert saved_report is not None
        assert saved_report.id == "test-report-1"
        assert saved_report.baseline == "1M"
        assert saved_report.twr_pct == 5.0
        assert saved_report.decision_ids == ["dec-1", "dec-2"]


pytestmark = pytest.mark.vc4
