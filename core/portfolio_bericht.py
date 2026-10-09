# portfolio_bericht.py
# #4187 (H-5d, #4285): Rebalancing und Bericht des Portfolio-Kerns, als Mixin von PortfolioManager.
"""Rebalancing-Empfehlungen, Portfolio-Bericht und Debattenverlauf (``BerichtMixin``).

Schnitt: ``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` (#4187), Abschnitte H-5d
und H-5e; Umsetzung #4285. Die Methoden lesen den Zustand von ``PortfolioManager`` über ``self``.

Zugriffsregel (Entscheidung §3): Kein Zielmodul importiert ``core.portfolio_manager``. Wer am
Modulobjekt patcht, patcht dort, wo der Name gelesen wird — ``load_approved_constraints`` also
hier, in ``core.portfolio_bericht``.
"""

import logging
from typing import Dict, List, Optional, Tuple

from core.governance.portfolio_constraints import load_approved_constraints


class BerichtMixin:
    """Rebalancing und Bericht von ``PortfolioManager`` (#4187, H-5d)."""

    def get_rebalance_recommendations(
        self, symbol_sector_map: Optional[Dict[str, str]] = None
    ) -> List[Dict]:
        """
        Analyze portfolio and recommend rebalancing actions.
        Only recommends if drift exceeds threshold and cooldowns are satisfied.

        #2654 (Epic #2655): an ADDITIVE sector-cap stage enforces HITL-approved relative
        sector caps (four-eyes store, #2653) with market-value-proportional REDUCE
        recommendations. ``symbol_sector_map`` comes from the engine's portfolio_context
        (same source the gatekeeper reads); callers that don't pass it get today's
        behaviour byte-identically — and if approved caps exist without a map this cycle,
        that gap is WARNED loudly, never silently skipped.
        """
        self.refresh_positions()
        recommendations = []

        if not self._position_scores:
            return recommendations

        # Validate total_capital to avoid weird distribution (negative or zero)
        if self.total_capital <= 0:
            logging.warning(
                "Portfolio Manager: total_capital <= 0 - skipping rebalance recommendations"
            )
            return recommendations

        respects, target_pct = self._rebalance_ziel()
        recommendations = self._drift_empfehlungen(respects, target_pct)

        # --- #2654: HITL-approved sector caps → native proportional REDUCE (upstream of the
        # Iron Dome, which stays the untouched fail-closed net). Fail-safe empty store ⇒ the
        # whole stage is a no-op and the output above is byte-identical to before.
        recommendations += self._sektor_deckel_empfehlungen(
            recommendations, symbol_sector_map
        )

        # Sort by absolute drift (largest first)
        recommendations.sort(key=lambda x: abs(x["drift_pct"]), reverse=True)

        return recommendations

    def _rebalance_ziel(self) -> Tuple[bool, float]:
        """#4285 (H-5e): Zielautorität des Drift-Passes → ``(respects, target_pct)``."""
        # Target-weight authority (conviction-weighting fix). RESPECTS_CONVICTION → de-concentrate
        # ONLY genuine over-concentration past MAX_POSITION_PERCENT (the hard sizing/gatekeeper cap),
        # so the conviction sizer's 5–25% band is left intact — winners are not trimmed back to a
        # flat 1/N weight the sizer just built past (which caused the buy-high/sell-low churn).
        # Legacy (flag off) → flat 1/max_positions equal-weight target, symmetric REDUCE/INCREASE.
        #
        # #3284 (Epic #3086): clean-weight sizing changes the TRIM target authority too, so the
        # rebalance-target does not contradict the clean-weight buy-target. Read at call time,
        # getattr-safe: if CLEAN_WEIGHT_SIZING is absent (e.g. the sizer PR not merged yet) →
        # "off" → byte-identical to today. Arm "a" (strict 1/N) → the symmetric flat-1/N Legacy
        # target. Arm "b" (1/N × vol) INTERIM → keep the conviction/cap-breach trim: it only trims
        # genuine over-concentration past MAX and therefore never fights the per-symbol vol-tilt;
        # a vol-AWARE symmetric arm-b trim needs per-symbol forecast_vol plumbed into the rebalance
        # path (PositionScore carries none) — separate follow-up.
        import config as _cfg

        _clean_mode = (
            str(getattr(_cfg, "CLEAN_WEIGHT_SIZING", "off") or "off").strip().lower()
        )
        _respects = self._trim_respects_conviction and _clean_mode != "a"
        if _respects:
            target_pct = self._max_position_pct * 100.0
        else:
            target_pct = 100.0 / self.max_positions
        return _respects, target_pct

    def _drift_empfehlungen(self, _respects: bool, target_pct: float) -> List[Dict]:
        """#4285 (H-5e): Drift-Pass über den Bestand, in der Reihenfolge der Positionen."""
        recommendations = []
        target_allocation = self.total_capital * (target_pct / 100.0)

        for symbol, score in self._position_scores.items():
            current_pct = (score.market_value / self.total_capital) * 100
            drift = current_pct - target_pct

            # RESPECTS_CONVICTION: only weight OVER the cap is a problem (a below-cap conviction
            # weight is legitimate — never trimmed, never INCREASEd). Legacy / clean-weight arm "a":
            # symmetric drift band around the flat-1/N target.
            if _respects:
                if drift < self._drift_threshold_pct:
                    continue
            elif abs(drift) < self._drift_threshold_pct:
                continue

            # Check cooldown
            if not self._can_trade_symbol(symbol):
                continue

            action = "REDUCE" if drift > 0 else "INCREASE"
            target_value = target_allocation
            current_value = score.market_value
            adjustment = target_value - current_value

            recommendations.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "current_pct": current_pct,
                    "target_pct": target_pct,
                    "drift_pct": drift,
                    "adjustment_value": adjustment,
                    # Additive (#sell-decisions Lever B): the current $-value of the
                    # holding. The de-concentration TRIM sizes a partial SELL as a
                    # dollar-fraction of THIS (held_qty * min(1, |adjustment|/market_value)),
                    # never a price division — the rec carries no price, and a 0/None
                    # price would crash the exit hook. Read-only reporting is unaffected.
                    "market_value": score.market_value,
                    "position_score": score.total_score,
                    "reasoning": f"{action} {symbol}: drift {drift:+.1f}% from target",
                }
            )
        return recommendations

    def _sektor_deckel_empfehlungen(
        self,
        recommendations: List[Dict],
        symbol_sector_map: Optional[Dict[str, str]],
    ) -> List[Dict]:
        """#4285 (H-5e): Sektor-Stufe (#2654) — REDUCE je genehmigtem Deckel, in Sektor-Folge.

        ``recommendations`` sind die Empfehlungen des Drift-Passes; sie werden nur gelesen.
        """
        sektor_empfehlungen: List[Dict] = []
        constraints = load_approved_constraints()
        if constraints:
            if not symbol_sector_map:
                logging.warning(
                    "SECTOR_CAP_NO_SECTOR_DATA: approved sector constraints %s present but "
                    "no symbol->sector map this cycle - caps NOT enforced (sector-source "
                    "brick pending in portfolio_context)",
                    sorted(constraints),
                )
            else:
                # Reductions already recommended above count toward each sector's excess —
                # a symbol is never double-dipped by the drift pass AND the sector pass.
                existing_reduce = {
                    r["symbol"]: abs(r["adjustment_value"])
                    for r in recommendations
                    if r["action"] == "REDUCE"
                }
                for sector, cap in constraints.items():
                    sektor_empfehlungen += self._sektor_reduktionen(
                        sector, cap, existing_reduce, symbol_sector_map
                    )
        return sektor_empfehlungen

    def _sektor_reduktionen(
        self,
        sector: str,
        cap: float,
        existing_reduce: Dict[str, float],
        symbol_sector_map: Dict[str, str],
    ) -> List[Dict]:
        """#4285 (H-5e): ein Sektor — Überschuss, Verteiler über die freien Namen, Rest-Warnung."""
        recommendations: List[Dict] = []
        members = [
            (sym, score)
            for sym, score in self._position_scores.items()
            if symbol_sector_map.get(sym) == sector
        ]
        if not members:
            return recommendations
        sector_value = sum(s.market_value for _, s in members)
        sector_pct = sector_value / self.total_capital
        if sector_pct <= cap:
            return recommendations
        excess = sector_value - cap * self.total_capital
        excess -= sum(existing_reduce.get(sym, 0.0) for sym, _ in members)
        if excess <= 0:
            return recommendations
        # Iterative allocator over the FREE names (not cooldown-locked, not
        # already reduced above), market-value-proportional.
        eligible = [
            (sym, s)
            for sym, s in members
            if sym not in existing_reduce and self._can_trade_symbol(sym)
        ]
        eligible_value = sum(s.market_value for _, s in eligible)
        planned = min(excess, eligible_value)
        if planned > 0 and eligible_value > 0:
            for sym, score in eligible:
                share = planned * (score.market_value / eligible_value)
                if share <= 0:
                    continue
                current_pct = (score.market_value / self.total_capital) * 100
                recommendations.append(
                    {
                        "symbol": sym,
                        "action": "REDUCE",
                        "current_pct": current_pct,
                        "target_pct": cap * 100,
                        "drift_pct": (sector_pct - cap) * 100,
                        "adjustment_value": -share,
                        # Same additive contract as the drift pass: the exit
                        # hook sizes the partial SELL from market_value, never
                        # a price division.
                        "market_value": score.market_value,
                        "position_score": score.total_score,
                        "reasoning": (
                            f"SECTOR_CAP {sector}: {sector_pct:.1%} > approved "
                            f"cap {cap:.1%} - reduce {sym} by ${share:,.0f}"
                        ),
                    }
                )
        if planned < excess:
            # Archon audit: a cooldown/conviction-blocked residual must never be
            # a silent breach — the operator sees WHY the cap is not yet met.
            remaining_pct = (sector_value - planned) / self.total_capital * 100
            logging.warning(
                "SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED: Sector %s remains "
                "at %.2f%% (Cap: %.2f%%)",
                sector,
                remaining_pct,
                cap * 100,
            )
        return recommendations

    def get_portfolio_summary(self) -> Dict:
        """Get a summary of current portfolio state"""
        self.refresh_positions()

        if not self._position_scores:
            return {
                "num_positions": 0,
                "max_positions": self.max_positions,
                "total_value": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "average_score": 0,
                "weakest": None,
                "strongest": None,
            }

        total_value = sum(p.market_value for p in self._position_scores.values())
        total_pnl = sum(p.unrealized_pnl for p in self._position_scores.values())
        avg_score = sum(p.total_score for p in self._position_scores.values()) / len(
            self._position_scores
        )

        weakest = self.get_weakest_position()
        strongest = self.get_strongest_position()

        return {
            "num_positions": len(self._position_scores),
            "max_positions": self.max_positions,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": (
                (total_pnl / (total_value - total_pnl) * 100)
                if total_value > total_pnl
                else 0
            ),
            "average_score": avg_score,
            "weakest": (
                {
                    "symbol": weakest.symbol,
                    "score": weakest.total_score,
                    "pnl_pct": weakest.unrealized_pnl_pct,
                }
                if weakest
                else None
            ),
            "strongest": (
                {
                    "symbol": strongest.symbol,
                    "score": strongest.total_score,
                    "pnl_pct": strongest.unrealized_pnl_pct,
                }
                if strongest
                else None
            ),
        }

    def get_debate_history(self, limit: int = 10) -> List[Dict]:
        """Get recent position swap debates"""
        return self._debate_history[-limit:]
