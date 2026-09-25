"""#3447 Schritt 2 — die Hauptabsendung des Executors geht durchs Tor.

``_submit_with_market_failsafe`` ist die Stelle, ueber die der Mandanten-Pfad seine Orders
absetzt — Erstversuch **und** Markt-Rueckfall (#2558). Beide riefen den Broker bisher
unmittelbar (``asyncio.to_thread(client.submit_order, …)``) und erzeugten damit **keine**
``ComplianceDecision``: Die Pruefkette konnte fuer diese Orders nicht beantworten, welche
Regel sie durchgelassen hat, und ein gescheiterter Broker-Aufruf hinterliess keinen
Datensatz.

**Was sich NICHT aendern darf** — und wofuer es je einen Gegentest gibt:

* Der Rueckgabewert bleibt das Broker-Order-Objekt; der Aufrufer pollt damit.
* Eine Broker-Ablehnung (``APIError``) eines Nicht-Exits erreicht den Aufrufer
  unveraendert — daran haengen Verdraengungs-Recovery und Tagesslot-Rueckgabe.
* Der Markt-Rueckfall eines abgelehnten Limit-Exits feuert weiter, mit dem Folgeschluessel
  aus ``next_attempt`` (#3387).
* Ein Schutz-Exit passiert auch bei Halt (#3380) — das Urteil faellt das Halt-Tor des
  Aufrufers, und das Tor hier faellt es nicht ein zweites Mal anders.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


def _executor():
    from core.engine.order_executor import OrderExecutorMixin

    return OrderExecutorMixin.__new__(OrderExecutorMixin)


def _req(coid="entry-0-entscheidung-5"):
    return MagicMock(client_order_id=coid, symbol="AAPL")


def _aufruf(executor, client, **abweichend):
    from alpaca.trading.enums import OrderSide

    argumente = dict(
        client=client,
        primary_req=_req(),
        symbol="AAPL",
        qty=2.0,
        side_enum=OrderSide.BUY,
        user_id="u1",
        is_exit=False,
        decision_id="entscheidung-5",
    )
    argumente.update(abweichend)
    return _run(executor._submit_with_market_failsafe(**argumente))


@pytest.fixture
def datensaetze(monkeypatch):
    """Faengt ab, was das Tor in die Senke schreibt — der Nachweis, dass es benutzt wurde."""
    from core.engine import order_executor as oe

    gesammelt = []
    monkeypatch.setattr(oe, "_record_gateway_decision", gesammelt.append)
    # Auf der KLASSE, nicht auf der Singleton-Instanz: monkeypatch schreibt beim
    # Zuruecksetzen den alten Wert per setattr zurueck. An einer Instanz bliebe die
    # gebundene Methode damit dauerhaft im __dict__ liegen und verdeckte jeden spaeteren
    # Klassen-Patch im selben Prozess (so gerissen: test_api_routes_health_halted).
    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    return gesammelt


# ---------------------------------------------------------------------------
# Der fuehrende rote Fall
# ---------------------------------------------------------------------------


def test_die_hauptabsendung_erzeugt_eine_compliancedecision(datensaetze) -> None:
    """Szenario: Jede Order der Hauptabsendung traegt eine ComplianceDecision.

    Heute rot: Der Pfad ruft ``client.submit_order`` unmittelbar, das Tor sieht die Order
    nie, und in der Senke landet nichts.
    """
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    order = _aufruf(_executor(), client)

    assert order is client.submit_order.return_value, (
        "Der Rueckgabewert ist nicht mehr das Broker-Order-Objekt — der Aufrufer pollt "
        "damit bis zur Fuellung."
    )
    assert len(datensaetze) == 1, (
        f"{len(datensaetze)} ComplianceDecisions fuer eine Order. Die Hauptabsendung geht "
        "am Tor vorbei — genau der Befund von #3366 (#3447 Schritt 2)."
    )
    assert datensaetze[0].decision_id == "entscheidung-5"
    assert datensaetze[0].approved is True


# ---------------------------------------------------------------------------
# Was sich nicht aendern darf
# ---------------------------------------------------------------------------


def test_eine_broker_ablehnung_erreicht_den_aufrufer_unveraendert(datensaetze) -> None:
    """An der ``APIError`` haengen Verdraengungs-Recovery und Slot-Rueckgabe."""
    from alpaca.common.exceptions import APIError

    client = MagicMock()
    client.submit_order.side_effect = APIError("insufficient buying power")

    with pytest.raises(APIError):
        _aufruf(_executor(), client)

    # Der gescheiterte Versuch ist eine Tatsache und gehoert in den Datensatz.
    assert len(datensaetze) == 1
    assert datensaetze[0].reason_code.value == "system_error"


def test_der_markt_rueckfall_feuert_weiter_und_geht_auch_durchs_tor(
    datensaetze,
) -> None:
    """#2558: Ein abgelehnter Limit-Exit faellt auf MARKET zurueck — die Position
    schliesst. Beide Versuche gehen durchs Tor; der Folgeversuch traegt den
    Folgeschluessel aus ``next_attempt`` (#3387), nicht denselben und keinen gewuerfelten.
    """
    from alpaca.common.exceptions import APIError
    from alpaca.trading.enums import OrderSide

    gesendet = []

    def _broker(req):
        gesendet.append(req)
        if len(gesendet) == 1:
            raise APIError("limit price too far")
        return MagicMock(id="brk-2")

    client = MagicMock()
    client.submit_order.side_effect = _broker

    order = _aufruf(
        _executor(),
        client,
        side_enum=OrderSide.SELL,
        is_exit=True,
        primary_req=_req("stop-0-entscheidung-5"),
    )

    assert order.id == "brk-2"
    assert len(gesendet) == 2, "Der Markt-Rueckfall hat nicht gefeuert."
    assert gesendet[1].client_order_id == "stop-1-entscheidung-5", (
        "Der Folgeversuch traegt nicht den Folgeschluessel aus next_attempt (#3387): "
        f"{gesendet[1].client_order_id!r}"
    )
    assert len(datensaetze) == 2, "Nicht beide Versuche sind im Datensatz."


def test_ein_schutz_exit_passiert_auch_bei_halt(datensaetze, monkeypatch) -> None:
    """#3380: Das Halt-Tor des Aufrufers hat den Schutz-Exit freigestellt — das Tor hier
    darf dieses Urteil nicht ein zweites Mal anders faellen."""
    from alpaca.trading.enums import OrderSide

    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: True
    )
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-3")

    order = _aufruf(
        _executor(),
        client,
        side_enum=OrderSide.SELL,
        is_exit=True,
        is_protective_exit=True,
    )

    assert order.id == "brk-3", "Der Schutz-Exit wurde am Halt blockiert."
    assert datensaetze[0].halted is True, "Der Halt-Zustand fehlt im Datensatz."


def test_ein_einstieg_bei_halt_erreicht_den_broker_nicht(
    datensaetze, monkeypatch
) -> None:
    """Tritt der Halt zwischen Halt-Tor und Absendung ein, ging die Order bisher trotzdem
    hinaus. Jetzt nicht mehr — und der Aufrufer sieht dieselbe Art Ausnahme, die
    ``check_halt`` wirft, damit seine Fehlerbehandlung unveraendert greift.
    """
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: True
    )
    client = MagicMock()

    with pytest.raises(Exception, match="(?i)halt"):
        _aufruf(_executor(), client)

    client.submit_order.assert_not_called()
    assert datensaetze[0].approved is False


def test_ein_unbrauchbarer_entscheidungsbezug_verhindert_keine_order(
    datensaetze,
) -> None:
    """Ein Buchfuehrungsproblem darf niemals eine Order verhindern.

    **Aus einem eigenen Fehler entstanden.** Meine erste Fassung reichte ``decision_id``
    ungeprueft in den ``OrderIntent``. Kam dort etwas anderes als eine Zeichenkette an,
    warf die Validierung — und **die Order ging nicht hinaus**. Sechzehn bestehende Tests
    wurden dadurch rot (dort ist der Kontext eine Attrappe).

    In Produktion ist der Wert immer eine Zeichenkette. Aber die Ausfallrichtung zaehlt:
    Waere der betroffene Auftrag ein Stop-Loss, bliebe die Position offen — wegen eines
    Feldes, das nur der Nachvollziehbarkeit dient. Der Datensatz darf schlechter werden,
    die Order nicht ausbleiben.
    """
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-9")

    order = _aufruf(
        _executor(),
        client,
        decision_id=MagicMock(name="kein-string"),
        primary_req=_req("ohne-struktur"),
    )

    assert (
        order.id == "brk-9"
    ), "Ein unbrauchbarer Entscheidungsbezug hat die Order verhindert."
    # #3429: Ersatz-IDs tragen ein erkennbares Praefix — aus ihnen leitet das Tor keinen
    # Idempotenz-Schluessel ab, weil sie nicht eindeutig je Entscheidung sind.
    assert datensaetze[0].decision_id == "ersatz-entry-u1-AAPL", (
        "Erwartet wird der Ersatzschluessel aus Art, Nutzer und Symbol: "
        f"{datensaetze[0].decision_id!r}"
    )


def test_ohne_entscheidungsbezug_zaehlt_die_entscheidung_im_schluessel(
    datensaetze,
) -> None:
    """#3449: Traegt der Schluessel die Entscheidung (``entry-0-<decision_id>``), gilt sie —
    der Ersatzschluessel nur, wenn auch der Schluessel nichts hergibt. Mit dem Ersatzschluessel
    leitete ein Neustart einen ANDEREN Schluessel ab (CH-4, so gemessen)."""
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-9")

    order = _aufruf(_executor(), client, decision_id=MagicMock(name="kein-string"))

    assert order.id == "brk-9"
    assert datensaetze[0].decision_id == "entscheidung-5"


def test_die_absendung_braucht_keine_instanz(datensaetze) -> None:
    """Die Ketten-Vorrichtung ruft die Absendung OHNE Instanz — das muss tragen.

    ``tests/chain/_ein_intent.py`` ruft
    ``OrderExecutorMixin._submit_with_market_failsafe(None, …)``: Der Absendepfad soll
    ohne eine hochgefahrene Engine pruefbar sein.

    **Aus einem eigenen Fehler entstanden.** Meine erste Fassung rief darin
    ``self._sende_durchs_tor(…)``. Mit ``self=None`` scheiterte das — und weil die
    Vorrichtung Absendefehler als „abgewiesen" protokolliert und weitermacht, setzte sie
    **gar keine Order mehr ab**. CH-4 meldete „0 Orders" statt „2", und die Abnahme des
    Epics waere still zur Attrappe geworden. In der CI haette es niemand bemerkt:
    ``Backend BDD Tests`` ist auf ``main`` ohnehin rot, ein geaenderter Fehlgrund faellt
    dort nicht auf.
    """
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import OrderExecutorMixin

    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-7")

    order = _run(
        OrderExecutorMixin._submit_with_market_failsafe(
            None,
            client=client,
            primary_req=_req(),
            symbol="AAPL",
            qty=1.0,
            side_enum=OrderSide.BUY,
            user_id="kette",
            is_exit=False,
        )
    )

    assert order.id == "brk-7", "Ohne Instanz erreicht die Absendung den Broker nicht."
    assert len(datensaetze) == 1
