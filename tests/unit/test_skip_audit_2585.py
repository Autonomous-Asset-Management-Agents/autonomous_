# tests/unit/test_skip_audit_2585.py
# #2585 (P0, observability-only) — SKIPPED-signal audit trail.
#
# Incident (2026-07-30): an approved BUY (INCY, "BUY - 91% consensus, Approved"
# on the audit chain at 16:29) reached neither the ComplianceGuardian nor the
# broker — no log, no error, no order (account: cash -39k$, 133% invested).
# The signal died at the tenant `size <= 0` guard, which logs at DEBUG only and
# writes NOTHING to the Art-14 audit chain.
#
# Contract under test: EVERY non-execution of an approved BUY/SELL signal emits
#   (a) a WARNING log naming the reason, and
#   (b) a "SKIPPED: <reason>" entry on the SAME Art-14 hash chain that carries
#       the Round-Table approval (via hitl_gate.log_execution_event,
#       HITLExecutionEvent(branch="skipped")).
# The happy path emits NO skip entry. Trading behaviour itself is unchanged.
#
# NB: WARNING assertions use a `logging.warning` spy, NOT caplog — the engine's
# structured_logging init strips and replaces the root handlers mid-path
# (structured_logging.py:91-110), which silently removes caplog's handler.

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from core.events import SignalEvent

# ---------------------------------------------------------------------------
# Helpers (mirroring tests/unit/test_order_executor.py fixtures)
# ---------------------------------------------------------------------------


def _make_decision_context(**kwargs):
    ctx = MagicMock()
    ctx.lstm_prediction = kwargs.get("lstm_prediction", 0.0)
    ctx.rl_stabilized_action = kwargs.get("rl_stabilized_action", 0)
    ctx.risk_approved = kwargs.get("risk_approved", True)
    ctx.portfolio_approved = kwargs.get("portfolio_approved", True)
    ctx.client_order_id = kwargs.get("client_order_id", "test-uuid")
    ctx.intelligence_approved = kwargs.get("intelligence_approved", True)
    ctx.current_price = kwargs.get("current_price", 150.0)
    ctx.conviction_score = kwargs.get("conviction_score", 0.8)
    ctx.alpaca_order_id = None
    return ctx


def _make_signal(action="BUY", symbol="INCY", qty=2.0, is_simulation=False):
    ctx = _make_decision_context()
    event = MagicMock(spec=SignalEvent)
    event.action = action
    event.symbol = symbol
    event.suggested_quantity = qty
    event.decision_context = ctx
    event.is_simulation = is_simulation
    return event


def _make_executor(api=None, compliance=None):
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = api or MagicMock()
    executor.compliance_guardian = compliance
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()
    executor.cloud_logger.log_decision = MagicMock()
    executor.live_universe = []
    return executor


def _redis_client_mock():
    """RedisClient.get_redis mock with publish + Redlock surface (AsyncMock, §5.2)."""
    return MagicMock(
        publish=AsyncMock(),
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        lock=MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        ),
    )


def _skip_events(audit_mock):
    """All HITLExecutionEvent objects with branch=='skipped' awaited on the mock."""
    return [
        c.args[0]
        for c in audit_mock.await_args_list
        if getattr(c.args[0], "branch", None) == "skipped"
    ]


def _warned(log_spy, *needles):
    """True iff one logging.warning call carries ALL needles across its args."""
    for c in log_spy.call_args_list:
        blob = " ".join(str(a) for a in c.args)
        if all(n in blob for n in needles):
            return True
    return False


# ---------------------------------------------------------------------------
# 1. Approved signal + sizing -> 0 ==> audit-chain SKIPPED entry + WARNING
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("#2585 SKIPPED-signal audit")
class TestSizingZeroEmitsSkipAudit:
    @pytest.mark.anyio
    async def test_tenant_sizing_zero_emits_skip_audit_and_warning(self):
        """
        Given: an approved BUY on the tenant path whose RiskManager sizes to 0
               (the exact INCY drop point: order_executor `size <= 0`, DEBUG only)
        When:  _execute_tenant_order runs
        Then:  a "SKIPPED: sizing_zero" HITLExecutionEvent lands on the audit
               chain AND a WARNING names symbol + reason. No broker submit.
        """
        executor = _make_executor()
        tenant = {"user_id": "user-1", "client": MagicMock(), "equity": 10000.0}
        event = _make_signal(action="BUY", symbol="INCY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 0.0  # sizing -> 0 (mocked)
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        audit = AsyncMock()
        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.hitl_gate.log_execution_event", new=audit),
            patch("logging.warning", wraps=logging.warning) as log_spy,
        ):
            mock_redis.get_redis = AsyncMock(return_value=_redis_client_mock())
            await executor._execute_tenant_order(tenant, event)

        tenant["client"].submit_order.assert_not_called()
        skips = _skip_events(audit)
        assert len(skips) == 1, "expected exactly one SKIPPED audit-chain entry"
        ev = skips[0]
        assert ev.symbol == "INCY"
        assert ev.action == "BUY"
        assert ev.reason.startswith("SKIPPED: sizing_zero")
        assert _warned(
            log_spy, "SKIPPED approved", "INCY", "sizing_zero"
        ), "expected a WARNING naming symbol + reason sizing_zero"

    @pytest.mark.anyio
    async def test_fallback_sizing_zero_emits_skip_audit_and_warning(self):
        """
        Given: an approved BUY on the desktop/global fallback path resolving to
               qty 0 (no sizer available -> qty stays 0)
        When:  _process_signal_event runs
        Then:  "SKIPPED: sizing_zero" on the chain + WARNING; no broker submit.
        """
        api = MagicMock()
        executor = _make_executor(api=api)
        executor.live_risk_manager = None  # qty stays 0 on the fallback path
        event = _make_signal(action="BUY", symbol="INCY", qty=0.0)

        audit = AsyncMock()
        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.hitl_gate.log_execution_event", new=audit),
            patch("logging.warning", wraps=logging.warning) as log_spy,
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        skips = _skip_events(audit)
        assert len(skips) == 1
        assert skips[0].symbol == "INCY"
        assert skips[0].action == "BUY"
        assert skips[0].reason.startswith("SKIPPED: sizing_zero")
        assert _warned(log_spy, "SKIPPED approved", "INCY", "sizing_zero")


# ---------------------------------------------------------------------------
# 2. Approved signal + submission exception ==> SKIPPED entry + WARNING
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("#2585 SKIPPED-signal audit")
class TestSubmitExceptionEmitsSkipAudit:
    @pytest.mark.anyio
    async def test_tenant_submit_exception_emits_skip_audit_and_warning(self):
        """
        Given: an approved BUY on the tenant path whose broker submit raises
               (swallowed today by the outer `except Exception` at ERROR level,
               with no audit-chain record)
        When:  _execute_tenant_order runs
        Then:  "SKIPPED: execution_error" on the chain + WARNING.
        """
        executor = _make_executor()
        tenant_api = MagicMock()
        tenant_api.submit_order.side_effect = RuntimeError("broker down")
        tenant = {"user_id": "user-1", "client": tenant_api, "equity": 10000.0}
        event = _make_signal(action="BUY", symbol="INCY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        audit = AsyncMock()
        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
            patch("core.hitl_gate.log_execution_event", new=audit),
            patch("logging.warning", wraps=logging.warning) as log_spy,
        ):
            ks.check_halt = MagicMock()
            mock_redis.get_redis = AsyncMock(return_value=_redis_client_mock())
            await executor._execute_tenant_order(tenant, event)

        skips = _skip_events(audit)
        assert len(skips) == 1
        ev = skips[0]
        assert ev.symbol == "INCY"
        assert ev.action == "BUY"
        assert ev.reason.startswith("SKIPPED: execution_error")
        assert "broker down" in ev.reason
        assert _warned(log_spy, "SKIPPED approved", "INCY", "execution_error")

    @pytest.mark.anyio
    async def test_fallback_submit_exception_emits_skip_audit_and_warning(self):
        """
        Given: an approved BUY on the desktop/global fallback path whose broker
               submit raises (swallowed today by `_process_signal_event`'s outer
               `except Exception` — "Multi-tenant execution failed", ERROR only)
        When:  _process_signal_event runs
        Then:  "SKIPPED: execution_error" on the chain + WARNING.
        """
        api = MagicMock()
        api.submit_order.side_effect = RuntimeError("alpaca 500")
        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", symbol="INCY", qty=2.0)

        audit = AsyncMock()
        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
            patch("core.hitl_gate.log_execution_event", new=audit),
            patch("logging.warning", wraps=logging.warning) as log_spy,
        ):
            ks.check_halt = MagicMock()
            await executor._process_signal_event(event)

        skips = _skip_events(audit)
        assert len(skips) == 1
        ev = skips[0]
        assert ev.symbol == "INCY"
        assert ev.action == "BUY"
        assert ev.reason.startswith("SKIPPED: execution_error")
        assert _warned(log_spy, "SKIPPED approved", "INCY", "execution_error")


# ---------------------------------------------------------------------------
# 3. Happy path ==> NO skip entry
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("#2585 SKIPPED-signal audit")
class TestHappyPathEmitsNoSkipEntry:
    @pytest.mark.anyio
    async def test_tenant_happy_path_no_skip_entry(self):
        """A tenant BUY that reaches the broker and FILLS emits NO skip entry."""
        from alpaca.trading.enums import OrderStatus

        executor = _make_executor()
        tenant_api = MagicMock()
        tenant_api.submit_order.return_value = MagicMock(id="order-happy-1")
        tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)
        tenant = {"user_id": "user-1", "client": tenant_api, "equity": 10000.0}
        event = _make_signal(action="BUY", symbol="INCY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        audit = AsyncMock()
        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
            patch("core.hitl_gate.log_execution_event", new=audit),
        ):
            ks.check_halt = MagicMock()
            mock_redis.get_redis = AsyncMock(return_value=_redis_client_mock())
            await executor._execute_tenant_order(tenant, event)

        tenant_api.submit_order.assert_called_once()
        assert _skip_events(audit) == [], "happy path must NOT write a skip entry"

    @pytest.mark.anyio
    async def test_fallback_happy_path_no_skip_entry(self):
        """A fallback BUY that submits successfully emits NO skip entry."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-happy-2")
        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", symbol="INCY", qty=2.0)

        audit = AsyncMock()
        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.hitl_gate.log_execution_event", new=audit),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()
        assert _skip_events(audit) == [], "happy path must NOT write a skip entry"


# ---------------------------------------------------------------------------
# 4. Taxonomy is closed + the chain writer is reused (no second writer)
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("#2585 SKIPPED-signal audit")
class TestSkipReasonTaxonomy:
    def test_taxonomy_is_the_agreed_closed_set(self):
        """The reason taxonomy is deliberately small; consumers rely on this set."""
        from core.engine import order_executor as oe

        assert oe.SKIP_REASON_INSUFFICIENT_BUYING_POWER == "insufficient_buying_power"
        assert oe.SKIP_REASON_BELOW_ORDER_FLOOR == "below_order_floor"
        assert oe.SKIP_REASON_SIZING_ZERO == "sizing_zero"
        assert oe.SKIP_REASON_EXECUTION_ERROR == "execution_error"
        assert oe.SKIP_REASON_OTHER == "other"

    @pytest.mark.anyio
    async def test_skip_audit_failure_never_raises_and_still_warns(self):
        """Chain-writer failure degrades to WARNING-only — never into the order path."""
        from core.engine.order_executor import (
            SKIP_REASON_SIZING_ZERO,
            _audit_skipped_signal,
        )

        with (
            patch(
                "core.hitl_gate.log_execution_event",
                new=AsyncMock(side_effect=RuntimeError("disk full")),
            ),
            patch("logging.warning", wraps=logging.warning) as log_spy,
        ):
            await _audit_skipped_signal("INCY", "BUY", SKIP_REASON_SIZING_ZERO, "x")

        assert _warned(log_spy, "INCY", "sizing_zero")
