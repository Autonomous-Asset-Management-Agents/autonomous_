"""Abnahme auf Epic-Ebene für #3738 — die Zusage „keine Verhaltensänderung" (#4049).

#3738 Abschnitt 5: „Jeder PR dieses Epics ist verhaltensneutral zu führen." Das
Szenario ruft die Verhaltensnetze, die die Sub-Issues gebaut haben, und verlangt von
jedem eine leere Befundliste (Plan #4049 §2, Option A):

* Geld-Gate Mandantenpfad (``_geld_gate.py``, #3735) und Strategiepfad
  (``_geld_gate_strategie.py``, G-7a #4098),
* HTTP-Fläche gegen ``tests/unit/api_flaeche.json`` (G-4 #4091),
* Specialist-Bericht gegen die Charakterisierung von vor G-8a (#4153),
* die vor ARC-E6 importierbaren Agenten-Namen (G-6a #4097),
* tote Test-Patches auf ``agents`` und ``api_routes`` (G-6a #4097, G-4 #4091).

Aus den ``test_*``-Modulen kommen nur benannte Funktionen und die Namensliste, nie eine
``test_*``-Funktion — pytest sammelte sie sonst hier ein zweites Mal ein.

Eigene Datei ohne xfail: Die Netze sind grün, also auch dieses Szenario.
"""

import asyncio
import json

import pytest
from pytest_bdd import given, scenario, then, when

from tests.architecture import regeln
from tests.unit import _geld_gate as geld_gate_mandant
from tests.unit import _geld_gate_strategie as geld_gate_strategie
from tests.unit import _specialist_bericht as specialist_bericht
from tests.unit.test_agenten_keine_toten_patches import (
    pruefe_patches,
    reexportierte_namen,
)
from tests.unit.test_agenten_reexport import NAMEN, fehlende_namen
from tests.unit.test_api_flaeche_schnappschuss import flaeche, unterschiede
from tests.unit.test_routes_patch_ziele import gepatchte_namen, pruefe

pytestmark = [pytest.mark.vc0]

PAKET = regeln.PAKET
TESTS = PAKET / "tests"
AGENTS_PY = PAKET / "core" / "round_table" / "agents.py"
ROUTES = PAKET / "core" / "engine" / "routes"
SCHNAPPSCHUSS = TESTS / "unit" / "api_flaeche.json"


@pytest.fixture(autouse=True)
def _event_loop_zuruecklassen():
    """Die Netze rufen ``asyncio.run``; das setzt die Loop des Haupt-Threads am Ende auf
    ``None``. Unter Python 3.12 wirft ein spaeteres ``asyncio.get_event_loop()`` dann
    (CI 05.10.2026: ``test_golden_path`` Story 05, danach gesammelt). Wer den Zustand
    veraendert, stellt ihn wieder her."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


@scenario("../epic_3738.feature", "Der Umbau hat kein Verhalten verändert")
def test_der_umbau_hat_kein_verhalten_veraendert():
    pass


def _geld_gate(netz, pfad: str) -> list[str]:
    befunde = netz.befunde(netz.lade_referenz(), netz.messe_alle())
    return [f"{pfad} {art} {name}: {was}" for art, name, was in befunde]


def _specialist() -> list[str]:
    referenz = specialist_bericht.lade_referenz()
    aus = []
    for name, ist in specialist_bericht.messe_alle().items():
        if name not in referenz:
            aus.append(f"[{name}] Szenario ohne Referenz")
            continue
        aus += [
            f"[{name}] {b}" for b in specialist_bericht.befunde(referenz[name], ist)
        ]
    return aus


def _http_flaeche() -> list[str]:
    from core.engine import api_routes

    soll = json.loads(SCHNAPPSCHUSS.read_text(encoding="utf-8"))
    return unterschiede(soll, flaeche(api_routes.app))


def _agenten_namen() -> list[str]:
    from core.round_table import agents

    return fehlende_namen(agents, NAMEN)


def _tote_patches() -> list[str]:
    reexportiert = reexportierte_namen(AGENTS_PY.read_text(encoding="utf-8"))
    return pruefe_patches(TESTS, reexportiert) + pruefe(ROUTES, gepatchte_namen(TESTS))


@given("die Verhaltensnetze der ARC-E6-Umbauten", target_fixture="netze")
def schritt_1():
    return {
        "geld": lambda: _geld_gate(geld_gate_mandant, "Mandantenpfad")
        + _geld_gate(geld_gate_strategie, "Strategiepfad"),
        "http": _http_flaeche,
        "specialist": _specialist,
        "agenten": _agenten_namen,
        "patches": _tote_patches,
    }


@when("die Verhaltensabnahme des Epics läuft", target_fixture="befunde")
def schritt_2(netze):
    return {name: netz() for name, netz in netze.items()}


def _keine(befunde: list[str], was: str) -> None:
    assert befunde == [], f"{was}:\n" + "\n".join(f"  {b}" for b in befunde)


@then(
    "entspricht jeder Broker-Aufruf im Mandantenpfad und im Strategiepfad der eingecheckten Referenz"
)
def schritt_3(befunde):
    _keine(befunde["geld"], "Geld-Gate: was den Broker erreicht, weicht ab")


@then("stimmt die HTTP-Fläche mit dem eingecheckten Schnappschuss überein")
def schritt_4(befunde):
    _keine(befunde["http"], "HTTP-Fläche weicht vom Schnappschuss ab")


@then("entspricht der Specialist-Bericht der Charakterisierung von vor G-8a")
def schritt_5(befunde):
    _keine(befunde["specialist"], "Specialist-Bericht weicht ab")


@then("ist jeder vor ARC-E6 importierbare Agenten-Name weiter importierbar")
def schritt_6(befunde):
    _keine(befunde["agenten"], "Nicht mehr importierbar aus core.round_table.agents")


@then("greift kein Test-Patch auf Agenten- oder Router-Module ins Leere")
def schritt_7(befunde):
    _keine(befunde["patches"], "Test-Patches, die ins Leere greifen")
