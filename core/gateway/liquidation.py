"""Bestandsliquidation ueber das Tor — je Bewegung ein Datensatz (#3383, Epic #3366).

Die drei Ausnahmepfade — Notverkauf, Breaker-Liquidation, Strategiewechsel — bewegen auf
einen Schlag den groessten Teil des Bestands und hinterliessen bisher keinen Datensatz.
Zwei von ihnen riefen ``close_all_positions``: ein Aufruf, der weder Symbol noch Menge
kennt. Ein Datensatz dazu koennte nur „alles" sagen — eine Absichtserklaerung, keine
Aufzeichnung. Deshalb wird der Sammelaufruf hier in eine Schleife ueber die tatsaechlich
gehaltenen Positionen aufgeloest.

**Das aendert das Ausfallverhalten, und zwar bewusst.** ``close_all_positions`` geht ganz
oder gar nicht durch; eine Schleife kann bei Position sieben von zehn scheitern und einen
teilweise liquidierten Bestand hinterlassen. Genau deshalb faengt diese Funktion jeden
Fehlschlag einzeln ab, macht weiter — der Rest des Bestands muss geschlossen werden — und
**beziffert die Differenz am Ende**, statt wie bisher in einer ERROR-Zeile zu enden, aus
der niemand ablesen kann, was offen blieb.

Broker-neutral: die Auftragsform kommt als ``request_factory`` von aussen herein. Die
fuenf heutigen Request-Formen werden unterschiedlich gebaut; ihre Vereinheitlichung
gehoert zu #3398 (BrokerPort), nicht hierher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence, Tuple

from core.contracts import IntentKind, OrderIntent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiquidationReport:
    """Was versucht wurde, was durchging, und was offen blieb."""

    attempted: int = 0
    submitted: int = 0
    #: ``(symbol, grund)`` je Position, die nicht geschlossen werden konnte.
    failed: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        return not self.failed


def _menge(position: Any) -> float:
    """Die schliessbare Menge — ``qty_available`` hat Vorrang.

    ``qty`` enthaelt auch Stuecke, die bereits in einer offenen Order gebunden sind; auf
    sie eine zweite Verkaufsorder zu setzen, liefe auf eine Leerverkaufs-Ablehnung hinaus.
    Bruchteile bleiben erhalten: sie hier auf ganze Stuecke zu kuerzen, liesse im
    Notverkauf genau die Reste stehen, die danach niemand mehr schliesst.
    """
    roh = getattr(position, "qty_available", None)
    if roh is None:
        roh = getattr(position, "qty", 0)
    try:
        return abs(float(roh))
    except (TypeError, ValueError):
        return 0.0


def liquidate_positions(
    gateway,
    positions: Iterable[Any],
    *,
    decision_id: str,
    intent_kind: IntentKind,
    request_factory: Callable[[str, float], Any],
    halted: bool = False,
    time_in_force: str = "day",
) -> LiquidationReport:
    """Schliesst jede Position einzeln ueber das Tor.

    Args:
        gateway: Das ``OrderGateway``. Es entscheidet, fuehrt aus und protokolliert.
        positions: Die gehaltenen Positionen des Brokers.
        decision_id: Die Entscheidung, aus der die Liquidation folgt — sie verbindet alle
            Einzelbewegungen desselben Vorgangs miteinander.
        intent_kind: ``panic``, ``breaker`` oder ``strategy_switch``. Alle drei stehen in
            der Freistellungs-Matrix: protokolliert, nie blockiert.
        request_factory: ``(symbol, qty) -> Auftrag`` in der Form des jeweiligen Brokers.
        halted: Der Halt-Zustand zum Zeitpunkt der Entscheidung. Er wird mitgefuehrt,
            nicht abgefragt — beim Notverkauf ist er absichtlich gesetzt.
    """
    versucht = 0
    abgesetzt = 0
    gescheitert: list[Tuple[str, str]] = []

    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "")
        qty = _menge(position)
        if not symbol or qty <= 0:
            continue

        versucht += 1
        try:
            intent = OrderIntent(
                decision_id=decision_id,
                symbol=symbol,
                side="sell",
                qty=qty,
                intent_kind=intent_kind,
                halted=halted,
                time_in_force=time_in_force,
            )
            entscheidung = gateway.submit(intent, request=request_factory(symbol, qty))
        except Exception as exc:  # eine Position darf die anderen nicht mitreissen
            gescheitert.append((symbol, str(exc)))
            logger.error(
                "[Liquidation %s] %s konnte nicht geschlossen werden: %s",
                intent_kind,
                symbol,
                exc,
            )
            continue

        if entscheidung.approved and entscheidung.reason_code.value != "system_error":
            abgesetzt += 1
        else:
            gescheitert.append((symbol, entscheidung.reason_code.value))

    bericht = LiquidationReport(
        attempted=versucht, submitted=abgesetzt, failed=tuple(gescheitert)
    )

    if not bericht.complete:
        # Die Differenz wird beziffert, nicht nur erwaehnt: ein teilweise liquidierter
        # Bestand ist ein Zustand, in dem jemand handeln muss.
        logger.error(
            "[Liquidation %s] UNVOLLSTAENDIG: %d von %d Positionen geschlossen, "
            "%d offen: %s",
            intent_kind,
            bericht.submitted,
            bericht.attempted,
            len(bericht.failed),
            ", ".join(f"{s} ({g})" for s, g in bericht.failed),
        )
    else:
        logger.warning(
            "[Liquidation %s] %d Position(en) geschlossen (decision_id=%s)",
            intent_kind,
            bericht.submitted,
            decision_id,
        )

    return bericht


def positions_summary(positions: Sequence[Any]) -> str:
    """Kurzform fuer Logzeilen: ``AAPL 2.0, MSFT 1.5``."""
    return ", ".join(f"{getattr(p, 'symbol', '?')} {_menge(p)}" for p in positions)
