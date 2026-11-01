"""#3655 — the slot freed by a stop-loss exit stays held for STOP_EXIT_SLOT_HOLD_DAYS
trading days: no NEW name takes it; the sold name itself may return once its own
re-entry lockout (#3604) has run out.

Measured (installed app, fills 26.07.–23.09.2026, 38 full stop exits): the immediate
replacement bought within a trading day lost −1,890 $ over 74 closed lots (26 winners);
the same name bought back a day or more later made +1,446 $ over 9 lots. 14 sold names
were rated BUY by the round table more than 3,000 times afterwards and never bought —
the slot was taken and the replacement's 20-day minimum hold blocked displacement.
"""

import inspect
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config as _config
from core.engine.reentry_lockout import ReentryLockout, reserved_slots
from core.engine.trading_loop import TradingLoopMixin
from core.trading_settings import REGISTRY

pytestmark = pytest.mark.vc3

HERE = os.path.dirname(inspect.getfile(_config))
TUE = datetime(2026, 9, 22, 18, 1, tzinfo=timezone.utc)  # Tue 14:01 ET
WED = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
FRI = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)
MON = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def _store(tmp_path):
    return ReentryLockout(str(tmp_path / "reentry_lockout.json"))


# --- store: held slots ---------------------------------------------------------


def test_a_stop_exit_holds_its_slot_for_the_trading_day(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=1, stop_exits={"HOOD"})
    assert s.held_slots(TUE + timedelta(hours=2), held_symbols=set()) == 1
    assert s.held_slots(WED, held_symbols=set()) == 0  # free again next trading day


def test_weekend_is_skipped(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, FRI, days=1, hold_days=1, stop_exits={"HOOD"})
    assert s.held_slots(FRI + timedelta(days=1), held_symbols=set()) == 1  # Saturday
    assert s.held_slots(MON, held_symbols=set()) == 0


def test_only_stop_exits_hold_a_slot(tmp_path):
    """Rotation / displacement / SELL vote / trailing exits lock the NAME (#3604) but
    never hold a slot."""
    s = _store(tmp_path)
    s.arm({"ROT", "STOP"}, TUE, days=1, hold_days=1, stop_exits={"STOP"})
    assert s.active(TUE) == {"ROT", "STOP"}  # name lockout as before
    assert s.held_slots(TUE, held_symbols=set()) == 1
    assert s.entry("ROT").get("hold_until") is None
    assert s.entry("STOP").get("hold_until") == "2026-09-23"


def test_two_stop_exits_hold_two_slots(tmp_path):
    s = _store(tmp_path)
    s.arm({"A", "B"}, TUE, days=1, hold_days=1, stop_exits={"A", "B"})
    assert s.held_slots(TUE, held_symbols=set()) == 2


def test_the_returner_does_not_count_against_itself(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=2, stop_exits={"HOOD"})
    # Wed: name lockout over (1 day), slot still held (2 days) — but not for HOOD
    assert s.active(WED) == set()
    assert s.held_slots(WED, held_symbols=set(), exclude="HOOD") == 0
    assert s.held_slots(WED, held_symbols=set()) == 1


def test_a_held_slot_is_released_once_the_name_is_back_in_the_book(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=2, stop_exits={"HOOD"})
    assert s.held_slots(WED, held_symbols={"HOOD"}) == 0


def test_slot_hold_without_name_lockout(tmp_path):
    """REENTRY_LOCKOUT_DAYS 0 + STOP_EXIT_SLOT_HOLD_DAYS 1: the name is free, the slot
    is held."""
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=0, hold_days=1, stop_exits={"HOOD"})
    assert s.active(TUE) == set()
    assert s.held_slots(TUE, held_symbols=set()) == 1


def test_held_slots_survive_a_restart_and_expire(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=1, stop_exits={"HOOD"})
    again = _store(tmp_path)
    assert again.held_slots(TUE + timedelta(hours=1), held_symbols=set()) == 1
    again.arm(set(), WED, days=1, hold_days=1)  # evicts expired entries
    assert again.entry("HOOD") is None


def test_a_simulated_past_sees_no_future_hold(tmp_path):
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=1, stop_exits={"HOOD"})
    assert s.held_slots(TUE - timedelta(days=1), held_symbols=set()) == 0


# --- module reader used by the buy path ------------------------------------------


def test_reserved_slots_is_off_at_zero_and_never_touches_the_store(
    tmp_path, monkeypatch
):
    cfg = SimpleNamespace(STOP_EXIT_SLOT_HOLD_DAYS=0)
    monkeypatch.setattr(
        "core.engine.reentry_lockout.get_config", lambda: cfg, raising=False
    )
    boom = MagicMock(side_effect=AssertionError("store must not be read"))
    with patch("core.engine.reentry_lockout.shared_store", boom):
        assert reserved_slots(TUE, held_symbols=set()) == 0


def test_reserved_slots_reads_the_shared_store(tmp_path, monkeypatch):
    cfg = SimpleNamespace(STOP_EXIT_SLOT_HOLD_DAYS=1)
    monkeypatch.setattr(
        "core.engine.reentry_lockout.get_config", lambda: cfg, raising=False
    )
    s = _store(tmp_path)
    s.arm({"HOOD"}, TUE, days=1, hold_days=1, stop_exits={"HOOD"})
    with patch("core.engine.reentry_lockout.shared_store", return_value=s):
        assert reserved_slots(TUE, held_symbols=set()) == 1
        assert reserved_slots(TUE, held_symbols=set(), exclude="HOOD") == 0


def test_reserved_slots_fails_open(monkeypatch, caplog):
    import logging

    cfg = SimpleNamespace(STOP_EXIT_SLOT_HOLD_DAYS=1)
    monkeypatch.setattr(
        "core.engine.reentry_lockout.get_config", lambda: cfg, raising=False
    )
    with patch(
        "core.engine.reentry_lockout.shared_store", side_effect=RuntimeError("disk")
    ), caplog.at_level(logging.WARNING):
        assert reserved_slots(TUE, held_symbols=set()) == 0
    assert any(r.exc_info for r in caplog.records)


# --- loop hook: only loss stops are marked ---------------------------------------


def _eng(tmp_path, days=1, hold=1):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng._reentry_store = _store(tmp_path)
    eng._reentry_prev_held = set()
    eng._reentry_partial_exits = set()
    return eng, SimpleNamespace(
        REENTRY_LOCKOUT_DAYS=days, STOP_EXIT_SLOT_HOLD_DAYS=hold
    )


def test_hook_hands_the_loss_stop_set_to_the_store(tmp_path):
    eng, cfg = _eng(tmp_path)
    eng._stop_loss_exits = {"HOOD"}  # marked by _run_position_stop_checks
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        eng._stopout_reentry_locked({"HOOD", "ROT"}, now=TUE)
    assert eng._reentry_store.held_slots(TUE, held_symbols=set()) == 1
    assert eng._reentry_store.entry("ROT").get("hold_until") is None
    assert eng._stop_loss_exits == set()  # consumed per cycle


def test_hook_with_lockout_off_still_holds_slots(tmp_path):
    eng, cfg = _eng(tmp_path, days=0, hold=1)
    eng._stop_loss_exits = {"HOOD"}
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._stopout_reentry_locked({"HOOD"}, now=TUE) == set()
    assert eng._reentry_store.held_slots(TUE, held_symbols=set()) == 1


def test_hook_both_off_touches_no_file(tmp_path):
    eng, cfg = _eng(tmp_path, days=0, hold=0)
    eng._stop_loss_exits = {"HOOD"}
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert eng._stopout_reentry_locked({"HOOD"}, now=TUE) == set()
    assert list(tmp_path.iterdir()) == []


def test_stop_checks_mark_loss_stops_only():
    src = inspect.getsource(TradingLoopMixin._run_position_stop_checks)
    assert "_stop_loss_exits" in src
    assert "LOSS CUT" in src and "HARD STOP" in src
    assert "TRAILING" not in src.split("_stop_loss_exits")[1].split("\n")[0]
    hook = inspect.getsource(TradingLoopMixin._stopout_reentry_locked)
    assert "STOP_EXIT_SLOT_HOLD_DAYS" in hook and 'getattr(cfg, "STOP_EXIT' not in hook


# --- portfolio manager: new names wait, returners pass ------------------------------


def _pm(max_positions=3):
    from core.portfolio_manager import PortfolioManager, PositionScore

    pm = PortfolioManager(client=MagicMock(), total_capital=100_000.0)
    pm.refresh_positions = lambda: None
    pm._last_refresh_ok = True
    pm.max_positions = max_positions
    for sym in ("A", "B"):
        pm._position_scores[sym] = PositionScore(
            symbol=sym,
            qty=10.0,
            avg_entry=100.0,
            current_price=100.0,
            market_value=1000.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
        )
    return pm


def _opp(symbol):
    from core.portfolio_manager import OpportunityScore

    return OpportunityScore(symbol=symbol, current_price=100.0, total_score=80.0)


def test_a_new_name_waits_while_the_freed_slot_is_held():
    pm = _pm(max_positions=3)  # 2 held, 1 free — the one a stop just freed
    with patch("core.engine.reentry_lockout.reserved_slots", return_value=1):
        ok, reason, close = pm.should_open_new_position(_opp("NEW"))
    assert ok is False and close is None
    assert reason.startswith("slot_hold")


def test_the_returner_takes_the_held_slot():
    pm = _pm(max_positions=3)

    def _res(now, held_symbols, exclude=None):
        return 0 if exclude == "HOOD" else 1

    with patch("core.engine.reentry_lockout.reserved_slots", side_effect=_res):
        ok, reason, _ = pm.should_open_new_position(_opp("HOOD"))
    assert ok is True, reason


def test_no_hold_is_byte_identical():
    pm = _pm(max_positions=3)
    with patch("core.engine.reentry_lockout.reserved_slots", return_value=0):
        ok, reason, _ = pm.should_open_new_position(_opp("NEW"))
    assert ok is True, reason


def test_a_truly_full_book_still_goes_to_the_swap_path_not_slot_hold():
    pm = _pm(max_positions=2)  # full
    with patch("core.engine.reentry_lockout.reserved_slots", return_value=1):
        ok, reason, _ = pm.should_open_new_position(_opp("NEW"))
    assert not reason.startswith("slot_hold")


# --- sizer: the cash-slot divisor is NOT touched (the returner keeps its cash share) ---


def test_cash_slot_divisor_is_untouched_by_the_hold():
    """Capacity, not cash: free_for_new = cap - held - reserved decides whether a NEW name
    may enter; the cash divisor stays cap - held so the held slot's cash share is kept
    for the returner instead of being spread over the other buys."""
    import inspect as _i

    from core.risk_manager import effective_free_slots

    assert "reserved" not in str(_i.signature(effective_free_slots))


# --- settings plumbing ---------------------------------------------------------------


def test_setting_registry_desktop_console_carry_the_knob():
    s = REGISTRY["STOP_EXIT_SLOT_HOLD_DAYS"]
    assert (s.default, s.lo, s.hi) == (1, 0, 10)
    assert int(_config.get_config().STOP_EXIT_SLOT_HOLD_DAYS) == 1
    cjs = open(
        os.path.join(HERE, "..", "desktop", "electron", "setup-manager.cjs"),
        encoding="utf-8",
    ).read()
    assert cjs.count('"STOP_EXIT_SLOT_HOLD_DAYS"') == 2
    fields = open(
        os.path.join(
            HERE, "..", "src", "console", "desktop", "advancedTradingFields.ts"
        ),
        encoding="utf-8",
    ).read()
    assert 'key: "STOP_EXIT_SLOT_HOLD_DAYS"' in fields


class TestErneutesArmenBehaeltHold:
    """Folgezyklus armt die verschwundenen Namen erneut OHNE Stop-Menge (vanished-Pfad);
    der Platz-Hold aus dem Verkaufszyklus darf dabei nicht verloren gehen."""

    def test_re_arm_ohne_stop_menge_behaelt_hold_until(self, tmp_path):
        from datetime import datetime, timezone

        from core.engine.reentry_lockout import ReentryLockout

        store = ReentryLockout(str(tmp_path / "reentry_lockout.json"))
        t1 = datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 8, 13, 45, tzinfo=timezone.utc)
        store.arm({"EIX", "LUV"}, t1, 1, hold_days=1, stop_exits={"EIX", "LUV"})
        store.arm({"EIX", "LUV"}, t2, 1, hold_days=1, stop_exits=())
        assert store._entries["EIX"]["hold_until"] == "2026-06-09"
        assert store._entries["EIX"]["exited_at"] == t1.isoformat()
        assert store._entries["EIX"]["until"] == "2026-06-09"
        assert (
            store.held_slots(
                datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc),
                {"CAH"},
                exclude="SPGI",
            )
            == 2
        )

    def test_re_arm_nimmt_das_spaetere_datum(self, tmp_path):
        from datetime import datetime, timezone

        from core.engine.reentry_lockout import ReentryLockout

        store = ReentryLockout(str(tmp_path / "reentry_lockout.json"))
        t1 = datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 9, 13, 30, tzinfo=timezone.utc)
        store.arm({"EIX"}, t1, 1, hold_days=1, stop_exits={"EIX"})
        store.arm({"EIX"}, t2, 1, hold_days=0, stop_exits=())
        assert store._entries["EIX"]["until"] == "2026-06-10"
        assert store._entries["EIX"]["hold_until"] == "2026-06-09"
        assert store._entries["EIX"]["exited_at"] == t1.isoformat()


class TestSlotHoldSperrtKeineNachkaeufe:
    """Die Platz-Sperre gilt NEUEN Titeln; ein gehaltener Titel ist kein Platz-Anwaerter
    (Sim 25.09.2026: CAH/DECK-Nachkaeufe wurden mit slot_hold abgelehnt)."""

    def test_gehaltener_titel_wird_nicht_mit_slot_hold_abgelehnt(self, monkeypatch):
        from core.portfolio_manager import PortfolioManager

        pm = PortfolioManager.__new__(PortfolioManager)
        pm._position_scores = {"CAH": object()}
        pm.max_positions = 10
        monkeypatch.setattr(pm, "_reserved_slots", lambda symbol: 4, raising=False)
        reason = pm._slot_hold_reason(
            type("Opp", (), {"symbol": "CAH"})(), num_positions=6
        )
        assert reason is None, reason

    def test_neuer_titel_wird_mit_slot_hold_abgelehnt(self, monkeypatch):
        from core.portfolio_manager import PortfolioManager

        pm = PortfolioManager.__new__(PortfolioManager)
        pm._position_scores = {"CAH": object()}
        pm.max_positions = 10
        monkeypatch.setattr(pm, "_reserved_slots", lambda symbol: 4, raising=False)
        reason = pm._slot_hold_reason(
            type("Opp", (), {"symbol": "SPGI"})(), num_positions=6
        )
        assert reason is not None and reason.startswith("slot_hold: 4 slot(s)")
