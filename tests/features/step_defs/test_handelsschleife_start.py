"""#3489 (ARC-E2.13) — die Startschritte der Handelsschleife tragen Outbox und Sperre (Epic #3367).

Die bisherige Kette ruft die Absendestelle direkt; der Outbox-Abgleich beim Start und der Erwerb
der Schreibberechtigung im Zyklus waren nur mit Unit-Tests abgenommen. Hier laeuft ein
Engine-Prozess genau die Schritte von ``live_trading_loop`` — ``tests/chain/_engine_start.py``.

Plan: ``docs/3489-abnahme-handelsschleife/implementation_plan.md`` (PR #3492, ``plan-approved``),
Option A: Start- und Zyklusschritte der echten Schleife samt Reihenfolge-Pruefung.
"""

from __future__ import annotations

import asyncio

from pytest_bdd import given, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung
from tests.chain.zwei_instanzen import ZweiInstanzenVorrichtung

scenarios("../handelsschleife_start.feature")

UHR = "2026-09-16 09:35"
SEED = 7
_MODUL = "tests.chain._engine_start"


def _lauf(vorrichtung, **umgebung):
    return vorrichtung.lauf(modul=_MODUL, umgebung_zusatz=umgebung)


def _ereignisse(vorrichtung, art):
    return [e for e in vorrichtung.beobachtung() if e.get("ereignis") == art]


# ---------------------------------------------------------------------------
# Reihenfolge
# ---------------------------------------------------------------------------


@given("die Startschritte der Handelsschleife", target_fixture="quelle")
def _quelle():
    """Der Quelltext von ``live_trading_loop`` — gelesen, nicht importiert: Ein Import von
    ``core.engine`` bootet die ganze Engine im Testprozess (``core/lease.py`` erklaert, warum).
    """
    import ast
    from pathlib import Path

    pfad = Path(__file__).resolve().parents[3] / "core" / "engine" / "trading_loop.py"
    text = pfad.read_text(encoding="utf-8")
    for knoten in ast.walk(ast.parse(text)):
        if (
            isinstance(knoten, ast.AsyncFunctionDef)
            and knoten.name == "live_trading_loop"
        ):
            return ast.get_source_segment(text, knoten)
    raise AssertionError("live_trading_loop nicht gefunden")


@then(
    "ruft live_trading_loop Outbox-Abgleich, Erwerb und Zyklus-Erwerb in dieser Reihenfolge"
)
def _reihenfolge(quelle):
    abgleich = quelle.index("await self._start_outbox_abgleich()")
    erwerb = quelle.index("await self._sichere_schreibberechtigung()")
    schleife = quelle.index("while self.strategy_running.is_set()")
    im_zyklus = quelle.index("await self._sichere_schreibberechtigung()", schleife)
    assert abgleich < erwerb < schleife < im_zyklus, (
        "live_trading_loop hat seine Startschritte geaendert — die Abnahme folgt der alten "
        "Reihenfolge (tests/chain/_engine_start.py) und prueft damit nicht mehr die Schleife."
    )


# ---------------------------------------------------------------------------
# Outbox-Abgleich beim Start
# ---------------------------------------------------------------------------


@given(
    "ein Engine-Prozess stirbt zwischen Absenden und Bestaetigung",
    target_fixture="vorrichtung",
)
def _tod_im_broker(tmp_path):
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    erster = _lauf(v, KETTE_TOETEN_BEI="im_broker")
    assert not erster.sauber_beendet, (
        "Der Prozess ist nicht im Broker-Fenster gestorben — dann misst dieses Szenario "
        "nichts.\n" + erster.ausgabe[-1500:]
    )
    assert len(v.broker_orders()) == 1, "Der Broker muss die Order dauerhaft halten."
    return v


@when("die Engine mit ihren Startschritten neu startet")
def _neustart_nur_start(vorrichtung):
    lauf = _lauf(vorrichtung, KETTE_OHNE_EINSTIEG="1")
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@then("ist der Intent in der Outbox bestaetigt")
def _outbox_bestaetigt(vorrichtung):
    from core.outbox import Outbox
    from core.state import SqliteStateAdapter

    (intent,) = _ereignisse(vorrichtung, "intent")
    coid = intent["client_order_id"]
    broker_id = vorrichtung.broker_orders()[0]["broker_order_id"]

    async def _lesen():
        port = SqliteStateAdapter(vorrichtung.verzeichnis / "engine_state.db")
        try:
            return await Outbox(port).lese(coid)
        finally:
            await port.aclose()

    eintrag = asyncio.run(_lesen())
    assert eintrag is not None, "Der Intent fehlt in der Outbox."
    assert (eintrag.zustand, eintrag.broker_order_id) == (
        "bestaetigt",
        broker_id,
    ), f"Der Start hat den Intent nicht beim Broker abgeglichen: {eintrag}"


@then("haelt der Broker genau eine Order")
def _eine_order(vorrichtung):
    orders = vorrichtung.broker_orders()
    assert len(orders) == 1, [o["client_order_id"] for o in orders]


# ---------------------------------------------------------------------------
# Zwei Engines, ein Konto
# ---------------------------------------------------------------------------


@given(
    "zwei Engine-Prozesse durchlaufen gleichzeitig ihre Startschritte",
    target_fixture="vorrichtung",
)
def _zwei_engines(tmp_path):
    v = ZweiInstanzenVorrichtung(tmp_path, seed=SEED, uhr=UHR, modul=_MODUL)
    ergebnisse = v.beide_starten()
    assert [i.rueckgabecode for i in ergebnisse] == [0, 0], [
        i.ausgabe[-1200:] for i in ergebnisse
    ]
    return v


@then("eine Engine hat gemeldet, dass sie nicht handelt")
def _eine_meldet(vorrichtung):
    nicht = _ereignisse(vorrichtung, "nicht_gehandelt")
    assert len(nicht) == 1, vorrichtung.beobachtung()


# ---------------------------------------------------------------------------
# Abloesung einer toten Engine
# ---------------------------------------------------------------------------


@given(
    "eine Engine haelt die Schreibberechtigung und stirbt hart",
    target_fixture="vorrichtung",
)
def _tod_nach_erwerb(tmp_path):
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    erster = _lauf(v, KETTE_TOETEN_BEI="nach_erwerb")
    assert not erster.sauber_beendet, erster.ausgabe[-1500:]
    (berechtigung,) = _ereignisse(v, "berechtigung")
    assert (
        berechtigung["erhalten"] is True
    ), "Die erste Engine muss Schreiber gewesen sein."
    return v


@when("sie auf demselben Rechner sofort neu startet")
def _sofortiger_neustart(vorrichtung):
    lauf = _lauf(vorrichtung)
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@then("handelt sie im ersten Zyklus")
def _handelt_sofort(vorrichtung):
    berechtigungen = _ereignisse(vorrichtung, "berechtigung")
    assert len(berechtigungen) == 2 and berechtigungen[1]["erhalten"] is True, (
        "Der Neustart hat die Berechtigung nicht sofort bekommen — die tote Engine hielt "
        "sie bis zum Ablauf der Frist (Owner-Entscheid 18.09.: tote Halter uebernehmen)."
    )
    assert len(vorrichtung.broker_orders()) == 1
    assert not _ereignisse(vorrichtung, "nicht_gehandelt")
