"""#2783 Inkrement 2 — der wahre Order-Endzustand in beiden Ebenen.

**Der Bruch.** Die Kette weiss, dass eine Order *freigegeben* wurde, aber nicht, was aus
ihr geworden ist. ``HITLExecutionEvent`` traegt den Nominalwert (``order_value``) und den
Zweig — **nicht** Menge, Ausfuehrungspreis, Status oder Broker-Order-ID. Und die
analytische Senke ``log_trade(TradeRecord)`` (``cloud_logger.py:958``) hat seit ihrem
Bestehen **null Produktionsaufrufer**: Der einzige Treffer im Bestand ist die Simulation.

Damit ist die Ausfuehrung nicht rekonstruierbar: Eine abgesetzte Order, die nie gefuellt
wurde, sieht im Pruefprotokoll aus wie eine, die gefuellt wurde.

**Warum beide Ebenen und nicht nur eine** (Korrektur gegenueber dem urspruenglichen
#2909-Plan): Der Endzustand ist eine **audit-relevante Tatsache** und gehoert auf die
verkettete Ebene. Die analytische Tabelle daneben traegt dieselbe Tatsache in einer Form,
die sich auswerten laesst. Nur die Tabelle zu fuellen hiesse, den Nachweis in einer Senke
zu fuehren, die niemand verkettet.

**Die Ausfallrichtung ist die wichtigste Zusage dieses Inkrements.** Die Erfassung ist
**reine Beobachtung**: Sie darf den Orderpfad unter keinen Umstaenden zum Stehen bringen.
Eine Order, die beim Broker liegt, ist eine Tatsache — ob wir sie aufschreiben koennen,
aendert daran nichts. Darum schluckt die Erfassung jeden Fehler und meldet ihn.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

pytestmark = pytest.mark.unit


class _BrokerOrder:
    """Eine Broker-Order, wie der Bestand sie liefert — Attribute, kein Vertrag."""

    def __init__(self, **felder):
        vorgabe = {
            "id": "brk-1",
            "client_order_id": "entry-0-entscheidung-9",
            "symbol": "AAPL",
            "side": "buy",
            "filled_qty": "3",
            "filled_avg_price": "101.25",
            "status": "filled",
        }
        vorgabe.update(felder)
        for k, v in vorgabe.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Ebene 1 — die verkettete WORM-Ebene
# ---------------------------------------------------------------------------


def test_das_audit_ereignis_kennt_den_endzustand() -> None:
    """Szenario: Das Pruefereignis nennt Menge, Preis, Status und Order-ID.

    Heute rot: ``HITLExecutionEvent`` hat acht Felder, und keines davon sagt, was aus der
    Order geworden ist. Ein ``branch='approved'`` belegt, dass sie den Broker erreicht hat
    — nicht, dass sie ausgefuehrt wurde.
    """
    from core.round_table.senate_log import HITLExecutionEvent

    ereignis = HITLExecutionEvent(
        timestamp="2026-09-18T06:00:00+00:00",
        symbol="AAPL",
        action="BUY",
        branch="approved",
        policy_hash="p-1",
        order_value=303.75,
        decision_id="entscheidung-9",
        broker_order_id="brk-1",
        filled_qty=3.0,
        filled_avg_price=101.25,
        order_status="filled",
    )

    als_dict = asdict(ereignis)
    for feld, erwartet in (
        ("decision_id", "entscheidung-9"),
        ("broker_order_id", "brk-1"),
        ("filled_qty", 3.0),
        ("filled_avg_price", 101.25),
        ("order_status", "filled"),
    ):
        assert als_dict.get(feld) == erwartet, (
            f"Das Pruefereignis traegt '{feld}' nicht. Ohne den Endzustand ist eine "
            "abgesetzte Order im Protokoll nicht von einer ausgefuehrten zu "
            "unterscheiden (#2783 Inkrement 2)."
        )


def test_die_neuen_felder_erreichen_die_kette() -> None:
    """Der Serialisierer darf die neuen Felder nicht unterwegs verlieren.

    ``_hitl_event_to_dict`` benutzt ``asdict`` ohne Feldliste — das ist der Grund, warum
    die Erweiterung durchreicht. Geprueft statt geglaubt: Eine spaetere Umstellung auf
    eine feste Liste wuerde die Felder still schlucken, und der Datensatz saehe
    vollstaendig aus.
    """
    from core.round_table.senate_log import HITLExecutionEvent, _hitl_event_to_dict

    eintrag = _hitl_event_to_dict(
        HITLExecutionEvent(
            timestamp="2026-09-18T06:00:00+00:00",
            symbol="AAPL",
            action="BUY",
            branch="approved",
            policy_hash="p-1",
            order_value=303.75,
            filled_qty=3.0,
            order_status="filled",
        )
    )
    assert eintrag["event_type"] == "hitl_execution"
    assert eintrag.get("filled_qty") == 3.0
    assert eintrag.get("order_status") == "filled"
    # Der Datensatz muss serialisierbar bleiben — er wird gehasht.
    json.dumps(eintrag, sort_keys=True)


# ---------------------------------------------------------------------------
# Ebene 2 — die analytische Senke
# ---------------------------------------------------------------------------


def test_die_analytische_senke_bekommt_den_broker_endzustand() -> None:
    """Szenario: ``log_trade`` wird aus dem BROKER-Objekt gespeist, nicht aus der Absicht.

    Der Unterschied ist der ganze Punkt: Die Absicht sagt, was wir wollten; das
    Broker-Objekt sagt, was geschah. Eine Erfassung aus der Absicht waere eine Kopie
    unserer eigenen Annahme.
    """
    from core.engine.order_executor import baue_trade_record

    satz = baue_trade_record(
        _BrokerOrder(), decision_id="entscheidung-9", strategy_name="RLAgent"
    )

    assert satz.symbol == "AAPL"
    assert satz.qty == 3.0, "Die Menge stammt nicht aus filled_qty."
    assert satz.price == 101.25, "Der Preis stammt nicht aus filled_avg_price."
    assert satz.order_status == "filled"
    assert satz.decision_id == "entscheidung-9"
    assert satz.total_value == pytest.approx(3.0 * 101.25)


def test_ein_nicht_gefuellter_auftrag_gilt_nie_als_erfolg() -> None:
    """Szenario: Ein stornierter Auftrag wird als solcher erfasst.

    Der Vorgabewert von ``TradeRecord.order_status`` ist ``"filled"``. Wer den Satz baut,
    ohne den Status zu uebernehmen, erklaert damit jede Order zum Erfolg — auch die, die
    nie ausgefuehrt wurde. Das Ticket verlangt ausdruecklich das Gegenteil: nicht
    gefuellte Auftraege werden erfasst, **nie als Erfolg**.
    """
    from core.engine.order_executor import baue_trade_record

    satz = baue_trade_record(
        _BrokerOrder(status="canceled", filled_qty="0", filled_avg_price="0"),
        decision_id="entscheidung-9",
    )

    assert satz.order_status == "canceled", (
        "Ein stornierter Auftrag traegt den Vorgabewert 'filled' — er gilt damit als "
        "ausgefuehrt. Genau das darf nicht passieren (#2783 Inkrement 2)."
    )
    assert satz.qty == 0.0
    assert satz.total_value == 0.0


# ---------------------------------------------------------------------------
# Die Zusage, die den Eingriff in den Orderpfad ueberhaupt vertretbar macht
# ---------------------------------------------------------------------------


async def test_beide_senken_werden_wirklich_erreicht(monkeypatch) -> None:
    """Szenario: Der Endzustand kommt in BEIDEN Ebenen an — nachgewiesen, nicht vermutet.

    **Dieser Test ist die Lehre aus einem eigenen Fehler.** Die Erfassung kapselt jede
    Senke in einen fehlerschluckenden Block — das ist richtig, weil eine Ausnahme den
    Orderpfad abbraeche, nachdem Kapital bewegt wurde. Aber dieselbe Kapselung
    **versteckt Verdrahtungsfehler**: Meine erste Fassung importierte
    ``cloud_logger`` (den es nicht gibt; die Senke heisst ``logger_instance``), der
    Import scheiterte still im Fehlerblock — und die Testsuite blieb gruen, obwohl in die
    analytische Senke nie etwas geschrieben wurde.

    Ein fehlerschluckender Wrapper braucht darum immer einen Test, der den **Erfolgsfall**
    positiv nachweist. Sonst prueft man nur, dass nichts explodiert.
    """
    from core import cloud_logger as cl
    from core import hitl_gate as hg
    from core.engine import order_executor as oe

    ketten_eintraege = []
    senken_saetze = []

    async def _kette(ereignis):
        ketten_eintraege.append(ereignis)

    class _Senke:
        def log_trade(self, satz):
            senken_saetze.append(satz)

    monkeypatch.setattr(hg, "log_execution_event", _kette)
    monkeypatch.setattr(hg, "policy_snapshot", lambda: {})
    monkeypatch.setattr(hg, "policy_hash", lambda _s: "p-1")
    monkeypatch.setattr(cl, "logger_instance", _Senke())

    await oe.erfasse_order_endzustand(
        order=_BrokerOrder(),
        symbol="AAPL",
        action="BUY",
        decision_id="entscheidung-9",
        order_value=303.75,
    )

    assert ketten_eintraege, (
        "Die verkettete Pruefebene hat keinen Eintrag bekommen. Der Fehlerblock hat "
        "etwas geschluckt (#2783 Inkrement 2)."
    )
    assert ketten_eintraege[0].filled_qty == 3.0
    assert ketten_eintraege[0].order_status == "filled"
    assert ketten_eintraege[0].decision_id == "entscheidung-9"

    assert senken_saetze, (
        "Die analytische Senke hat nichts bekommen. Genau dieser Fall war bei meinem "
        "ersten Entwurf gruen, obwohl nichts geschrieben wurde."
    )
    assert senken_saetze[0].qty == 3.0
    assert senken_saetze[0].decision_id == "entscheidung-9"


async def test_die_erfassung_wirft_nie_in_den_orderpfad(monkeypatch) -> None:
    """Szenario: Eine kaputte Erfassung haelt den Handel nicht auf.

    Die Order liegt zu diesem Zeitpunkt bereits beim Broker. Ob wir sie aufschreiben
    koennen, aendert daran **nichts** — aber eine Ausnahme an dieser Stelle wuerde den
    Pfad abbrechen, nachdem Kapital bewegt wurde. Das waere schlimmer als ein fehlender
    Datensatz.

    Der Test sabotiert beide Senken gleichzeitig und verlangt, dass die Erfassung
    trotzdem zurueckkehrt.
    """
    from core.engine import order_executor as oe

    def _kaputt(*_a, **_kw):
        raise RuntimeError("Senke kaputt")

    async def _kaputt_async(*_a, **_kw):
        raise RuntimeError("Kette kaputt")

    monkeypatch.setattr(oe, "baue_trade_record", _kaputt, raising=False)

    import core.hitl_gate as hg

    monkeypatch.setattr(hg, "log_execution_event", _kaputt_async, raising=False)

    # Darf NICHT werfen.
    await oe.erfasse_order_endzustand(
        order=_BrokerOrder(),
        symbol="AAPL",
        action="BUY",
        decision_id="entscheidung-9",
        order_value=303.75,
    )


async def test_ohne_order_objekt_passiert_nichts_schlimmes() -> None:
    """Ein fehlendes Broker-Objekt ist kein Grund, etwas zu erfinden.

    Lieber kein Datensatz als ein erfundener: Ein Endzustand, den wir nicht kennen, darf
    nicht als Null-Fuellung erscheinen — das waere von einer echten Null-Fuellung nicht zu
    unterscheiden.
    """
    from core.engine import order_executor as oe

    await oe.erfasse_order_endzustand(
        order=None,
        symbol="AAPL",
        action="BUY",
        decision_id="entscheidung-9",
        order_value=303.75,
    )
