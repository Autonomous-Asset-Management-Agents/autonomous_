"""#3489 — Prozess der Kettenabnahme mit den Startschritten der Handelsschleife.

Die bisherige Kette ruft die Absendestelle direkt. Dieser Prozess baut eine Engine-Huelle aus
``TradingLoopMixin`` mit dem Ledger-Broker als ``api`` und laeuft **dieselben Schritte in derselben
Reihenfolge** wie ``live_trading_loop`` vor und zu Beginn eines Zyklus:

1. ``_start_outbox_abgleich()`` — unbestaetigte Intents beim Broker abgleichen, nie nachsenden
2. ``_sichere_schreibberechtigung()`` — die Schreibberechtigung erwerben
3. zu Zyklusbeginn erneut ``_sichere_schreibberechtigung()``
4. ein Einstieg ueber die echte Absendestelle

Dass ``live_trading_loop`` genau diese Reihenfolge hat, prueft die Abnahme am Quelltext — aendert
jemand die Schleife, wird sie rot, statt still an der Schleife vorbeizulaufen.

Umgebung: ``KETTE_INSTANZ``; ``KETTE_OHNE_EINSTIEG=1`` (nur Startschritte);
``KETTE_TOETEN_BEI`` = ``im_broker`` (der Broker hat die Order dauerhaft, die Bestaetigung
erreicht uns nie) oder ``nach_erwerb`` (die Berechtigung ist erworben, dann Tod).
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sys

from tests.chain._ein_intent import (
    MARKE_SAUBERES_ENDE,
    _broker,
    _gleichzeitig,
    _protokoll,
    _toete,
    _verzeichnis,
)

#: Das Konto der Engine: ``user_id`` leer heisst ``global`` — dasselbe Konto, das
#: ``_sichere_schreibberechtigung`` und ``_start_outbox_abgleich`` meinen.
_KONTO = "global"


def _huelle(broker):
    import threading

    from core.engine.trading_loop import TradingLoopMixin

    class _Engine(TradingLoopMixin):
        pass

    engine = _Engine()
    engine.api = broker
    engine.strategy_running = threading.Event()
    engine._shutdown_event = threading.Event()
    return engine


def _toetender_broker(verzeichnis, broker):
    """Der Broker nimmt die Order dauerhaft an; danach stirbt unser Prozess, bevor die
    Antwort ankommt — das Fenster „zwischen Absenden und Bestaetigung" innerhalb der
    Absendestelle."""
    echt = broker.submit_order

    def _submit(order_data):
        echt(order_data)
        _toete(verzeichnis, "im_broker")

    broker.submit_order = _submit
    return broker


async def _einstieg(verzeichnis, broker, symbol: str, menge: float, instanz: str):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.cloud_logger import DecisionContext
    from core.engine.order_executor import OrderExecutorMixin, _derived_coid

    kontext = DecisionContext(symbol=symbol)
    anfrage = MarketOrderRequest(
        symbol=symbol,
        qty=menge,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=_derived_coid(kontext, "entry", 0),
    )
    _protokoll(verzeichnis, "intent", client_order_id=anfrage.client_order_id)
    try:
        order = await OrderExecutorMixin._submit_with_market_failsafe(
            None,
            client=broker,
            primary_req=anfrage,
            symbol=symbol,
            qty=menge,
            side_enum=OrderSide.BUY,
            user_id=_KONTO,
            is_exit=False,
            decision_id=kontext.decision_id,
        )
    except Exception as exc:
        if "Schreibberechtigung" in str(exc):
            _protokoll(verzeichnis, "nicht_gehandelt", instanz=instanz, grund=str(exc))
            return
        raise
    _protokoll(verzeichnis, "bestaetigt", broker_order_id=str(order.id))


async def _lauf(verzeichnis, broker, symbol, menge, instanz, toeten_bei):
    engine = _huelle(broker)

    await engine._start_outbox_abgleich()
    _protokoll(verzeichnis, "outbox_abgleich", instanz=instanz)

    erhalten = await engine._sichere_schreibberechtigung()
    _protokoll(verzeichnis, "berechtigung", instanz=instanz, erhalten=bool(erhalten))
    _gleichzeitig(verzeichnis, instanz)
    if toeten_bei == "nach_erwerb":
        _toete(verzeichnis, toeten_bei)

    # Zyklusbeginn — wie in live_trading_loop.
    await engine._sichere_schreibberechtigung()

    if os.environ.get("KETTE_OHNE_EINSTIEG") != "1":
        if toeten_bei == "im_broker":
            broker = _toetender_broker(verzeichnis, broker)
        await _einstieg(verzeichnis, broker, symbol, menge, instanz)


def main() -> int:
    verzeichnis = _verzeichnis()
    atexit.register(
        lambda: (verzeichnis / MARKE_SAUBERES_ENDE).write_text("ok", encoding="utf-8")
    )
    symbol = os.environ.get("KETTE_SYMBOL", "AAPL")
    preis = float(os.environ.get("KETTE_PREIS", "100.0"))
    menge = float(os.environ.get("KETTE_MENGE", "1"))
    instanz = os.environ.get("KETTE_INSTANZ", "einzeln")
    toeten_bei = os.environ.get("KETTE_TOETEN_BEI", "").strip()

    broker = _broker(verzeichnis, symbol, preis)
    asyncio.run(_lauf(verzeichnis, broker, symbol, menge, instanz, toeten_bei))
    return 0


if __name__ == "__main__":
    sys.exit(main())
