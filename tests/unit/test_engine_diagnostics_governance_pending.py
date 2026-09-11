"""ADR-OBS-01 follow-up: ``governance.pending_policy_change`` — a cheap, cached,
fail-safe count of PENDING (awaiting-approval, not-yet-applied) four-eyes policy changes.

Invariants under test:
  (a) the count reflects N seeded PENDING (``applied=False``) rows;
  (b) the TTL cache avoids a second DB query within the window — the underlying query fn
      is invoked ONCE across two rapid calls;
  (c) SAFETY — any DB/query error → ``None`` AND ``/engine-diagnostics`` still returns 200
      with a ``governance`` block present;
  (d) an APPLIED change (``applied=True``) is NOT counted.

The count MUST never do heavy per-request DB work on the always-200 hot path and MUST never
raise. See ``core.governance.pending_policy_change``.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import core.governance.pending_policy_change as ppc_mod
from core.database.models import Base, PendingPolicyChange


async def _build_seeded_factory(rows):
    """Build an in-memory async session factory seeded with ``rows`` PendingPolicyChange.

    Must be awaited on the SAME event loop that later runs the count query (the in-memory
    SQLite connection is loop-bound), so seeding + the call live inside one ``asyncio.run``.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for r in rows:
            s.add(r)
        await s.commit()
    return factory


def _row(pid, *, applied=False):
    now = datetime.now(timezone.utc)
    return PendingPolicyChange(
        id=pid,
        initiator="alice",
        requested_policy={"daily_drawdown_pct": 0.20},
        approvals=[],
        created_at=now,
        cooloff_until=now + timedelta(minutes=10),
        applied=applied,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    # Ensure each test starts with a cold TTL cache.
    ppc_mod._reset_cache_for_tests()
    yield
    ppc_mod._reset_cache_for_tests()


def test_count_reflects_seeded_pending_rows(monkeypatch):
    async def _scenario():
        factory = await _build_seeded_factory([_row("p1"), _row("p2"), _row("p3")])
        monkeypatch.setattr(ppc_mod, "AsyncSessionLocal", factory)
        return await ppc_mod.get_pending_policy_change_count()

    assert asyncio.run(_scenario()) == 3


def test_applied_change_is_not_counted(monkeypatch):
    # 2 pending + 2 applied → only the 2 pending are counted.
    async def _scenario():
        factory = await _build_seeded_factory(
            [
                _row("pending-1"),
                _row("pending-2"),
                _row("applied-1", applied=True),
                _row("applied-2", applied=True),
            ]
        )
        monkeypatch.setattr(ppc_mod, "AsyncSessionLocal", factory)
        return await ppc_mod.get_pending_policy_change_count()

    assert asyncio.run(_scenario()) == 2


def test_ttl_cache_avoids_second_query_within_window(monkeypatch):
    calls = {"n": 0}
    real_query = ppc_mod._run_count_query

    async def _counting_query():
        calls["n"] += 1
        return await real_query()

    async def _scenario():
        factory = await _build_seeded_factory([_row("p1"), _row("p2")])
        monkeypatch.setattr(ppc_mod, "AsyncSessionLocal", factory)
        monkeypatch.setattr(ppc_mod, "_run_count_query", _counting_query)
        a = await ppc_mod.get_pending_policy_change_count()
        b = await ppc_mod.get_pending_policy_change_count()
        return a, b

    a, b = asyncio.run(_scenario())
    assert a == 2
    assert b == 2
    # The second call is served from the TTL cache — the DB query ran only ONCE.
    assert calls["n"] == 1


def test_db_error_returns_none_failsafe(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ppc_mod, "_run_count_query", _boom)

    count = asyncio.run(ppc_mod.get_pending_policy_change_count())
    assert count is None


def test_endpoint_still_200_with_governance_block_when_count_errors(monkeypatch):
    # SAFETY end-to-end: even if the count path blows up, /engine-diagnostics stays 200
    # and the governance subsystem is still present (with pending_policy_change = None).
    from core.auth import require_engine_key
    from core.engine.api_routes import app

    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ppc_mod, "_run_count_query", _boom)

    app.dependency_overrides[require_engine_key] = lambda: None
    try:
        client = TestClient(app)
        r = client.get("/engine-diagnostics")
        assert r.status_code == 200
        body = r.json()
        assert "governance" in body
        gov = body["governance"]
        assert "_error" not in gov  # governance collector itself must not fail
        assert gov.get("pending_policy_change") is None
    finally:
        app.dependency_overrides.clear()
