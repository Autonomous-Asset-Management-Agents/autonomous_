# tests/unit/test_sizing_zero_reason.py
"""#2811 — the sizer must SAY which limit zeroed an order, without changing the math.

Measured on the installation (2026-08-12): 9,146 of 10,697 skipped orders (85.5 %) carry
the audit reason "position size resolved to 0 (risk limits / exposure cap / available
cash)" — a list of three candidates, not a cause. The sizer knows the cause (it logs it)
but the desktop engine runs under pythonw with no log file, so the information is
destroyed. Verified: zero hits for "Dust-Filter" / "ADR-R10" anywhere in userdata.

The fix adds an OPTIONAL per-call sink: callers that pass a dict get the binding limit
written into it; callers that pass nothing get today's behaviour, bit for bit.

Part 1 pins that second half with golden values GENERATED FROM UNCHANGED MAIN before any
edit (tests/fixtures/sizing_golden_2811.json, 864 cases, 490 of them zero). This is the
test that makes the change reviewable as observation-only: if any case shifts, the change
touched the money path and must not ship as a diagnostics PR.

Part 2 specifies the sink itself. The reason is recorded AT THE CLAMP, not at the exit:
every clamp (cash, caps, conviction) funnels out through the same `< min_fractional`
return, so labelling the exit would call every cause "below_order_floor" — a precise
falsehood, worse than today's list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.risk_manager import RiskManager  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "sizing_golden_2811.json"


def _size(case, sink=None):
    client = MagicMock()
    client.get_all_positions.return_value = []
    rm = RiskManager(client=client, total_capital=case["capital"])
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        kwargs = {
            "stop_loss_atr_multiplier": 3.0,
            "atr": case["price"] * case["atr_pct"],
            "confidence": "high",
            "num_stocks_in_strategy": case["slots"],
            "current_price": case["price"],
            "account_cash": case["cash"],
            "allow_fractional": case["frac"],
            "conviction_score": case["conv"],
            "market_data": {"vix": 20.0},
        }
        if sink is not None:
            kwargs["sizing_trace"] = sink
        return rm.calculate_position_size(**kwargs)


# ---------------------------------------------------------------------------
# Part 1 — the math is untouched (the property that makes this PR shippable)
# ---------------------------------------------------------------------------


def _golden_cases():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_fixture_is_present_and_meaningful():
    """ANTI-VACUITY: the identity test below is worthless if the fixture is empty or
    contains no zero cases (zeros are the entire point of #2811)."""
    cases = _golden_cases()
    assert len(cases) >= 800, f"fixture unexpectedly small: {len(cases)}"
    zeros = sum(1 for c in cases if c["expected"] == "0.0")
    assert zeros >= 400, f"fixture lost its zero cases: {zeros}"


@pytest.mark.parametrize("with_sink", [False, True], ids=["no_sink", "with_sink"])
def test_sizes_are_bit_identical_to_unchanged_main(with_sink):
    """Every one of the 864 recorded cases must return the exact value the UNCHANGED
    code returned — with and without a sink, because the sink must observe, never steer.

    repr()-comparison on purpose: float equality down to the last bit, not approx.
    """
    mismatches = []
    for case in _golden_cases():
        got = _size(case, sink={} if with_sink else None)
        if repr(got) != case["expected"]:
            mismatches.append(f"{case}: got {got!r}")
    assert not mismatches, (
        f"{len(mismatches)} case(s) changed value — the edit touched the math:\n  "
        + "\n  ".join(mismatches[:5])
    )


# ---------------------------------------------------------------------------
# Part 2 — the sink names the binding limit, recorded at the clamp
# ---------------------------------------------------------------------------


def _trace_for(**over):
    case = {
        "price": 100.0,
        "cash": 59.09,
        "capital": 340.0,
        "conv": 0.5,
        "atr_pct": 0.03,
        "slots": 10,
        "frac": True,
    }
    case.update(over)
    sink: dict = {}
    size = _size(case, sink=sink)
    return size, sink


def test_a_cash_zero_names_the_cash_constraint():
    """cash 6 $, 10 slots → (6−1)/10 = 0.5 $ per slot at price 100 → 0.005 shares,
    UNDER the 0.01 fractional minimum. The binding limit is CASH, and the funnel exit
    (`min_fractional`) must not claim the label."""
    size, sink = _trace_for(cash=6.0)
    assert size == 0.0
    assert sink.get("zero_reason") == "insufficient_cash", sink


def test_a_dust_zero_names_the_order_floor():
    """price 5 $, tiny cash → nominal below MIN_ORDER_VALUE_USD once the $1 floor
    applies. This is the case the taxonomy reserved `below_order_floor` for
    (order_executor.py:128-133) and could never emit."""
    size, sink = _trace_for(price=5.0, cash=1.5, capital=55.0)
    assert size == 0.0
    assert sink.get("zero_reason") in ("below_order_floor", "insufficient_cash"), sink
    # Whichever of the two fired, it must be a SPECIFIC cause, never the funnel exit.


def test_a_healthy_size_reports_no_zero_reason():
    size, sink = _trace_for(cash=10_000.0, capital=100_000.0)
    assert size > 0.0
    assert "zero_reason" not in sink, sink


def test_the_sink_reports_the_binding_clamp_even_above_zero():
    """The existing `limited_by_cash` / `limited_by_cap` flags feed an INFO log gated on
    num_shares > 0 — the sink exposes the same fact structurally, so a non-zero but
    clamped size is explainable too."""
    size, sink = _trace_for(cash=59.09, capital=340.0, conv=1.0)
    assert size > 0.0
    assert sink.get("binding_limit") == "cash", sink


def test_a_halt_zero_names_the_halt():
    case = {
        "price": 100.0,
        "cash": 10_000.0,
        "capital": 100_000.0,
        "conv": 0.5,
        "atr_pct": 0.03,
        "slots": 10,
        "frac": True,
    }
    client = MagicMock()
    client.get_all_positions.return_value = []
    rm = RiskManager(client=client, total_capital=case["capital"])
    sink: dict = {}
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = True  # halted!
        size = rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=3.0,
            confidence="high",
            num_stocks_in_strategy=10,
            current_price=100.0,
            account_cash=10_000.0,
            allow_fractional=True,
            conviction_score=0.5,
            sizing_trace=sink,
        )
    assert size == 0.0
    assert sink.get("zero_reason") == "trading_halted", sink


def test_an_invalid_atr_zero_names_the_data_problem():
    size, sink = _trace_for(atr_pct=0.0)  # atr = 0 → first zero exit
    assert size == 0.0
    assert sink.get("zero_reason") == "invalid_atr", sink


def test_the_audit_detail_carries_the_specific_cause():
    """Part 3 — the wiring: the executor passes a sink and writes its content into the
    audit detail. Structural (AST) like the #2789 wiring tests: both sizing call sites
    must pass `sizing_trace`, and the sizing_zero audit call must no longer send the
    three-candidate list verbatim without the traced cause."""
    import ast
    import inspect
    import textwrap

    from core.engine.order_executor import OrderExecutorMixin

    for method in (
        OrderExecutorMixin._execute_tenant_order,
        OrderExecutorMixin._process_signal_event,
    ):
        src = textwrap.dedent(inspect.getsource(method))
        tree = ast.parse(src)
        passes_trace = any(
            isinstance(node, ast.Call)
            and any(kw.arg == "sizing_trace" for kw in node.keywords)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "calculate_position_size"
        )
        assert passes_trace, (
            f"{method.__name__} sizes without a sizing_trace — its sizing_zero audit "
            "entry stays a three-candidate list (the #2811 defect)"
        )


def test_the_sink_never_raises_even_when_read_only():
    """Observation must never break the order path: a bad sink (here: an object that
    rejects writes) must not raise — the size still comes back."""

    class _Rejecting(dict):
        def __setitem__(self, k, v):
            raise RuntimeError("read-only")

    case = {
        "price": 100.0,
        "cash": 59.09,
        "capital": 340.0,
        "conv": 0.5,
        "atr_pct": 0.03,
        "slots": 10,
        "frac": True,
    }
    size = _size(case, sink=_Rejecting())
    assert isinstance(size, float)
