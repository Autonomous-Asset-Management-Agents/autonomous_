"""#3604 — re-entry lockout in TRADING DAYS after a full exit, persisted.

Measured on the installed app's broker fills (23.09.2026): re-entries 4–24 h after a
full exit lost −1,769 $ (n=14), the 4-hour minute brake (#2554/#2721) no longer
covered them. REENTRY_LOCKOUT_DAYS replaces STOPOUT_REENTRY_COOLDOWN_MIN: a fully
exited name is not bought back before the next trading day (1) — whatever the exit
reason, including a SELL vote or a manual sell (position snapshot: held → gone).
Sells are never touched; the lockout can only delay a buy.
"""

import inspect
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.engine.reentry_lockout import ReentryLockout, lockout_until, trading_day_et
from core.engine.trading_loop import TradingLoopMixin

pytestmark = pytest.mark.vc3

ET_TUE_1401 = datetime(2026, 9, 22, 18, 1, tzinfo=timezone.utc)  # Tue 14:01 ET
ET_FRI_1500 = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)  # Fri 15:00 ET


# --- pure -------------------------------------------------------------------


def test_trading_day_is_the_et_calendar_day():
    late = datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc)  # 21:30 ET on the 22nd
    assert trading_day_et(late) == date(2026, 9, 22)


def test_one_day_means_until_the_next_trading_day():
    assert lockout_until(ET_TUE_1401, 1) == date(2026, 9, 23)
    assert lockout_until(ET_FRI_1500, 1) == date(2026, 9, 28)  # weekend skipped


def test_more_days_count_weekdays_only():
    assert lockout_until(ET_TUE_1401, 3) == date(2026, 9, 25)
    assert lockout_until(ET_FRI_1500, 2) == date(2026, 9, 29)


def test_zero_days_never_locks():
    assert lockout_until(ET_TUE_1401, 0) is None


# --- store -------------------------------------------------------------------


def _store(tmp_path):
    return ReentryLockout(str(tmp_path / "reentry_lockout.json"))


def test_locked_same_day_free_next_trading_day(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, ET_TUE_1401, days=1)
    assert s.active(ET_TUE_1401 + timedelta(hours=1)) == {"HOOD"}
    assert s.active(ET_TUE_1401 + timedelta(hours=9)) == {"HOOD"}  # 23:01 ET, same day
    next_open = datetime(2026, 9, 23, 13, 31, tzinfo=timezone.utc)  # Wed 09:31 ET
    assert s.active(next_open) == set()


def test_lockout_survives_a_restart(tmp_path):
    _store(tmp_path).arm({"HOOD"}, ET_TUE_1401, days=1)
    fresh = _store(tmp_path)  # a new engine process
    assert fresh.active(ET_TUE_1401 + timedelta(hours=2)) == {"HOOD"}


def test_repeat_exit_refreshes_and_expired_entries_are_evicted(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD", "ALL"}, ET_TUE_1401, days=1)
    wed = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    s.arm({"HOOD"}, wed, days=1)  # exited again on Wednesday
    assert s.active(wed) == {"HOOD"}  # ALL expired, HOOD re-armed
    blob = json.loads((tmp_path / "reentry_lockout.json").read_text(encoding="utf-8"))
    assert set(blob) == {"HOOD"}


def test_unreadable_file_is_fail_open_and_gets_rebuilt(tmp_path):
    p = tmp_path / "reentry_lockout.json"
    p.write_text("{not json", encoding="utf-8")
    s = _store(tmp_path)
    assert s.active(ET_TUE_1401) == set()
    s.arm({"HOOD"}, ET_TUE_1401, days=1)
    assert json.loads(p.read_text(encoding="utf-8"))["HOOD"]["until"] == "2026-09-23"


def test_a_simulated_past_never_sees_a_future_lockout(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, ET_TUE_1401, days=1)
    assert s.active(datetime(2024, 5, 1, tzinfo=timezone.utc)) == set()


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, ET_TUE_1401, days=1)
    assert [f.name for f in tmp_path.iterdir()] == ["reentry_lockout.json"]


# --- loop hook ---------------------------------------------------------------


def _eng(tmp_path, days=1, held=None):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng._reentry_store = _store(tmp_path)
    eng._reentry_prev_held = set(held or ())
    eng._reentry_partial_exits = set()
    return eng, SimpleNamespace(REENTRY_LOCKOUT_DAYS=days, STOP_EXIT_SLOT_HOLD_DAYS=0)


def test_hook_locks_a_stopped_name_for_the_trading_day(tmp_path):
    eng, cfg = _eng(tmp_path)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert "HOOD" in eng._stopout_reentry_locked({"HOOD"}, now=ET_TUE_1401)
        later = ET_TUE_1401 + timedelta(hours=3)
        assert "HOOD" in eng._stopout_reentry_locked(set(), now=later)
        wed = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
        assert "HOOD" not in eng._stopout_reentry_locked(set(), now=wed)


def test_hook_locks_a_name_that_vanished_from_the_book(tmp_path):
    """A SELL vote or a manual sell never reaches the stop/rotation sets — the
    position snapshot (held last cycle, gone now) is the exit witness."""
    eng, cfg = _eng(tmp_path, held={"HOOD", "ALL"})
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        eng._note_position_snapshot({"ALL": {"qty": 3.0}}, confirmed=True)
        locked = eng._stopout_reentry_locked(set(), now=ET_TUE_1401)
    assert locked == {"HOOD"}


def test_unconfirmed_snapshot_never_counts_as_an_exit(tmp_path):
    eng, cfg = _eng(tmp_path, held={"HOOD"})
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        eng._note_position_snapshot({}, confirmed=False)  # broker did not answer
        assert eng._stopout_reentry_locked(set(), now=ET_TUE_1401) == set()


def test_partial_exit_is_not_a_re_entry(tmp_path):
    eng, cfg = _eng(tmp_path, held={"HOOD"})
    eng._reentry_partial_exits = {"HOOD"}  # a deconcentration trim this cycle
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._stopout_reentry_locked({"HOOD"}, now=ET_TUE_1401) == set()


def test_zero_days_is_off_and_touches_no_file(tmp_path):
    eng, cfg = _eng(tmp_path, days=0)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._stopout_reentry_locked({"HOOD"}, now=ET_TUE_1401) == set()
    assert list(tmp_path.iterdir()) == []


def test_hook_reads_the_setting_without_a_silent_default():
    """Architecture ratchet (#3394): getattr(config, "NAME", default) hides a typo."""
    src = inspect.getsource(TradingLoopMixin._stopout_reentry_locked)
    assert "REENTRY_LOCKOUT_DAYS" in src
    assert (
        "getattr(" not in src.split("def _stopout_reentry_locked")[-1].split("days")[0]
    )
    assert "STOPOUT_REENTRY_COOLDOWN_MIN" not in src


def test_locked_candidates_are_removed_and_audited():
    import core.engine.trading_loop as tl

    src = inspect.getsource(tl)
    hook = src[src.index("reentry_locked = self._stopout_reentry_locked(") :]
    hook = hook[: hook.index("if not symbols_to_process:")]
    assert "blocked:reentry_lockout" in hook
    assert "_rec_outcome(" in hook


# --- settings plumbing -------------------------------------------------------


def test_setting_replaces_the_minutes_knob_everywhere():
    import os

    import config as _config
    from core.trading_settings import REGISTRY

    assert "REENTRY_LOCKOUT_DAYS" in REGISTRY
    assert (
        REGISTRY["REENTRY_LOCKOUT_DAYS"].default,
        REGISTRY["REENTRY_LOCKOUT_DAYS"].lo,
        REGISTRY["REENTRY_LOCKOUT_DAYS"].hi,
    ) == (1, 0, 10)
    assert "STOPOUT_REENTRY_COOLDOWN_MIN" not in REGISTRY
    assert int(_config.get_config().REENTRY_LOCKOUT_DAYS) == 1
    here = os.path.dirname(inspect.getfile(_config))
    settings_txt = open(os.path.join(here, "settings.py"), encoding="utf-8").read()
    assert "STOPOUT_REENTRY_COOLDOWN_MIN:" not in settings_txt  # no assignment left
    cjs = open(
        os.path.join(here, "..", "desktop", "electron", "setup-manager.cjs"),
        encoding="utf-8",
    ).read()
    assert cjs.count('"REENTRY_LOCKOUT_DAYS"') == 2  # ALLOWED + INJECTED
    fields = open(
        os.path.join(
            here, "..", "src", "console", "desktop", "advancedTradingFields.ts"
        ),
        encoding="utf-8",
    ).read()
    assert 'key: "REENTRY_LOCKOUT_DAYS"' in fields


@pytest.mark.parametrize("bad", [-1, 11, "x"])
def test_registry_clamps_out_of_range_values(bad):
    from core.trading_settings import REGISTRY, canonical_text

    try:
        out = canonical_text("REENTRY_LOCKOUT_DAYS", bad)
    except (TypeError, ValueError):
        return
    lo, hi = REGISTRY["REENTRY_LOCKOUT_DAYS"].lo, REGISTRY["REENTRY_LOCKOUT_DAYS"].hi
    assert lo <= int(out) <= hi
