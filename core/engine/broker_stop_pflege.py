"""#3976: Ausfuehrung des Broker-Stop-Plans — stornieren, Stornierung bestaetigen, legen.

Der Planer (``core/broker_stops.py``) sagt, was liegen soll. Diese Datei setzt es um:

1. **Stornieren** ueber die vom Aufrufer gereichte Funktion. Der eine Storno-Zugriff bleibt
   im Aufrufer (``trading_loop._maintain_broker_stops``, Architektur-Vertrag
   ``broker_aufrufer.ausnahmen``); hier wird nur aufgerufen, was hereingereicht wird.
2. **Bestaetigen**, mit kurzer Frist (Plan-Review #3985: hoechstens 1–2 s), damit der
   Handelstakt nicht wartet. Ohne Bestaetigung wird fuer dieses Symbol in diesem Zyklus
   NICHT neu gelegt — sonst lehnte der Broker die Neuanlage mangels freier Stuecke ab.
3. **Legen** durch das Tor (ADR-019). Meldet der Broker einen bereits verwendeten
   Schluessel (422/40010001, #3473), wird die Order zu diesem Schluessel gelesen: Ist sie
   offen, liegt der Stop schon (echte Wiederholung). Sonst folgt der naechste Versuch
   (``next_attempt``) — der Schluessel eines stornierten Stops ist beim Broker verbraucht.

Zurueck kommt die Liste der Positionen ohne vollstaendigen Schutz, mit Grund. Sie fliesst in
``merke_stand``; ``letzter_stand`` liest die Engine-Diagnose (``/engine-diagnostics``), weil
die Desktop-Engine kein Logfile schreibt und ein Fehler nur im Log unsichtbar waere.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Callable, Iterable, List, Optional, Sequence, Set, Tuple

#: ADR-R19 (#3976): hoechstens so viele LESENDE Abfragen, um den ersten freien Schluessel
#: zu finden. Basis: jeder Ersatz eines stornierten Stops verbraucht beim Broker einen
#: Schluessel; die verbrauchten bilden die lueckenlose Folge 0..k, weil immer der erste
#: freie genommen wird. Gesucht wird galoppierend plus binaer — 64 Abfragen reichen fuer
#: k bis 2**31. Begruendung: ein fester Versuchsdeckel liefe bei oft ersetzten Stops
#: irgendwann voll und liesse die Position dauerhaft ungeschuetzt. Jaehrliche Pruefung.
MAX_ABFRAGEN = 64
#: Wie oft hintereinander gelegt wird, wenn der gefundene Schluessel inzwischen doch
#: vergeben ist (Wettlauf) — danach ungeschuetzt mit Grund.
MAX_RUNDEN = 3

#: ADR-R19 (#3976): Frist fuer die Storno-Bestaetigung in Sekunden (Plan-Review #3985).
STORNO_FRIST_S = 1.5
_ABFRAGE_TAKT_S = 0.1

#: Status, in denen eine Stop-Order beim Broker noch wirkt.
_OFFEN = {
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "held",
    "partially_filled",
    "pending_replace",
}
#: Status, in denen eine stornierte Order keine Stuecke mehr bindet. ``filled`` gehoert
#: NICHT dazu: Dann hat der Stop ausgeloest, die Position ist veraendert — neu geplant wird
#: im naechsten Zyklus auf dem frischen Bestand.
_STORNO_ENDE = {"canceled", "expired", "rejected", "replaced", "done_for_day"}

_STAND: dict = {"positionen": 0, "geschuetzt": 0, "ungeschuetzt": [], "tag": None}

Fehlschlag = Tuple[str, str]


def _status(order: Any) -> str:
    roh = getattr(order, "status", "")
    return str(getattr(roh, "value", roh) or "").lower()


async def _warte_auf_stornos(
    api: Any, order_ids: Sequence[str], frist_s: float
) -> Set[str]:
    """Die Order-IDs, deren Stornierung der Broker in der Frist bestaetigt hat."""
    offen = set(order_ids)
    bestaetigt: Set[str] = set()
    if not offen:
        return bestaetigt

    gemeldet: Set[str] = set()

    async def _schleife() -> None:
        while offen:
            for oid in list(offen):
                # Review #3990 POLICY-01: ein Abfragefehler gilt nur fuer DIESE Order —
                # die anderen werden weiter geprueft, diese bleibt offen bis zur Frist.
                try:
                    order = await asyncio.to_thread(api.get_order_by_id, oid)
                except Exception:  # noqa: BLE001 — je Order, nie fuer alle
                    if oid not in gemeldet:
                        gemeldet.add(oid)
                        logging.warning(
                            "[BrokerStops] Storno-Status von %s nicht lesbar — "
                            "weiter geprueft bis zur Frist (#3976).",
                            oid,
                            exc_info=True,
                        )
                    continue
                if _status(order) in _STORNO_ENDE:
                    bestaetigt.add(oid)
                    offen.discard(oid)
            if offen:
                await asyncio.sleep(_ABFRAGE_TAKT_S)

    try:
        await asyncio.wait_for(_schleife(), timeout=frist_s)
    except asyncio.TimeoutError:
        logging.warning(
            "[BrokerStops] Stornierung nicht in %.1fs bestaetigt: %s — Neulegen im "
            "naechsten Zyklus (#3976).",
            frist_s,
            ", ".join(sorted(offen)),
        )
    except Exception:  # noqa: BLE001 — eine Statusabfrage darf die Pflege nie abbrechen
        logging.warning(
            "[BrokerStops] Storno-Status nicht lesbar — Neulegen im naechsten Zyklus "
            "(#3976).",
            exc_info=True,
        )
    return bestaetigt


def _sende(api: Any, stop: Any, client_order_id: str, halted: bool) -> None:
    """Ein Stop durch das Tor — als Schutz-Exit, protokolliert, nie blockiert (#3379)."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import StopOrderRequest

    from core.contracts import OrderIntent
    from core.engine.order_executor import gateway_for

    req = StopOrderRequest(
        symbol=stop.symbol,
        qty=stop.qty,
        side=OrderSide.SELL,
        time_in_force=(
            TimeInForce.GTC if stop.time_in_force == "gtc" else TimeInForce.DAY
        ),
        stop_price=stop.stop_price,
        client_order_id=client_order_id,
    )
    gateway_for(api).submit(
        OrderIntent(
            decision_id=stop.decision_id,
            symbol=stop.symbol,
            side="sell",
            qty=stop.qty,
            intent_kind="stop",
            is_protective_exit=True,
            exit_kind="risk",
            stop_price=stop.stop_price,
            time_in_force=stop.time_in_force,
            halted=halted,
        ),
        request=req,
    )


def _schluessel(stop: Any, versuch: int) -> str:
    from core.idempotency import derive_client_order_id

    return derive_client_order_id(stop.decision_id, "stop", versuch)


def _nicht_gefunden(exc: BaseException) -> bool:
    """Meldet der Broker „keine Order zu diesem Schluessel" (HTTP 404)?"""
    try:
        return int(getattr(exc, "status_code", 0) or 0) == 404
    except Exception:  # noqa: BLE001 — unlesbar heisst: kein Beleg fuer „frei"
        return False


class _Abfragen:
    """Lesende Schluessel-Abfragen mit Deckel (ADR-R19)."""

    def __init__(self, api: Any, stop: Any) -> None:
        self.api, self.stop, self.zahl = api, stop, 0

    async def order(self, versuch: int) -> Any:
        """Die Order zu Versuch ``versuch`` — ``None``, wenn der Schluessel frei ist."""
        self.zahl += 1
        if self.zahl > MAX_ABFRAGEN:
            raise RuntimeError(f"mehr als {MAX_ABFRAGEN} Schluessel-Abfragen")
        try:
            return await asyncio.to_thread(
                self.api.get_order_by_client_id, _schluessel(self.stop, versuch)
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — 404 heisst frei, alles andere ist Fehler
            if _nicht_gefunden(exc):
                return None
            raise

    async def erster_freier(self, vergeben: int) -> int:
        """Erster freier Versuch hinter ``vergeben`` (die vergebenen sind 0..k, lueckenlos)."""
        lo, schritt = vergeben, 1
        while await self.order(lo + schritt) is not None:
            lo, schritt = lo + schritt, schritt * 2
        hi = lo + schritt
        while hi - lo > 1:
            mitte = (lo + hi) // 2
            if await self.order(mitte) is not None:
                lo = mitte
            else:
                hi = mitte
        return hi


async def _lege(api: Any, stop: Any, halted: bool) -> Optional[str]:
    """Legt einen Stop; ``None`` bei Erfolg (oder wenn er schon liegt), sonst der Grund."""
    from core.engine.order_executor import ist_duplikat

    abfragen = _Abfragen(api, stop)
    versuch = 0
    for _ in range(MAX_RUNDEN):
        schluessel = _schluessel(stop, versuch)
        try:
            await asyncio.to_thread(_sende, api, stop, schluessel, halted)
            logging.warning(
                "[BrokerStops] %s: %s-Stop ueber %s @ %.2f gelegt (%s, %s).",
                stop.symbol,
                stop.time_in_force.upper(),
                stop.qty,
                stop.stop_price,
                stop.leg,
                schluessel,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — jeder Fehler wird gemeldet
            if not ist_duplikat(exc):
                logging.error(
                    "[BrokerStops] %s: Stop konnte NICHT gelegt werden (%s) — die "
                    "Position ist ohne Broker-Schutz.",
                    stop.symbol,
                    exc,
                    exc_info=True,
                )
                return f"Broker lehnt den Stop ab: {exc}"
        try:
            vorhanden = await abfragen.order(versuch)
            if vorhanden is not None and _status(vorhanden) in _OFFEN:
                logging.info(
                    "[BrokerStops] %s: Stop %s liegt bereits (Wiederholung).",
                    stop.symbol,
                    schluessel,
                )
                return None
            versuch = await abfragen.erster_freier(versuch)
        except Exception as exc:  # noqa: BLE001 — unlesbar heisst: kein Beleg
            logging.error(
                "[BrokerStops] %s: Schluessel %s verwendet, kein freier Schluessel "
                "ermittelbar — nicht neu gelegt (#3976).",
                stop.symbol,
                schluessel,
                exc_info=True,
            )
            return f"Schluessel {schluessel} verwendet, kein freier ermittelbar: {exc}"
    logging.error(
        "[BrokerStops] %s: nach %d Runden kein Stop gelegt (#3976).",
        stop.symbol,
        MAX_RUNDEN,
    )
    return f"kein Stop nach {MAX_RUNDEN} Runden"


async def fuehre_plan_aus(
    api: Any,
    plan: Any,
    liegende: Iterable[Any],
    *,
    storno: Callable[[str], Any],
    halted: bool,
    frist_s: float = STORNO_FRIST_S,
) -> List[Fehlschlag]:
    """Setzt ``plan`` um. Gibt ``(symbol, grund)`` je Stop zurueck, der nicht liegt."""
    symbol_je_order = {
        str(getattr(o, "id", "")): str(getattr(o, "symbol", "")) for o in liegende
    }
    for order_id in plan.to_cancel:
        try:
            await asyncio.to_thread(storno, order_id)
        except Exception as exc:  # noqa: BLE001 — ein Storno-Fehler blockiert nichts
            logging.warning(
                "[BrokerStops] Storno %s fehlgeschlagen: %s",
                order_id,
                exc,
                exc_info=True,
            )
    bestaetigt = await _warte_auf_stornos(api, list(plan.to_cancel), frist_s)
    wartend = {
        symbol_je_order.get(str(oid), "")
        for oid in plan.to_cancel
        if oid not in bestaetigt
    }
    gescheitert: List[Fehlschlag] = []
    for stop in plan.to_place:
        if stop.symbol in wartend:
            gescheitert.append(
                (
                    stop.symbol,
                    "Storno des alten Stops nicht bestaetigt — Neulegen im naechsten "
                    "Zyklus",
                )
            )
            continue
        grund = await _lege(api, stop, halted)
        if grund:
            gescheitert.append((stop.symbol, grund))
    return gescheitert


def merke_stand(
    symbole: Iterable[str], ungeschuetzt: Iterable[Fehlschlag], tag: date
) -> None:
    """Haelt den letzten Schutz-Stand fuer die Diagnose fest (je Symbol ein Grund)."""
    alle = sorted({str(s) for s in symbole})
    gruende: dict = {}
    for symbol, grund in ungeschuetzt:
        gruende.setdefault(str(symbol), str(grund))
    _STAND.update(
        positionen=len(alle),
        geschuetzt=len([s for s in alle if s not in gruende]),
        ungeschuetzt=[{"symbol": s, "grund": g} for s, g in sorted(gruende.items())],
        tag=tag.isoformat(),
    )


def letzter_stand() -> dict:
    """Kopie des letzten Schutz-Stands (fuer ``/engine-diagnostics``)."""
    return {
        **_STAND,
        "ungeschuetzt": [dict(e) for e in _STAND["ungeschuetzt"]],
    }
