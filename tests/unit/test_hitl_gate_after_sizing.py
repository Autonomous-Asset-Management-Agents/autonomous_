# tests/unit/test_hitl_gate_after_sizing.py
# #2704 — the HITL gate must decide AFTER the order size is known.
#
# THE DEFECT. The gate judged an order's VALUE 204 lines before the value existed:
#
#   order_executor.py:1758   hitl_gate.should_hold(...)     <- decided
#                :1962       qty = <sizer result>            <- size exists only here
#
# Board signals carry no quantity by construction (round_table/runner.py:1044-1048 — the board
# decides WHETHER, the executor HOW MUCH), so `suggested_quantity` was the 0.0 default
# (core/events.py:20) => order_value 0 => the `unknown_value` branch (hitl_gate.py:363).
# Measured live on 2026-08-06 with HITL on:
#   1. 13 queued orders, every one showing Qty 0 — not approvable.
#   2. The ADR-016 ceiling was skipped on approval, because it reads
#      `if _approved_qty > 0` (order_executor.py:996-1001) and the queued qty was 0 — so
#      confirming "TEL BUY" could execute a full slot the operator never saw.
#   3. All 13 arrived at once, because cash filter / book cap / cooldown / displacement all run
#      AFTER the gate. The operator was asked to triage a list the system itself would have cut
#      down to one or two.
#
# THE FIX is positional, not arithmetic: move the gate to a point where the real quantity exists
# and nothing has been mutated yet, and pass that quantity explicitly (`calculated_qty`).
#
# WHY EXPLICIT AND NOT `event.suggested_quantity = qty` (Dual Design, Option B — rejected):
# the field is read downstream (order_executor.py:1715 and the ADR-016 ceiling at :996-1001), so
# mutating it changes behaviour outside this change's radius, and the audit could no longer tell a
# board-supplied quantity from a sizer-derived one. For an Art-14 control point an implicit side
# effect is the wrong direction.
#
# WHY NOT AT LINE 954 IN THE TENANT PATH: an audit review proposed placing the gate after the
# buying-power pre-check. Verified by indentation, line 954 (36 spaces) sits INSIDE
# `if symbol_to_close:` (line 884, 16 spaces). There, only orders that trigger a displacement
# would be gated — every order with room in the book, the common case, would execute UNGATED.
# That is strictly worse than the bug being fixed. `test_gate_precedes_the_displacement_block`
# and `test_order_without_displacement_is_still_gated` pin this.

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.engine.order_executor import OrderExecutorMixin  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Quantity propagation — the interface the fix depends on
# ---------------------------------------------------------------------------


def _event(qty=0.0, action="BUY", position_qty=0.0, price=100.0):
    ctx = SimpleNamespace(
        current_price=price, position_qty=position_qty, conviction_score=0.7
    )
    return SimpleNamespace(
        symbol="AAPL",
        action=action,
        suggested_quantity=qty,
        is_simulation=False,
        decision_context=ctx,
    )


@pytest.fixture
def _all_manual(monkeypatch):
    """Both limits 0 = the documented all-manual pair; the mode #2700 leaves in place."""
    import config
    import core.hitl_gate as gate

    cfg = SimpleNamespace(
        HITL_ENABLED=True,
        HITL_AUTONOMOUS_UNLIMITED=False,
        HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS=False,
        HITL_MAX_VALUE_PER_TRADE=0.0,
        HITL_MAX_VALUE_PER_DAY=0.0,
        HITL_EXPIRY_SECONDS=900,
    )
    monkeypatch.setattr(config, "get_config", lambda: cfg, raising=False)
    monkeypatch.setattr(gate, "get_config", lambda: cfg, raising=False)
    return cfg


def _capture_queued(monkeypatch):
    """Record what the gate pushes into the approval queue, without touching storage."""
    import core.hitl_gate as gate

    seen: dict = {}

    async def _fake_queue(event, user_id, qty, price, order_value, phash, breached):
        seen.update(
            qty=qty,
            price=price,
            order_value=order_value,
            breached=breached,
            symbol=event.symbol,
        )

    monkeypatch.setattr(gate, "_queue_and_audit", _fake_queue, raising=False)
    monkeypatch.setattr(gate.HitlQueue, "has_pending", AsyncMock(return_value=False))
    return seen


def test_calculated_qty_is_used_instead_of_the_empty_event(_all_manual, monkeypatch):
    """The whole point: a board signal carries qty 0, the sizer says 42 — the gate must judge 42."""
    import core.hitl_gate as gate

    seen = _capture_queued(monkeypatch)
    held = asyncio.run(gate.should_hold(_event(qty=0.0), "global", calculated_qty=42.0))
    assert held is True  # all-manual holds either way
    assert seen["qty"] == pytest.approx(
        42.0
    ), "the queue must carry the REAL quantity, not 0"
    assert seen["order_value"] == pytest.approx(4200.0)
    assert (
        seen["breached"] != "unknown_value"
    ), "with a known quantity this is a normal all-manual hold, not an unvaluable order"


def test_without_calculated_qty_the_event_is_still_used(_all_manual, monkeypatch):
    """Byte-identical for every existing caller — the parameter defaults to None."""
    import core.hitl_gate as gate

    seen = _capture_queued(monkeypatch)
    asyncio.run(gate.should_hold(_event(qty=7.0), "global"))
    assert seen["qty"] == pytest.approx(7.0)


def test_signature_keeps_the_old_two_argument_call_valid():
    """An Art-14 control point must not break its callers; the new parameter is optional."""
    import core.hitl_gate as gate

    params = inspect.signature(gate.should_hold).parameters
    assert "calculated_qty" in params
    assert params["calculated_qty"].default is None


def test_zero_or_negative_calculated_qty_falls_back_to_the_event(
    _all_manual, monkeypatch
):
    """A sizer that produced nothing must not be mistaken for an authoritative 0."""
    import core.hitl_gate as gate

    seen = _capture_queued(monkeypatch)
    asyncio.run(gate.should_hold(_event(qty=3.0), "global", calculated_qty=0.0))
    assert seen["qty"] == pytest.approx(3.0)


def test_unknown_value_branch_survives_as_a_real_fallback(_all_manual, monkeypatch):
    """It stops being the normal case but remains the honest answer when nothing is known."""
    import core.hitl_gate as gate

    seen = _capture_queued(monkeypatch)
    held = asyncio.run(gate.should_hold(_event(qty=0.0, price=0.0), "global"))
    assert held is True
    assert seen["breached"] == "unknown_value"


def test_mode_c_still_short_circuits_before_any_valuation(monkeypatch):
    """Full autonomy must not be slowed or changed by the new parameter."""
    import config
    import core.hitl_gate as gate

    cfg = SimpleNamespace(
        HITL_ENABLED=True,
        HITL_AUTONOMOUS_UNLIMITED=True,
        HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS=False,
        HITL_MAX_VALUE_PER_TRADE=0.0,
        HITL_MAX_VALUE_PER_DAY=0.0,
        HITL_EXPIRY_SECONDS=900,
    )
    monkeypatch.setattr(config, "get_config", lambda: cfg, raising=False)
    monkeypatch.setattr(gate, "get_config", lambda: cfg, raising=False)
    monkeypatch.setattr(gate, "log_execution_event", AsyncMock(), raising=False)
    assert (
        asyncio.run(gate.should_hold(_event(qty=0.0), "global", calculated_qty=99.0))
        is False
    )


def test_gate_still_fails_closed_with_the_new_parameter(monkeypatch):
    """Fail-closed is the property that matters most now that the gate sits closer to the broker."""
    import core.hitl_gate as gate

    async def _boom(*a, **kw):
        raise RuntimeError("gate fault")

    monkeypatch.setattr(gate, "_decide", _boom, raising=False)
    assert asyncio.run(gate.should_hold(_event(), "global", calculated_qty=5.0)) is True


# ---------------------------------------------------------------------------
# 2. Placement — proved on the AST, not by text search
# ---------------------------------------------------------------------------


def _call_lines(func, needle: str) -> list[int]:
    """Line numbers of every call inside `func` whose dotted name ends with `needle`."""
    # dedent: a bound method's source is class-indented, which ast.parse rejects.
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (
            f.attr
            if isinstance(f, ast.Attribute)
            else (f.id if isinstance(f, ast.Name) else "")
        )
        if name == needle:
            out.append(node.lineno)
    return out


def _first(func, needle: str) -> int:
    lines = _call_lines(func, needle)
    assert lines, f"no call to {needle}() found in {func.__name__}"
    return min(lines)


def test_gate_is_not_called_before_sizing_in_the_desktop_path():
    """The regression guard. `should_hold` must come AFTER the sizing call, not before."""
    fn = OrderExecutorMixin._process_signal_event
    assert _first(fn, "_hitl_holds_order") > _first(
        fn, "calculate_position_size"
    ), "the gate still decides before the size exists — that is the whole defect"


def test_desktop_gate_sits_after_the_portfolio_manager():
    """Only orders that survived cash / book cap / cooldown / displacement may be queued —
    otherwise the operator is handed 13 candidates the system would itself have cut down.
    """
    fn = OrderExecutorMixin._process_signal_event
    assert _first(fn, "_hitl_holds_order") > _first(fn, "should_open_new_position")


def test_desktop_gate_sits_before_any_state_mutation():
    """A held order must not consume the daily trade budget."""
    fn = OrderExecutorMixin._process_signal_event
    assert _first(fn, "_hitl_holds_order") < _first(fn, "record_trade")


def test_tenant_gate_sits_after_sizing_and_the_portfolio_manager():
    fn = OrderExecutorMixin._execute_tenant_order
    assert _first(fn, "_hitl_holds_order") > _first(fn, "calculate_position_size")
    assert _first(fn, "_hitl_holds_order") > _first(fn, "should_open_new_position")


def test_gate_precedes_the_displacement_block_in_the_tenant_path():
    """THE audit-review correction. In the tenant path the displacement SELL reaches the broker
    at line 968 — BEFORE the compliance counter (1128). A gate placed after it would have sold a
    position to make room for an order that then waits for approval: the book one position
    shorter, for nothing.

    And the gate must sit OUTSIDE `if symbol_to_close:` — inside it, an order with room in the
    book (the common case) would never be gated at all.
    """
    fn = OrderExecutorMixin._execute_tenant_order
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    gate_line = _first(fn, "_hitl_holds_order")

    displacement_ifs = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Name)
        and n.test.id == "symbol_to_close"
    ]
    assert displacement_ifs, "could not locate `if symbol_to_close:` in the tenant path"
    assert gate_line < min(displacement_ifs), (
        "the gate must run BEFORE the displacement block — both so a held order never triggers "
        "a sell, and so it is not nested inside a branch that only some orders take"
    )

    # Structural proof it is not nested in that branch: the gate's indentation must be no deeper
    # than the `if symbol_to_close:` line's.
    lines = src.splitlines()
    offset = fn.__code__.co_firstlineno
    gate_indent = len(lines[gate_line - 1]) - len(lines[gate_line - 1].lstrip())
    disp_indent = len(lines[min(displacement_ifs) - 1]) - len(
        lines[min(displacement_ifs) - 1].lstrip()
    )
    assert gate_indent <= disp_indent, (
        f"gate at indent {gate_indent} is nested deeper than the displacement branch "
        f"({disp_indent}) — it would only gate displacement orders (offset {offset})"
    )


def test_the_held_outcome_is_recorded_in_the_one_shared_helper():
    """An audit review asked for the outcome recording to be DUPLICATED at both call sites.
    Extracting ONE helper is the stronger answer: the two paths cannot drift apart, so a held
    order can never end up queued-but-invisible in `decision_outcomes` on only one of them.
    """
    src = inspect.getsource(OrderExecutorMixin._hitl_holds_order)
    assert "hitl_held" in src
    assert "_capture_outcome" in src
    assert "should_hold" in src


def test_both_paths_go_through_that_helper():
    for fn in (
        OrderExecutorMixin._process_signal_event,
        OrderExecutorMixin._execute_tenant_order,
    ):
        assert _call_lines(
            fn, "_hitl_holds_order"
        ), f"{fn.__name__} does not consult the gate"


def test_the_helper_is_dormant_when_hitl_is_off():
    """Default-off must stay a single boolean check — no import, no gate call, no behaviour."""
    src = inspect.getsource(OrderExecutorMixin._hitl_holds_order)
    assert src.index("HITL_ENABLED") < src.index(
        "should_hold("
    ), "the flag must be checked before the gate is reached"


def test_gate_is_guarded_on_a_positive_size_in_both_paths():
    """In the tenant path `if size <= 0: return` only comes at 1003 — AFTER the displacement
    sell. Queueing a 0-size order would reproduce the very symptom being fixed, so each call
    site gates on a positive size itself."""
    for fn in (
        OrderExecutorMixin._process_signal_event,
        OrderExecutorMixin._execute_tenant_order,
    ):
        src = inspect.getsource(fn)
        gate_line = _first(fn, "_hitl_holds_order")
        # `_first` works on the DEDENTED source, whose line 1 is the `def` — so the index into
        # dedented lines is simply gate_line - 1. Look at the guard on the same statement.
        lines = textwrap.dedent(src).splitlines()
        window = "\n".join(lines[max(0, gate_line - 3) : gate_line])
        assert ("size > 0" in window) or (
            "qty > 0" in window
        ), f"{fn.__name__}: the gate call is not guarded on a positive size:\n{window}"


# ---------------------------------------------------------------------------
# 3. Behaviour at the new seam
# ---------------------------------------------------------------------------


class _Halt(BaseException):
    """BaseException so the method's own `except Exception` cannot swallow it."""


def test_hold_at_the_new_seam_prevents_submission(monkeypatch):
    """End of the line: with HITL on and the gate holding, nothing reaches the broker."""
    import config

    me = MagicMock(spec=OrderExecutorMixin)
    me.active_uid = None
    me._market_closed_blocks_order = AsyncMock(return_value=False)
    # The gate now sits past this boundary, so reaching it is EXPECTED; halt at the tenant fan-out
    # instead, which is where the old placement used to stop.
    me.get_active_tenant_clients = AsyncMock(side_effect=_Halt())

    cfg = SimpleNamespace(HITL_ENABLED=True, BYPASS_MARKET_HOURS=True)
    orig = config.get_config
    config.get_config = lambda: cfg
    gate = AsyncMock(return_value=True)
    try:
        with patch("core.hitl_gate.should_hold", gate):
            try:
                asyncio.run(
                    OrderExecutorMixin._process_signal_event(me, _event(qty=0.0))
                )
            except _Halt:
                pass
    finally:
        config.get_config = orig

    # The gate is no longer consulted before the fan-out — that is the change under test.
    gate.assert_not_awaited()
