"""TDD — Decision-Outcome-Capture Increment 1 (#2113, Epic #1913 MLR, plan Rev 2).

Pins the durable, owner-only ``decision_outcomes`` capture row (plan §5):

* R1 — Flag OFF (default): no row is enqueued AND no capture-transport field
  leaks into the ``decisions`` sink payload (BORA byte-identity).
* R2 — Flag ON: exactly one row per ``decision_id`` carrying votes_json /
  voted_price / per-cycle OHLC / vix / regime.
* R3 — Lineage-4 (model_version_id, git_sha, config_sha, feature_list_hash)
  set and not 'unknown'/empty.
* R4 — ``is_simulation`` is copied (Pflicht-Split).
* R5 — capture is PURE OBSERVATION: a broken sink can never raise into the
  trading path; the sink E2E lands on SQLite via ``_dialect_insert_ignore``.

Run: cd ai_trading_bot && python -m pytest tests/unit/test_decision_outcome_capture.py -o addopts="" -q
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import config
from core.cloud_logger import DecisionContext

CAPTURE_TRANSPORT_FIELDS = (
    "session_id",
    "consensus_score",
    "gatekeeper_approved",
    "gatekeeper_reason",
    "round_table_votes",
    "cycle_open",
    "cycle_high",
    "cycle_low",
    "cycle_close",
)


def _ctx(**kw) -> DecisionContext:
    defaults = {
        "symbol": "AAPL",
        "action": "BUY",
        "current_price": 150.0,
        "conviction_score": 0.8,
        "model_version_id": "mv-test-1",
    }
    defaults.update(kw)
    return DecisionContext(**defaults)


def _attach(ctx: DecisionContext) -> DecisionContext:
    """Simulate what runner.run_round_table attaches when the flag is ON."""
    ctx.session_id = "sess-1"
    ctx.round_table_votes = [
        {
            "agent_name": "TestAgent",
            "score": 0.7,
            "weight": 1.0,
            "reasoning": "r",
            "vetoed": False,
        }
    ]
    ctx.consensus_score = 0.71
    ctx.gatekeeper_approved = True
    ctx.gatekeeper_reason = "ok"
    ctx.cycle_open = 149.0
    ctx.cycle_high = 151.0
    ctx.cycle_low = 148.5
    ctx.cycle_close = 150.0
    return ctx


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "DECISION_CAPTURE_ENABLED", True, raising=False
    )


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "DECISION_CAPTURE_ENABLED", False, raising=False
    )


# ── R1 — Flag OFF (default) ──────────────────────────────────────────────────


class TestFlagOffIsByteIdentical:
    def test_flag_defaults_on(self):
        """#2839: capture defaults ON (= desktop launcher profile; local-only, no egress)."""
        assert config.get_config().DECISION_CAPTURE_ENABLED is True

    def test_no_row_enqueued_when_flag_off(self, flag_off):
        from core.decision_capture.capture import capture_decision_outcome

        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(_attach(_ctx()))
        sink.log_decision_outcome.assert_not_called()

    def test_to_dict_carries_no_transport_fields(self):
        """The `decisions` sink payload must stay byte-identical to main:
        the capture-transport fields never appear in to_dict()."""
        d = _attach(_ctx()).to_dict()
        for f in CAPTURE_TRANSPORT_FIELDS:
            assert f not in d, f"transport field {f!r} leaked into decisions payload"

    def test_attach_helper_is_noop_when_flag_off(self, flag_off):
        from core.decision_capture.capture import attach_round_table_capture_fields

        ctx = _ctx()
        attach_round_table_capture_fields(
            ctx,
            session_id="sess-x",
            votes=[{"agent_name": "A"}],
            consensus_score=0.9,
            gatekeeper_approved=True,
            gatekeeper_reason="ok",
            ohlc={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        )
        assert ctx.session_id == ""
        assert ctx.round_table_votes is None
        assert ctx.consensus_score is None
        assert ctx.cycle_close is None


# ── R2 — Flag ON: one row per decision_id ────────────────────────────────────


class TestCaptureRow:
    def test_exactly_one_row_keyed_by_decision_id(self, flag_on):
        from core.decision_capture.capture import capture_decision_outcome

        ctx = _attach(_ctx())
        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(ctx)

        sink.log_decision_outcome.assert_called_once()
        row = sink.log_decision_outcome.call_args[0][0]
        assert row["decision_id"] == ctx.decision_id
        assert row["symbol"] == "AAPL"
        assert row["session_id"] == "sess-1"
        assert row["signal_action"] == "BUY"
        assert row["consensus_score"] == 0.71
        assert row["gatekeeper_approved"] is True
        assert row["gatekeeper_reason"] == "ok"
        assert row["votes_json"] == ctx.round_table_votes
        assert row["voted_price"] == 150.0
        assert row["cycle_open"] == 149.0
        assert row["cycle_high"] == 151.0
        assert row["cycle_low"] == 148.5
        assert row["cycle_close"] == 150.0
        assert row["vix_level"] == ctx.vix_level
        assert row["market_regime"] == ctx.market_regime

    def test_attach_helper_attaches_when_flag_on(self, flag_on):
        from core.decision_capture.capture import attach_round_table_capture_fields

        ctx = _ctx()
        attach_round_table_capture_fields(
            ctx,
            session_id="sess-y",
            votes=[{"agent_name": "A"}],
            consensus_score=0.42,
            gatekeeper_approved=False,
            gatekeeper_reason="veto",
            ohlc={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        )
        assert ctx.session_id == "sess-y"
        assert ctx.round_table_votes == [{"agent_name": "A"}]
        assert ctx.consensus_score == 0.42
        assert ctx.gatekeeper_approved is False
        assert ctx.gatekeeper_reason == "veto"
        assert (ctx.cycle_open, ctx.cycle_high, ctx.cycle_low, ctx.cycle_close) == (
            1.0,
            2.0,
            0.5,
            1.5,
        )


# ── R3 — Lineage-4 ───────────────────────────────────────────────────────────


class TestLineage:
    def test_lineage_four_set_and_not_unknown(self, flag_on, monkeypatch):
        monkeypatch.setenv("AAA_APP_VERSION", "9.9.9-test")
        from core.decision_capture.capture import capture_decision_outcome

        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(_attach(_ctx()))

        row = sink.log_decision_outcome.call_args[0][0]
        assert row["model_version_id"] == "mv-test-1"
        assert row["git_sha"] == "9.9.9-test"
        assert row["git_sha"] not in ("", "unknown")
        # SHA-256 hex digests
        assert len(row["config_sha"]) == 64 and row["config_sha"] != ""
        assert len(row["feature_list_hash"]) == 64 and row["feature_list_hash"] != ""

    def test_config_sha_is_deterministic(self):
        from core.decision_capture.lineage import compute_config_sha

        assert compute_config_sha() == compute_config_sha()

    def test_feature_list_hash_pins_the_ordered_field_list(self):
        from core.decision_capture.lineage import (
            FEATURE_FIELDS,
            compute_feature_list_hash,
        )

        assert len(FEATURE_FIELDS) > 0
        # Every declared feature field must exist on the DecisionContext schema —
        # the hash couples to #2389's snapshot schema via these field names.
        ctx = _ctx()
        for f in FEATURE_FIELDS:
            assert hasattr(ctx, f), f"FEATURE_FIELDS names unknown context field {f!r}"
        assert compute_feature_list_hash() == compute_feature_list_hash()


# ── R4 — is_simulation split ─────────────────────────────────────────────────


class TestSimulationSplit:
    @pytest.mark.parametrize("sim", [True, False])
    def test_is_simulation_copied(self, flag_on, sim):
        from core.decision_capture.capture import capture_decision_outcome

        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(_attach(_ctx(is_simulation=sim)))
        row = sink.log_decision_outcome.call_args[0][0]
        assert row["is_simulation"] is sim


# ── R5 — pure observation + SQLite sink E2E ──────────────────────────────────


class TestFailSafeAndSink:
    def test_broken_sink_never_raises_into_the_trading_path(self, flag_on):
        from core.decision_capture.capture import capture_decision_outcome

        with patch(
            "core.cloud_logger.get_cloud_logger", side_effect=RuntimeError("boom")
        ):
            # Must swallow — observation may never touch execution.
            capture_decision_outcome(_attach(_ctx()))

    def test_executor_wrapper_never_raises(self, flag_on):
        from core.engine.order_executor import _capture_outcome

        with patch(
            "core.decision_capture.capture.capture_decision_outcome",
            side_effect=RuntimeError("boom"),
        ):
            _capture_outcome(_attach(_ctx()))  # must not raise

    async def test_decision_outcome_insert_sqlite_e2e(self):
        """The new ORM row lands on SQLite via the BORA _dialect_insert_ignore
        helper (mirrors tests/unit/test_cloud_logger_sqlite.py)."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from core.cloud_logger import _dialect_insert_ignore
        from core.database.models import Base, DecisionOutcome

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        did = str(uuid.uuid4())
        values = {
            "decision_id": did,
            "session_id": "sess-1",
            "symbol": "AAPL",
            "decision_time": datetime.now(timezone.utc),
            "consensus_score": 0.71,
            "signal_action": "BUY",
            "gatekeeper_approved": True,
            "gatekeeper_reason": "ok",
            "votes_json": [{"agent_name": "A", "score": 0.7}],
            "voted_price": 150.0,
            "execution_outcome": "blocked:risk",
            "execution_reason": "position size resolved to 0 (risk/cash)",
            "git_sha": "test-sha",
            "config_sha": "c" * 64,
            "feature_list_hash": "f" * 64,
            "is_simulation": False,
        }
        async with maker() as session:
            stmt = _dialect_insert_ignore(
                DecisionOutcome, dict(values), "decision_id", "sqlite"
            )
            await session.execute(stmt)
            await session.commit()

            # duplicate insert is ignored (append-only, idempotent)
            stmt2 = _dialect_insert_ignore(
                DecisionOutcome,
                {**values, "execution_outcome": "executed"},
                "decision_id",
                "sqlite",
            )
            await session.execute(stmt2)
            await session.commit()

            res = await session.execute(
                select(DecisionOutcome).where(DecisionOutcome.decision_id == did)
            )
            row = res.scalars().first()
        await engine.dispose()

        assert row is not None
        assert row.symbol == "AAPL"
        assert row.execution_outcome == "blocked:risk"  # first insert wins
        assert row.votes_json == [{"agent_name": "A", "score": 0.7}]

    def test_worker_drains_decision_outcomes_every_cycle_not_flush_only(self):
        """R-B lesson (plan §2): a flush/shutdown-only queue never lands on a
        hard-killed desktop engine. The worker loop must drain the
        decision_outcome queue EVERY cycle — without any flush() call."""
        import time as _t
        from unittest.mock import AsyncMock

        from core.cloud_logger import CloudLogger, get_cloud_logger

        logger = get_cloud_logger()
        assert hasattr(logger, "_decision_outcome_queue")
        assert hasattr(logger, "log_decision_outcome")

        with patch.object(
            CloudLogger, "_send_batch", new_callable=AsyncMock
        ) as send_batch:
            logger.log_decision_outcome({"decision_id": "d-cycle-1", "symbol": "AAPL"})
            deadline = _t.time() + 5.0
            seen = False
            while _t.time() < deadline and not seen:
                seen = any(
                    c.args and c.args[0] == "decision_outcomes"
                    for c in send_batch.call_args_list
                )
                _t.sleep(0.1)
        assert seen, (
            "decision_outcomes queue was not drained by the worker loop within 5s "
            "— it must NOT be flush/shutdown-only (plan §5.2, R-B)"
        )
