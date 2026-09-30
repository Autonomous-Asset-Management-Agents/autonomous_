"""Broker-seitige Stops — der Schutz ueberlebt den Ausfall der Engine (#3382, Epic #3366).

Im Kern lag bisher **keine einzige** Stop-Order beim Broker. Stops wurden ausschliesslich
von der laufenden Engine ausgewertet (``core/position_stop.py``); stirbt sie, ist die
Position ungeschuetzt. #3380 hat dafuer gesorgt, dass die Engine **im Halt** weiter
schuetzt — diese Datei sorgt dafuer, dass der Schutz **ohne** Engine haelt.

**Bruchstuecke bleiben** (Owner-Entscheid: grosser Vorteil bei kleinen Konten). Alpaca
nimmt fraktionale Orders aber nur als Tages-Order an; eine GTC-Stop-Order ueber einen
Bruchteil ist nicht moeglich. Daraus folgt die Aufteilung:

* **ganze Stuecke → GTC-Stop.** Er liegt, bis er ausgefuehrt oder abgeraeumt wird —
  ueber Nacht, ueber einen Neustart, ueber einen Engine-Ausfall hinweg.
* **Bruchstueck-Rest → Tages-Stop**, der zu Sitzungsbeginn erneuert werden muss.

Das getragene Restrisiko ist damit auf **weniger als ein Stueck je Position** begrenzt —
und zwar nur in dem Zeitfenster, in dem ein Tages-Stop abgelaufen und noch nicht erneuert
ist. Es wird ausgewiesen (``unprotected``), nicht versteckt.

Diese Datei ist ein **reiner Planer**: kein I/O, keine Konfiguration, kein Broker. Sie
sagt, was liegen soll und was weg muss; das Absetzen gehoert zum Aufrufer und laeuft ueber
das Tor (#3379).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Optional, Sequence, Tuple

from core.idempotency import derive_client_order_id

#: Kleinste Menge, fuer die sich beim Broker eine Order absetzen laesst. Darunter gibt es
#: keinen Schutz — das ist eine Eigenschaft des Brokers, keine Entscheidung von uns, und
#: die betroffene Position wird deshalb als ungeschuetzt ausgewiesen.
MIN_BROKER_QTY = 0.001

#: Toleranz beim Vergleich von Mengen und Preisen. Ein Stop wird nur ersetzt, wenn er
#: wirklich abweicht — jedes Ersetzen oeffnet eine Luecke, in der nichts liegt.
EPSILON = 1e-6

Leg = Literal["whole", "fraction"]
TimeInForce = Literal["gtc", "day"]


@dataclass(frozen=True)
class StopOrder:
    """Ein Stop, der beim Broker liegen soll."""

    symbol: str
    qty: float
    stop_price: float
    time_in_force: TimeInForce
    leg: Leg
    decision_id: str
    client_order_id: str


@dataclass(frozen=True)
class BrokerStopPlan:
    """Was gelegt, was abgeraeumt werden muss — und was ungeschuetzt bleibt."""

    to_place: Tuple[StopOrder, ...] = ()
    to_cancel: Tuple[str, ...] = ()
    #: ``(symbol, grund)`` je Position, fuer die kein Stop gelegt werden kann.
    unprotected: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Traegt jede Position Schutz?"""
        return not self.unprotected


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _menge(position: Any) -> float:
    """Die schliessbare Menge — ``qty_available`` hat Vorrang.

    ``qty`` enthaelt auch Stuecke, die bereits in einer offenen Order gebunden sind. Ein
    Stop ueber die volle Menge liefe dort auf eine Leerverkaufs-Ablehnung hinaus.
    """
    roh = getattr(position, "qty_available", None)
    if roh is None:
        roh = getattr(position, "qty", 0)
    return abs(_f(roh))


def _stop_preis(avg_entry_price: float, stop_loss_pct: float) -> float:
    """Die Schwelle unter dem Einstand, auf ganze Cent gerundet."""
    return round(avg_entry_price * (1.0 - abs(stop_loss_pct) / 100.0), 2)


def _decision_id(symbol: str, leg: Leg, session_date: Optional[date]) -> str:
    """Die Identitaet des Stops — und damit sein Idempotenz-Schluessel (#3387).

    Der GTC-Schluessel ist **ueber Neustarts stabil**: nach einem Neustart wird derselbe
    Stop neu geplant, und er darf dann kein Duplikat sein. Der Tages-Schluessel traegt das
    Sitzungsdatum, weil die Erneuerung am naechsten Tag eine absichtlich neue Order ist.
    """
    if leg == "whole":
        return f"stop-{symbol}"
    return f"stop-{symbol}-{session_date.isoformat() if session_date else 'day'}"


def _bauen(
    symbol: str, qty: float, preis: float, leg: Leg, session_date: Optional[date]
) -> StopOrder:
    decision_id = _decision_id(symbol, leg, session_date)
    return StopOrder(
        symbol=symbol,
        qty=qty,
        stop_price=preis,
        time_in_force="gtc" if leg == "whole" else "day",
        leg=leg,
        decision_id=decision_id,
        client_order_id=derive_client_order_id(decision_id, "stop", 0),
    )


def _passt(vorhanden: Any, gewuenscht: StopOrder) -> bool:
    """Deckt ein liegender Stop den gewuenschten ab?

    Menge und Preis muessen stimmen; die Zeitgeltung auch — ein Tages-Stop kann keinen
    GTC-Stop ersetzen, er ist am naechsten Morgen weg.
    """
    if str(getattr(vorhanden, "symbol", "")) != gewuenscht.symbol:
        return False
    tif = str(getattr(vorhanden, "time_in_force", "")).lower()
    if tif != gewuenscht.time_in_force:
        return False
    if abs(_f(getattr(vorhanden, "qty", 0)) - gewuenscht.qty) > EPSILON:
        return False
    return abs(_f(getattr(vorhanden, "stop_price", 0)) - gewuenscht.stop_price) <= 0.005


def plan_broker_stops(
    positions: Iterable[Any],
    *,
    stop_loss_pct: float,
    existing_stops: Sequence[Any] = (),
    session_date: Optional[date] = None,
    existing_session_date: Optional[date] = None,
) -> BrokerStopPlan:
    """Plant den broker-seitigen Schutz fuer die gehaltenen Positionen.

    Args:
        positions: Die gehaltenen Broker-Positionen.
        stop_loss_pct: Die eingestellte Schwelle in Prozent unter dem Einstand
            (``STOP_LOSS_PCT``; hart begrenzt durch ``HARD_STOP_LOSS_PCT``).
        existing_stops: Die beim Broker liegenden Stop-Orders.
        session_date: Der laufende Handelstag.
        existing_session_date: Der Tag, an dem die liegenden Tages-Stops gelegt wurden.
            Weicht er von ``session_date`` ab, sind sie abgelaufen und werden erneuert —
            das ist der Ausloeser „Sitzungsbeginn".
    """
    to_place: list[StopOrder] = []
    unprotected: list[Tuple[str, str]] = []
    gebraucht: set[str] = set()

    vorhanden = list(existing_stops or [])
    # Ein Tages-Stop aus einer frueheren Sitzung liegt nicht mehr — er darf nicht als
    # Deckung zaehlen, sonst bliebe der Bruchstueck-Rest stillschweigend ungeschuetzt.
    if existing_session_date is not None and session_date is not None:
        if existing_session_date != session_date:
            vorhanden = [
                s
                for s in vorhanden
                if str(getattr(s, "time_in_force", "")).lower() != "day"
            ]

    for pos in positions or []:
        symbol = str(getattr(pos, "symbol", "") or "")
        if not symbol:
            continue
        gebraucht.add(symbol)

        qty = _menge(pos)
        avg = _f(getattr(pos, "avg_entry_price", 0))

        if avg <= 0:
            unprotected.append(
                (symbol, "kein Einstandspreis — eine Schwelle waere erfunden")
            )
            continue
        if qty < MIN_BROKER_QTY:
            unprotected.append(
                (symbol, f"Menge {qty} unter der Broker-Mindestmenge {MIN_BROKER_QTY}")
            )
            continue

        preis = _stop_preis(avg, stop_loss_pct)
        if preis <= 0:
            unprotected.append((symbol, "Stop-Preis waere nicht positiv"))
            continue

        ganze = float(int(qty))
        rest = round(qty - ganze, 9)

        gewuenscht: list[StopOrder] = []
        if ganze >= 1:
            gewuenscht.append(_bauen(symbol, ganze, preis, "whole", session_date))
        if rest >= MIN_BROKER_QTY:
            gewuenscht.append(_bauen(symbol, rest, preis, "fraction", session_date))

        if not gewuenscht:
            unprotected.append(
                (symbol, f"Menge {qty} ergibt kein absetzbares Stop-Bein")
            )
            continue

        for stop in gewuenscht:
            if not any(_passt(v, stop) for v in vorhanden):
                to_place.append(stop)

    # Abzuraeumen ist, was zu keiner gewuenschten Deckung gehoert: ein Stop ohne Position
    # ist ein Leerverkauf in Wartestellung, ein Stop auf falscher Menge oder falschem
    # Preis ist ein Schutz, der nicht schuetzt.
    alle_gewuenschten = [s for s in to_place]
    to_cancel: list[str] = []
    for v in list(existing_stops or []):
        oid = getattr(v, "id", None)
        if not oid:
            continue
        v_symbol = str(getattr(v, "symbol", ""))
        if v_symbol not in gebraucht:
            to_cancel.append(str(oid))
            continue
        # Der Stop gehoert zu einer gehaltenen Position — passt er zu einer der
        # gewuenschten Deckungen, bleibt er; sonst wird er durch die neue ersetzt.
        ersetzt = any(s.symbol == v_symbol for s in alle_gewuenschten)
        if ersetzt and not any(
            _passt(v, s) for s in alle_gewuenschten if s.symbol == v_symbol
        ):
            to_cancel.append(str(oid))

    return BrokerStopPlan(
        to_place=tuple(to_place),
        to_cancel=tuple(to_cancel),
        unprotected=tuple(unprotected),
    )
