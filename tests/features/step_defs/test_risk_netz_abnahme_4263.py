"""#4263 (H-3a) — Netz für Konto, Halt, Liquidation und Vorprüfung als Abnahme.

Feature: ``tests/features/risk_netz_4263.feature``. Der Treiber ist
``tests/unit/_risk_netz.py``; die Wächter (kein Teilschritt gemockt, nur erlaubte
Patch-Ziele) stehen in ``tests/unit/test_risk_netz_4263.py``. Dieser Name weicht vom
Plan ab: Derselbe Dateiname kollidierte beim Sammeln mit dem Wächter (pytest importiert
beide als Modul ``test_risk_netz_4263``) — wie bei #4242.
"""

from __future__ import annotations

import copy
import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.unit import _risk_netz as netz

pytestmark = [pytest.mark.vc4]

scenarios("../risk_netz_4263.feature")


def _szenario(name: str) -> netz.Szenario:
    return next(sz for sz in netz.SZENARIEN if sz.name == name)


@pytest.fixture
def kontext() -> dict:
    return {}


def _fahre(kontext, name: str) -> None:
    kontext["szenario"] = _szenario(name)
    kontext["ist"] = netz.fahre(kontext["szenario"])


def _letzter(kontext) -> dict:
    return kontext["ist"]["schritte"][-1]


def _wie_referenz(kontext) -> None:
    name = kontext["szenario"].name
    referenz = netz.lade_referenz()[name]
    gefunden = netz.befunde({name: referenz}, {name: kontext["ist"]})
    assert not gefunden, "\n".join(" | ".join(b) for b in gefunden)


# ── Konto, Halt, Liquidation ────────────────────────────────────────────────


@given("ein echter RiskManager mit eigenem Halt und zwei gehaltenen Positionen")
def mit_positionen(kontext):
    kontext["name"] = "portfolio_stop"


@given(
    "ein echter RiskManager mit eigenem Halt, zwei Positionen und abgeschaltetem "
    "Portfolio-Stop"
)
def ohne_portfolio_stop(kontext):
    kontext["name"] = "tages_limit"


@given("ein echter RiskManager mit eigenem Halt und einem Broker ohne Positionsabfrage")
def ohne_positionsabfrage(kontext):
    kontext["name"] = "tages_limit_ohne_positionsabfrage"


@when("das Eigenkapital den Portfolio-Stop reisst")
@when("der Drawdown das Tages-Limit ueberschreitet")
def konto_faellt(kontext):
    _fahre(kontext, kontext["name"])


@then("ist der eigene Halt gesetzt")
@then("der eigene Halt ist gesetzt")
def eigener_halt_gesetzt(kontext):
    letzter = _letzter(kontext)
    assert letzter["zustand"]["trading_halted"] is True
    assert letzter["eigener_halt"] is True


@then("Orders am Broker-Client und Risiko-Ereignisse entsprechen der Referenz")
@then("gehen die Orders aus der Referenz durch das echte Tor an den Broker-Client")
def wie_referenz(kontext):
    _wie_referenz(kontext)


@then(parsers.parse('jede traegt die Absicht "{absicht}"'))
def absicht(kontext, absicht):
    letzter = _letzter(kontext)
    orders = [a for a in letzter["broker"] if a["methode"] == "submit_order"]
    assert orders, "keine Order durch das Tor"
    assert len(letzter["tor"]) == len(orders)
    assert all(t["grund"] == absicht for t in letzter["tor"]), letzter["tor"]


@then("geht keine Order an den Broker-Client")
def keine_order(kontext):
    _wie_referenz(kontext)
    assert not [
        a for a in _letzter(kontext)["broker"] if a["methode"] == "submit_order"
    ]


@when("der Drawdown zwischen 60 und 100 Prozent des Tages-Limits liegt")
def warnstufe(kontext):
    _fahre(kontext, "warnstufe")


@then("ist die Bemessung reduziert, der Halt nicht gesetzt und keine Order gesendet")
def reduziert(kontext):
    _wie_referenz(kontext)
    erster = kontext["ist"]["schritte"][0]
    assert erster["zustand"]["trading_reduced"] is True
    assert erster["zustand"]["trading_halted"] is False
    assert erster["broker"] == []


@then("nach der Erholung unter 50 Prozent ist die Bemessung wieder normal")
def wieder_normal(kontext):
    assert _letzter(kontext)["zustand"]["trading_reduced"] is False


@given("der Breaker hat ausgeloest")
def breaker_ausgeloest(kontext):
    kontext["praefix"] = "erholung_"


@when(parsers.parse("das Eigenkapital sich erholt im Fall {fall}"))
def erholung(kontext, fall):
    _fahre(kontext, kontext["praefix"] + fall)


@then(parsers.parse("entspricht der Halt-Zustand der Referenz fuer {fall}"))
def halt_wie_referenz(kontext, fall):
    assert kontext["szenario"].name.endswith(fall)
    _wie_referenz(kontext)


@then(parsers.parse("der Halt ist {halt}"))
def halt_ist(kontext, halt):
    erwartet = {"gesetzt": True, "aufgehoben": False}[halt]
    letzter = _letzter(kontext)
    assert letzter["zustand"]["trading_halted"] is erwartet
    assert letzter["eigener_halt"] is erwartet


@given("der Portfolio-Stop hat ausgeloest")
def portfolio_stop_ausgeloest(kontext):
    kontext["name"] = "erholung_nach_portfolio_stop"


@when("das Eigenkapital sich vollstaendig erholt")
def voll_erholt(kontext):
    _fahre(kontext, kontext["name"])


@then("ist der eigene Halt weiter gesetzt")
def weiter_gesetzt(kontext):
    _wie_referenz(kontext)
    eigener_halt_gesetzt(kontext)


@when("reset_daily_limit mit neuem Eigenkapital laeuft")
def reset(kontext):
    _fahre(kontext, "reset_daily_limit")


@then("sind Tages-Limit und Halt-Zustand wie in der Referenz")
def reset_wie_referenz(kontext):
    _wie_referenz(kontext)


# ── Vorprüfung ───────────────────────────────────────────────────────────────


@when("evaluate_new_trade ohne bestaetigten VIX laeuft")
def ohne_vix(kontext):
    _fahre(kontext, "vix_unbestaetigt")


@then("wird ein Kauf abgelehnt und ein Verkauf freigegeben")
def kauf_ab_verkauf_frei(kontext):
    _wie_referenz(kontext)
    kauf, verkauf = kontext["ist"]["schritte"]
    assert kauf["ergebnis"][0] is False
    assert verkauf["ergebnis"][0] is True


@given("eine aktive Regel block_trade passt auf den Trade")
def regel(kontext):
    kontext["name"] = "ki_regel_blockiert"


@given("der eigene Halt ist gesetzt")
def halt_vorher(kontext):
    kontext["name"] = "halt_ausgang"


@when("evaluate_new_trade laeuft")
def pruefe(kontext):
    _fahre(kontext, kontext["name"])


@then("ist der Trade abgelehnt")
def abgelehnt(kontext):
    assert kontext["ist"]["schritte"][0]["ergebnis"][0] is False


@then(
    'der Span "risk.evaluate_trade" traegt symbol, trade.side, risk.approved und '
    "risk.reason wie in der Referenz"
)
def span_wie_referenz(kontext):
    _wie_referenz(kontext)
    (span,) = kontext["ist"]["schritte"][0]["spans"]
    assert span["name"] == "risk.evaluate_trade"
    assert {"symbol", "trade.side", "risk.approved", "risk.reason"} <= set(
        span["attribute"]
    )


@then("wird der Trade ohne Span abgelehnt")
def ohne_span(kontext):
    _wie_referenz(kontext)
    erster = kontext["ist"]["schritte"][0]
    assert erster["ergebnis"][0] is False
    assert erster["spans"] == []


# ── Netz als Ganzes ──────────────────────────────────────────────────────────


@when("alle Szenarien gelaufen sind")
def alle(kontext):
    kontext["alle"] = netz.messe_alle()


@then("wurde der globale Kill-Switch weder ausgeloest noch aufgehoben")
def global_unberuehrt(kontext):
    for name, wert in kontext["alle"].items():
        assert wert["globaler_halt"] == {"resets": 0, "trips": 0}, name


@when("alle Szenarien dreimal hintereinander laufen")
def dreimal(kontext):
    kontext["laeufe"] = [
        json.dumps(netz.messe_alle(), sort_keys=True) for _ in range(3)
    ]


@then("sind die drei Messungen bitgleich")
def bitgleich(kontext):
    assert len(set(kontext["laeufe"])) == 1


@given("die gemessene Menge einer Breaker-Order weicht um ein Stueck ab")
def menge_veraendert(kontext):
    kontext["referenz"] = netz.lade_referenz()
    kontext["ist"] = copy.deepcopy(kontext["referenz"])
    assert netz.veraendere_eine_menge(kontext["ist"])


@given('der gemessene Halt-Zustand nach dem Tages-Limit ist "nicht gehalten"')
def halt_veraendert(kontext):
    kontext["referenz"] = netz.lade_referenz()
    kontext["ist"] = copy.deepcopy(kontext["referenz"])
    assert netz.veraendere_den_halt(kontext["ist"])


@when("das Netz gegen die Referenz vergleicht")
def vergleiche(kontext):
    kontext["befunde"] = netz.befunde(kontext["referenz"], kontext["ist"])


@then("meldet es einen Befund")
def befund(kontext):
    assert kontext["befunde"], "Veränderung blieb ohne Befund"
    assert kontext["befunde"][0][0] == netz.ABWEICHUNG
