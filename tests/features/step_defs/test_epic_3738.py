"""Abnahme auf Epic-Ebene für #3738 — beim Anlegen rot (#3785).

Diese Datei ist vom Maker erzeugt. Sie beschreibt, woran man erkennt, dass das
Epic erfüllt ist, und nicht, wie es umgesetzt wird. Wer ein Sub-Issue umsetzt,
ersetzt hier die Schritte, die sein Teil betrifft, durch echte Prüfungen.

Der xfail-Marker fällt erst, wenn alle Schritte echt sind — bis dahin hält er
die Suite grün, ohne die offene Abnahme zu verstecken."""

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../epic_3738.feature")

pytestmark = pytest.mark.xfail(
    reason="Epic #3738 ist nicht umgesetzt — die Abnahme ist offen.",
    strict=True,
)

_OFFEN = (
    "Schritt aus der Abnahme von #3738 ist noch nicht hinterlegt. "
    "Wer das zugehörige Sub-Issue umsetzt, ersetzt ihn durch eine echte Prüfung."
)


@given("der Architektur-Vertrag `ai_trading_bot/tests/architecture/vertrag.toml`")
def schritt_1():
    raise NotImplementedError(_OFFEN)


@when("die Abnahme des Epics läuft")
def schritt_2():
    raise NotImplementedError(_OFFEN)


@then("enthält der Abschnitt `[modus]` den Eintrag `groessen = 'blockieren'`")
def schritt_3():
    raise NotImplementedError(_OFFEN)


@then("die eingetragene Dateischwelle ist höchstens 1000 Zeilen")
def schritt_4():
    raise NotImplementedError(_OFFEN)


@then("die eingetragene Funktionsschwelle ist höchstens 200 Zeilen")
def schritt_5():
    raise NotImplementedError(_OFFEN)


@given("der gemessene Produktivcode unter `ai_trading_bot/` ohne Tests und Vendor")
def schritt_6():
    raise NotImplementedError(_OFFEN)


@when("eine Datei über 2000 Zeilen oder eine Funktion über 400 Zeilen liegt")
def schritt_7():
    raise NotImplementedError(_OFFEN)


@when("die zugehörige Vertragszeile kein gefülltes Feld `begruendung` trägt")
def schritt_8():
    raise NotImplementedError(_OFFEN)


@then("schlägt die Abnahme fehl und nennt Datei und Zeile")
def schritt_9():
    raise NotImplementedError(_OFFEN)


@given(
    "die sechs Dateien über 2000 Zeilen und die acht Funktionen über 400 Zeilen aus dem Befund vom 28./30.09.2026"
)
def schritt_10():
    raise NotImplementedError(_OFFEN)


@when("die Abnahme läuft")
def schritt_11():
    raise NotImplementedError(_OFFEN)


@then(
    "liegt jede dieser Einheiten unter ihrer Stufe-1-Zahl oder trägt eine begründete Vertragszeile"
)
def schritt_12():
    raise NotImplementedError(_OFFEN)


@given("eine Einheit mit eingetragener Obergrenze im Vertrag")
def schritt_13():
    raise NotImplementedError(_OFFEN)


@when("ihr gemessener Wert unter die eingetragene Zahl fällt")
def schritt_14():
    raise NotImplementedError(_OFFEN)


@then("schlägt die Abnahme fehl, bis die Zahl im Vertrag mitgesenkt ist")
def schritt_15():
    raise NotImplementedError(_OFFEN)
