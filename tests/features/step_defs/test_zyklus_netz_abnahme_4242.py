"""#4242 (H-2a) — Zyklus-Netz der Handelsschleife als Abnahme.

Feature: ``tests/features/zyklus_netz_4242.feature``. Der Treiber ist
``tests/unit/_zyklus_netz.py``; die Wächter (kein Teilschritt gemockt, nur erlaubte
Patch-Ziele) stehen in ``tests/unit/test_zyklus_netz_4242.py``. Dieser Name weicht vom
Plan ab: Derselbe Dateiname kollidierte beim Sammeln mit dem Wächter (pytest importiert
beide als Modul ``test_zyklus_netz_4242``).
"""

from __future__ import annotations

import copy
import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.unit import _zyklus_netz as netz

pytestmark = [pytest.mark.vc3]

scenarios("../zyklus_netz_4242.feature")


def _szenario(name: str) -> netz.Szenario:
    return next(sz for sz in netz.SZENARIEN if sz.name == name)


@pytest.fixture
def kontext() -> dict:
    return {}


@given("eine Engine mit einer gehaltenen Position und offenem Markt")
def offener_markt(kontext):
    kontext["szenario"] = _szenario("offener_markt_mit_position")


@given("keine Methode der Handelsschleife ist gemockt")
def nichts_gemockt(kontext):
    engine, _ = netz.baue_engine(kontext["szenario"])
    from core.engine.trading_loop import TradingLoopMixin

    ueberdeckt = {n for n in vars(engine) if hasattr(TradingLoopMixin, n)}
    assert not ueberdeckt, f"Instanzattribut überdeckt {sorted(ueberdeckt)}"


@given("eine Engine bei geschlossenem Markt")
def geschlossener_markt(kontext):
    kontext["szenario"] = _szenario("markt_geschlossen")


@given("das Shutdown-Signal ist nach dem ersten Zyklus gesetzt")
def shutdown(kontext):
    kontext["szenario"] = _szenario("shutdown_an_der_zyklusgrenze")


@given(parsers.parse("das Flag {flag} ist eingeschaltet"))
def flag_an(kontext, flag):
    kontext["szenario"] = _szenario(f"flag_{flag}")


@when("ein vollstaendiger Zyklus von live_trading_loop laeuft")
def zyklus(kontext):
    kontext["ist"] = netz.fahre(kontext["szenario"])


def _referenz(kontext) -> dict:
    return netz.lade_referenz()[kontext["szenario"].name]


@then("entsprechen die Aufrufe am Broker-Client der eingecheckten Referenz")
def broker_wie_referenz(kontext):
    name = kontext["szenario"].name
    gefunden = netz.befunde({name: _referenz(kontext)}, {name: kontext["ist"]})
    assert not gefunden, "\n".join(" | ".join(b) for b in gefunden)


@then(
    "Stop-Pflege, HWM-Fortschreibung, Ausstiegsrunde und Kontostand wurden ausgefuehrt"
)
def offen_gefahren(kontext):
    gelaufen = kontext["ist"]["gelaufen"]
    for methode in (
        "_maintain_broker_stops",
        "_run_position_stop_checks",
        "_ratchet_high_water_marks",
        "_run_deconcentration_and_rotation_exits",
        "_update_live_account_equity",
    ):
        assert gelaufen.get(methode), f"{methode} lief nicht"
    assert kontext["ist"]["hwm"], "HWM wurde nicht fortgeschrieben"


@then("liefen _markt_geschlossen_berichten und _run_closed_report_pass")
def geschlossen_gefahren(kontext):
    gelaufen = kontext["ist"]["gelaufen"]
    assert gelaufen.get("_markt_geschlossen_berichten")
    assert gelaufen.get("_run_closed_report_pass")


@then("lief _perform_graceful_handover genau einmal")
def uebergabe_einmal(kontext):
    assert kontext["ist"]["gelaufen"].get("_perform_graceful_handover") == 1


@then(
    parsers.parse(
        "entspricht der Zustand vor dem Round-Table-Graph der Referenz fuer {flag}"
    )
)
def zustand_wie_referenz(kontext, flag):
    referenz = _referenz(kontext)
    assert kontext["ist"]["zustand"] == referenz["zustand"]
    aus = netz.lade_referenz()["offener_markt_mit_position"]["zustand"]
    assert (
        referenz["zustand"] != aus
    ), f"{flag} ändert den Zustand nicht — Netz mit Loch"


@when("alle Szenarien dreimal hintereinander laufen")
def dreimal(kontext):
    kontext["laeufe"] = [
        json.dumps(netz.messe_alle(), sort_keys=True) for _ in range(3)
    ]


@then("sind die drei Messungen bitgleich")
def bitgleich(kontext):
    assert len(set(kontext["laeufe"])) == 1


@given("die gemessene Menge einer Order weicht um eine Stueckzahl ab")
def veraendert(kontext):
    kontext["referenz"] = netz.lade_referenz()
    kontext["ist"] = copy.deepcopy(kontext["referenz"])
    assert netz.veraendere_eine_menge(kontext["ist"])


@when("das Netz gegen die Referenz vergleicht")
def vergleiche(kontext):
    kontext["befunde"] = netz.befunde(kontext["referenz"], kontext["ist"])


@then("meldet es einen Befund und der Test schlaegt fehl")
def befund(kontext):
    assert kontext["befunde"], "veränderte Menge blieb ohne Befund"
    assert kontext["befunde"][0][0] == netz.ABWEICHUNG
