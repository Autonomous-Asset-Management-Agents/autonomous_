"""#3429 — das Tor garantiert den Idempotenz-Schlüssel (Plan: docs/3429-idempotenz-im-tor, PR #3471).

Plan-Entscheide: Das Tor **ergänzt** einen fehlenden Schlüssel (überschreibt nie einen gesetzten),
und Auftragsformen ohne das Feld werden gemeldet statt umgebaut.

Zwei Korrekturen am Plan, vor dem Bau gefunden (Kommentar an PR #3471):

1. **Nur aus echten Entscheidungen.** Ersatz-IDs (``<art>-<nutzer>-<symbol>``) sind nicht
   eindeutig je Entscheidung. Zwei Verdrängungen desselben Symbols bekämen denselben Schlüssel;
   Alpaca weist einen doppelten Schlüssel ab, solange die erste Order aktiv ist (HTTP 422,
   ``40010001``, Beleg in #3473).
2. **Das Symbol gehört in den Schlüssel.** Eine Liquidation benutzt **eine** ``decision_id`` für
   **alle** Positionen. Ohne Symbol hätte jede Position denselben Schlüssel — der Notverkauf
   bliebe ab dem zweiten Symbol als „Duplikat" stehen.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

pytestmark = pytest.mark.unit


def _tor(broker):
    from core.gateway.order_gateway import OrderGateway

    return OrderGateway(
        broker=broker, is_halted=lambda *_: False, record=lambda d: None
    )


def _intent(decision_id="panic-3f2b", symbol="AAPL", kind="panic"):
    from core.contracts import OrderIntent

    return OrderIntent(
        decision_id=decision_id,
        symbol=symbol,
        side="sell",
        qty=1.0,
        intent_kind=kind,
        halted=False,
    )


def _anfrage(symbol="AAPL", coid=None):
    return MarketOrderRequest(
        symbol=symbol,
        qty=1,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        client_order_id=coid,
    )


def _gesendet(broker):
    return broker.submit_order.call_args[0][0]


# ---------------------------------------------------------------------------
# Ergaenzen, nie ueberschreiben
# ---------------------------------------------------------------------------


def test_das_tor_ergaenzt_einen_fehlenden_schluessel() -> None:
    broker = MagicMock()
    _tor(broker).submit_with_result(_intent(), request=_anfrage())

    schluessel = _gesendet(broker).client_order_id
    assert schluessel, "Die Order ging ohne Idempotenz-Schluessel durchs Tor (#3429)."


def test_ein_gesetzter_schluessel_hat_vorrang() -> None:
    """Der HITL-Freigabepfad setzt seinen Schluessel bewusst selbst."""
    broker = MagicMock()
    _tor(broker).submit_with_result(_intent(), request=_anfrage(coid="hitl-1-abc"))

    assert _gesendet(broker).client_order_id == "hitl-1-abc"


def test_der_schluessel_ist_reproduzierbar() -> None:
    """Derselbe Intent ergibt denselben Schluessel — sonst erkennt der Broker keine
    Wiederholung."""
    a, b = MagicMock(), MagicMock()
    _tor(a).submit_with_result(_intent(), request=_anfrage())
    _tor(b).submit_with_result(_intent(), request=_anfrage())

    assert _gesendet(a).client_order_id == _gesendet(b).client_order_id


# ---------------------------------------------------------------------------
# Korrektur 2: das Symbol gehoert in den Schluessel
# ---------------------------------------------------------------------------


def test_eine_liquidation_bekommt_je_position_einen_eigenen_schluessel() -> None:
    """Ein Notverkauf: eine Entscheidung, viele Positionen. Gleiche Schluessel liessen ihn
    ab dem zweiten Symbol als Duplikat stehen."""
    schluessel = set()
    for symbol in ("AAPL", "MSFT", "BRK.B"):
        broker = MagicMock()
        _tor(broker).submit_with_result(
            _intent(symbol=symbol), request=_anfrage(symbol=symbol)
        )
        schluessel.add(_gesendet(broker).client_order_id)

    assert len(schluessel) == 3, f"Schluessel kollidieren ueber Symbole: {schluessel}"


# ---------------------------------------------------------------------------
# Korrektur 1: nur aus echten Entscheidungen
# ---------------------------------------------------------------------------


def test_eine_ersatz_entscheidung_bekommt_keinen_abgeleiteten_schluessel(
    caplog,
) -> None:
    from core.idempotency import ersatz_decision_id

    broker = MagicMock()
    with caplog.at_level(logging.INFO):
        _tor(broker).submit_with_result(
            _intent(
                decision_id=ersatz_decision_id("displacement", "u1", "AAPL"),
                kind="displacement",
            ),
            request=_anfrage(),
        )

    assert _gesendet(broker).client_order_id is None, (
        "Aus einer Ersatz-ID wurde ein Schluessel abgeleitet — zwei Verdraengungen "
        "desselben Symbols bekaemen denselben."
    )
    assert any("Ersatz" in r.getMessage() for r in caplog.records)


def test_ersatz_ids_sind_erkennbar() -> None:
    from core.idempotency import ersatz_decision_id, ist_ersatz

    assert ist_ersatz(ersatz_decision_id("entry", "user-mit-bindestrich", "AAPL"))
    assert not ist_ersatz("3f2b1c4d-0000-4000-8000-000000000001")
    assert not ist_ersatz("panic-3f2b")
    assert not ist_ersatz("")


def test_die_absendestelle_baut_ersatz_ids_ueber_den_helfer() -> None:
    """Die drei Stellen, die bisher Ersatz-IDs von Hand bauten, benutzen den Helfer —
    sonst erkennt das Tor sie nicht."""
    import re
    from pathlib import Path

    kern = Path(__file__).resolve().parents[2] / "core"
    von_hand = []
    for datei in ("engine/order_executor.py", "strategies/base.py"):
        text = (kern / datei).read_text(encoding="utf-8")
        for n, zeile in enumerate(text.splitlines(), 1):
            if re.search(r'decision_id=f"(displacement|\{art\})-', zeile) or re.search(
                r'else f"\{art\}-\{', zeile
            ):
                von_hand.append(f"{datei}:{n}")
    assert not von_hand, f"Ersatz-IDs noch von Hand gebaut: {von_hand}"


# ---------------------------------------------------------------------------
# Formen ohne das Feld (Entwurfsfrage 3)
# ---------------------------------------------------------------------------


def test_eine_form_ohne_schluesselfeld_wird_gemeldet_nicht_umgebaut(caplog) -> None:
    from core.gateway.order_gateway import KwargsAuftrag

    broker = MagicMock()
    auftrag = KwargsAuftrag(symbol="AAPL", qty=1.0, side="sell", type="market")
    with caplog.at_level(logging.WARNING):
        _tor(broker).submit_with_result(_intent(), request=auftrag)

    assert "client_order_id" not in broker.submit_order.call_args.kwargs
    assert any("Schluessel" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Der aufgeschobene Verdraengungs-SELL traegt die echte Entscheidung
# ---------------------------------------------------------------------------


def test_der_verdraengungs_sell_reicht_die_echte_entscheidung_durch(
    monkeypatch,
) -> None:
    from core.engine import order_executor as oe

    gesehen = []

    class _Tor:
        def submit(self, intent, *, request, decision=None):
            gesehen.append((intent.decision_id, request.client_order_id))
            return MagicMock(approved=True)

    monkeypatch.setattr(oe, "gateway_for", lambda client: _Tor())
    oe.submit_deferred_displacement_sell(
        client=MagicMock(),
        pm=MagicMock(),
        guardian=None,
        user_id="u1",
        symbol="AAPL",
        qty=2.0,
        decision_id="echte-entscheidung-9",
    )

    assert gesehen and gesehen[0][0] == "echte-entscheidung-9"


# ---------------------------------------------------------------------------
# Zusammenspiel mit der Outbox (#3449): festgeschrieben wird unter dem Schluessel,
# den das Tor sendet
# ---------------------------------------------------------------------------


@pytest.fixture
async def ablage(monkeypatch, tmp_path):
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    yield tmp_path
    await zusammenbau.schliesse_alle()


@pytest.fixture
def tor_offen(monkeypatch):
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)


async def _sende(client, request, **mehr):
    from core.engine.order_executor import OrderExecutorMixin

    argumente = dict(
        client=client,
        request=request,
        symbol="AAPL",
        side_enum=OrderSide.SELL,
        qty=1.0,
        user_id="global",
    )
    argumente.update(mehr)
    return await OrderExecutorMixin._sende_durchs_tor(**argumente)


async def test_ohne_schluessel_landet_die_order_unter_dem_gesendeten_in_der_outbox(
    ablage, tor_offen
) -> None:
    """Der aufgeschobene Verdraengungs-SELL und die Liquidationen kommen ohne Schluessel. Das
    Tor ergaenzt ihn; die Outbox muss denselben kennen, sonst gleicht der Neustart nichts ab.
    """
    from core.outbox import Outbox
    from core.state.zusammenbau import state_port

    broker = MagicMock()
    broker.submit_order.return_value = MagicMock(id="brk-7")

    await _sende(broker, _anfrage(), decision_id="panic-3f2b", is_protective_exit=True)

    gesendet = _gesendet(broker).client_order_id
    assert gesendet, "Das Tor hat keinen Schluessel ergaenzt."
    eintrag = await Outbox(await state_port()).lese(gesendet)
    assert eintrag is not None, "Die Order fehlt in der Outbox."
    assert (eintrag.zustand, eintrag.broker_order_id) == ("bestaetigt", "brk-7")


async def test_eine_ersatz_entscheidung_geht_ohne_outbox_eintrag_hinaus(
    ablage, tor_offen, caplog
) -> None:
    """Ohne Schluessel gibt es nichts, unter dem der Neustart abgleichen koennte. Die Order geht
    trotzdem hinaus — und es entsteht kein Fehler-Rauschen im Log."""
    broker = MagicMock()
    broker.submit_order.return_value = MagicMock(id="brk-8")

    with caplog.at_level(logging.ERROR):
        order = await _sende(broker, _anfrage())

    assert order.id == "brk-8"
    assert _gesendet(broker).client_order_id is None
    assert not [r for r in caplog.records if "Outbox" in r.getMessage()]
