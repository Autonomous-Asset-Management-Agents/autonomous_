"""#3487 — Prozess der Kettenabnahme fuer „Halt und Tagesbudget ueberleben den Neustart".

Ein eigener Prozess neben ``_ein_intent.py``, damit dessen Abnahmen unberuehrt bleiben. Er
benutzt dieselben Bausteine (Ledger-Broker, Beobachtungsspur, harter Tod) und fuehrt, gesteuert
ueber die Umgebung, genau die Kernfunktionen aus, die #3449 Schritt 5 aendert:

* ``KETTE_TRIP=<grund>``: ``kill_switch.trip(grund)``; mit ``KETTE_NACH_TRIP_TOETEN=1`` stirbt
  der Prozess unmittelbar danach hart.
* ``KETTE_BUDGET_VERBRAUCHEN=<n>``: ``n`` Trades ueber ``ComplianceGuardian.record_trade()``.
* ``KETTE_STARTPRUEFUNG=1``: die Startpruefung des Halts (``halt_beim_start_raeumen``) — fehlt
  sie im Bestand, wird das protokolliert und weitergemacht.
* ``KETTE_EINSTIEGE=<k>``: ``k`` Einstiege, **wie der Executor sie absetzt**: erst
  ``check_order`` (Regelwerk), dann ``check_trade`` (dort sitzt der Tagesdeckel, ADR-C04), dann
  die echte Absendestelle (dort sitzt der Halt), danach ``record_trade``.

Ereignisse der Spur: ``einstieg_gesendet``, ``einstieg_halt``, ``einstieg_budget``,
``einstieg_compliance``.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sys
import time

from tests.chain._ein_intent import (
    MARKE_SAUBERES_ENDE,
    _broker,
    _protokoll,
    _toete,
    _verzeichnis,
)


def _ganzzahl(name: str) -> int:
    return int(os.environ.get(name, "0") or 0)


def _einstieg(verzeichnis, broker, guardian, symbol: str, preis: float, menge: float):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.cloud_logger import DecisionContext
    from core.engine.order_executor import OrderExecutorMixin, _derived_coid

    kontext = DecisionContext(symbol=symbol)
    auftrag = {
        "symbol": symbol,
        "side": "buy",
        "quantity": float(menge),
        "price": float(preis),
        "strategy_id": "kette",
        "timestamp": time.time(),
        "user_id": "kette",
        "held_qty": 0.0,
    }
    # Dieselbe Reihenfolge wie der Executor (order_executor.py, _process_signal_event):
    # erst check_order (Regelwerk), dann check_trade (dort sitzt der Tagesdeckel, ADR-C04).
    if not guardian.check_order(auftrag):
        _protokoll(verzeichnis, "einstieg_compliance", decision_id=kontext.decision_id)
        return
    if not guardian.check_trade(auftrag):
        _protokoll(verzeichnis, "einstieg_budget", decision_id=kontext.decision_id)
        return

    anfrage = MarketOrderRequest(
        symbol=symbol,
        qty=menge,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=_derived_coid(kontext, "entry", 0),
    )

    async def _absenden():
        return await OrderExecutorMixin._submit_with_market_failsafe(
            None,
            client=broker,
            primary_req=anfrage,
            symbol=symbol,
            qty=menge,
            side_enum=OrderSide.BUY,
            user_id="kette",
            is_exit=False,
            decision_id=kontext.decision_id,
        )

    try:
        order = asyncio.run(_absenden())
    except Exception as exc:
        if "HALTED" in str(exc).upper():
            _protokoll(verzeichnis, "einstieg_halt", text=str(exc)[:200])
            return
        raise
    guardian.record_trade(auftrag)
    _protokoll(verzeichnis, "einstieg_gesendet", broker_order_id=str(order.id))


def main() -> int:
    verzeichnis = _verzeichnis()
    atexit.register(
        lambda: (verzeichnis / MARKE_SAUBERES_ENDE).write_text("ok", encoding="utf-8")
    )
    symbol = os.environ.get("KETTE_SYMBOL", "AAPL")
    preis = float(os.environ.get("KETTE_PREIS", "100.0"))
    menge = float(os.environ.get("KETTE_MENGE", "1"))

    from core.kill_switch import kill_switch

    # Kein Massen-Storno ueber das Netz: Die Kette hat keinen echten Broker dahinter.
    kill_switch._run_async_mass_cancel = lambda *a, **k: None

    grund = os.environ.get("KETTE_TRIP", "").strip()
    if grund:
        kill_switch.trip(grund, fail_closed=False)
        _protokoll(verzeichnis, "trip", grund=grund)
        if os.environ.get("KETTE_NACH_TRIP_TOETEN") == "1":
            _toete(verzeichnis, "nach_trip")

    if os.environ.get("KETTE_STARTPRUEFUNG") == "1":
        try:
            from core.kill_switch import halt_beim_start_raeumen
        except ImportError:
            _protokoll(verzeichnis, "startpruefung_fehlt")
        else:
            halt_beim_start_raeumen(kill_switch)
            _protokoll(verzeichnis, "startpruefung", halt=kill_switch.is_halted())

    from core.compliance import ComplianceGuardian

    guardian = ComplianceGuardian()
    for _ in range(_ganzzahl("KETTE_BUDGET_VERBRAUCHEN")):
        guardian.record_trade()
    if _ganzzahl("KETTE_BUDGET_VERBRAUCHEN"):
        _protokoll(verzeichnis, "budget_verbraucht", stand=guardian.daily_trades)

    einstiege = _ganzzahl("KETTE_EINSTIEGE")
    if einstiege:
        broker = _broker(verzeichnis, symbol, preis)
        for _ in range(einstiege):
            _einstieg(verzeichnis, broker, guardian, symbol, preis, menge)
    return 0


if __name__ == "__main__":
    sys.exit(main())
