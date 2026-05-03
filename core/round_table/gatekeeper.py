# core/round_table/gatekeeper.py
# Epic 2.5 — Round Table V2: ComplianceGatekeeper (Iron Dome)
#
# Deterministischer Hard-Block vor Trade-Execution.
# Kein AI-Agent kann diesen Block überschreiben.
#
# Checks:
#   1. PDT (Pattern Day Trader): < 3 Day Trades in 5 Tagen
#   2. Konzentrations-Limit: Position > 25% Portfolio
#   3. Tages-Limit: > max_daily_trades
#   4. Position-Lock Guard: position_locked=True (Partial Fill Reconciliation)
#
# Policy: CODING_POLICY.md §1 Compliance-First, §11.5 TDD

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GatekeeperDecision:
    """
    Ergebnis einer ComplianceGatekeeper-Prüfung.
    approved=False + reason erklärt das Veto (MiFID II Audit-Trail).
    """

    approved: bool
    reason: str
    symbol: str


class ComplianceGatekeeper:
    """
    The 'Iron Dome' des Round Table V2.

    Alle Checks sind synchron und dict-basiert (kein async I/O, keine DB-Calls).
    portfolio_context wird vom Aufrufer (run_round_table) injiziert.

    portfolio_context erwartet:
        {
          "day_trades_last_5d": int,          # PDT-Check
          "max_daily_trades": int,             # Tages-Limit
          "current_daily_trades": int,         # Tages-Limit
          "symbol_weights": dict[str, float],  # Konzentration
          "position_locked": bool,             # Partial Fill Guard
        }
    """

    # Konfigurierbare Schwellenwerte (können in Config extern gesetzt werden)
    PDT_MAX_DAY_TRADES = 3
    CONCENTRATION_LIMIT = 0.25  # 25% max pro Symbol

    async def check(
        self,
        symbol: str,
        score: float,
        portfolio_context: dict,
    ) -> GatekeeperDecision:
        """
        Prüft ob ein Symbol die Compliance-Grenzen einhält.

        Returns:
            GatekeeperDecision(approved=True) wenn alle Checks bestanden,
            GatekeeperDecision(approved=False, reason=...) bei Veto.
        """
        # 1. Partial Fill / Position Lock Guard
        if portfolio_context.get("position_locked", False):
            return GatekeeperDecision(
                approved=False,
                reason="PositionLocked: Offene Position oder laufender Swap verhindert neuen Trade",
                symbol=symbol,
            )

        # 2. PDT-Check (Pattern Day Trader Rule)
        day_trades = portfolio_context.get("day_trades_last_5d", 0)
        if day_trades >= self.PDT_MAX_DAY_TRADES:
            logger.warning(
                "ComplianceGatekeeper: PDT-Limit erreicht für %s (%d >= %d)",
                symbol,
                day_trades,
                self.PDT_MAX_DAY_TRADES,
            )
            return GatekeeperDecision(
                approved=False,
                reason=f"PDTLimit: {day_trades} Day Trades in 5 Tagen (Limit: {self.PDT_MAX_DAY_TRADES})",
                symbol=symbol,
            )

        # 3. Konzentrations-Limit
        symbol_weights = portfolio_context.get("symbol_weights", {})
        symbol_weight = symbol_weights.get(symbol, 0.0)
        if symbol_weight > self.CONCENTRATION_LIMIT:
            logger.warning(
                "ComplianceGatekeeper: Konzentrations-Limit für %s: %.1f%% > %.1f%%",
                symbol,
                symbol_weight * 100,
                self.CONCENTRATION_LIMIT * 100,
            )
            return GatekeeperDecision(
                approved=False,
                reason=(
                    f"ConcentrationLimit: {symbol} bereits {symbol_weight:.1%} "
                    f"des Portfolios (Limit: {self.CONCENTRATION_LIMIT:.1%})"
                ),
                symbol=symbol,
            )

        # 4. Tages-Limit
        current_daily = portfolio_context.get("current_daily_trades", 0)
        max_daily = portfolio_context.get("max_daily_trades", 50)
        if current_daily >= max_daily:
            return GatekeeperDecision(
                approved=False,
                reason=f"DailyLimit: {current_daily}/{max_daily} Trades heute erreicht",
                symbol=symbol,
            )

        # Alle Checks bestanden
        logger.debug(
            "ComplianceGatekeeper: %s APPROVED (score=%.3f, day_trades=%d, weight=%.1f%%)",
            symbol,
            score,
            day_trades,
            symbol_weight * 100,
        )
        return GatekeeperDecision(
            approved=True,
            reason="AllChecksPasssed: PDT, Konzentration und Tageslimit OK",
            symbol=symbol,
        )
