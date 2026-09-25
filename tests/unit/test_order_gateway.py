"""OrderGateway — ein Tor zum Broker (#3379, ARC-E1.3).

Der Plan (docs/3379-ordergateway-fassade/implementation_plan.md §7) setzt zwei rote
Tests an den Anfang: den Architektur-Test „genau ein Broker-Aufrufer" — der steht schon
als CH-2-Szenario aus #3377 — und „ein Schutz-Exit wird nie blockiert", die Gegenprobe
zum Hauptrisiko dieses Umbaus: *Ein Tor, das alles prueft, ist genau dann gefaehrlich,
wenn es auch den Notausgang prueft.*

Schnitt dieses Schritts: Das Gateway ist der einzige Ort, der den Broker ruft und den
Datensatz schreibt. Die Pruefungen selbst wandern in den folgenden Schritten hinein —
sie hier gleichzeitig zu verschieben hiesse, die Rueckrollstufe auf das ganze Epic zu
vergroessern (Plan §4, Option C verworfen).
"""

from unittest.mock import MagicMock

import pytest

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode
from core.gateway import OrderGateway

pytestmark = pytest.mark.unit


def _intent(**overrides):
    base = dict(
        decision_id="dec-1",
        symbol="AAPL",
        side="sell",
        qty=2.0,
        intent_kind="entry",
        halted=False,
    )
    base.update(overrides)
    return OrderIntent(**base)


def _gateway(halted=False):
    broker = MagicMock()
    broker.submit_order.return_value = MagicMock(id="order-1")
    audit = MagicMock()
    gw = OrderGateway(
        broker=broker,
        is_halted=lambda user_id=None: halted,
        record=audit,
    )
    return gw, broker, audit


# ---------------------------------------------------------------------------
# Der Notausgang
# ---------------------------------------------------------------------------


def test_protective_exit_is_never_blocked_even_while_halted():
    gw, broker, _ = _gateway(halted=True)
    intent = _intent(intent_kind="stop", is_protective_exit=True, exit_kind="risk")

    decision = gw.submit(intent, request=MagicMock())

    assert decision.approved is True
    broker.submit_order.assert_called_once()


def test_protective_exit_carries_the_halt_state_in_its_record():
    """Der Datensatz muss zeigen, dass die Order im Halt herausging — sonst ist die
    Ausnahme im Nachhinein nicht von einem Normalfall zu unterscheiden."""
    gw, _, audit = _gateway(halted=True)
    intent = _intent(intent_kind="stop", is_protective_exit=True, exit_kind="risk")

    decision = gw.submit(intent, request=MagicMock())

    assert decision.halted is True
    assert decision.reason_code is ReasonCode.EXIT_EXEMPT
    audit.assert_called_once()
    assert isinstance(audit.call_args[0][0], ComplianceDecision)


# ---------------------------------------------------------------------------
# Der Halt gilt fuer alles andere
# ---------------------------------------------------------------------------


def test_entry_is_blocked_while_halted():
    gw, broker, _ = _gateway(halted=True)

    decision = gw.submit(_intent(side="buy", intent_kind="entry"), request=MagicMock())

    assert decision.approved is False
    assert decision.reason_code is ReasonCode.TRADING_HALTED
    broker.submit_order.assert_not_called()


def test_rotation_is_blocked_while_halted():
    """Eine Rotation ist ein Meinungs-Exit, kein Schutz-Exit — sie geniesst die
    Freistellung nicht (compliance.py:582 nimmt gelabelte Rotation ausdruecklich aus).
    """
    gw, broker, _ = _gateway(halted=True)

    decision = gw.submit(
        _intent(intent_kind="trim", exit_kind="rotation"), request=MagicMock()
    )

    assert decision.approved is False
    broker.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Der Normalfall
# ---------------------------------------------------------------------------


def test_an_approved_order_reaches_the_broker_and_is_recorded():
    gw, broker, audit = _gateway(halted=False)

    decision = gw.submit(_intent(), request=MagicMock())

    assert decision.approved is True
    assert decision.reason_code is ReasonCode.APPROVED
    assert decision.decision_id == "dec-1"
    broker.submit_order.assert_called_once()
    audit.assert_called_once()


def test_every_order_produces_a_record_even_when_rejected():
    """CH-2 verlangt: keine Broker-Order ohne Entscheidung — und keine Ablehnung ohne
    Datensatz. Sonst bleibt der haeufigste Fall unsichtbar."""
    gw, _, audit = _gateway(halted=True)

    gw.submit(_intent(side="buy"), request=MagicMock())

    audit.assert_called_once()
    assert audit.call_args[0][0].approved is False


def test_a_broker_failure_is_reported_not_swallowed():
    gw, broker, audit = _gateway(halted=False)
    broker.submit_order.side_effect = RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        gw.submit(_intent(), request=MagicMock())

    # Auch der gescheiterte Versuch hinterlaesst einen Datensatz.
    audit.assert_called_once()
    assert audit.call_args[0][0].reason_code is ReasonCode.SYSTEM_ERROR


# ---------------------------------------------------------------------------
# Eine vorab getroffene Entscheidung wird respektiert
# ---------------------------------------------------------------------------


def test_a_prior_rejection_is_honoured_and_not_second_guessed():
    """Solange die Compliance-Pruefung noch im Executor sitzt, reicht sie ihr Ergebnis
    herein. Das Gateway darf es nicht ueberstimmen — es fuehrt es aus und schreibt es.
    """
    gw, broker, audit = _gateway(halted=False)
    vorab = ComplianceDecision(
        decision_id="dec-1",
        approved=False,
        reason_code=ReasonCode.WASH_TRADE,
        halted=False,
    )

    decision = gw.submit(_intent(), request=MagicMock(), decision=vorab)

    assert decision.approved is False
    assert decision.reason_code is ReasonCode.WASH_TRADE
    broker.submit_order.assert_not_called()
    audit.assert_called_once()


def test_a_prior_rejection_never_blocks_a_protective_exit():
    """Das Hauptrisiko in einem Satz: Eine Compliance-Regel darf einen Notausgang nicht
    zusperren. Der Verkauf geht raus, die Ablehnung wird protokolliert."""
    gw, broker, audit = _gateway(halted=False)
    vorab = ComplianceDecision(
        decision_id="dec-1",
        approved=False,
        reason_code=ReasonCode.MAX_ORDER_VALUE,
        halted=False,
    )
    intent = _intent(intent_kind="stop", is_protective_exit=True, exit_kind="risk")

    decision = gw.submit(intent, request=MagicMock(), decision=vorab)

    assert decision.approved is True
    assert decision.reason_code is ReasonCode.EXIT_EXEMPT
    assert "max_order_value" in decision.detail
    broker.submit_order.assert_called_once()


# ---------------------------------------------------------------------------
# #3447 Schritt 2 — das Tor gibt die Broker-Antwort zurueck
# ---------------------------------------------------------------------------
#
# Bis hierhin gab ``submit()`` nur die Entscheidung zurueck und verwarf, was der Broker
# antwortete. Fuer den ersten umgehaengten Pfad (den aufgeschobenen
# Verdraengungs-Verkauf) genuegte das. Die HAUPT-Absendungen des Executors brauchen aber
# das Order-Objekt: Sie pollen damit bis zur Fuellung (``order.id``). Ohne Rueckgabe
# koennte keiner dieser Pfade durchs Tor — und der CH-2-Zaehler bliebe fuer immer stehen.


def test_das_tor_gibt_die_broker_antwort_zurueck():
    gw, broker, _audit = _gateway()

    entscheidung, order = gw.submit_with_result(_intent(), request={"symbol": "AAPL"})

    assert entscheidung.approved is True
    assert order is broker.submit_order.return_value, (
        "Das Tor hat die Broker-Antwort verworfen. Die Haupt-Absendungen pollen mit "
        "order.id bis zur Fuellung — ohne das Objekt koennen sie nicht durchs Tor (#3447)."
    )


def test_eine_abgelehnte_order_hat_keine_broker_antwort():
    """Gehalten und nicht freigestellt: kein Broker-Aufruf, also auch kein Objekt.

    ``None`` statt eines erfundenen Platzhalters — der Aufrufer muss den Unterschied
    zwischen „abgelehnt" und „abgesetzt" an der Entscheidung ablesen, nicht raten.
    """
    gw, broker, audit = _gateway(halted=True)

    entscheidung, order = gw.submit_with_result(_intent(), request={"symbol": "AAPL"})

    assert entscheidung.approved is False
    assert entscheidung.reason_code == ReasonCode.TRADING_HALTED
    assert order is None
    broker.submit_order.assert_not_called()
    audit.assert_called_once()


def test_submit_bleibt_unveraendert():
    """Die bestehende Schnittstelle darf sich nicht bewegen — sie hat Aufrufer."""
    gw, broker, _audit = _gateway()

    ergebnis = gw.submit(_intent(), request={"symbol": "AAPL"})

    assert isinstance(ergebnis, ComplianceDecision)
    broker.submit_order.assert_called_once()


def test_das_tor_ruft_den_broker_weiterhin_genau_einmal():
    """``submit`` delegiert an ``submit_with_result`` — es gibt EINEN Weg hinaus.

    Der CH-2-Architekturtest verlangt genau einen Broker-Aufruf im Tor. Zwei Methoden
    mit je eigenem Aufruf waeren zwei Wege — und der zweite der, den irgendwann jemand
    vergisst abzusichern.
    """
    import ast
    import inspect

    from core.gateway import order_gateway

    baum = ast.parse(inspect.getsource(order_gateway))
    aufrufe = [
        k
        for k in ast.walk(baum)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "submit_order"
    ]
    assert (
        len(aufrufe) == 1
    ), f"{len(aufrufe)} Broker-Aufrufe im Tor — es darf nur einer sein."
