# ADR-SEC-06 (#1597) · queryable audit mirror (iron_dome_policy_audit). TDD RED first.
# The Art-14 hash chain stays the primary tamper-evident trail; this mirror table is the
# queryable copy (one row per change). The mirror write is best-effort — it must never block
# a change (the Art-14 record already guarantees the audit).

import asyncio
from unittest.mock import patch

import sqlalchemy as sa

from core.governance.iron_dome_audit import (
    _build_audit_row,
    _write_audit_mirror,
    record_iron_dome_policy_change,
)


def test_build_audit_row_captures_change():
    row = _build_audit_row({"max_daily_trades": 10}, {"max_daily_trades": 5}, "admin")
    assert row.actor == "admin"
    assert row.old_policy == {"max_daily_trades": 10}
    assert row.new_policy == {"max_daily_trades": 5}
    assert row.id  # non-empty unique id
    assert row.event_time is not None


def test_record_writes_art14_then_mirror():
    calls = []

    async def fake_log(old, new, actor, *, strict):
        calls.append(("art14", strict))

    async def fake_mirror(old, new, actor):
        calls.append(("mirror", old, new))

    with patch("core.governance.iron_dome_audit.log_policy_event", fake_log), patch(
        "core.governance.iron_dome_audit._write_audit_mirror", fake_mirror
    ):
        asyncio.run(record_iron_dome_policy_change({"a": 1}, {"a": 2}, "admin"))
    # Art-14 (strict) is recorded BEFORE the queryable mirror.
    assert calls[0] == ("art14", True)
    assert calls[1][0] == "mirror"


def test_write_audit_mirror_persists_a_row():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from core.database.models import Base, IronDomePolicyAudit

    async def run():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        with patch("core.database.session.AsyncSessionLocal", factory):
            await _write_audit_mirror({"a": 1}, {"a": 2}, "admin")
        async with factory() as s:
            return (await s.execute(sa.select(IronDomePolicyAudit))).scalars().all()

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0].actor == "admin"
    assert rows[0].new_policy == {"a": 2}
