"""TDD — durable execution outcome per decision_id + newly wired codes (#2113).

* R6 — the capture row joins the execution outcome per ``decision_id`` (NOT the
  fragile symbol+5s serve-heuristic): a decision_id-stamped record wins; a
  legacy (unstamped) record is accepted only when recorded at/after this
  decision's creation — the two-cycles-same-symbol collision resolves correctly.
* R7 — ``blocked:kill_switch`` (order_executor.py kill-switch gates) and
  ``hitl_held`` (HITL hold seam) are emitted when DECISION_CAPTURE_ENABLED.
  (``resized`` is deliberately NOT wired — main has no size_scaler<1 path; see
  plan §12.2, deferred as follow-up.)
* R8 — gate control flow is byte-identical: flag OFF emits nothing new and the
  halt/hold behaviour is exactly today's; flag ON never changes which orders
  reach the broker.

Run: cd ai_trading_bot && python -m pytest tests/unit/test_execution_outcome_durable.py -o addopts="" -q
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.cloud_logger import DecisionContext
from core.events import SignalEvent
from core.round_table.execution_outcomes import (
    BLOCKED_KILL_SWITCH,
    HITL_HELD,
    clear_execution_outcomes,
    get_execution_outcome,
    record_execution_outcome,
)


@pytest.fixture(autouse=True)
def _clean_store():
    clear_execution_outcomes()
    yield
    clear_execution_outcomes()


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


def _ctx(**kw) -> DecisionContext:
    defaults = {"symbol": "AAPL", "action": "BUY", "current_price": 150.0}
    defaults.update(kw)
    return DecisionContext(**defaults)


# ── R6 — decision_id join ────────────────────────────────────────────────────


class TestDecisionIdJoin:
    def test_record_stores_decision_id(self):
        record_execution_outcome("AAPL", "executed", "ok", decision_id="d-1")
        rec = get_execution_outcome("AAPL")
        assert rec is not None and rec["decision_id"] == "d-1"

    def test_record_without_decision_id_keeps_legacy_shape(self):
        """Existing call sites are untouched — no decision_id key appears."""
        record_execution_outcome("AAPL", "executed", "ok")
        rec = get_execution_outcome("AAPL")
        assert rec is not None and "decision_id" not in rec

    def test_matching_decision_id_attaches(self):
        from core.decision_capture.capture import resolve_execution_outcome

        ctx = _ctx()
        record_execution_outcome(
            "AAPL", BLOCKED_KILL_SWITCH, "halt", decision_id=ctx.decision_id
        )
        outcome, reason = resolve_execution_outcome(ctx)
        assert outcome == BLOCKED_KILL_SWITCH
        assert reason == "halt"

    def test_foreign_decision_id_is_rejected(self):
        """Collision case: the store still holds the PREVIOUS decision's outcome
        for the same symbol — it must NOT be attributed to the new decision."""
        from core.decision_capture.capture import resolve_execution_outcome

        record_execution_outcome(
            "AAPL", "executed", "old", decision_id="other-decision"
        )
        outcome, reason = resolve_execution_outcome(_ctx())
        assert outcome is None and reason is None

    def test_legacy_record_after_decision_creation_attaches(self):
        """Unstamped records (today's emit sites) recorded DURING this decision's
        processing — i.e. at/after its creation — belong to it (the executor
        processes signals serially)."""
        from core.decision_capture.capture import resolve_execution_outcome

        ctx = _ctx()
        record_execution_outcome("AAPL", "blocked:risk", "size 0", ts=time.time() + 1)
        outcome, reason = resolve_execution_outcome(ctx)
        assert outcome == "blocked:risk"
        assert reason == "size 0"

    def test_stale_legacy_record_from_previous_cycle_is_rejected(self):
        """Two cycles, same symbol: an outcome recorded BEFORE this decision was
        created belongs to the previous cycle's decision."""
        from core.decision_capture.capture import resolve_execution_outcome

        record_execution_outcome("AAPL", "executed", "prev cycle", ts=time.time() - 60)
        ctx = _ctx(decision_time=datetime.now(timezone.utc) - timedelta(seconds=5))
        outcome, reason = resolve_execution_outcome(ctx)
        assert outcome is None and reason is None

    def test_capture_row_carries_the_resolved_outcome(self, flag_on):
        from core.decision_capture.capture import capture_decision_outcome

        ctx = _ctx()
        record_execution_outcome(
            "AAPL", BLOCKED_KILL_SWITCH, "halt", decision_id=ctx.decision_id
        )
        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(ctx)
        row = sink.log_decision_outcome.call_args[0][0]
        assert row["execution_outcome"] == BLOCKED_KILL_SWITCH
        assert row["execution_reason"] == "halt"


# ── executor harness (mirrors tests/unit/test_sizing_drop_is_visible.py) ─────


def _make_executor(api):
    from core.engine.order_executor import OrderExecutorMixin

    ex = OrderExecutorMixin.__new__(OrderExecutorMixin)
    ex.api = api
    ex.compliance_guardian = None
    ex._log_strategy_thought = MagicMock()
    ex.cloud_logger = MagicMock()
    ex.cloud_logger.log_decision = MagicMock()
    ex.live_universe = []
    return ex


def _make_buy_event(qty: float = 2.0):
    ctx = _ctx(action="BUY", atr_14d=3.0, vix_level=20.0, conviction_score=0.8)
    ev = MagicMock(spec=SignalEvent)
    ev.action = "BUY"
    ev.symbol = "AAPL"
    ev.suggested_quantity = qty
    ev.decision_context = ctx
    ev.is_simulation = False
    return ev


# ── R7/R8 — blocked:kill_switch (fallback submit gates) ──────────────────────


class TestKillSwitchOutcome:
    async def _run_halted(self):
        api = MagicMock()
        api.get_account.return_value = MagicMock(cash="100000")
        executor = _make_executor(api)
        ev = _make_buy_event(qty=2.0)
        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ), patch("core.cloud_logger.get_cloud_logger", return_value=MagicMock()), patch(
            "core.engine.order_executor.kill_switch"
        ) as ks:
            ks.check_halt.side_effect = Exception("System is HALTED by Kill Switch")
            await executor._process_signal_event(ev)
        return api, ev

    async def test_flag_on_records_blocked_kill_switch(self, flag_on):
        api, ev = await self._run_halted()
        api.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert rec is not None, "halted order left no outcome"
        assert rec["outcome"] == BLOCKED_KILL_SWITCH
        assert rec.get("decision_id") == ev.decision_context.decision_id

    async def test_flag_off_is_byte_identical_no_new_code(self, flag_off):
        api, _ev = await self._run_halted()
        api.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert (
            rec is None or rec["outcome"] != BLOCKED_KILL_SWITCH
        ), "flag OFF must not emit the new code (BORA byte-identity)"

    async def test_flag_on_never_submits_despite_capture(self, flag_on):
        """THE SAFETY AXIOM: observation must not decide — the halt still halts."""
        api, _ev = await self._run_halted()
        api.submit_order.assert_not_called()


class TestKillSwitchOutcomeTenantPath:
    async def _run_tenant_halted(self):
        api = MagicMock()
        executor = _make_executor(api)
        client = MagicMock()
        client.get_account.return_value = MagicMock(cash="100000")
        tenant = {"user_id": "u1", "client": client, "equity": 100000.0}

        rm = MagicMock()
        rm.calculate_position_size.return_value = 2.0
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "ok", None)
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)
        executor.compliance_guardian = MagicMock()
        executor.compliance_guardian.check_order.return_value = True
        executor.compliance_guardian.check_trade.return_value = True

        ev = _make_buy_event(qty=0.0)
        with patch(
            "core.engine.order_executor.RedisClient.get_redis",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.engine.order_executor.restore_pm_state_from_redis",
            new=AsyncMock(),
        ), patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ), patch(
            "core.cloud_logger.get_cloud_logger", return_value=MagicMock()
        ), patch(
            "core.engine.order_executor.kill_switch"
        ) as ks:
            ks.check_halt.side_effect = Exception("System is HALTED by Kill Switch")
            await executor._execute_tenant_order(tenant, ev)
        return client, ev

    async def test_tenant_gate_records_blocked_kill_switch(self, flag_on):
        client, ev = await self._run_tenant_halted()
        client.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert rec is not None and rec["outcome"] == BLOCKED_KILL_SWITCH
        assert rec.get("decision_id") == ev.decision_context.decision_id

    async def test_tenant_gate_flag_off_no_new_code(self, flag_off):
        client, _ev = await self._run_tenant_halted()
        client.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert rec is None or rec["outcome"] != BLOCKED_KILL_SWITCH


# ── R7/R8 — hitl_held ────────────────────────────────────────────────────────


class TestHitlHeldOutcome:
    async def _run_held(self, monkeypatch):
        monkeypatch.setattr(config.get_config(), "HITL_ENABLED", True, raising=False)
        monkeypatch.setattr(
            config.get_config(), "BYPASS_MARKET_HOURS", True, raising=False
        )
        api = MagicMock()
        executor = _make_executor(api)
        ev = _make_buy_event(qty=2.0)
        sink = MagicMock()
        with patch(
            "core.hitl_gate.should_hold", new=AsyncMock(return_value=True)
        ), patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            await executor._process_signal_event(ev)
        return api, ev, sink

    async def test_flag_on_records_and_captures_hitl_held(self, flag_on, monkeypatch):
        api, ev, sink = await self._run_held(monkeypatch)
        api.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert rec is not None and rec["outcome"] == HITL_HELD
        assert rec.get("decision_id") == ev.decision_context.decision_id
        # the held decision is persisted durably (capture row), Gherkin §7 Sc. 3
        sink.log_decision_outcome.assert_called_once()
        row = sink.log_decision_outcome.call_args[0][0]
        assert row["decision_id"] == ev.decision_context.decision_id
        assert row["execution_outcome"] == HITL_HELD

    async def test_flag_off_hold_behaviour_identical_no_code(
        self, flag_off, monkeypatch
    ):
        api, _ev, sink = await self._run_held(monkeypatch)
        api.submit_order.assert_not_called()  # still held — behaviour unchanged
        assert get_execution_outcome("AAPL") is None
        sink.log_decision_outcome.assert_not_called()


# ── regression: the already-emitted codes stay untouched ─────────────────────


class TestExistingCodesUnchanged:
    async def test_blocked_risk_still_emitted_with_capture_flag_on(self, flag_on):
        """Gherkin Sc. 4: blocked:risk stays exactly as in main (no doubling)."""
        api = MagicMock()
        api.get_account.return_value = MagicMock(cash="100000")
        executor = _make_executor(api)
        executor.live_risk_manager = MagicMock()
        executor.live_risk_manager.calculate_position_size.return_value = 0.0
        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ), patch("core.cloud_logger.get_cloud_logger", return_value=MagicMock()):
            await executor._process_signal_event(_make_buy_event(qty=0.0))
        rec = get_execution_outcome("AAPL")
        assert rec is not None and rec["outcome"] == "blocked:risk"
