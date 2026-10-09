"""#4282 (H-5a) — Netz für Zulassung, Verdrängung und Bericht als Abnahme.

Feature: ``tests/features/pm_netz_4282.feature``. Der Treiber ist
``tests/unit/_pm_netz.py``; die Wächter (kein Teilschritt gemockt, kein Patch am Modul)
stehen in ``tests/unit/test_pm_netz_4282.py``. Dieser Name weicht vom Plan ab: Derselbe
Dateiname kollidierte beim Sammeln mit dem Wächter (pytest importiert beide als Modul
``test_pm_netz_4282``) — wie bei #4242 und #4263.
"""

from __future__ import annotations

import copy
import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.unit import _pm_netz as netz

pytestmark = [pytest.mark.vc3]

scenarios("../pm_netz_4282.feature")


def _szenario(name: str) -> netz.Szenario:
    return next(sz for sz in netz.SZENARIEN if sz.name == name)


@pytest.fixture
def kontext() -> dict:
    return {}


def _fahre(kontext, name: str) -> None:
    kontext["szenario"] = _szenario(name)
    kontext["ist"] = netz.fahre(kontext["szenario"])


def _wie_referenz(kontext, feld: str | None = None) -> None:
    """Vergleich gegen die Referenz — ganz oder nur ein Feld je Schritt."""
    name = kontext["szenario"].name
    referenz = netz.lade_referenz()[name]
    gefunden = netz.befunde({name: referenz}, {name: kontext["ist"]})
    if feld is not None:
        gefunden = [b for b in gefunden if f" {feld}:" in b[2] or "Schritte" in b[2]]
    assert not gefunden, "\n".join(" | ".join(b) for b in gefunden)


# ── Aufbau ───────────────────────────────────────────────────────────────────


@given("ein echter PortfolioManager ueber einen Broker-Client mit festem Bestand")
def echter_pm(kontext):
    assert len(netz.BESTAND) == 3


@given("eine feste Sim-Uhr")
def feste_uhr(kontext):
    assert netz.T0.tzinfo is not None


# ── Zulassung ────────────────────────────────────────────────────────────────


@given(parsers.parse("die Lage {fall}"))
def lage(kontext, fall):
    kontext["name"] = fall


@when("should_open_new_position laeuft")
def zulassung(kontext):
    _fahre(kontext, kontext["name"])


@then(
    parsers.parse(
        "entsprechen erlaubt, Grund und zu schliessende Position der Referenz fuer {fall}"
    )
)
def ergebnis_wie_referenz(kontext, fall):
    assert kontext["szenario"].name == fall
    _wie_referenz(kontext, "ergebnis")
    sz = kontext["szenario"]
    erlaubt, grund, _ = kontext["ist"]["schritte"][-1]["ergebnis"]
    assert erlaubt is sz.erlaubt
    assert grund.startswith(sz.grund_beginnt), grund


@then(parsers.parse("die neue Debattenzeile entspricht der Referenz fuer {fall}"))
def debatte_wie_referenz(kontext, fall):
    _wie_referenz(kontext)


# ── Bericht ──────────────────────────────────────────────────────────────────


@when("get_portfolio_summary, get_strongest_position und get_weakest_position laufen")
def bericht(kontext):
    kontext["laeufe"] = []
    for name in ("bericht_summary", "bericht_staerkste_schwaechste"):
        _fahre(kontext, name)
        kontext["laeufe"].append((kontext["szenario"], kontext["ist"]))


@then("entsprechen die Rueckgaben der Referenz")
def bericht_wie_referenz(kontext):
    for sz, ist in kontext["laeufe"]:
        kontext["szenario"], kontext["ist"] = sz, ist
        _wie_referenz(kontext)
    summary = kontext["laeufe"][0][1]["schritte"]
    assert summary[0]["ergebnis"]["num_positions"] == 3
    assert summary[-1]["ergebnis"]["num_positions"] == 0


@when(
    "Verkaufssignale gezaehlt, zurueckgesetzt und geloescht werden und "
    "update_total_capital laeuft"
)
def signale_und_kapital(kontext):
    kontext["laeufe"] = []
    for name in ("verkaufssignale", "gesamtkapital"):
        _fahre(kontext, name)
        kontext["laeufe"].append((kontext["szenario"], kontext["ist"]))


@then("entsprechen Zaehlerstaende, can_sell_position und total_capital der Referenz")
def signale_wie_referenz(kontext):
    for sz, ist in kontext["laeufe"]:
        kontext["szenario"], kontext["ist"] = sz, ist
        _wie_referenz(kontext)


# ── Netz als Ganzes ──────────────────────────────────────────────────────────


@when("alle Szenarien dreimal hintereinander laufen")
def dreimal(kontext):
    kontext["laeufe"] = [
        json.dumps(netz.messe_alle(), sort_keys=True) for _ in range(3)
    ]


@then("sind die drei Messungen bitgleich")
def bitgleich(kontext):
    assert len(set(kontext["laeufe"])) == 1


@given('das gemessene "erlaubt" im Fall voll_debatte_gewonnen ist falsch')
def zulassung_veraendert(kontext):
    kontext["referenz"] = netz.lade_referenz()
    kontext["ist"] = copy.deepcopy(kontext["referenz"])
    assert netz.veraendere_die_zulassung(kontext["ist"])


@given(
    "die gemessene zu schliessende Position im Fall voll_debatte_gewonnen ist eine "
    "andere"
)
def verdraengung_veraendert(kontext):
    kontext["referenz"] = netz.lade_referenz()
    kontext["ist"] = copy.deepcopy(kontext["referenz"])
    assert netz.veraendere_die_verdraengung(kontext["ist"])


@when("das Netz gegen die Referenz vergleicht")
def vergleiche(kontext):
    kontext["befunde"] = netz.befunde(kontext["referenz"], kontext["ist"])


@then("meldet es einen Befund")
def befund(kontext):
    assert kontext["befunde"], "Veränderung blieb ohne Befund"
    assert kontext["befunde"][0][0] == netz.ABWEICHUNG
