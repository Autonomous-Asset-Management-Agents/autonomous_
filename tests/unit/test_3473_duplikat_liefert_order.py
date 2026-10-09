"""#3473 — ein abgewiesenes Duplikat liefert die bestehende Order (Plan: docs/3473-…, PR #3474).

Eine idempotente Absendung liefert beim zweiten Mal dasselbe Ergebnis wie beim ersten. Bisher war
ein abgewiesenes Duplikat ein Fehler: Der Idempotenz-Schluessel (#3387) verhinderte die zweite
Order, aber der Aufrufer erfuhr nicht, WELCHE Order es gibt.

Die Erkennung ist eng und belegt (Kommentar an #3473, Alpaca „30 Common Errors"): HTTP 422 **und**
(Code ``40010001`` **oder** Text ``client_order_id must be unique``). Jede andere Ablehnung bleibt
ein Fehler — eine abgelehnte Order fuer angekommen zu halten, ist der teure Irrtum.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

pytestmark = pytest.mark.unit

_COID = "entry-0-entscheidung-7"


def _api_fehler(status, code, message):
    http = SimpleNamespace(response=SimpleNamespace(status_code=status), request=None)
    return APIError(json.dumps({"code": code, "message": message}), http)


def _duplikat():
    return _api_fehler(422, 40010001, "client_order_id must be unique")


@pytest.fixture(autouse=True)
def tor_offen(monkeypatch):
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)


@pytest.fixture
async def ablage(monkeypatch, tmp_path):
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    yield tmp_path
    await zusammenbau.schliesse_alle()


def _anfrage(coid=_COID, symbol="AAPL"):
    return MarketOrderRequest(
        symbol=symbol,
        qty=1,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=coid,
    )


async def _sende(client, request=None, **mehr):
    from core.engine.order_executor import OrderExecutorMixin

    argumente = dict(
        client=client,
        request=request or _anfrage(),
        symbol="AAPL",
        side_enum=OrderSide.BUY,
        qty=1.0,
        user_id="global",
        decision_id="entscheidung-7",
    )
    argumente.update(mehr)
    return await OrderExecutorMixin._sende_durchs_tor(**argumente)


def _broker_mit_bestehender_order(symbol="AAPL"):
    client = MagicMock()
    client.submit_order.side_effect = _duplikat()
    client.get_order_by_client_id.return_value = SimpleNamespace(
        id="brk-1", client_order_id=_COID, symbol=symbol, status="filled"
    )
    return client


# ---------------------------------------------------------------------------
# Szenario: Ein abgewiesenes Duplikat liefert die bestehende Order
# ---------------------------------------------------------------------------


async def test_ein_duplikat_liefert_die_bestehende_order() -> None:
    client = _broker_mit_bestehender_order()

    order = await _sende(client)

    assert order.id == "brk-1"
    client.get_order_by_client_id.assert_called_once_with(_COID)
    assert client.submit_order.call_count == 1, "Es darf keine zweite Order entstehen."


async def test_das_duplikat_bestaetigt_die_outbox(ablage) -> None:
    from core.outbox import Outbox
    from core.state.zusammenbau import state_port

    await _sende(_broker_mit_bestehender_order())

    eintrag = await Outbox(await state_port()).lese(_COID)
    assert (eintrag.zustand, eintrag.broker_order_id) == ("bestaetigt", "brk-1")


async def test_der_text_allein_genuegt_bei_422() -> None:
    client = _broker_mit_bestehender_order()
    client.submit_order.side_effect = _api_fehler(
        422, 40010099, "client_order_id must be unique"
    )

    assert (await _sende(client)).id == "brk-1"


# ---------------------------------------------------------------------------
# Szenario: Andere Ablehnungen bleiben Fehler
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fehler",
    [
        _api_fehler(422, 40310000, "insufficient buying power"),
        _api_fehler(403, 40010001, "client_order_id must be unique"),
        _api_fehler(None, 40010001, "client_order_id must be unique"),
        RuntimeError("client_order_id must be unique"),
    ],
    ids=["kaufkraft-422", "falscher-status", "ohne-status", "kein-apifehler"],
)
async def test_andere_ablehnungen_bleiben_fehler(fehler) -> None:
    client = MagicMock()
    client.submit_order.side_effect = fehler

    with pytest.raises(type(fehler)):
        await _sende(client)

    client.get_order_by_client_id.assert_not_called()


async def test_laesst_sich_die_bestehende_order_nicht_holen_bleibt_der_fehler() -> None:
    client = _broker_mit_bestehender_order()
    client.get_order_by_client_id.side_effect = TimeoutError("keine Antwort")

    with pytest.raises(APIError):
        await _sende(client)


async def test_eine_bestehende_order_zu_einem_anderen_symbol_ist_kein_ergebnis() -> (
    None
):
    """Derselbe Schluessel fuer ein anderes Symbol waere ein Fehler im Schluessel — nie eine
    Order, die man dem Aufrufer als seine ausgibt."""
    client = _broker_mit_bestehender_order(symbol="MSFT")

    with pytest.raises(APIError):
        await _sende(client)


async def test_ohne_schluessel_ist_nichts_nachzuschlagen() -> None:
    client = MagicMock()
    client.submit_order.side_effect = _duplikat()

    with pytest.raises(APIError):
        await _sende(client, request=_anfrage(coid=None), decision_id="")

    client.get_order_by_client_id.assert_not_called()


# ---------------------------------------------------------------------------
# Der Exit-Failsafe sendet bei einem Duplikat keine zweite Order
# ---------------------------------------------------------------------------


async def test_ein_duplikat_beim_exit_loest_keinen_market_fallback_aus() -> None:
    """Bisher fing ``_submit_with_market_failsafe`` den APIError eines Exits und sendete eine
    MARKET-Order unter neuem Versuchsschluessel hinterher — beim Duplikat eine ZWEITE echte
    Verkaufsorder. Jetzt ist das Duplikat kein Fehler mehr, und der Fallback greift nicht.
    """
    from core.engine.order_executor import OrderExecutorMixin

    client = _broker_mit_bestehender_order()
    anfrage = LimitOrderRequest(
        symbol="AAPL",
        qty=1,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        limit_price=99.0,
        client_order_id="stop-0-entscheidung-7",
    )
    client.get_order_by_client_id.return_value = SimpleNamespace(
        id="brk-1", client_order_id="stop-0-entscheidung-7", symbol="AAPL"
    )

    order = await OrderExecutorMixin._submit_with_market_failsafe(
        None,
        client=client,
        primary_req=anfrage,
        symbol="AAPL",
        qty=1.0,
        side_enum=OrderSide.SELL,
        user_id="global",
        is_exit=True,
        is_protective_exit=True,
        decision_id="entscheidung-7",
    )

    assert order.id == "brk-1"
    assert client.submit_order.call_count == 1, "Der Fallback hat nachgesendet."


# ---------------------------------------------------------------------------
# Die Vorrichtung ist broker-treu
# ---------------------------------------------------------------------------


def test_der_ledger_broker_weist_ein_duplikat_in_alpacas_form_ab(tmp_path) -> None:
    from core.engine.order_executor import ist_duplikat
    from tests.chain.ledger_broker import LedgerBroker

    inner = MagicMock()
    inner.submit_order.return_value = SimpleNamespace(
        id="brk-1", symbol="AAPL", side="buy", qty="1", status="filled"
    )
    broker = LedgerBroker(tmp_path / "buch.jsonl", inner)
    broker.submit_order(_anfrage())

    with pytest.raises(APIError) as fehler:
        broker.submit_order(_anfrage())

    assert ist_duplikat(fehler.value)
    bestehend = LedgerBroker(tmp_path / "buch.jsonl", MagicMock())  # neuer Prozess
    order = bestehend.get_order_by_client_id(_COID)
    assert (order.id, order.symbol, order.status) == ("brk-1", "AAPL", "filled")
