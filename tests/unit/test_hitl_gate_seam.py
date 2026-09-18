# tests/unit/test_hitl_gate_seam.py
# ii-4b (PR-0a-ii, GAP2): the order-executor seam that invokes the HITL gate.
#
# Proves the two properties that matter for the seam itself (the gate's *decision* matrix is
# covered by test_hitl_gate.py):
#   1. DORMANCY — with HITL_ENABLED False the gate is never invoked and execution proceeds
#      exactly as before (byte-identical dormant path).
#   2. SHORT-CIRCUIT — with HITL on and the gate returning HOLD, the execution path is not
#      entered at all (the order is not submitted).
#
# We drive the real OrderExecutorMixin._process_signal_event bound to a fake `self`, and halt at
# the execution boundary with a BaseException sentinel — the method's own `except Exception`
# cannot swallow it, so reaching get_active_tenant_clients is observable.
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.engine.order_executor import OrderExecutorMixin  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _Halt(BaseException):
    """Sentinel raised at the execution boundary; BaseException so `except Exception` skips it."""


def _buy_event():
    ctx = SimpleNamespace(current_price=100.0, position_qty=0.0, conviction_score=0.7)
    return SimpleNamespace(
        symbol="AAPL",
        action="BUY",
        suggested_quantity=10.0,
        is_simulation=False,
        decision_context=ctx,
    )


def _fake_self():
    me = MagicMock(spec=OrderExecutorMixin)
    me.active_uid = None
    # INC-6 added an explicit market-closed gate BEFORE the HITL seam in _process_signal_event.
    # On a spec'd MagicMock this async method auto-mocks to a truthy return, which would
    # short-circuit ("blocked:market_closed") before the HITL gate is ever consulted. Neutralise
    # it here (market open ⇒ not blocked) so this test isolates the HITL seam; the gate's own
    # behaviour is covered by test_order_executor.py::TestMarketClosedOrderGate.
    me._market_closed_blocks_order = AsyncMock(return_value=False)
    # First call inside the execution block — raise the sentinel to halt right at the boundary.
    me.get_active_tenant_clients = AsyncMock(side_effect=_Halt())
    return me


def _drive(hitl_enabled, should_hold_returns):
    import config

    me = _fake_self()
    gate = AsyncMock(return_value=should_hold_returns)
    cfg = SimpleNamespace(HITL_ENABLED=hitl_enabled, BYPASS_MARKET_HOURS=True)
    orig_get_config = config.get_config
    config.get_config = lambda: cfg
    try:
        with patch("core.hitl_gate.should_hold", gate):
            _run(OrderExecutorMixin._process_signal_event(me, _buy_event()))
    except _Halt:
        pass  # reached the execution boundary
    finally:
        config.get_config = orig_get_config
    return me, gate


def test_dormant_when_disabled_gate_not_invoked_execution_proceeds():
    me, gate = _drive(hitl_enabled=False, should_hold_returns=True)
    gate.assert_not_awaited()  # gate never consulted when HITL is off
    me.get_active_tenant_clients.assert_awaited_once()  # execution path entered (dormant)


# ── #2704: the seam MOVED — these two tests were rewritten, not deleted ──────
#
# They used to assert the gate is consulted BEFORE `get_active_tenant_clients` and that a HOLD
# stops the path there. That WAS the defect: at that point the order has no quantity yet (board
# signals leave `suggested_quantity` at 0.0), so the gate valued every order at 0, queued it as
# `unknown_value`, and the operator saw 13 unapprovable `Qty 0` rows on 2026-08-06.
#
# The gate now sits AFTER position sizing and the portfolio decision in BOTH execution paths, so
# reaching the tenant fan-out is EXPECTED and no longer evidence of a dormant gate. What this file
# can still prove is dormancy (above) and the absence of the old pre-sizing call (below). The new
# position, the quantity propagation and the fail-closed behaviour are proved in
# `test_hitl_gate_after_sizing.py` — including AST guards that this call site cannot regress.


def test_gate_is_no_longer_consulted_before_the_tenant_fan_out():
    """The move itself. With HITL ON, nothing is asked of the gate this early — there is no
    quantity to judge yet."""
    me, gate = _drive(hitl_enabled=True, should_hold_returns=True)
    gate.assert_not_awaited()
    me.get_active_tenant_clients.assert_awaited_once()


def test_a_hold_verdict_cannot_stop_the_path_this_early_any_more():
    """Complements the above: even a HOLD-returning gate does not short-circuit here, because it
    is not reached here. A regression that moved the call back would fail this AND the AST guard
    in test_hitl_gate_after_sizing.py."""
    me, gate = _drive(hitl_enabled=True, should_hold_returns=False)
    gate.assert_not_awaited()
    me.get_active_tenant_clients.assert_awaited_once()
