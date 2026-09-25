# tests/unit/test_sim_mark_to_market.py
# #2632 — the sim never marked HELD positions, so its P&L answered the wrong question.
#
# FOUND BY RUNNING THE FIRST REAL A/B (old vs new rotation parameters, 2026-08-06):
#   ALT (cap 3): 3 SELLs (AAPL, KO, PG) → P&L +193.40
#   NEU (cap 2): 2 SELLs (AAPL, KO)     → P&L +102.50      Δ -90.90
# The delta is EXACTLY PG: prior-day close 144.97 → sim-day close 148.00 = 3.03/share × 30 = 90.90.
#
# Mechanism: `DummyPosition.__init__` sets `current_price = avg_entry_price` and
# `unrealized_pl/plpc = "0"`. A `mark(price)` method exists for exactly this purpose but is
# NEVER CALLED anywhere in the codebase (nor in any test) — dead code. So:
#   * a SOLD position converts to cash at the sim-day price → the prior→sim-day move IS booked;
#   * a HELD position stays priced at its entry → the same move is INVISIBLE.
# `broker._equity()` sums `qty * current_price`, so ending_equity systematically REWARDS SELLING
# on an up day (and would reward holding on a down day). Two arms that differ in how many exits
# they take can therefore never be compared on P&L — the metric is confounded with the very
# variable under test. That is the "rotation looks profitable" bias, manufactured by the harness.
#
# Second consequence, worse for exit work: every held position reports unrealized_plpc == 0 for the
# WHOLE run. `core/intelligent_exit.py` decides loss tiers and trailing stops off that number, so
# they can never fire. The runbook blamed the static daily bar — but an intraday corpus alone would
# NOT fix it: without mark(), positions read flat no matter how rich the price path is.
#
# The invariant these tests pin: selling a position AT the mark must not change equity. Any harness
# where it does is measuring its own accounting, not the strategy.

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _pos(symbol="PG", qty=30.0, price=144.97):
    from core.sim.fill_engine import DummyPosition

    return DummyPosition(symbol, qty, price)


def _broker(bars: dict, positions=None, cash=0.0):
    """VirtualLiveBroker with a stubbed data client returning `bars` (symbol → close)."""
    from core.sim.broker import VirtualLiveBroker

    b = VirtualLiveBroker.__new__(VirtualLiveBroker)
    b.cash = cash
    b.starting_equity = 100_000.0
    b.positions = dict(positions or {})
    b.orders = []
    b.clock = SimpleNamespace(
        current_time=__import__("datetime").datetime(2026, 8, 4, 14, 0)
    )

    class _DC:
        def __init__(self, data):
            self._data = data

        def get_stock_bars(self, request):
            syms = request.symbol_or_symbols
            syms = [syms] if isinstance(syms, str) else list(syms)
            return SimpleNamespace(
                data={
                    s: [SimpleNamespace(close=self._data[s])]
                    for s in syms
                    if s in self._data
                }
            )

    b.data_client = _DC(bars)
    b.fill_engine = SimpleNamespace(process_orders=lambda o: None)
    return b


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_held_position_is_marked_to_the_current_bar():
    b = _broker({"PG": 148.0}, {"PG": _pos()})
    b.mark_to_market()
    assert float(b.positions["PG"].current_price) == pytest.approx(148.0)


def test_marking_updates_unrealized_pl_so_exit_logic_can_see_it():
    """intelligent_exit reads unrealized_plpc for loss tiers / trailing — flat forever means
    those paths are untestable, whatever the corpus contains."""
    b = _broker({"PG": 148.0}, {"PG": _pos()})
    b.mark_to_market()
    pos = b.positions["PG"]
    assert float(pos.unrealized_pl) == pytest.approx((148.0 - 144.97) * 30)
    assert float(pos.unrealized_plpc) == pytest.approx(148.0 / 144.97 - 1.0)
    assert float(pos.market_value) == pytest.approx(148.0 * 30)


def test_equity_counts_the_move_on_a_held_position():
    """The bug in one line: 30 PG bought at 144.97 and worth 148.00 must show +90.90."""
    b = _broker({"PG": 148.0}, {"PG": _pos()})
    before = b._equity()
    b.mark_to_market()
    assert b._equity() - before == pytest.approx(90.90, abs=0.01)


def test_selling_at_the_mark_leaves_equity_unchanged():
    """THE invariant that was violated, and the reason the A/B was unusable: an exit must
    move value between position and cash, not create it."""
    b = _broker({"PG": 148.0}, {"PG": _pos()})
    b.mark_to_market()
    equity_holding = b._equity()

    # simulate the exit at the mark
    b.cash += 30 * 148.0
    del b.positions["PG"]

    assert b._equity() == pytest.approx(equity_holding)


def test_equity_is_invariant_to_exit_count_across_arms():
    """Two arms, same prices, different number of exits → identical equity. Previously the
    arm that sold more looked better purely because holds were priced at entry."""
    bars = {"PG": 148.0, "KO": 60.0}
    arm_a = _broker(bars, {"PG": _pos(), "KO": _pos("KO", 50.0, 58.0)})
    arm_b = _broker(bars, {"PG": _pos(), "KO": _pos("KO", 50.0, 58.0)})

    arm_a.mark_to_market()
    arm_b.mark_to_market()
    # arm B additionally exits PG at the mark
    arm_b.cash += 30 * 148.0
    del arm_b.positions["PG"]
    arm_b.mark_to_market()

    assert arm_a._equity() == pytest.approx(arm_b._equity())


# ---------------------------------------------------------------------------
# Robustness — marking must never be able to kill a run
# ---------------------------------------------------------------------------


def test_symbol_without_a_bar_keeps_its_last_price():
    """A corpus gap must leave the stale mark in place, not zero the position out (which
    would read as a total loss and could trip risk gates)."""
    b = _broker({}, {"PG": _pos()})
    b.mark_to_market()
    assert float(b.positions["PG"].current_price) == pytest.approx(144.97)


def test_marking_survives_a_broken_data_client():
    b = _broker({"PG": 148.0}, {"PG": _pos()})

    def _explode(request):
        raise RuntimeError("corpus unavailable")

    b.data_client.get_stock_bars = _explode
    b.mark_to_market()  # must not raise
    assert float(b.positions["PG"].current_price) == pytest.approx(144.97)


def test_marking_an_empty_book_is_a_noop():
    b = _broker({"PG": 148.0}, {})
    b.mark_to_market()
    assert b.positions == {}


def test_non_numeric_bar_is_ignored():
    b = _broker({"PG": "n/a"}, {"PG": _pos()})
    b.mark_to_market()
    assert float(b.positions["PG"].current_price) == pytest.approx(144.97)


def test_position_without_mark_method_is_skipped():
    """Duck-typed positions appear in tests and fixtures; marking must tolerate them."""
    b = _broker(
        {"PG": 148.0}, {"PG": SimpleNamespace(qty="30", current_price="144.97")}
    )
    b.mark_to_market()  # must not raise
    assert float(b.positions["PG"].current_price) == pytest.approx(144.97)


# ---------------------------------------------------------------------------
# Wiring — the mark has to actually happen during a run
# ---------------------------------------------------------------------------


def test_sim_driver_marks_the_book_each_cycle():
    """Marking once at the end would still leave every intra-run exit decision blind."""
    import inspect

    from core.engine.trading_loop import TradingLoopMixin

    src = inspect.getsource(TradingLoopMixin.live_trading_loop)
    assert "mark_to_market" in src, "the per-cycle sim driver must mark the book"


def test_runner_marks_before_reading_the_final_equity():
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner.run_sim)
    mark = src.find("mark_to_market")
    equity = src.find("ending_equity = ")
    assert mark != -1, "run_sim must mark before it reports equity"
    assert mark < equity, "the final mark must precede reading ending_equity"
