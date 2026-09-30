# tests/unit/test_dust_exit_floor.py
"""#2789 — a RELATIVE dust floor at the submit paths, so exit dust is suppressed
without blocking small accounts.

WHY RELATIVE. #2714 found "kein Notional-Floor an den Submit-Stellen". #2721 answered
by raising the ENTRY knob MIN_ORDER_VALUE_USD to $50 — the wrong instrument twice over:
its only exit-side consumer is the trim lever (DECONCENTRATION_TRIM_ENABLED default
False), so it suppressed no exit dust at all, while a flat $50 blocked every account
under ~$1,000 (reverted in #2784). Dust originates at the EXITS by construction:
displacement remainders, rotation part-sells, trim slivers, partial fills.

WHY BOTH PATHS. order_executor.py builds requests in two disjoint places —
`_execute_tenant_order` (:1364/:1373) and `_process_signal_event` (:2501), the latter
marked in the source as "the desktop/[Global] path — this is the one the 2026-07-28
rotation flush burned through". A floor wired to one path would be inert on desktop,
i.e. the #2721 mistake in a new costume. The AST section below pins both.

SAFETY INVARIANTS, and they outrank the churn goal:
  * protective stops (exit_kind "risk") and unlabeled paths (None) are NEVER skipped;
  * a FULL close is never skipped — otherwise the floor traps the remaining position;
  * the floor only ever SKIPS, never enlarges an order (ADR-016 approved-quantity).

TDD Red→Green (CLAUDE.md §5.1).
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.dust_floor import dust_exit_skip  # noqa: E402
from core.engine.order_executor import (  # noqa: E402
    OrderExecutorMixin,
    _dust_floor_skips_exit,
)

# Plan defaults; passed explicitly so a config change cannot silently re-tune the test.
PCT_POS = 0.10
PCT_EQ = 0.001


def _skip(**kw):
    """dust_exit_skip with the armed, ordinary-trim baseline; override per scenario."""
    args = {
        "enabled": True,
        "notional": 12.0,
        "position_value": 5_000.0,
        "equity": 100_000.0,
        "exit_kind": "trim",
        "is_full_close": False,
        "min_position_pct": PCT_POS,
        "min_equity_pct": PCT_EQ,
    }
    args.update(kw)
    return dust_exit_skip(**args)


# ---------------------------------------------------------------------------
# 1. The behaviour it exists for
# ---------------------------------------------------------------------------


def test_dust_partial_exit_is_suppressed():
    """12 $ off a 5,000 $ position = 0.24 % — a sliver, not a portfolio action."""
    skip, reason = _skip()
    assert skip is True
    assert (
        "position" in reason.lower()
    ), f"reason must name the binding rule: {reason!r}"


def test_small_account_is_not_blocked():
    """A 55 $ account trimming 3 $ off a 20 $ position clears both relative rules.

    This is requirement (a): the floor must be unable to block a small account. 3 $ is
    15 % of the position (> 10 %) and 5.5 % of equity (> 0.1 %).
    """
    skip, _ = _skip(notional=3.0, position_value=20.0, equity=55.0)
    assert skip is False


@pytest.mark.parametrize("kind", [None, "risk"])
def test_protective_and_unlabeled_exits_are_never_suppressed(kind):
    """A stop-out below every threshold must still submit — trapping a position is
    strictly more dangerous than the dust it would avoid. `None` keeps the #2713
    fail-safe exemption for paths not yet labeled."""
    skip, _ = _skip(notional=4.0, exit_kind=kind)
    assert skip is False


def test_full_close_is_never_suppressed():
    """A 6 $ remainder must be closable, or the floor locks the position in."""
    skip, _ = _skip(notional=6.0, is_full_close=True)
    assert skip is False


def test_disabled_flag_is_byte_identical():
    skip, reason = _skip(enabled=False)
    assert skip is False
    assert reason == ""


# ---------------------------------------------------------------------------
# 2. Rule interaction — the part that is easy to get subtly wrong
# ---------------------------------------------------------------------------


def test_position_rule_binds_where_the_equity_backstop_would_allow():
    """ANTI-VACUITY for rule A: 150 $ passes the equity backstop (0.1 % of 100k = 100 $)
    but not the position rule (10 % of 5,000 = 500 $). If A were not evaluated, this
    case would come back allowed — so it proves A is on the path, not just B."""
    skip, reason = _skip(notional=150.0)
    assert skip is True
    assert "position" in reason.lower()


def test_equity_backstop_applies_when_position_value_is_unknown():
    """position_value 0 (context missing) → rule A cannot be evaluated; the backstop
    still catches an absurdly small order."""
    skip, reason = _skip(notional=50.0, position_value=0.0)
    assert skip is True
    assert "equity" in reason.lower()

    # ...and it must not swallow a legitimate order in the same unknown-position state.
    skip_ok, _ = _skip(notional=200.0, position_value=0.0)
    assert skip_ok is False


def test_backstop_is_strictly_weaker_than_the_position_rule():
    """The backstop must never become the binding rule for an ordinary book.

    With 10 equal-weight positions a position is ~10 % of equity, so rule A binds at
    ~1 % of equity and the backstop at 0.1 % — 10x weaker. A stronger backstop would
    suppress legitimate de-concentration trims on large accounts: the flat-floor
    mistake of #2721, merely dressed up as a relative one.
    """
    equity = 100_000.0
    position_value = equity / 10
    assert PCT_EQ * equity < PCT_POS * position_value


def test_floor_never_enlarges_an_order():
    """The contract is skip/allow only — a size can never come back from this function,
    so it cannot inflate an order past what was approved (ADR-016)."""
    result = _skip()
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], bool) and isinstance(result[1], str)


# ---------------------------------------------------------------------------
# 3. Wiring — both submit paths, or the floor is inert where it matters most
# ---------------------------------------------------------------------------


def _called_names(func) -> set[str]:
    """Every function/attribute name called inside func (idiom: test_hitl_gate_after_sizing)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (
            f.attr
            if isinstance(f, ast.Attribute)
            else (f.id if isinstance(f, ast.Name) else "")
        )
        if name:
            names.add(name)
    return names


@pytest.mark.parametrize(
    "method",
    [
        OrderExecutorMixin._execute_tenant_order,
        OrderExecutorMixin._process_signal_event,
    ],
    ids=["tenant_path", "desktop_global_path"],
)
def test_both_submit_paths_consult_the_floor(method):
    """Link 1 of 2: each path calls the adapter. Link 2 is the next test — together they
    prove the pure decision is reached from both paths."""
    names = _called_names(method)
    # ANTI-VACUITY: prove the AST walk actually sees this method's calls at all.
    assert "getattr" in names, "AST walk found no calls — the parse target is wrong"
    assert "_dust_floor_skips_exit" in names, (
        f"{method.__name__} does not consult the dust floor — a floor wired to only "
        "one path is inert on the other (the #2721 failure mode)"
    )


def test_adapter_reaches_the_pure_decision():
    """Link 2 of 2."""
    assert "dust_exit_skip" in _called_names(_dust_floor_skips_exit)


def test_floor_runs_after_the_iron_dome_and_before_record_trade():
    """Position in the compliance chain is load-bearing, in both directions.

    AFTER the Iron Dome: compliance is a hard rail, a product guard must not pre-empt it.
    BEFORE `record_trade`: that call increments the churn budget, so skipping afterwards
    would spend a trade the broker never saw. Pinned per path because the two chains are
    written differently (`approved` flag vs early `return`).
    """
    for method in (
        OrderExecutorMixin._execute_tenant_order,
        OrderExecutorMixin._process_signal_event,
    ):
        src = textwrap.dedent(inspect.getsource(method))
        i_dome = src.index("check_order(")
        i_floor = src.index("_dust_floor_skips_exit(")
        # QUALIFIED on purpose: the tenant path also calls `pm.record_trade(...)` for
        # displacement bookkeeping, earlier and unrelated. The churn budget is the
        # guardian's.
        i_record = src.index("compliance_guardian.record_trade(")
        assert i_dome < i_floor < i_record, (
            f"{method.__name__}: floor must sit between the Iron Dome and record_trade "
            f"(dome={i_dome}, floor={i_floor}, record={i_record})"
        )


def test_displacement_close_is_submitted_before_the_floor_can_see_it():
    """A displacement's closing leg must stay ungated — it is a FULL close of a name the
    book is giving up, and delaying it after a BUY already filled would leave the account
    over-exposed. It submits inside the `symbol_to_close` block, upstream of the floor;
    this pins that ordering so a later refactor cannot route it through the guard.
    """
    src = textwrap.dedent(inspect.getsource(OrderExecutorMixin._execute_tenant_order))
    assert src.index("req_close") < src.index("_dust_floor_skips_exit(")


# ---------------------------------------------------------------------------
# 4. The adapter's mapping — where real bugs live, so behavioural not structural
# ---------------------------------------------------------------------------


@pytest.fixture
def _armed(monkeypatch):
    """Arm the floor with the plan defaults on the module the adapter actually reads."""
    import config

    monkeypatch.setattr(config, "DUST_EXIT_FLOOR_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "DUST_EXIT_MIN_POSITION_PCT", PCT_POS, raising=False)
    monkeypatch.setattr(config, "DUST_EXIT_MIN_EQUITY_PCT", PCT_EQ, raising=False)


def _adapt(**kw):
    from types import SimpleNamespace

    args = {
        "event": SimpleNamespace(
            triggered_by_stop=False, portfolio_reason="trim: drift"
        ),
        "context": SimpleNamespace(current_price=100.0),
        "qty": 0.12,  # 12 $ off a 5,000 $ position
        "held_qty": 50.0,
        "symbol": "AAPL",
        "equity": 100_000.0,
    }
    args.update(kw)
    return _dust_floor_skips_exit(**args)


def test_adapter_converts_qty_and_price_into_notional(_armed):
    """qty*price vs held_qty*price — an inverted or missing multiplication here would
    make the whole guard nonsense while every pure-function test stayed green."""
    assert _adapt() is True  # 12 $ < 10 % of 5,000 $
    assert _adapt(qty=6.0) is False  # 600 $ > 500 $ threshold


def test_adapter_treats_a_rounded_sell_everything_as_a_full_close(_armed):
    """A float-rounded full exit must not be mistaken for dust, or the last sliver of a
    position becomes unsellable."""
    assert _adapt(qty=49.999, held_qty=50.0) is False


def test_adapter_never_skips_a_stop_exit(_armed):
    from types import SimpleNamespace

    stop = SimpleNamespace(triggered_by_stop=True, portfolio_reason="stop_loss")
    assert _adapt(event=stop, qty=0.01) is False


def test_adapter_fails_open_without_a_price(_armed):
    """No price → notional 0 → not this guard's business; never block an exit on missing
    market data."""
    from types import SimpleNamespace

    assert _adapt(context=SimpleNamespace(current_price=0.0)) is False
    assert _adapt(context=None) is False


def test_adapter_records_the_skip_so_it_is_visible(_armed):
    """A suppressed de-concentration wish must not vanish silently."""
    seen = []
    assert _adapt(rec_outcome=lambda *a: seen.append(a)) is True
    assert seen and seen[0][1] == "skipped:dust_floor", seen


def test_adapter_survives_a_raising_outcome_sink(_armed):
    """Observation must never raise into the trading path (`_rec_outcome` contract)."""

    def _boom(*_a):
        raise RuntimeError("sink down")

    assert _adapt(rec_outcome=_boom) is True


def test_adapter_is_inert_when_the_flag_is_off(monkeypatch):
    import config

    monkeypatch.setattr(config, "DUST_EXIT_FLOOR_ENABLED", False, raising=False)
    assert _adapt() is False
