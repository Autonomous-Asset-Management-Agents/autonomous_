# tests/unit/test_symbol_to_close_capture_3008.py
"""#3008 step 1 — record which incumbent a BUY displaced, on the decision row.

THE PROBLEM, in numbers. Measured on the live account (Alpaca fills, 24.07-21.08): ~30 % of
turnover is round-trip churn, 27 % of it NON-STOP (no `triggered_by_stop` row), and it ran WITH
the #2935 carousel fix active. So it is neither #3006 (trailing) nor the fixed #2935 carousel —
but the branch that produces it cannot be named, because `decisions.symbol_to_close` /
`portfolio_reason` ship empty.

Root cause (proven): `should_open_new_position` returns `symbol_to_close` and it drives the
displacement SELL, but it is never attached to the logged `DecisionContext`
(order_executor.py:2189). The column (database/models.py:70) and the dataclass field
(cloud_logger.DecisionContext.symbol_to_close) already exist.

This is observation-only: it populates two existing columns on the decision row and changes no
order. Structural (AST) wiring assertion on both execution paths — the same idiom as the #2811
diagnostics wiring test (`test_sizing_zero_reason.py::test_the_audit_detail_carries_the_specific_cause`),
because driving the full async `_process_signal_event` / `_execute_tenant_order` is intractable and
would test the harness, not the wiring.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from pathlib import Path

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.cloud_logger import DecisionContext  # noqa: E402
from core.engine.order_executor import OrderExecutorMixin  # noqa: E402

_PATHS = (
    OrderExecutorMixin._execute_tenant_order,
    OrderExecutorMixin._process_signal_event,
)


def _assigns_context_attr(method, attr: str) -> bool:
    """True if `method` contains an assignment `context.<attr> = <something>`."""
    src = textwrap.dedent(inspect.getsource(method))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == attr
                and isinstance(target.value, ast.Name)
                and target.value.id == "context"
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# The field already exists on the record; today it is never populated.
# ---------------------------------------------------------------------------


def test_decision_context_carries_the_field_and_defaults_empty():
    """ANTI-VACUITY: the capture is worthless if the transport field is absent. It must
    exist AND default empty, so an un-displacing BUY records no phantom close."""
    ctx = DecisionContext(symbol="NEW", action="BUY", current_price=100.0)
    assert ctx.symbol_to_close == ""
    assert ctx.portfolio_reason == ""


def test_reasoning_summary_surfaces_the_displaced_symbol():
    """Once populated, the human-readable summary names the displaced incumbent — the one
    existing consumer of the field (cloud_logger.py:175)."""
    ctx = DecisionContext(
        symbol="NEW", action="BUY", current_price=100.0, symbol_to_close="OLD"
    )
    assert "Close: OLD" in ctx.build_reasoning_summary()


# ---------------------------------------------------------------------------
# The wiring: BOTH execution paths must attach the swap result to the context
# that is logged. Fails today because the assignment does not exist.
# ---------------------------------------------------------------------------


def test_both_paths_record_symbol_to_close_on_the_logged_context():
    for method in _PATHS:
        assert _assigns_context_attr(method, "symbol_to_close"), (
            f"{method.__name__} never writes symbol_to_close onto `context` — the "
            "displacement it just decided is lost from the decision row (#3008)"
        )


def test_both_paths_record_portfolio_reason_on_the_logged_context():
    for method in _PATHS:
        assert _assigns_context_attr(method, "portfolio_reason"), (
            f"{method.__name__} never writes portfolio_reason onto `context` — the "
            "why of a portfolio decision is lost from the decision row (#3008)"
        )
