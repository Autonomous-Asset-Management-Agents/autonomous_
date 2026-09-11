# tests/unit/test_sizing_drop_is_visible.py
# ADR-OBS-01 — an order that dies at sizing leaves no trace anywhere.
#
# THE HOLE (core/engine/order_executor.py, in `_process_signal_event`):
#
#     if qty > 0:
#         ... compliance check, audit log, submit, outcome recorded ...
#     # <-- no else. qty <= 0 simply falls through.
#
# No log line, no audit entry, no execution outcome. The order does not fail — it
# ceases to exist. On 2026-07-16 the `decisions` table recorded 1013 gatekeeper-approved
# BUYs, every one with `execution_qty=0.0`, while `compliance_audit.log` held 9 lines
# (all SELLs, from the previous day). 1013 approvals produced zero compliance checks
# because the orders died one step earlier, silently.
#
# ⚠ Note what is and is not claimed: the 1013 dropped orders are measured. WHY they sized
# to zero on that particular account is NOT this fix's business (that account carried ~194%
# exposure and belongs to a different program). A silent drop on the trading path is a
# defect in its own right, whatever the cause upstream — it is what made everything else
# unreadable.
#
# THE FIX IS AN ANNOUNCEMENT, NOT A BEHAVIOUR CHANGE. Nothing about which orders execute
# changes: qty <= 0 still submits nothing. The only difference is that it now says so.
#
# Nothing new is invented for it. `BLOCKED_RISK = "blocked:risk"` already exists in
# core/round_table/execution_outcomes.py:30, documented verbatim as "position size resolved
# to 0 (risk/cash)". Both frontends already render it — src/console/desktop/pages/
# Decisions.tsx:45 and src/pages/LiveDemo.tsx:51 both map it to "Blocked · risk sizing".
# The badge was built and wired and could never fire: nothing in the engine emitted that
# code. Zero producers, two consumers. This connects them.
#
# The sibling path already gets this right-ish: order_executor.py:631 handles `size <= 0`
# with a reason string and an explainability event — but logs it at DEBUG, which
# CLAUDE.md §5.6 forbids outright ("Fallback Values — Always log at WARNING, never DEBUG").
# This one logs at WARNING.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events import SignalEvent
from core.round_table.execution_outcomes import (
    BLOCKED_RISK,
    clear_execution_outcomes,
    get_execution_outcome,
)


@pytest.fixture(autouse=True)
def _clean_store():
    clear_execution_outcomes()
    yield
    clear_execution_outcomes()


def _make_executor(api):
    """Mirrors tests/unit/test_async_broker_offload.py:13 — the established minimal
    shape for driving _process_signal_event."""
    from core.engine.order_executor import OrderExecutorMixin

    ex = OrderExecutorMixin.__new__(OrderExecutorMixin)
    ex.api = api
    ex.compliance_guardian = None
    ex._log_strategy_thought = MagicMock()
    ex.cloud_logger = MagicMock()
    ex.cloud_logger.log_decision = MagicMock()
    ex.live_universe = []
    return ex


def _make_buy_event(qty: float = 0.0):
    ctx = MagicMock()
    ctx.current_price = 150.0
    ctx.conviction_score = 0.8
    ctx.alpaca_order_id = None
    # ⚠ These MUST be real numbers, not left as MagicMock attributes. order_executor.py:1353
    # does `if atr <= 0:` — a MagicMock there raises TypeError, the sizing block's `except`
    # swallows it and sets qty=0.0, and the test then measures an EXCEPTION path while
    # believing it measured a clean zero size. A first draft of this file did exactly that,
    # and the stray "[AAPL] Global RiskManager sizing failed" WARNING it produced made the
    # log assertion below pass for entirely the wrong reason.
    ctx.atr_14d = 3.0
    ctx.vix_level = 20.0
    ev = MagicMock(spec=SignalEvent)
    ev.action = "BUY"
    ev.symbol = "AAPL"
    ev.suggested_quantity = qty
    ev.decision_context = ctx
    ev.is_simulation = False
    return ev


def _sized_to_zero_executor():
    """The real production shape of the 1013 drops: the gatekeeper approved a BUY, the
    risk manager sized it to 0 (exposure cap / cash), and the order vanished."""
    api = MagicMock()
    api.get_account.return_value = MagicMock(cash="100000")
    ex = _make_executor(api)
    ex.live_risk_manager = MagicMock()
    ex.live_risk_manager.calculate_position_size.return_value = 0.0
    return ex, api


def test_the_literal_in_the_engine_matches_the_constant_the_gui_reads():
    """order_executor.py spells the code as the literal "blocked:risk" rather than importing
    BLOCKED_RISK — on purpose (_rec_outcome imports the recorder lazily inside its try, so a
    broken observability module can never raise into the trading path; a module-level import
    would undo that). The cost of the literal is drift: if the constant is ever renamed, the
    engine keeps emitting a code nothing renders. This is the only thing holding them
    together."""
    assert BLOCKED_RISK == "blocked:risk", (
        "BLOCKED_RISK changed value — order_executor.py emits the literal 'blocked:risk' "
        "and both frontends switch on it (Decisions.tsx:45, LiveDemo.tsx:51). Update all "
        "four together or the badge goes dead again."
    )


class TestTheDropIsAnnounced:
    async def test_a_buy_sized_to_zero_records_blocked_risk(self):
        """The whole point: the order must leave a trace. Asserted against the REAL
        outcome store (not a mock of the recorder), so this pins the actual wiring the
        GUI reads."""
        executor, _api = _sized_to_zero_executor()

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(_make_buy_event(qty=0.0))

        outcome = get_execution_outcome("AAPL")
        assert outcome is not None, (
            "a BUY that sized to 0 left NO execution outcome — this is the silent drop: "
            "1013 orders vanished this way on 2026-07-16"
        )
        assert outcome["outcome"] == BLOCKED_RISK, (
            f"expected {BLOCKED_RISK!r} (the code that already exists for exactly this, "
            f"and that both frontends already render) — got {outcome['outcome']!r}"
        )

    async def test_the_reason_is_specific_enough_to_act_on(self):
        """'blocked' without a why is only marginally better than silence. The reason must
        name the sizer, so the reader knows where to look."""
        executor, _api = _sized_to_zero_executor()

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(_make_buy_event(qty=0.0))

        reason = (get_execution_outcome("AAPL") or {}).get("reason", "")
        assert reason, "the outcome carries no reason at all"
        assert (
            "0" in reason or "zero" in reason.lower()
        ), f"the reason should state that the size resolved to zero — got {reason!r}"

    async def test_it_warns_not_debugs(self, caplog):
        """CLAUDE.md §5.6: a fallback/rejection on the trading path logs at WARNING.
        The sibling site (order_executor.py:631) logs the same event at DEBUG, which is
        exactly why nobody saw 1013 orders die."""
        import logging

        executor, _api = _sized_to_zero_executor()

        with caplog.at_level(logging.WARNING), patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(_make_buy_event(qty=0.0))

        assert any(
            "AAPL" in r.message or "AAPL" in str(r.args)
            for r in caplog.records
            if r.levelno >= logging.WARNING
        ), "the drop produced no WARNING — it stays invisible in the logs (CLAUDE.md 5.6)"


class TestNothingAboutExecutionChanges:
    """⚠ THE SAFETY AXIOM. This PR announces; it must not decide. If it alters which orders
    reach the broker, it is the wrong change."""

    async def test_a_zero_sized_buy_still_submits_nothing(self):
        """Fail-safe preserved: no order is created to 'fix' the drop."""
        executor, api = _sized_to_zero_executor()

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(_make_buy_event(qty=0.0))

        api.submit_order.assert_not_called()

    async def test_a_normal_buy_still_executes(self):
        """The happy path is untouched — mirrors test_async_broker_offload.py:40, which
        pins the same submit through a different lens."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-123")
        executor = _make_executor(api)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(_make_buy_event(qty=2.0))

        api.submit_order.assert_called_once()
        assert (get_execution_outcome("AAPL") or {}).get(
            "outcome"
        ) != BLOCKED_RISK, "an executed order must not be marked blocked"
