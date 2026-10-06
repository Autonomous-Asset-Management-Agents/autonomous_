"""#4007 (ARC-E6 G-2b) — die Stufen vor dem Konsens als benannte Schritte.

Plan: ``docs/4007-*/implementation_plan.md`` §2/§5/§6.

``live_trading_loop`` ruft für den Anfang jedes Durchlaufs vier Schritte auf. Jeder bekommt
einen frischen ``ZyklusZustand`` und gibt ``Zyklus`` zurück; der Dirigent übersetzt das in
``break``/``continue``. Geprüft wird je Schritt: Rückgabe, gesetzte Felder und die heutigen
Aussprünge (Kill Switch, Shutdown, keine Strategie, Markt zu, Halt, keine Symbole) — samt der
Schlafdauer, die vor dem heutigen ``continue`` stand.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.schlaf import schlaf_nur_im_modul

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_SCHRITTE = [
    "_zyklus_vorbereiten",
    "_marktzeit_pruefen",
    "_markt_geschlossen_berichten",
    "_schutz_vor_konsens",
]


def _mixin(*, strategie=None, uhr_offen=True):
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.strategy_running = threading.Event()
    m.strategy_running.set()
    m.strategy_lock = threading.Lock()
    m.active_strategy = strategie
    m._sichere_schreibberechtigung = AsyncMock()
    m._hitl_day_rollover = AsyncMock()
    m._warm_lstm_bar_cache = AsyncMock()
    m._drain_hitl_approvals = AsyncMock()
    m._reconcile_active_strategy_entry_time = AsyncMock()
    m._run_closed_report_pass = AsyncMock(return_value=True)
    m._update_live_account_equity = AsyncMock()
    m._maintain_broker_stops = AsyncMock()
    m._run_position_stop_checks = AsyncMock(return_value=set())
    m._run_deconcentration_and_rotation_exits = AsyncMock(return_value=set())
    m._stopout_reentry_locked = MagicMock(return_value=set())
    m._log_strategy_thought = MagicMock()
    m.compliance_guardian = None
    m._last_cycle_details = {}
    uhr = SimpleNamespace(
        is_open=uhr_offen, next_open=datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
    )
    m.api = MagicMock()
    m.api.get_clock.return_value = uhr
    return m


def _strategie(symbole=("AAPL", "MSFT"), halt=False):
    s = MagicMock()
    s.symbols = list(symbole)
    s.risk_manager = SimpleNamespace(trading_halted=halt)
    return s


def _zustand(**felder):
    from core.engine.trading_loop import ZyklusZustand

    z = ZyklusZustand()
    for k, v in felder.items():
        setattr(z, k, v)
    return z


def _lauf(coro):
    return asyncio.run(coro)


@pytest.fixture
def schlaf():
    # Nur den Schlaf von trading_loop ersetzen, nicht prozessweit: Hintergrund-Tasks anderer
    # Tests schliefen sonst ueber den Mock und verfaelschten die Zaehlung (tests/helpers/schlaf.py).
    with schlaf_nur_im_modul("core.engine.trading_loop") as s:
        yield s


@pytest.fixture
def wurzel():
    uhr = SimpleNamespace(
        now=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        time=lambda: 1_790_000_000.0,
    )
    with patch("core.engine.trading_loop.CompositionRoot") as cr:
        cr.get_instance.return_value = SimpleNamespace(clock_port=uhr)
        yield cr


# ── Typen ────────────────────────────────────────────────────────────────────


def test_zyklus_zustand_ist_je_durchlauf_frisch():
    from core.engine.trading_loop import Zyklus, ZyklusZustand

    a, b = ZyklusZustand(), ZyklusZustand()
    a.symbols_to_process.append("AAPL")
    assert b.symbols_to_process == []
    assert (a.local_active_strategy, a.cycle_market_closed) == (None, False)
    assert {e.name for e in Zyklus} == {"WEITER", "NAECHSTER", "STOPP"}


# ── _zyklus_vorbereiten ──────────────────────────────────────────────────────


def test_kill_switch_stoppt_setzt_shutdown_und_leert_running():
    from core.engine.trading_loop import Zyklus

    m = _mixin()
    with patch("core.engine.trading_loop.kill_switch", create=True) as ks:
        ks.is_halted.return_value = True
        ergebnis = _lauf(m._zyklus_vorbereiten(_zustand()))
    assert ergebnis is Zyklus.STOPP
    assert m._shutdown_event.is_set()
    assert not m.strategy_running.is_set()
    m._sichere_schreibberechtigung.assert_awaited_once()


def test_ohne_kill_switch_geht_es_weiter():
    from core.engine.trading_loop import Zyklus

    m = _mixin()
    with patch("core.engine.trading_loop.kill_switch", create=True) as ks:
        ks.is_halted.return_value = False
        ergebnis = _lauf(m._zyklus_vorbereiten(_zustand()))
    assert ergebnis is Zyklus.WEITER
    assert not m._shutdown_event.is_set()
    # Erster Durchlauf: NY-Tag rollt -> Bar-Cache wird gewärmt.
    m._warm_lstm_bar_cache.assert_awaited_once()


# ── _marktzeit_pruefen ───────────────────────────────────────────────────────


def test_marktzeit_offen_setzt_strategie_und_symbole(schlaf):
    from core.engine.trading_loop import Zyklus

    s = _strategie()
    m = _mixin(strategie=s)
    z = _zustand()
    with patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", False):
        ergebnis = _lauf(m._marktzeit_pruefen(z))
    assert ergebnis is Zyklus.WEITER
    assert z.local_active_strategy is s
    assert z.symbols_to_process == ["AAPL", "MSFT"]
    assert z.cycle_market_closed is False
    m._drain_hitl_approvals.assert_awaited_once()
    m._reconcile_active_strategy_entry_time.assert_awaited_once()


def test_marktzeit_geschlossen_markiert_und_drainiert_nicht(schlaf):
    from core.engine.trading_loop import Zyklus

    m = _mixin(strategie=_strategie(), uhr_offen=False)
    z = _zustand()
    with patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", False):
        ergebnis = _lauf(m._marktzeit_pruefen(z))
    assert ergebnis is Zyklus.WEITER
    assert z.cycle_market_closed is True
    m._drain_hitl_approvals.assert_not_awaited()


def test_marktzeit_ohne_strategie_naechster_nach_fuenf_sekunden(schlaf):
    from core.engine.trading_loop import Zyklus

    m = _mixin(strategie=None)
    ergebnis = _lauf(m._marktzeit_pruefen(_zustand()))
    assert ergebnis is Zyklus.NAECHSTER
    schlaf.assert_awaited_once_with(5)
    m._reconcile_active_strategy_entry_time.assert_not_awaited()


def test_marktzeit_shutdown_nach_uhrpruefung_stoppt(schlaf):
    from core.engine.trading_loop import Zyklus

    m = _mixin(strategie=_strategie())
    m._shutdown_event.set()
    ergebnis = _lauf(m._marktzeit_pruefen(_zustand()))
    assert ergebnis is Zyklus.STOPP
    m._drain_hitl_approvals.assert_not_awaited()


# ── _markt_geschlossen_berichten ─────────────────────────────────────────────


def _cfg(report_only=True):
    return SimpleNamespace(MARKET_CLOSED_REPORT_ONLY=report_only)


def test_markt_geschlossen_genau_ein_bericht_dann_naechster(schlaf, wurzel):
    from core.engine.trading_loop import Zyklus

    s = _strategie()
    m = _mixin(strategie=s, uhr_offen=False)
    z = _zustand(
        local_active_strategy=s,
        cycle_market_closed=True,
        symbols_to_process=["AAPL"],
        clock=m.api.get_clock.return_value,
    )
    with (
        patch("core.engine.trading_loop.get_config", return_value=_cfg()),
        patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", False),
    ):
        ergebnis = _lauf(m._markt_geschlossen_berichten(z))
        assert ergebnis is Zyklus.NAECHSTER
        # Zweiter geschlossener Durchlauf: das Latch hält, kein zweiter Bericht.
        ergebnis2 = _lauf(m._markt_geschlossen_berichten(z))
    assert ergebnis2 is Zyklus.NAECHSTER
    m._run_closed_report_pass.assert_awaited_once_with(s, ["AAPL"])
    assert m._closed_panel_refreshed is True
    assert m._last_cycle_details["timestamp"] == 1_790_000_000.0
    relevante_schlafe = [c for c in schlaf.call_args_list if c[0] == (300.0,)]
    assert len(relevante_schlafe) == 2 or schlaf.await_count == 2


def test_markt_offen_kein_bericht_weiter(schlaf):
    from core.engine.trading_loop import Zyklus

    m = _mixin(strategie=_strategie())
    z = _zustand(cycle_market_closed=False)
    with patch("core.engine.trading_loop.get_config", return_value=_cfg()):
        ergebnis = _lauf(m._markt_geschlossen_berichten(z))
    assert ergebnis is Zyklus.WEITER
    m._run_closed_report_pass.assert_not_awaited()
    schlaf.assert_not_awaited()


# ── _schutz_vor_konsens ──────────────────────────────────────────────────────


def test_halt_fuehrt_stops_aus_und_beendet_den_durchlauf(schlaf, wurzel):
    from core.engine.trading_loop import Zyklus

    s = _strategie(halt=True)
    m = _mixin(strategie=s)
    m._run_position_stop_checks = AsyncMock(return_value={"AAPL"})
    z = _zustand(local_active_strategy=s, symbols_to_process=["AAPL", "MSFT"])
    ergebnis = _lauf(m._schutz_vor_konsens(z))
    assert ergebnis is Zyklus.NAECHSTER
    m._maintain_broker_stops.assert_awaited_once()
    m._run_position_stop_checks.assert_awaited_once()
    m._run_deconcentration_and_rotation_exits.assert_not_awaited()
    schlaf.assert_awaited_once_with(60)


def test_ohne_symbole_naechster_nach_zehn_sekunden(schlaf, wurzel):
    from core.engine.trading_loop import Zyklus

    s = _strategie()
    m = _mixin(strategie=s)
    m._run_position_stop_checks = AsyncMock(return_value={"AAPL"})
    z = _zustand(local_active_strategy=s, symbols_to_process=["AAPL"])
    ergebnis = _lauf(m._schutz_vor_konsens(z))
    assert ergebnis is Zyklus.NAECHSTER
    assert z.symbols_to_process == []
    schlaf.assert_awaited_once_with(10)


def test_schutz_filtert_gestoppte_ausgestiegene_und_gesperrte(schlaf, wurzel):
    from core.engine.trading_loop import Zyklus

    s = _strategie()
    m = _mixin(strategie=s)
    m._run_position_stop_checks = AsyncMock(return_value={"AAPL"})
    m._run_deconcentration_and_rotation_exits = AsyncMock(return_value={"MSFT"})
    m._stopout_reentry_locked = MagicMock(return_value={"TSLA"})
    z = _zustand(
        local_active_strategy=s, symbols_to_process=["AAPL", "MSFT", "TSLA", "NVDA"]
    )
    with patch("core.engine.order_executor._rec_outcome") as badge:
        ergebnis = _lauf(m._schutz_vor_konsens(z))
    assert ergebnis is Zyklus.WEITER
    assert z.symbols_to_process == ["NVDA"]
    m._update_live_account_equity.assert_awaited_once_with(s)
    badge.assert_called_once()
    schlaf.assert_not_awaited()


# ── Dirigent ─────────────────────────────────────────────────────────────────


def test_dirigent_ruft_die_vier_schritte_in_reihenfolge():
    from tests.unit import _schleifen_quelle

    schritte = _schleifen_quelle.schritte()
    positionen = [schritte.index(n) for n in _SCHRITTE]
    assert positionen == sorted(positionen)


def test_jeder_schritt_liegt_unter_der_funktionsschwelle():
    from tests.unit import _schleifen_quelle

    baum = ast.parse(_schleifen_quelle.PFAD.read_text(encoding="utf-8"))
    klasse = next(
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "TradingLoopMixin"
    )
    laengen = {
        f.name: f.end_lineno - f.lineno + 1
        for f in klasse.body
        if isinstance(f, ast.AsyncFunctionDef) and f.name in _SCHRITTE
    }
    assert set(laengen) == set(_SCHRITTE)
    assert all(n < 150 for n in laengen.values()), laengen
