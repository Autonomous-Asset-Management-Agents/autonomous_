"""Abnahme auf Epic-Ebene für #3863 — beim Anlegen rot (#3785).

Diese Datei ist vom Maker erzeugt. Sie beschreibt, woran man erkennt, dass das
Epic erfüllt ist, und nicht, wie es umgesetzt wird. Wer ein Sub-Issue umsetzt,
ersetzt hier die Schritte, die sein Teil betrifft, durch echte Prüfungen.

Der xfail-Marker fällt erst, wenn alle Schritte echt sind — bis dahin hält er
die Suite grün, ohne die offene Abnahme zu verstecken."""

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../epic_3863.feature")

pytestmark = pytest.mark.xfail(
    reason="Epic #3863 ist nicht umgesetzt — die Abnahme ist offen.",
    strict=True,
)

_OFFEN = (
    "Schritt aus der Abnahme von #3863 ist noch nicht hinterlegt. "
    "Wer das zugehörige Sub-Issue umsetzt, ersetzt ihn durch eine echte Prüfung."
)


@given("die Sichtbarkeitspruefungen aus FAB-1 laufen ueber den Stand von main")
def schritt_1():
    raise NotImplementedError(_OFFEN)


@when("der Bericht ueber die Rueckmeldung der Fabrik erzeugt wird")
def schritt_2():
    raise NotImplementedError(_OFFEN)


@then(
    "nennt er keinen Agenten-Workflow, der ohne sein Kernkommando success melden kann"
)
def schritt_3():
    raise NotImplementedError(_OFFEN)


@then("kein Testverzeichnis, das in keinem Workflow vorkommt")
def schritt_4():
    raise NotImplementedError(_OFFEN)


@then("keinen Cron-Workflow, der einen Lauf ohne Auftrag als success abschliesst")
def schritt_5():
    raise NotImplementedError(_OFFEN)


@then(
    "keinen Agenten-Workflow, dessen letztes Lebenszeichen aelter ist als die vereinbarte Frist"
)
def schritt_6():
    raise NotImplementedError(_OFFEN)
