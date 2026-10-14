"""Verkaufsgrund — warum eine Position verkauft wurde, mit den Ausloese-Werten (#4125).

Plan ``docs/4125-4126-audit-ablage/implementation_plan.md`` §2 Punkt 3 und §12. Bisher entstand
der Grund nur als Freitext (``ExitAnalysis.reason``, ``DecisionContext.stop_type``); Verlust %,
Haltedauer, Stufe, Hoch und Abstand gingen als eigene Felder verloren. Dieser Vertrag entsteht
**am Ort der Entscheidung** und wandert mit dem Signal zur Order und in die Audit-Ablage.

Die Klassen sind eine geschlossene Liste (§12b): jeder Verkaufsweg im Code hat genau eine.
``unknown`` ist der Rueckfall, wenn ein Weg keinen Grund liefert — immer mit WARNING.

``frozen=True``: Ein abgegebener Grund wird nicht nachtraeglich umgeschrieben.
Der Zeitpunkt wird uebergeben (Plan §9d), es gibt keinen ``default_factory=datetime.now``.
"""

import logging
from datetime import datetime
from typing import Dict, Literal, Optional, Union, get_args

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

VerkaufsgrundKlasse = Literal[
    # intelligent_exit.analyze_exit (Wege 1-7)
    "hard_stop",
    "trailing",
    "loss_cut",
    "loss_escalation",
    "momentum_fade",
    "composite",
    # Strategie- und Buchregeln (Wege 8, 10-12)
    "rank_drop",
    "rotation",
    "book_overflow",
    "trim",
    # Signale (Wege 13, 14)
    "consensus_sell",
    "model_sell",
    # Ausnahmepfade mit festem intent_kind (Wege 15-18)
    "displacement",
    "panic",
    "breaker",
    "strategy_switch",
    # Fills beim Broker (Wege 19, 20)
    "broker_stop",
    "manual",
    # Rueckfall, immer mit WARNING
    "unknown",
]

#: Alle zulaessigen Klassen (§12b). Eine neue Verkaufsstelle braucht hier ihre Klasse.
VERKAUFSGRUND_KLASSEN = frozenset(get_args(VerkaufsgrundKlasse))


class Verkaufsgrund(BaseModel):
    """Klasse und Ausloese-Werte eines Verkaufs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    klasse: VerkaufsgrundKlasse
    # Die Werte, die den Verkauf ausgeloest haben — je Klasse andere (Plan §12b).
    werte: Dict[str, Union[int, float, str]] = Field(default_factory=dict)
    # Wann entschieden wurde. Uebergeben vom Aufrufer, der die Uhr kennt (§9d).
    zeitpunkt: Optional[datetime] = None


def unbekannter_grund(quelle: str) -> Verkaufsgrund:
    """Rueckfall fuer einen Verkaufsweg ohne Grund: ``unknown``, nie still (CODING_POLICY 5.6)."""
    logger.warning(
        "Verkaufsgrund fehlt (%s) -> 'unknown'. Jeder Verkaufsweg braucht eine Klasse "
        "(#4125, Plan §12b).",
        quelle,
    )
    return Verkaufsgrund(klasse="unknown")
