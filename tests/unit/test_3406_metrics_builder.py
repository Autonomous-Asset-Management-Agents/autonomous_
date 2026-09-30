from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from core.database.models import Base, DecisionOutcome, PortfolioSnapshot
from core.engine.api_routes import _return_metrics_from_points
from core.engine.metrics_builder import build_metrics_report

pytestmark = [pytest.mark.unit, pytest.mark.vc6]


@pytest.fixture
async def memory_db_session():
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


@pytest.mark.asyncio
async def test_metrics_builder_parallel_comparison(
    memory_db_session: AsyncSession,
):
    """Verify build_metrics_report returns identical metrics."""
    session = memory_db_session
    start = datetime.now(timezone.utc) - timedelta(days=5)

    # Add snaps
    snaps = []
    equity = 10000.0
    for i in range(5):
        t = start + timedelta(days=i)
        equity += 100.0 * ((-1) ** i)
        cashflow = 50.0 if i == 2 else 0.0
        equity += cashflow
        snap = PortfolioSnapshot(
            id=f"snap-{i}",
            timestamp=t,
            total_equity=equity,
            cash=100.0,
            strategy_name="test",
            is_simulation=False,
            paper_trading=True,
        )
        snaps.append(snap)
        session.add(snap)

    await session.commit()

    # Add a decision
    dec = DecisionOutcome(
        decision_id="test-dec-1",
        symbol="AAPL",
        decision_time=start + timedelta(days=2),
        is_simulation=False,
    )
    session.add(dec)
    await session.commit()

    # Call new builder
    report = await build_metrics_report(
        session, start, start + timedelta(days=10), "ALL", True
    )

    # Call old way
    points = [
        {
            "date": s.timestamp.strftime("%Y-%m-%d"),
            "equity": s.total_equity,
            "cashflow": s.cash,
        }
        for s in snaps
    ]
    old_metrics = _return_metrics_from_points(points)

    assert report.initial_capital == points[0]["equity"]
    assert report.final_equity == points[-1]["equity"]
    assert report.twr_pct == old_metrics.get("twr_pct")
    assert "test-dec-1" in report.decision_ids
