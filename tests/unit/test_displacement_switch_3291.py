"""#3291 — DISPLACEMENT_ENABLED master switch + consensus-retention on the swap path.

Even with ROTATION_EXIT_ENABLED off, the full-book DISPLACEMENT swap
(``should_open_new_position`` Case 2 → ``debate_position_swap``) still sells the weakest
holding — a BUY-rated name — to fund a new BUY. This adds:
  * ``DISPLACEMENT_ENABLED`` (default ON = byte-identical): OFF ⇒ a full book waits for a slot.
  * the shared ``consensus_retention_veto`` (``CONSENSUS_RETENTION_THRESHOLD``, default 0 = off)
    now guards the swap path too — a name the live round table still rates ≥ threshold is kept.
"""

from __future__ import annotations

import threading

import pytest

from core.portfolio_manager import OpportunityScore, PortfolioManager, PositionScore


def _pm():
    """PortfolioManager via __new__ with only the state these decision boundaries read."""
    pm = PortfolioManager.__new__(PortfolioManager)
    pm.max_positions = 10
    pm._position_scores = {}
    pm._trade_history = {}
    pm._last_rebalance = {}
    pm._max_trades_per_day = 8
    pm._min_order_interval_sec = 0
    pm._rebalance_cooldown_hours = 0.5
    pm._min_hold_hours = 1.0
    pm._consecutive_sell_signals = {}
    pm._consecutive_sell_threshold = 5
    pm._debate_history = []
    pm._history_lock = threading.RLock()
    pm._last_refresh_ok = True
    pm._topup_dead_band_pct = 0.0
    pm.total_capital = 100000.0
    pm._live_consensus = {}
    return pm


def _pos(symbol, days_held=25, score=40.0):
    return PositionScore(
        age_known=True,
        symbol=symbol,
        qty=30.0,
        avg_entry=100.0,
        current_price=100.0,
        market_value=3000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        total_score=score,
        days_held=days_held,
    )


def _opp(score=80.0, symbol="NVDA"):
    return OpportunityScore(symbol=symbol, current_price=500.0, total_score=score)


def _set(monkeypatch, **kw):
    """Override just the flags under test on the REAL config singleton (all other reads real)."""
    import config

    cfg = config.get_config()
    for k, v in kw.items():
        monkeypatch.setattr(cfg, k, v, raising=False)
    return cfg


def _fill_book(pm):
    pm._position_scores = {f"S{i}": _pos(f"S{i}") for i in range(pm.max_positions)}


# ---------------------------------------------------------------------------
# DISPLACEMENT_ENABLED master switch
# ---------------------------------------------------------------------------


def test_displacement_off_full_book_waits_for_a_slot(monkeypatch):
    _set(monkeypatch, DISPLACEMENT_ENABLED=False, BOOK_CAP_ENFORCEMENT_ENABLED=False)
    pm = _pm()
    _fill_book(pm)
    should_open, reason, close_sym = pm.should_open_new_position(_opp())
    assert should_open is False
    assert close_sym is None
    assert "displacement disabled" in reason.lower()


def test_displacement_on_full_book_still_swaps(monkeypatch):
    """Default ON ⇒ byte-identical: a strong candidate displaces the weakest holding."""
    _set(
        monkeypatch,
        DISPLACEMENT_ENABLED=True,
        CONSENSUS_RETENTION_THRESHOLD=0.0,
        BOOK_CAP_ENFORCEMENT_ENABLED=False,
    )
    pm = _pm()
    _fill_book(pm)
    should_open, reason, close_sym = pm.should_open_new_position(_opp(score=90.0))
    assert should_open is True, reason
    assert close_sym is not None


# ---------------------------------------------------------------------------
# Consensus retention on the swap path
# ---------------------------------------------------------------------------


def test_retention_keeps_a_still_backed_name(monkeypatch):
    _set(monkeypatch, DISPLACEMENT_ENABLED=True, CONSENSUS_RETENTION_THRESHOLD=0.65)
    pm = _pm()
    pm._live_consensus = {"PG": 0.72}  # board still backs it (> 0.65)
    should_swap, reason = pm.debate_position_swap(
        _opp(score=90.0), _pos("PG", days_held=25)
    )
    assert should_swap is False
    assert "retains" in reason.lower() or "consensus" in reason.lower()


def test_retention_off_is_byte_identical(monkeypatch):
    """Threshold 0 ⇒ gate off: a high live consensus does NOT keep the name."""
    _set(monkeypatch, DISPLACEMENT_ENABLED=True, CONSENSUS_RETENTION_THRESHOLD=0.0)
    pm = _pm()
    pm._live_consensus = {"PG": 0.99}
    should_swap, _ = pm.debate_position_swap(_opp(score=90.0), _pos("PG", days_held=25))
    assert should_swap is True
