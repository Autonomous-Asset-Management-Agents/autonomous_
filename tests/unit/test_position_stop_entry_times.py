"""#2555: the per-cycle stop must use the CURRENT holding's entry-time, not
min(_trade_history) — which returns the oldest trade in the 30-day window and goes stale
across a sold-then-rebought symbol (inflated hours_held → panic-bypass + tightened
loss-cut on a fresh position).

The pure-function precedence/fallback is covered in test_position_stop.py; this file
covers the trading-loop ratchet: seed, lock-in, evict, rebuy-reseed, flag-off. Mirrors
the #2538 high-water-mark ratchet tests (test_position_stop_hwm.py).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core.engine.trading_loop import TradingLoopMixin


def _sn_pos(symbol):
    return SimpleNamespace(
        symbol=symbol, qty=1.0, avg_entry_price=1.0, current_price=1.0
    )


def _eng():
    return TradingLoopMixin.__new__(TradingLoopMixin)


def test_ratchet_seeds_locks_evicts_and_reseeds_on_rebuy():
    eng = _eng()
    cfg = SimpleNamespace(POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED=True)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        # First cycle: HOOD seeded from the reconciled trade_history (true entry, #1994);
        # AAPL has no history → stamped now().
        true_entry = datetime.now(timezone.utc) - timedelta(hours=50)
        m1 = eng._ratchet_entry_times(
            [_sn_pos("HOOD"), _sn_pos("AAPL")], {"HOOD": [true_entry]}
        )
        assert m1["HOOD"] == true_entry
        assert isinstance(m1["AAPL"], datetime)
        aapl_first = m1["AAPL"]

        # Held continuously → entry locked, immune to trade_history growth (record_trade
        # appending a later timestamp must NOT move the entry).
        m2 = eng._ratchet_entry_times(
            [_sn_pos("HOOD"), _sn_pos("AAPL")],
            {"HOOD": [true_entry, datetime.now(timezone.utc)]},
        )
        assert m2["HOOD"] == true_entry
        assert m2["AAPL"] == aapl_first

        # HOOD sold to flat → evicted from the map.
        m3 = eng._ratchet_entry_times([_sn_pos("AAPL")], {})
        assert "HOOD" not in m3

        # HOOD rebought (in-session, not first cycle) → FRESH entry, NOT the stale
        # true_entry that still lingers in trade_history.
        m4 = eng._ratchet_entry_times(
            [_sn_pos("AAPL"), _sn_pos("HOOD")], {"HOOD": [true_entry]}
        )
        assert m4["HOOD"] != true_entry
        assert (datetime.now(timezone.utc) - m4["HOOD"]) < timedelta(minutes=5)


def test_ratchet_disabled_returns_none_byte_identical():
    eng = _eng()
    cfg = SimpleNamespace(POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED=False)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._ratchet_entry_times([_sn_pos("HOOD")], {}) is None
