"""#2066 Increment 1 — pure per-position stop evaluation.

Unit tests for `core.position_stop.evaluate_position_stop`, a pure wrapper around
`intelligent_exit.analyze_exit` that builds a PositionContext from the broker-
authoritative cost basis (`avg_entry_price`) + reconciled `entry_time` (#2046) and
returns the ExitAnalysis when a risk-exit fires, else None. No execution, no wiring.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import allure
import pytest

from core.position_stop import evaluate_position_stop

_NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_hard_stop_fires_on_deep_loss():
    # -10% (<= HARD_STOP_LOSS_PCT -8%), even inside the panic-protection window.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=90.0,
        avg_entry_price=100.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is not None
    assert res.should_sell is True


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_no_stop_within_panic_on_small_loss():
    # -5% (> hard stop) inside the panic window → no sell.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=95.0,
        avg_entry_price=100.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is None


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_no_stop_on_gain():
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=110.0,
        avg_entry_price=100.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is None


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_defensive_zero_entry_price():
    # Unknown/invalid cost basis → no false stop.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=50.0,
        avg_entry_price=0.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is None


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_defensive_missing_entry_time():
    # No reconciled entry_time (#2046 gap) → no false stop.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=50.0,
        avg_entry_price=100.0,
        entry_time=None,
        now=_NOW,
    )
    assert res is None


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_defensive_zero_current_price():
    # #2556: a fabricated/missing current_price of 0.0 must NOT manufacture a -100%
    # loss and fire a spurious hard stop on a healthy position. One-sided fail-safe:
    # bad price → no exit, never a false SELL.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=0.0,
        avg_entry_price=100.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is None


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_defensive_negative_current_price():
    # #2556: guard is symmetric — a negative price is equally implausible and must
    # not fire a stop.
    res = evaluate_position_stop(
        symbol="HOOD",
        current_price=-5.0,
        avg_entry_price=100.0,
        entry_time=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert res is None


# --- #2066 Increment 2: plan_position_stops (over a set of held positions) --------

from core.position_stop import plan_position_stops  # noqa: E402


def _pos(symbol, qty, avg, current):
    """Mirror the duck-typed Alpaca SDK ``Position`` (``.symbol``/``.qty``/…).

    The broker SDK returns *string* numeric fields (``qty="381"``); production code
    coerces via ``float(p.qty)`` (see ``api_routes.py``/``base.py``). ``plan_position_stops``
    relies on ``_f`` for the same coercion — ``test_plan_stops_coerces_alpaca_string_fields``
    exercises that path with production-shaped string values.
    """
    return SimpleNamespace(
        symbol=symbol, qty=qty, avg_entry_price=avg, current_price=current
    )


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_only_for_losers():
    positions = [
        _pos("HOOD", 381.0, 138.0, 110.0),  # -20% → hard stop
        _pos("AAPL", 10.0, 100.0, 112.0),  # +12% → no stop
    ]
    th = {"HOOD": [_NOW - timedelta(hours=1)], "AAPL": [_NOW - timedelta(hours=1)]}
    plans = plan_position_stops(positions, th, now=_NOW)
    syms = {p["symbol"] for p in plans}
    assert syms == {"HOOD"}
    plan = plans[0]
    assert plan["qty"] == 381.0
    assert plan["avg_entry_price"] == 138.0
    assert plan["reason"]  # non-empty stop reason


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_skips_zero_qty_and_missing_entry():
    positions = [
        _pos("HOOD", 0.0, 138.0, 90.0),  # qty<=0 → skip
        _pos("WDC", 10.0, 700.0, 500.0),  # loser but no entry_time → skip (defensive)
    ]
    th = {}  # no trade history at all
    plans = plan_position_stops(positions, th, now=_NOW)
    assert plans == []


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_skips_zero_current_price():
    # #2556: a fabricated 0.0 current_price (bad snapshot / missing price attr upstream)
    # must be skipped, not turned into a -100% hard-stop SELL of a real position.
    positions = [_pos("HOOD", 10.0, 100.0, 0.0)]
    th = {"HOOD": [_NOW - timedelta(hours=1)]}
    plans = plan_position_stops(positions, th, now=_NOW)
    assert plans == []


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_entry_times_overrides_stale_history():
    # #2555: a sold-then-rebought symbol keeps the OLD (already-closed) position's
    # timestamp in trade_history, so min() inflates hours_held → the fresh rebuy is
    # wrongly treated as an aged position (panic-protection bypassed, loss-cut tightened).
    # The reconciled current-holding entry (entry_times) must take precedence.
    positions = [_pos("HOOD", 10.0, 100.0, 94.0)]  # -6% loser (not a hard stop)
    stale = _NOW - timedelta(hours=26)  # an older, already-closed holding's entry
    th = {"HOOD": [stale]}
    fresh = _NOW - timedelta(
        minutes=20
    )  # the CURRENT rebuy — inside the 2h panic window

    # Control: min(history)=26h → outside the panic window → today's code cuts the loser.
    assert plan_position_stops(positions, th, now=_NOW), "control: stale min() cuts"

    # Fix: entry_times says the current holding is 20 min old → panic-protected → no cut.
    plans = plan_position_stops(positions, th, entry_times={"HOOD": fresh}, now=_NOW)
    assert plans == []


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_entry_times_falls_back_to_history_when_unmapped():
    # #2555: when a symbol is absent from entry_times, fall back to the legacy
    # min(trade_history) — byte-identical to the pre-fix behaviour (no regression).
    positions = [_pos("HOOD", 381.0, 138.0, 110.0)]  # -20% → hard stop
    th = {"HOOD": [_NOW - timedelta(hours=1)]}
    plans = plan_position_stops(positions, th, entry_times={"AAPL": _NOW}, now=_NOW)
    assert {p["symbol"] for p in plans} == {"HOOD"}


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_empty_positions():
    assert plan_position_stops([], {}, now=_NOW) == []


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
def test_plan_stops_coerces_alpaca_string_fields():
    # Production parity: the Alpaca SDK Position returns *string* numeric fields.
    # plan_position_stops must coerce them (via _f) — regressing that would silently
    # break every live stop. -20% loser with string qty/prices → one plan, numeric.
    pos = _pos("HOOD", "381", "138.0", "110.0")
    plans = plan_position_stops([pos], {"HOOD": [_NOW - timedelta(hours=1)]}, now=_NOW)
    assert len(plans) == 1
    plan = plans[0]
    assert plan["symbol"] == "HOOD"
    assert plan["qty"] == 381.0 and isinstance(plan["qty"], float)
    assert plan["avg_entry_price"] == 138.0 and isinstance(
        plan["avg_entry_price"], float
    )
    assert plan["current_price"] == 110.0
    assert plan["reason"]  # non-empty stop reason
