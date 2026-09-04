# ADR-SEC-06 (#1598) — a pending policy change + its approvals must survive a restart.
# Mirrors the real-DB round-trip pattern: insert a PendingPolicyChange in one session, then
# query it back in a NEW session and assert the initiator + approvals persisted.

import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa


def test_pending_policy_change_survives_restart():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from core.database.models import Base, PendingPolicyChange

    async def run():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with factory() as s:
            s.add(
                PendingPolicyChange(
                    id="pc1",
                    initiator="alice",
                    requested_policy={"daily_drawdown_pct": 0.20},
                    approvals=["bob"],
                    created_at=now,
                    cooloff_until=now + timedelta(minutes=10),
                    applied=False,
                )
            )
            await s.commit()
        # NEW session — simulates a fresh process after a restart.
        async with factory() as s2:
            row = (
                (await s2.execute(sa.select(PendingPolicyChange).filter_by(id="pc1")))
                .scalars()
                .first()
            )
            return row

    row = asyncio.run(run())
    assert row is not None
    assert row.initiator == "alice"
    assert row.approvals == ["bob"]
    assert row.applied is False
