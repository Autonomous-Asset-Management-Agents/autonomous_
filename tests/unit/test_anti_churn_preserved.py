# tests/unit/test_anti_churn_preserved.py
# #2031 safety-invariant: the fix touches ONLY the decision layer (gatekeeper/veto).
# The execution-layer anti-churn guard — PortfolioManager.can_sell_position() with
# MIN_HOLD_HOURS — is UNCHANGED and must keep blocking a freshly-bought name from
# being sold immediately (no buy-then-quick-resell churn), while allowing a mature
# (weeks-held) position to exit. This pins that invariant so the #2031 fix cannot
# silently regress churn behaviour.

from datetime import datetime, timedelta

from core.portfolio_manager import PortfolioManager


def _pm(min_hold_hours: float = 1.0) -> PortfolioManager:
    # Construct without the broker-client __init__ — we exercise only the pure
    # can_sell_position() logic, which reads exactly these four attributes.
    pm = PortfolioManager.__new__(PortfolioManager)
    pm._trade_history = {}
    pm._consecutive_sell_signals = {}
    pm._consecutive_sell_threshold = 8
    pm._min_hold_hours = min_hold_hours
    return pm


def test_fresh_position_cannot_be_sold():
    pm = _pm()
    pm._trade_history["HOOD"] = [datetime.now()]  # bought just now
    ok, reason = pm.can_sell_position("HOOD")
    assert ok is False
    assert "hold" in reason.lower()


def test_weeks_held_position_can_be_sold():
    pm = _pm()
    pm._trade_history["HOOD"] = [datetime.now() - timedelta(days=7)]
    ok, _ = pm.can_sell_position("HOOD")
    assert ok is True


def test_consecutive_sell_bypass_allows_exit():
    pm = _pm()
    pm._trade_history["HOOD"] = [datetime.now()]  # fresh — would normally block
    pm._consecutive_sell_signals["HOOD"] = 8  # >= threshold → emergency exit
    ok, _ = pm.can_sell_position("HOOD")
    assert ok is True
