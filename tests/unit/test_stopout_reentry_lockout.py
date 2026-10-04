"""#2554 -> #3604: the exit-side re-entry brake, now in TRADING DAYS and persisted.

The minute version (STOPOUT_REENTRY_COOLDOWN_MIN, 240 min since #2721) stopped the
same-afternoon carousel but not the next-morning re-buy (-1,769 $, n=14, measured
2026-09-23). Full behaviour tests: tests/unit/test_reentry_lockout_3604.py. This file
keeps the original three scenarios alive under the new unit.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core.engine.reentry_lockout import ReentryLockout
from core.engine.trading_loop import TradingLoopMixin

TUE = datetime(2026, 9, 22, 18, 1, tzinfo=timezone.utc)  # Tue 14:01 ET


def _eng(tmp_path, days):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng._reentry_store = ReentryLockout(str(tmp_path / "reentry_lockout.json"))
    return eng, SimpleNamespace(REENTRY_LOCKOUT_DAYS=days, STOP_EXIT_SLOT_HOLD_DAYS=0)


def test_lockout_blocks_reentry_until_the_next_trading_day(tmp_path):
    eng, cfg = _eng(tmp_path, 1)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert "HOOD" in eng._stopout_reentry_locked({"HOOD"}, now=TUE)
        assert "HOOD" in eng._stopout_reentry_locked(
            set(), now=TUE + timedelta(hours=5)
        )
        wed = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
        assert "HOOD" not in eng._stopout_reentry_locked(set(), now=wed)


def test_lockout_refreshes_on_repeat_exit(tmp_path):
    eng, cfg = _eng(tmp_path, 1)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        eng._stopout_reentry_locked({"HOOD"}, now=TUE)
        wed = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
        eng._stopout_reentry_locked({"HOOD"}, now=wed)  # exited again on Wednesday
        assert "HOOD" in eng._stopout_reentry_locked(
            set(), now=wed + timedelta(hours=2)
        )
        thu = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
        assert "HOOD" not in eng._stopout_reentry_locked(set(), now=thu)


def test_lockout_zero_is_off(tmp_path):
    eng, cfg = _eng(tmp_path, 0)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._stopout_reentry_locked({"HOOD"}, now=TUE) == set()
