"""#2554: a stop-out / harvest must lock the symbol out of BUY re-entry for a cooldown, so
the round table can't re-buy a just-sold name within minutes (the churn carousel). The
only existing brakes are BUY-side cooldowns that never key on a stop-out; this adds a
stop-out-keyed lockout on the exit side.

Flag-gated STOPOUT_REENTRY_COOLDOWN_MIN (minutes; <= 0 → disabled → empty set, byte-
identical). Mirrors the #2538 HWM / #2555 entry-time ratchet test style.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core.engine.trading_loop import TradingLoopMixin


def _eng():
    return TradingLoopMixin.__new__(TradingLoopMixin)


def test_lockout_blocks_reentry_until_cooldown_expires():
    eng = _eng()
    cfg = SimpleNamespace(STOPOUT_REENTRY_COOLDOWN_MIN=15)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        now = datetime(2026, 7, 30, 15, 0, 0, tzinfo=timezone.utc)
        # HOOD just stopped out → armed.
        assert "HOOD" in eng._stopout_reentry_locked({"HOOD"}, now=now)
        # 10 min later, no new exit → still locked.
        assert "HOOD" in eng._stopout_reentry_locked(
            set(), now=now + timedelta(minutes=10)
        )
        # 16 min later → cooldown expired → free to re-enter.
        assert "HOOD" not in eng._stopout_reentry_locked(
            set(), now=now + timedelta(minutes=16)
        )


def test_lockout_refreshes_on_repeat_exit():
    eng = _eng()
    cfg = SimpleNamespace(STOPOUT_REENTRY_COOLDOWN_MIN=15)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        now = datetime(2026, 7, 30, 15, 0, 0, tzinfo=timezone.utc)
        eng._stopout_reentry_locked({"HOOD"}, now=now)
        # Re-exited at +10 min → cooldown restarts from there, so +20 min is still locked.
        eng._stopout_reentry_locked({"HOOD"}, now=now + timedelta(minutes=10))
        assert "HOOD" in eng._stopout_reentry_locked(
            set(), now=now + timedelta(minutes=20)
        )


def test_lockout_disabled_by_default_returns_empty():
    eng = _eng()
    cfg = SimpleNamespace(STOPOUT_REENTRY_COOLDOWN_MIN=0)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert (
            eng._stopout_reentry_locked({"HOOD"}, now=datetime.now(timezone.utc))
            == set()
        )
