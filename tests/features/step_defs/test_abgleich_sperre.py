"""#3488 (ARC-E2.12) — die Abgleich-Sperre sperrt und wird von einem Menschen aufgehoben.

**Gegen ``main`` ohne #3491 ROT, und das ist der Auftrag.** Die Sperre aus #3389 war sichtbar und
aufhebbar (#3430), hielt aber keine Order zurueck: ``blocks()`` hatte keinen Aufrufer im
Order-Pfad. Gruen wird die Abnahme mit PR #3495.

Plan: ``docs/3488-abnahme-abgleich-sperre/implementation_plan.md`` (PR #3492, ``plan-approved``).
Der Prozess: ``tests/chain/_abgleich_sperre.py``.
"""

from __future__ import annotations

import json

from pytest_bdd import given, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung

scenarios("../abgleich_sperre.feature")

UHR = "2026-09-16 09:35"
SEED = 7


def _ereignisse(vorrichtung, art, **filter_):
    return [
        e
        for e in vorrichtung.beobachtung()
        if e.get("ereignis") == art and all(e.get(k) == v for k, v in filter_.items())
    ]


@given(
    "beim Broker liegt eine Order, die die Engine nicht kennt, und die Sperrwirkung ist an",
    target_fixture="vorrichtung",
)
def _vorrichtung(tmp_path):
    return ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)


@when("der Abgleich laeuft")
def _abgleich(vorrichtung):
    lauf = vorrichtung.lauf(
        modul="tests.chain._abgleich_sperre",
        umgebung_zusatz={
            "RECONCILIATION_BLOCK_ON_BREAK": "true",
            # Der Desktop hat keinen Signatur-Proxy (resolve_require_sig: aus ohne Cloud
            # Run). Die Wurzel-conftest setzt REQUIRE_SIG fuer den Testprozess, und der
            # Kettenprozess erbte es — er waere dann ein Enterprise-Server ohne Proxy.
            "REQUIRE_SIG": "false",
        },
    )
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-2000:]


@then("ist die Sperre gesetzt")
def _gesetzt(vorrichtung):
    (abgleich,) = _ereignisse(vorrichtung, "abgleich")
    assert abgleich["abweichungen"] >= 1 and abgleich["gesperrt"] is True, abgleich


@then("wird ein Einstieg zurueckgehalten")
def _einstieg_gesperrt(vorrichtung):
    (versuch,) = _ereignisse(vorrichtung, "einstieg", phase="gesperrt")
    assert versuch["ergebnis"] == "gesperrt", (
        "Der Einstieg ging trotz gesetzter Abgleich-Sperre hinaus — blocks() wird im "
        "Order-Pfad nicht gefragt (#3491)."
    )


@then("geht ein Schutz-Exit hinaus")
def _schutz_exit(vorrichtung):
    (versuch,) = _ereignisse(vorrichtung, "schutz_exit", phase="gesperrt")
    assert versuch["ergebnis"] == "gesendet", versuch


@then("findet der Folgelauf keine Abweichung mehr")
def _folgelauf_sauber(vorrichtung):
    (folgelauf,) = _ereignisse(vorrichtung, "folgelauf")
    assert folgelauf["sauber"] is True, folgelauf


@then("bleibt die Sperre bestehen")
def _bleibt(vorrichtung):
    (folgelauf,) = _ereignisse(vorrichtung, "folgelauf")
    assert (
        folgelauf["gesperrt"] is True
    ), "Ein sauberer Folgelauf hat die Sperre aufgehoben — das darf nur ein Mensch (#3389)."


@then("nimmt der Engine-Endpunkt die Aufhebung an")
def _aufhebung(vorrichtung):
    (aufhebung,) = _ereignisse(vorrichtung, "aufhebung")
    assert aufhebung["status"] == 200, aufhebung


@then("geht der naechste Einstieg hinaus")
def _einstieg_frei(vorrichtung):
    (versuch,) = _ereignisse(vorrichtung, "einstieg", phase="aufgehoben")
    assert versuch["ergebnis"] == "gesendet", versuch


@then("traegt das Protokoll Zeitpunkt, Urheber und die freigegebene Abweichung")
def _protokoll(vorrichtung):
    zeilen = (
        (vorrichtung.verzeichnis / "reconciliation_audit.log")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    satz = json.loads(zeilen[-1])
    assert satz["event"] == "release" and satz["ts"] and satz["by"], satz
    assert satz["breaks"], "Festgehalten werden muss auch, WAS freigegeben wurde."
