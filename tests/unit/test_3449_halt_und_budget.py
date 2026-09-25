"""#3449 Schritt 5 — Halt und Tagesbudget ueberleben den Neustart (Plan: docs/3449-schritt5-…).

Plan-Entscheid (PR #3482, Option A): eine **synchrone Seite** des Ports. ``is_halted()``,
``trip()`` und ``record_trade()`` sind synchron; ein Trip muss dauerhaft sein, **bevor**
``trip()`` zurueckkehrt. Redis hat den synchronen Client schon; SQLite bekommt ihn hier — auf
**derselben Datei und Tabelle** wie der asynchrone Adapter.

Owner-Entscheid 17.09.: **Ein gesetzter Halt ohne lesbaren Trip-Satz bleibt stehen.**
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.iron_dome]


# ---------------------------------------------------------------------------
# Die synchrone Seite: derselbe Vertrag gegen beide Ablagen
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sqlite", "redis"])
def sync_ablage(request, tmp_path):
    if request.param == "sqlite":
        from core.state.synchron import SqliteSyncAblage

        ablage = SqliteSyncAblage(tmp_path / "engine_state.db")
        yield ablage
        ablage.schliessen()
    else:
        fakeredis = pytest.importorskip("fakeredis")
        yield fakeredis.FakeRedis(decode_responses=True)


def test_gesetzt_gelesen_geloescht(sync_ablage) -> None:
    assert sync_ablage.get("system_halted") is None
    sync_ablage.set("system_halted", "true")
    assert sync_ablage.get("system_halted") == "true"
    assert sync_ablage.delete("system_halted") == 1
    assert sync_ablage.get("system_halted") is None


def test_frist_laesst_den_wert_verschwinden(sync_ablage) -> None:
    import time

    sync_ablage.set("kurz", "v", ex=1)
    sync_ablage.set("lang", "v", ex=30)
    time.sleep(1.3)
    assert sync_ablage.get("kurz") is None
    assert sync_ablage.get("lang") == "v"


def test_zaehler_zaehlt(sync_ablage) -> None:
    assert float(sync_ablage.incrbyfloat("budget", 1.0)) == 1.0
    assert float(sync_ablage.incrbyfloat("budget", 1.0)) == 2.0
    assert float(sync_ablage.incrbyfloat("budget", -1.0)) == 1.0
    assert float(sync_ablage.get("budget")) == 1.0


async def test_synchron_geschrieben_ist_asynchron_lesbar(tmp_path) -> None:
    """Dieselbe Datei, dieselbe Tabelle: Was ``trip()`` synchron schreibt, sieht der
    asynchrone Port — sonst gaebe es zwei Wahrheiten ueber den Halt."""
    from core.state import SqliteStateAdapter
    from core.state.synchron import SqliteSyncAblage

    pfad = tmp_path / "engine_state.db"
    sync = SqliteSyncAblage(pfad)
    sync.set("system_halted", "true")
    port = SqliteStateAdapter(pfad)
    try:
        assert await port.get("system_halted") == "true"
        await port.set("zurueck", "ja")
        assert sync.get("zurueck") == "ja"
    finally:
        await port.aclose()
        sync.schliessen()


def test_die_desktop_ablage_liegt_unter_user_data_dir(monkeypatch, tmp_path) -> None:
    from core.state.synchron import SqliteSyncAblage, synchrone_ablage

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    ablage = synchrone_ablage()
    try:
        assert isinstance(ablage, SqliteSyncAblage)
        assert str(tmp_path) in str(ablage.pfad)
    finally:
        ablage.schliessen()


def test_ohne_dauerhafte_ablage_gibt_es_keine(monkeypatch) -> None:
    from core.state.synchron import synchrone_ablage

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    assert synchrone_ablage() is None


# ---------------------------------------------------------------------------
# Der Halt ueberlebt den Neustart
# ---------------------------------------------------------------------------


@pytest.fixture
def frischer_kill_switch(monkeypatch, tmp_path):
    """Ein KillSwitch wie nach einem Neustart: neue Instanz, dieselbe Ablage."""
    from core import kill_switch as ks

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ks, "send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr(ks.KillSwitch, "_run_async_mass_cancel", lambda *a, **k: None)
    monkeypatch.setattr(ks.KillSwitch, "_apply_fail_closed", lambda *a, **k: None)

    erzeugt = []

    def _neu():
        monkeypatch.setattr(ks.KillSwitch, "_instance", None)
        k = ks.KillSwitch()
        erzeugt.append(k)
        return k

    yield _neu
    for k in erzeugt:
        schliessen = getattr(k.redis_client, "schliessen", None)
        if schliessen:
            schliessen()
    monkeypatch.setattr(ks.KillSwitch, "_instance", None)


def test_ein_trip_ueberlebt_den_neustart(frischer_kill_switch) -> None:
    erster = frischer_kill_switch()
    erster.trip("Portfolio stop loss (7.1%)")

    zweiter = frischer_kill_switch()

    assert zweiter.is_halted() is True
    assert zweiter.last_trip()["reason"] == "Portfolio stop loss (7.1%)"


def test_ein_reset_ueberlebt_den_neustart(frischer_kill_switch) -> None:
    erster = frischer_kill_switch()
    erster.trip("Test")
    erster.reset()

    zweiter = frischer_kill_switch()

    assert zweiter.is_halted() is False
    assert zweiter.last_trip() is None


def test_der_trip_ist_dauerhaft_bevor_trip_zurueckkehrt(
    frischer_kill_switch, tmp_path
) -> None:
    """Der Kern von Option A: kein Fenster, in dem der Halt nur im Speicher steht."""
    import sqlite3

    frischer_kill_switch().trip("Watchdog")

    with sqlite3.connect(tmp_path / "engine_state.db") as db:
        wert = db.execute(
            "SELECT value FROM engine_state WHERE key = 'system_halted'"
        ).fetchone()
    assert wert == ("true",)


def test_ein_nutzer_halt_ueberlebt_den_neustart(frischer_kill_switch) -> None:
    frischer_kill_switch().trip("OAuth widerrufen", user_id="u-7")

    zweiter = frischer_kill_switch()

    assert zweiter.is_halted("u-7") is True
    assert zweiter.is_halted("u-8") is False


# ---------------------------------------------------------------------------
# Owner-Tabelle 17.09.: beim Strategiestart
# ---------------------------------------------------------------------------


def test_gesetzter_halt_mit_trip_satz_bleibt_beim_start(frischer_kill_switch) -> None:
    from core.kill_switch import halt_beim_start_raeumen

    frischer_kill_switch().trip("Portfolio stop loss")
    zweiter = frischer_kill_switch()

    assert halt_beim_start_raeumen(zweiter) is False
    assert zweiter.is_halted() is True


def test_gesetzter_halt_ohne_trip_satz_bleibt_beim_start(
    frischer_kill_switch,
) -> None:
    """Fail-closed: Unbekannt ist nicht dasselbe wie unschuldig. Heute wuerde hier geraeumt."""
    from core.kill_switch import TRIP_SATZ, halt_beim_start_raeumen

    erster = frischer_kill_switch()
    erster.trip("Watchdog")
    erster.redis_client.delete(TRIP_SATZ)

    zweiter = frischer_kill_switch()
    assert halt_beim_start_raeumen(zweiter) is False
    assert zweiter.is_halted() is True


def test_gesetzter_halt_mit_unlesbarem_trip_satz_bleibt_beim_start(
    frischer_kill_switch,
) -> None:
    from core.kill_switch import TRIP_SATZ, halt_beim_start_raeumen

    erster = frischer_kill_switch()
    erster.trip("Watchdog")
    erster.redis_client.set(TRIP_SATZ, "{kaputt")

    zweiter = frischer_kill_switch()
    assert halt_beim_start_raeumen(zweiter) is False
    assert zweiter.is_halted() is True


def test_ohne_halt_startet_die_strategie(frischer_kill_switch) -> None:
    from core.kill_switch import halt_beim_start_raeumen

    k = frischer_kill_switch()
    assert halt_beim_start_raeumen(k) is True
    assert k.is_halted() is False


def test_die_strategie_raeumt_keinen_halt_mehr_selbst() -> None:
    """base.py fragt die Owner-Tabelle, statt ueber ``_last_trip`` im Arbeitsspeicher zu raten."""
    import inspect

    from core.engine import base

    quelle = inspect.getsource(base)
    assert "halt_beim_start_raeumen" in quelle
    assert 'getattr(_ks, "_last_trip", None) is None' not in quelle


# ---------------------------------------------------------------------------
# Das Tagesbudget ueberlebt den Neustart
# ---------------------------------------------------------------------------


@pytest.fixture
def frischer_waechter(monkeypatch, tmp_path):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    erzeugt = []

    def _neu():
        from core.compliance import ComplianceGuardian

        w = ComplianceGuardian()
        erzeugt.append(w)
        return w

    yield _neu
    for w in erzeugt:
        ablage = getattr(w, "_budget_ablage", None)
        if ablage is not None and hasattr(ablage, "schliessen"):
            ablage.schliessen()


def test_das_verbrauchte_budget_ueberlebt_den_neustart(frischer_waechter) -> None:
    erster = frischer_waechter()
    for _ in range(8):
        erster.record_trade()

    zweiter = frischer_waechter()

    assert zweiter.daily_trades == 8


def test_eine_rueckgabe_ueberlebt_den_neustart(frischer_waechter) -> None:
    erster = frischer_waechter()
    erster.record_trade()
    erster.record_trade()
    erster.refund_trade()

    assert frischer_waechter().daily_trades == 1


def test_ein_neuer_tag_beginnt_bei_null(frischer_waechter, monkeypatch) -> None:
    from core import compliance

    erster = frischer_waechter()
    erster.record_trade()
    monkeypatch.setattr(compliance, "_ny_handelstag", lambda: "2099-01-02")

    assert frischer_waechter().daily_trades == 0


def test_ein_unlesbares_budget_gilt_als_ausgeschoepft(
    frischer_waechter, monkeypatch
) -> None:
    """Fail-closed wie beim Halt. Neue Einstiege werden abgelehnt; risikomindernde Exits nimmt
    ``COMPLIANCE_ALLOW_RISK_REDUCING_EXITS`` wie bisher vom Deckel aus."""
    from core.state import synchron

    class _Kaputt:
        def get(self, *_a, **_k):
            raise OSError("database disk image is malformed")

    monkeypatch.setattr(synchron, "synchrone_ablage", lambda: _Kaputt())
    w = frischer_waechter()

    assert w.daily_trades >= w.max_daily_trades


def test_ohne_dauerhafte_ablage_zaehlt_der_waechter_wie_bisher(monkeypatch) -> None:
    from core.compliance import ComplianceGuardian

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    w = ComplianceGuardian()
    w.record_trade()
    assert w.daily_trades == 1
    assert ComplianceGuardian().daily_trades == 0
