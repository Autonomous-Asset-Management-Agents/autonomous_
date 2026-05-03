# core/reconciliation.py
# Epic 2.3-Pre / PR-B — Reconciliation Loop (Watch-Compare-Act)
#
# Verantwortlichkeit:
#   - Abgleich von internem Bot-State mit Broker-Realität (Alpaca)
#   - Bereinigung verwaister Orders nach einem Graceful Handover
#   - Läuft als eigenständiger async Task parallel zum TradingLoop
#
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First
# Getestet in: tests/unit/test_reconciliation.py

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set, NamedTuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------


class ReconciliationBreak(NamedTuple):
    """Diskrepanz zwischen internem State und Broker-Realität."""

    order_id: str
    symbol: str
    break_type: str  # "orphaned_order" | "position_mismatch"


# ---------------------------------------------------------------------------
# ReconciliationService
# ---------------------------------------------------------------------------


class ReconciliationService:
    """
    Watch-Compare-Act-Loop für Broker-State-Synchronisation.

    Lifecycle:
        service = ReconciliationService(api, redis_client)
        await service.run_loop(interval_s=30)  # als asyncio Task

    Thread/Coroutine-Safety:
        _internal_order_ids wird vom TradingLoop bei Order-Submit befüllt.
        Zugriff ist asyncio-safe (single event loop).
    """

    def __init__(self, api: Any, redis_client: Any) -> None:
        """
        Args:
            api:          Alpaca TradingClient-Instanz.
            redis_client: Redis-Connection (sync oder async-kompatibel).
        """
        self.api = api
        self.redis_client = redis_client
        self._internal_order_ids: Set[str] = set()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_order(self, order_id: str) -> None:
        """Registriert eine Order als intern bekannt (nach Submit)."""
        self._internal_order_ids.add(order_id)

    def deregister_order(self, order_id: str) -> None:
        """Entfernt eine Order nach Fill oder Cancel."""
        self._internal_order_ids.discard(order_id)

    async def run_loop(self, interval_s: int = 30) -> None:
        """Hauptschleife: läuft kontinuierlich, Watch→Compare→Act alle interval_s."""
        self._running = True
        logger.info(
            "ReconciliationService: Loop gestartet (Interval: %ds).", interval_s
        )
        while self._running:
            try:
                broker_state = await self._watch()
                breaks = self._compare(broker_state)
                if breaks:
                    logger.warning(
                        "ReconciliationService: %d Breaks gefunden: %s",
                        len(breaks),
                        [b.order_id for b in breaks],
                    )
                await self._act(breaks)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Fehler-Isolation: Exception stoppt NICHT den TradingLoop
                logger.error("ReconciliationService: Loop-Fehler: %s", e, exc_info=True)
            await asyncio.sleep(interval_s)
        logger.info("ReconciliationService: Loop beendet.")

    def stop(self) -> None:
        """Stoppt den Reconciliation-Loop."""
        self._running = False

    # ------------------------------------------------------------------
    # Watch
    # ------------------------------------------------------------------

    async def _watch(self) -> Dict[str, List[Any]]:
        """
        Holt den aktuellen Broker-State von Alpaca.

        Returns:
            {"orders": [...], "positions": [...]}
        """
        try:
            orders = await asyncio.to_thread(self.api.get_orders)
        except Exception as e:
            logger.warning("ReconciliationService._watch: get_orders failed: %s", e)
            orders = []

        try:
            positions = await asyncio.to_thread(self.api.get_all_positions)
        except Exception as e:
            logger.warning(
                "ReconciliationService._watch: get_all_positions failed: %s", e
            )
            positions = []

        return {"orders": orders, "positions": positions}

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    def _compare(self, broker_state: Dict[str, List[Any]]) -> List[ReconciliationBreak]:
        """
        Vergleicht Broker-State mit internem State.

        Eine Order gilt als 'orphaned' wenn ihre ID nicht in
        _internal_order_ids bekannt ist (d.h. der Bot hat sie nicht platziert
        oder die Zuordnung ging verloren — z.B. nach einem Hot-Swap).

        Returns:
            Liste von ReconciliationBreak-Einträgen.
        """
        breaks: List[ReconciliationBreak] = []

        for order in broker_state.get("orders", []):
            order_id = getattr(order, "id", None)
            if order_id and order_id not in self._internal_order_ids:
                breaks.append(
                    ReconciliationBreak(
                        order_id=order_id,
                        symbol=getattr(order, "symbol", "UNKNOWN"),
                        break_type="orphaned_order",
                    )
                )
                logger.warning(
                    "ReconciliationService: Orphaned Order gefunden: %s (Symbol: %s)",
                    order_id,
                    getattr(order, "symbol", "?"),
                )

        return breaks

    # ------------------------------------------------------------------
    # Act
    # ------------------------------------------------------------------

    async def _act(self, breaks: List[ReconciliationBreak]) -> None:
        """
        Bereinigt Breaks automatisch.

        Für orphaned_order: Order via api.cancel_order_by_id() stornieren.
        Fehler bei einzelner Cancel-Operation werden geloggt, nicht propagiert.
        """
        for b in breaks:
            if b.break_type == "orphaned_order":
                try:
                    await asyncio.to_thread(self.api.cancel_order_by_id, b.order_id)
                    logger.info(
                        "ReconciliationService: Orphaned Order %s (%s) storniert.",
                        b.order_id,
                        b.symbol,
                    )
                except Exception as e:
                    logger.error(
                        "ReconciliationService: Cancel für %s fehlgeschlagen: %s",
                        b.order_id,
                        e,
                    )
