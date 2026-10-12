"""#3745 — Vergleich zweier Datensätze mit Einordnung je Feld.

Die Abnahme CH-5 verlangt, „zulaessige Adapter-Unterschiede von unzulaessigen
Kern-Unterschieden" zu trennen. Genau das tut diese Datei — und nur das. Sie ist
Vorrichtung, kein Produktivcode.

**Warum eine Einordnung noetig ist.** Zwei Laeufe desselben Order-Intents sind nicht
bitgleich, und das ist richtig so (gemessen am 28.09.2026):

* ``decision_id`` ist eine **frische Identitaet** je Entscheidung. Waere sie gleich,
  waere das der Fehler — zwei Entscheidungen truegen dieselbe Kennung.
* ``client_order_id`` wird aus der ``decision_id`` abgeleitet (#3387) und ist deshalb
  ebenfalls je Lauf verschieden.
* ``alpaca_order_id`` vergibt der Broker.

Gleich bleiben muss der **Kern**: Symbol, Seite, Menge, Fuellstand.

**Fehlerrichtung.** Ein Feld, das hier in keiner Liste steht, gilt als ``UNBEKANNT``
und damit als **unzulaessige** Abweichung. Ein neues Feld muss also bewusst eingeordnet
werden, statt still durchzurutschen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

KERN: Tuple[str, ...] = (
    "symbol",
    "side",
    "qty",
    "quantity",
    "gefuellt",
    "status",
    "time_in_force",
    "order_type",
)

IDENTITAET: Tuple[str, ...] = (
    "decision_id",
    "client_order_id",
)

ADAPTER: Tuple[str, ...] = (
    "alpaca_order_id",
    "broker_order_id",
    "id",
    "submitted_at",
    "created_at",
)


def einordnung(feld: str) -> str:
    if feld in KERN:
        return "KERN"
    if feld in IDENTITAET:
        return "IDENTITAET"
    if feld in ADAPTER:
        return "ADAPTER"
    return "UNBEKANNT"


@dataclass(frozen=True)
class Abweichung:
    """Eine Abweichung in einem Feld, samt Herkunft und Einordnung."""

    modul: str
    feld: str
    einordnung: str
    links: Any
    rechts: Any

    @property
    def unzulaessig(self) -> bool:
        """KERN und UNBEKANNT sind unzulaessig — im Zweifel unzulaessig."""
        return self.einordnung in ("KERN", "UNBEKANNT")

    def __str__(self) -> str:
        wertung = "unzulaessig" if self.unzulaessig else "zulaessig"
        return (
            f"{self.modul}.{self.feld} [{self.einordnung}, {wertung}]: "
            f"{self.links!r} vs {self.rechts!r}"
        )


def vergleiche(
    links: Dict[str, Any], rechts: Dict[str, Any], modul: str
) -> List[Abweichung]:
    """Alle Felder beider Saetze, Feld fuer Feld, mit Einordnung.

    ``modul`` benennt die Herkunft der Saetze (zum Beispiel ``unsere_saetze``), damit
    die Meldung „Modul und Feld der Abweichung" nennt, wie die Abnahme es verlangt.
    """
    abweichungen: List[Abweichung] = []
    for feld in sorted(set(links) | set(rechts)):
        if links.get(feld) != rechts.get(feld):
            abweichungen.append(
                Abweichung(
                    modul=modul,
                    feld=feld,
                    einordnung=einordnung(feld),
                    links=links.get(feld),
                    rechts=rechts.get(feld),
                )
            )
    return abweichungen


def unzulaessige(abweichungen: List[Abweichung]) -> List[Abweichung]:
    return [a for a in abweichungen if a.unzulaessig]


def bericht(abweichungen: List[Abweichung]) -> str:
    if not abweichungen:
        return "keine Abweichung"
    return "\n".join("  " + str(a) for a in abweichungen)
