"""Desktop exit fix: ``plan_position_stops`` must feed a REAL high-water-mark into
``evaluate_position_stop`` so ``intelligent_exit``'s trailing-stop factor can fire on a winner that
rolled over. Without a remembered peak the hwm defaults to ``max(entry, current) = current`` → the
drawdown from the high is always 0 → the trailing stop can NEVER fire. That is the live bug: DDOG
+152 % (and every held winner) is never trimmed, so cash stays stuck.
See docs/desktop-exit-eval/implementation_plan.md.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.position_stop import plan_position_stops


def _pos(symbol, qty, avg, current):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg,
        "current_price": current,
    }


def _history(symbol, hours_ago):
    return {symbol: [datetime.now(timezone.utc) - timedelta(hours=hours_ago)]}


@pytest.fixture(autouse=True)
def _mock_legacy_config(monkeypatch):
    import config as _cfg

    monkeypatch.setattr(_cfg, "TRAILING_FROM_PEAK_PCT", 8.0, raising=False)
    monkeypatch.setattr(_cfg, "TRAILING_STOP_PCT", 1.5, raising=False)
    monkeypatch.setattr(_cfg, "EXIT_TRAIL_PROFILE", "legacy", raising=False)


def test_trailing_stop_fires_when_real_hwm_supplied():
    """Winner: entry 100, peaked at 200 (+100 %), now 180 (+80 %, a 10 % drawdown from the peak).
    With the remembered peak supplied, the trailing stop (8 % tier at +35 %+) fires → SELL.
    """
    pos = _pos("DDOG", 10, 100.0, 180.0)
    plans = plan_position_stops(
        [pos], _history("DDOG", 100), high_water_marks={"DDOG": 200.0}
    )
    assert len(plans) == 1, plans
    assert plans[0]["symbol"] == "DDOG"
    assert "TRAILING" in plans[0]["reason"].upper()


def test_no_hwm_is_todays_behaviour_no_false_sell():
    """Same winner but WITHOUT a remembered peak → hwm defaults to current → drawdown 0 → no exit.
    This is the byte-identical flag-OFF / #2066-today behaviour."""
    pos = _pos("DDOG", 10, 100.0, 180.0)
    plans = plan_position_stops([pos], _history("DDOG", 100))
    assert plans == []


def test_hwm_below_current_does_not_false_trigger():
    """A hwm below the current price (a new high) must not create a negative/false drawdown → no sell."""
    pos = _pos("DDOG", 10, 100.0, 180.0)
    plans = plan_position_stops(
        [pos], _history("DDOG", 100), high_water_marks={"DDOG": 150.0}
    )
    assert plans == []


# ── trading-loop ratchet: maintain the per-symbol high-water-mark across cycles ─────────────
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from core.engine.trading_loop import TradingLoopMixin  # noqa: E402


def _sn_pos(symbol, current):
    return SimpleNamespace(
        symbol=symbol, qty=1.0, avg_entry_price=1.0, current_price=current
    )


def test_ratchet_up_and_evict_when_enabled():
    """The hwm map ratchets UP (max) per symbol and evicts symbols no longer held."""
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    cfg = SimpleNamespace(POSITION_EXIT_HWM_TRAILING_ENABLED=True)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        m1 = eng._ratchet_high_water_marks(
            [_sn_pos("DDOG", 100.0), _sn_pos("AAPL", 50.0)]
        )
        assert m1 == {"DDOG": 100.0, "AAPL": 50.0}
        # DDOG rises → peak up; AAPL falls → peak kept.
        m2 = eng._ratchet_high_water_marks(
            [_sn_pos("DDOG", 120.0), _sn_pos("AAPL", 45.0)]
        )
        assert m2 == {"DDOG": 120.0, "AAPL": 50.0}
        # AAPL no longer held → evicted; DDOG keeps its remembered peak.
        m3 = eng._ratchet_high_water_marks([_sn_pos("DDOG", 110.0)])
        assert m3 == {"DDOG": 120.0}
        assert "AAPL" not in eng._position_high_water_marks


def test_ratchet_disabled_returns_none_byte_identical():
    """Flag OFF → returns None → plan_position_stops falls back to today's behaviour."""
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    cfg = SimpleNamespace(POSITION_EXIT_HWM_TRAILING_ENABLED=False)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._ratchet_high_water_marks([_sn_pos("DDOG", 100.0)]) is None
