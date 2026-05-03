# core/agent_registry.py
# Epic 2.3-Pre / PR-A + PR-C — AgentRegistry
#
# Verantwortlichkeit:
#   - Zentrale Registry aller verfügbaren Handelsstrategien
#   - Graceful Swap: pending_swap-Flag → commit_swap() nach Cycle-Ende
#   - Shadow Mode: Paper-Trade-Validierung vor Live-Swap (PR-C)
#   - Thread-safe via threading.Lock
#
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First
# Aufgerufen von: core/engine/base.py, core/engine/trading_loop.py
# Getestet in:    tests/unit/test_agent_registry.py, tests/unit/test_shadow_mode.py

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, TYPE_CHECKING

from core.exceptions import SwapInProgressError

if TYPE_CHECKING:
    from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Modul-level Singleton — für LangGraph-Node-Zugriff (graph.py)
_global_registry: Optional["AgentRegistry"] = None


def get_global_registry() -> Optional["AgentRegistry"]:
    """Gibt die global registrierte AgentRegistry-Instanz zurück."""
    return _global_registry


def set_global_registry(registry: "AgentRegistry") -> None:
    """Setzt die global registrierte AgentRegistry-Instanz (in BotEngine.__init__)."""
    global _global_registry
    _global_registry = registry


class AgentRegistry:
    """
    Zentrale Registry für Trading-Strategien mit Graceful-Swap-Mechanismus.

    Lifecycle:
        1. register(name, strategy, set_active=True)  → Strategy bekannt machen
        2. swap(name, shadow_mode=False)               → Pending-Flag setzen
        3. has_pending_swap()                          → TradingLoop prüft am Cycle-Ende
        4. commit_swap()                               → Tatsächlicher Wechsel

    Thread-Safety:
        Alle public Methoden sind unter self._lock ausgeführt.
        get_active() ist lock-frei lesend (atomic reference read in CPython).
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, "BaseStrategy"] = {}
        self._active_name: Optional[str] = None
        self._pending_name: Optional[str] = None
        self._shadow_mode: bool = False  # Epic 2.3-Pre / PR-C: Shadow Mode Flag
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        strategy: "BaseStrategy",
        set_active: bool = True,
    ) -> None:
        """Registriert eine Strategie unter dem gegebenen Namen.

        Args:
            name:       Eindeutiger Bezeichner der Strategie (z.B. "RLAgent").
            strategy:   Initialisierte BaseStrategy-Instanz.
            set_active: Wenn True (default), wird diese Strategy sofort aktiv.
        """
        with self._lock:
            self._strategies[name] = strategy
            if set_active:
                self._active_name = name
                logger.info("AgentRegistry: '%s' registered as ACTIVE strategy.", name)
            else:
                logger.info("AgentRegistry: '%s' registered (standby).", name)

    def list_registered(self) -> Dict[str, str]:
        """Gibt alle registrierten Strategien zurück: {name → strategy_class_name}."""
        with self._lock:
            return {name: type(s).__name__ for name, s in self._strategies.items()}

    # ------------------------------------------------------------------
    # Active Strategy
    # ------------------------------------------------------------------

    def get_active(self) -> Optional["BaseStrategy"]:
        """Gibt die aktuell aktive Strategy zurück (None wenn keine gesetzt)."""
        name = self._active_name  # atomic read in CPython — kein Lock nötig
        if name is None:
            return None
        return self._strategies.get(name)

    # ------------------------------------------------------------------
    # Swap (Graceful — nicht sofortiger Wechsel)
    # ------------------------------------------------------------------

    def swap(self, name: str, shadow_mode: bool = False) -> bool:
        """Initiiert einen Graceful Strategy-Swap.

        Der Wechsel findet NICHT sofort statt — er wird als Pending-Flag
        gesetzt und vom TradingLoop am Cycle-Ende via commit_swap() ausgeführt.

        Args:
            name:        Name der Ziel-Strategie (muss registriert sein).
            shadow_mode: Wenn True, läuft neue Strategy erst im Paper-Mode
                         (kein Broker-Zugriff) bis freigegeben.

        Returns:
            True wenn swap geplant, False wenn name unbekannt.

        Raises:
            SwapInProgressError: Wenn bereits ein Swap aussteht.
        """
        with self._lock:
            if self._pending_name is not None:
                raise SwapInProgressError(
                    f"Swap zu '{self._pending_name}' ist bereits ausstehend. "
                    "Bitte commit_swap() abwarten bevor ein neuer Swap initiiert wird."
                )
            if name not in self._strategies:
                logger.warning(
                    "AgentRegistry.swap: Unbekannte Strategy '%s'. Registrierte: %s",
                    name,
                    list(self._strategies.keys()),
                )
                return False

            self._pending_name = name
            self._shadow_mode = shadow_mode
            logger.info(
                "AgentRegistry: Graceful Swap zu '%s' ausstehend (aktiv: '%s'%s). "
                "Wechsel erfolgt am nächsten Cycle-Ende.",
                name,
                self._active_name,
                " [SHADOW MODE]" if shadow_mode else "",
            )
            return True

    def has_pending_swap(self) -> bool:
        """True wenn ein Swap aussteht und noch nicht committed wurde."""
        return self._pending_name is not None

    def is_shadow_mode(self) -> bool:
        """True wenn der ausstehende Swap ein Shadow-Mode-Swap ist."""
        return self._shadow_mode

    def commit_swap(self) -> None:
        """Führt den ausstehenden Swap durch (am Cycle-Ende vom TradingLoop aufgerufen).

        Wechselt die aktive Strategy auf den pending_name und löscht das Flag.
        Kein-Op wenn kein Swap aussteht.
        """
        with self._lock:
            if self._pending_name is None:
                return
            old_name = self._active_name
            self._active_name = self._pending_name
            self._pending_name = None
            self._shadow_mode = False  # Shadow-Flag zurücksetzen nach commit
            logger.info(
                "AgentRegistry: Swap committed — '%s' → '%s'.",
                old_name,
                self._active_name,
            )
