# ADR-SEC-06 (#1583 §5) · #1598 — four-eyes core logic. TDD RED first.
# Loosening a risk limit (widening it toward the floor) needs two distinct admins; tightening
# does not. Four-eyes applies only in the enterprise edition (OSS/desktop is single-operator).

from datetime import datetime, timezone

from core.governance.four_eyes import (
    add_approval,
    four_eyes_required,
    is_loosening,
    is_ready_to_apply,
)


def test_is_loosening_higher_max_daily_trades():
    assert is_loosening({"max_daily_trades": 10}, {"max_daily_trades": 20}) is True


def test_is_loosening_false_for_tightening():
    assert is_loosening({"max_daily_trades": 10}, {"max_daily_trades": 5}) is False


def test_is_loosening_detects_omitted_field_widening_to_default():
    # #1635 P1 fix: old is TIGHTER than the strict default (max_daily_trades=5); omitting the
    # field in new resolves it back to the default (10) via load_policy -> a loosening that
    # must require four-eyes (previously bypassed by simply omitting the key).
    assert is_loosening({"max_daily_trades": 5}, {}) is True


def test_is_loosening_shorter_wash_window():
    # A shorter wash-trade window blocks fewer trades -> looser.
    assert (
        is_loosening(
            {"wash_trade_window_seconds": 60}, {"wash_trade_window_seconds": 30}
        )
        is True
    )


def test_is_loosening_higher_stop_loss_pct():
    # A higher stop-loss % tolerates more loss before halting -> looser.
    assert (
        is_loosening(
            {"portfolio_stop_loss_pct": 0.07}, {"portfolio_stop_loss_pct": 0.09}
        )
        is True
    )


def test_four_eyes_off_in_local_edition(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    assert four_eyes_required() is False


def test_four_eyes_on_in_enterprise_edition(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "CLOUD")
    assert four_eyes_required() is True


def test_add_approval_is_distinct_and_excludes_initiator():
    approvals = add_approval([], "admin2", initiator="admin1")
    assert approvals == ["admin2"]
    # the initiator cannot self-approve (segregation of duties)
    approvals = add_approval(approvals, "admin1", initiator="admin1")
    assert approvals == ["admin2"]
    # no duplicate approver
    approvals = add_approval(approvals, "admin2", initiator="admin1")
    assert approvals == ["admin2"]


def test_is_ready_needs_an_approver_and_elapsed_cooloff():
    past = datetime(2026, 1, 1, tzinfo=timezone.utc)
    future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert is_ready_to_apply(["admin2"], past, now) is True
    assert is_ready_to_apply([], past, now) is False  # no second admin
    assert is_ready_to_apply(["admin2"], future, now) is False  # cool-off not elapsed


def test_pending_policy_change_round_trips_through_db():
    import asyncio

    import sqlalchemy as sa
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
        now = datetime(2026, 6, 30, tzinfo=timezone.utc)
        async with factory() as s:
            s.add(
                PendingPolicyChange(
                    id="x",
                    initiator="admin1",
                    requested_policy={"max_daily_trades": 20},
                    approvals=["admin2"],
                    created_at=now,
                    cooloff_until=now,
                    applied=False,
                )
            )
            await s.commit()
        async with factory() as s:
            return (await s.execute(sa.select(PendingPolicyChange))).scalars().all()

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0].initiator == "admin1"
    assert rows[0].approvals == ["admin2"]
    assert rows[0].applied is False
