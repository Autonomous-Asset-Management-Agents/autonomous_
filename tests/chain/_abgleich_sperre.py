"""#3488 — Prozess der Kettenabnahme fuer die Abgleich-Sperre (#3389, #3430, #3491).

Ein Lauf, wie ihn ein Bediener erlebt — mit dem echten ``ReconciliationService`` gegen den
Ledger-Broker, der echten Absendestelle und dem echten Engine-Endpunkt zum Aufheben:

1. Der Abgleich uebernimmt den (leeren) Bestand.
2. Beim Broker entsteht eine Order **an der Engine vorbei** (wie von Hand in der Broker-App); der
   naechste Lauf findet die Abweichung und sperrt.
3. Ein Einstieg muss zurueckgehalten werden, ein Schutz-Exit muss hinausgehen.
4. Die Engine lernt den Bestand; ein sauberer Folgelauf — die Sperre bleibt.
5. Ein Bediener hebt sie ueber ``POST /api/reconciliation/release`` auf; der naechste Einstieg geht.

Spur: ``abgleich``, ``einstieg`` (mit ``phase`` und ``ergebnis``), ``schutz_exit``, ``folgelauf``,
``aufhebung``.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sys

from tests.chain._ein_intent import (
    MARKE_SAUBERES_ENDE,
    _broker,
    _protokoll,
    _verzeichnis,
)

_KEY = "kette-3488"
_KONTO = "global"


async def _absenden(broker, symbol, menge, *, seite, schutz, schluessel):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.engine.order_executor import OrderExecutorMixin

    side = OrderSide.BUY if seite == "buy" else OrderSide.SELL
    anfrage = MarketOrderRequest(
        symbol=symbol,
        qty=menge,
        side=side,
        time_in_force=TimeInForce.DAY,
        client_order_id=schluessel,
    )
    return await OrderExecutorMixin._sende_durchs_tor(
        client=broker,
        request=anfrage,
        symbol=symbol,
        side_enum=side,
        qty=menge,
        user_id=_KONTO,
        decision_id=schluessel,
        is_protective_exit=schutz,
    )


async def _versuch(verzeichnis, broker, symbol, menge, *, art, phase, schluessel):
    try:
        order = await _absenden(
            broker,
            symbol,
            menge,
            seite="buy" if art == "einstieg" else "sell",
            schutz=art == "schutz_exit",
            schluessel=schluessel,
        )
    except Exception as exc:
        if "Abgleich-Sperre" in str(exc):
            _protokoll(verzeichnis, art, phase=phase, ergebnis="gesperrt")
            return
        raise
    _protokoll(
        verzeichnis,
        art,
        phase=phase,
        ergebnis="gesendet",
        broker_order_id=str(order.id),
    )


def _engine_kennt_den_bestand(dienst, broker) -> None:
    """Was ein Bediener nach der Pruefung tut: Die Buecher der Engine kennen jetzt, was beim
    Broker liegt — der naechste Lauf ist sauber."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    try:
        alle_orders = broker.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL))
    except Exception:
        alle_orders = broker.get_orders()
    for order in alle_orders:
        dienst.register_order(str(order.id))
        st = getattr(order, "status", "")
        st_val = str(getattr(st, "value", st)).lower()
        if st_val in ("filled", "partially_filled"):
            dienst.on_broker_fill(order, source="kette")
    dienst.set_known_positions(
        {str(p.symbol): float(p.qty) for p in broker.get_all_positions()}
    )


async def _lauf(verzeichnis, broker, symbol, menge):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core import reconciliation
    from core.reconciliation import ReconciliationService

    dienst = ReconciliationService(broker, None, block_on_break=True)
    # Wie _start_reconciliation: der Dienst ist fuer die Absendestelle erreichbar (#3491).
    if hasattr(reconciliation, "setze_aktiven"):
        reconciliation.setze_aktiven(dienst)
    await dienst.run_once()

    # Eine Order an der Engine vorbei.
    broker.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=menge,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            client_order_id="von-hand-1",
        )
    )
    befund = await dienst.run_once()
    _protokoll(
        verzeichnis,
        "abgleich",
        abweichungen=len(befund.breaks),
        gesperrt=bool(dienst.entries_blocked),
    )

    await _versuch(
        verzeichnis,
        broker,
        symbol,
        menge,
        art="einstieg",
        phase="gesperrt",
        schluessel="entry-0-kette-a",
    )
    await _versuch(
        verzeichnis,
        broker,
        symbol,
        menge,
        art="schutz_exit",
        phase="gesperrt",
        schluessel="stop-0-kette-b",
    )

    _engine_kennt_den_bestand(dienst, broker)
    folgelauf = await dienst.run_once()
    _protokoll(
        verzeichnis,
        "folgelauf",
        sauber=folgelauf.clean,
        gesperrt=bool(dienst.entries_blocked),
    )

    antwort = _aufheben(dienst)
    _protokoll(
        verzeichnis, "aufhebung", status=antwort.status_code, antwort=antwort.text[:300]
    )

    await _versuch(
        verzeichnis,
        broker,
        symbol,
        menge,
        art="einstieg",
        phase="aufgehoben",
        schluessel="entry-0-kette-c",
    )


def _aufheben(dienst):
    """Ueber den echten Engine-Endpunkt — dieselbe Pruefung und dasselbe Protokoll wie in echt."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import core.engine.api_routes as ar

    os.environ["ENGINE_API_KEY"] = _KEY
    ar.engine = SimpleNamespace(reconciler=dienst)
    return TestClient(ar.app).post(
        "/api/reconciliation/release",
        headers={"X-Engine-Key": _KEY},
        json={"reason": "Broker-Bestand von Hand geprueft (Kette #3488)"},
    )


def main() -> int:
    verzeichnis = _verzeichnis()
    atexit.register(
        lambda: (verzeichnis / MARKE_SAUBERES_ENDE).write_text("ok", encoding="utf-8")
    )
    symbol = os.environ.get("KETTE_SYMBOL", "AAPL")
    preis = float(os.environ.get("KETTE_PREIS", "100.0"))
    menge = float(os.environ.get("KETTE_MENGE", "1"))

    broker = _broker(verzeichnis, symbol, preis)
    asyncio.run(_lauf(verzeichnis, broker, symbol, menge))
    return 0


if __name__ == "__main__":
    sys.exit(main())
