"""``ReconciliationRecord`` — was ein Abgleichlauf verglichen und gefunden hat (#3386).

Der vorhandene Abgleich kennt nur ein flaches Tupel (``core/reconciliation.py:26``,
``ReconciliationBreak`` mit ``order_id``/``symbol``/``break_type``). Man kann einem
solchen Befund nicht ansehen, **was** verglichen wurde, **wann**, und **wie** die beiden
Seiten aussahen. Ein Abgleich, dessen Ergebnis man nicht nachvollziehen kann, ist kein
Beleg — und ARC-E2 verlangt einen Beleg, weil eine Abweichung neue Orders sperrt.

Die Abweichungsarten sind eine **geschlossene** Liste. Eine offene Liste liesse zu, dass
ein neuer Befundtyp durch alle Auswertungen faellt, ohne dass jemand es merkt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: Geschlossene Liste. Die ersten beiden sind die heute schon bekannten
#: (``core/reconciliation.py:31``) und bleiben zeichengleich, damit der bestehende
#: Dienst in #3389 ohne Umbenennung darauf umgestellt werden kann.
BreakKind = Literal[
    "orphaned_order",  # Order beim Broker, die die Engine nicht kennt
    "position_mismatch",  # Position auf beiden Seiten, aber mit verschiedener Menge
    "unknown_position",  # Position beim Broker, die die Engine gar nicht kennt
    "missing_fill",  # Ausfuehrung beim Broker, zu der kein FillEvent vorliegt
    "missing_order",  # Order in den Buechern, die der Broker nicht kennt
    # #3389: Der Broker war nicht erreichbar. Das ist ein BEFUND, kein sauberer
    # Lauf — sonst meldete ein Netzausfall "alles in Ordnung", und das ist das
    # gefaehrlichste Ergebnis, das ein Abgleich liefern kann.
    "broker_unreachable",
]


class ReconciliationBreak(BaseModel):
    """Eine einzelne Abweichung — mit **beiden** Seiten des Vergleichs.

    ``broker_side`` und ``engine_side`` sind absichtlich Text: sie sollen im Alarm lesbar
    sein und je nach Abweichungsart Verschiedenes tragen (eine Menge, eine Order-ID, ein
    Zustand). Eine typisierte Union waere hier Praezision an der falschen Stelle — der
    Empfaenger dieses Datensatzes ist ein Mensch, der entscheiden muss.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: BreakKind
    symbol: str = ""
    order_id: str = ""
    #: Was beim Broker steht.
    broker_side: str = ""
    #: Was in unseren Buechern steht.
    engine_side: str = ""
    detail: str = ""
    #: #3589: Hat derselbe Lauf die Abweichung bereits beseitigt?
    #:
    #: Ein nachgetragener Fill ist ein Befund — die Buecher waren falsch — aber sie sind
    #: es nach dem Lauf nicht mehr. Ein solcher Befund wird gemeldet und **sperrt
    #: nicht**; nur ein offener tut das (``ReconciliationService._report``). Ohne die
    #: Unterscheidung haelt ein einziger Verbindungsabriss den Handel an, bis ein Mensch
    #: eine Sperre aufhebt, deren Ursache der Lauf selbst schon behoben hat (gemessen am
    #: 23.09.2026 am Paper-Konto: zwei solche Befunde aus einem Abriss).
    #:
    #: Bleibt nach dem Nachtragen wirklich etwas offen, erzeugt derselbe Lauf dafuer
    #: einen eigenen Befund (``position_mismatch``) — die Unterscheidung verliert nichts.
    geheilt: bool = False


class ReconciliationRecord(BaseModel):
    """Das Ergebnis **eines** Abgleichlaufs.

    ``clean`` ist abgeleitet und nicht gesetzt: ein Lauf ist genau dann sauber, wenn er
    keine Abweichung gefunden hat. Als eigenes Feld koennte es der Wahrheit widersprechen.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime

    #: Was verglichen wurde — je Seite die Anzahl der betrachteten Posten. Ein Lauf ohne
    #: Vergleichsumfang ist von einem Lauf, der nichts gefunden hat, sonst nicht zu
    #: unterscheiden.
    broker_orders: int = Field(default=0, ge=0)
    broker_positions: int = Field(default=0, ge=0)
    engine_orders: int = Field(default=0, ge=0)
    engine_positions: int = Field(default=0, ge=0)

    breaks: Tuple[ReconciliationBreak, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.breaks
