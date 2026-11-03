from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional


class AuditAblagePort(ABC):
    """Port zur Audit-Ablage (#4126, Plan §9c).

    Das Order-Tor (VC-3) darf die Ablage nicht direkt importieren: Die Umsetzung schreibt die
    Hash-Kette ueber ``core.round_table.senate_log``, und das Tor kennt die Recherche-Stufe
    nicht (Vertrag ``gateway-kennt-engine-nicht``). Die Composition Root baut die Umsetzung
    und meldet sie hier an (``setze_ablage``); das Tor holt sie mit ``hole_ablage``.
    """

    @abstractmethod
    def lege_ab(self, art: str, daten: Mapping[str, Any]) -> None:
        """Legt ein Ereignis ab. Wirft nie; ein Fehler wird geloggt und gezaehlt."""
        pass  # pragma: no cover


# #4126 (Plan §11): Die Anmeldung liegt hier, im neutralen Port-Modul, und nicht im Tor.
# Die Recherche erreicht die Composition Root (round_table.runner, drawdown_guard); meldete die
# Root beim Tor an, erreichte die Recherche VC-3 (Vertrag ``recherche-sendet-nicht``).
_angemeldet: Optional[AuditAblagePort] = None


def setze_ablage(port: Optional[AuditAblagePort]) -> None:
    """Meldet die Audit-Ablage an (``None`` meldet sie ab). Aufrufer: die Composition Root."""
    global _angemeldet
    _angemeldet = port


def hole_ablage() -> Optional[AuditAblagePort]:
    """Die angemeldete Audit-Ablage, oder ``None``."""
    return _angemeldet
