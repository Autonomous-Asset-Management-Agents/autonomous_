# core/round_table/agenten/upside_skew.py
# #4110 (ARC-E6 G-6e): UpsideSkewAgent samt Enable-Gate und Gewicht, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck (neuladetreu
# ueber _frisch). Tests patchen das Enable-Gate hier, nicht ueber agents
# (bewacht von tests/unit/test_agenten_keine_toten_patches.py).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agenten._basis import _consensus_weight
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


# #3154 (UXC-1 S1): UpsideSkew had the config key but never read it (Review B4) and now
# gets the same seam as the other directional voters. Unset env ⇒ byte-identical defaults.
_UPSIDE_SKEW_WEIGHT = _consensus_weight("UPSIDE_SKEW_WEIGHT", 0.30)


def _upside_skew_enabled() -> bool:
    """#3095 (b): master gate for the UpsideSkewAgent (25Δ risk-reversal). Default
    OFF (dark) — the agent abstains, byte-identical, until an owner arms it."""
    try:
        return bool(getattr(config.get_config(), "UPSIDE_SKEW_AGENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — a config read must never break a vote
        logger.exception("Fehler beim Lesen der UPSIDE_SKEW_AGENT_ENABLED Config")
        return False


class UpsideSkewAgent(VotingAgent):
    """#3095 (b): DIRECTIONAL upside signal from the options SKEW.

    The IV *level* (VIXAware) is directionless; the SKEW is not. The 25-delta
    risk-reversal ``RR = IV(25Δ Call) − IV(25Δ Put)`` is positive when the market
    pays up for UPSIDE — a bullish tilt. Score = percentile rank of this symbol's RR
    against the PREVIOUS session's cross-section (AC-7): high RR → high score → BUY
    pressure. This is a real direction voter (unlike the risk-only VIXAware), so it
    stays in the direction mean and is damped with every other remaining voter in
    risk-off (#3618: the damped set is derived from ``consensus_exclusions``).

    Reads only completed-cycle state channels (producer = Step-2): ``risk_reversal``
    (today, this symbol), ``risk_reversal_reference`` (yesterday's distribution),
    ``risk_reversal_reference_date``. Missing data → abstain (weight 0.0, never a
    guessed vote). Flag OFF → abstain (dark, byte-identical).
    """

    default_weight: float = (
        _UPSIDE_SKEW_WEIGHT  # #3154: Env-Naht verdrahtet (Default 0.30)
    )
    min_weight: float = 0.10
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> SignalCandidate:
        """Abstention with weight 0.0 DIRECTLY on the VoteResult (never via
        self.weight, which base_agent would clamp up to min_weight = a real vote)."""
        return SignalCandidate(
            agent_name="UpsideSkewAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.NO_DATA,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]
        if not _upside_skew_enabled():
            return self._abstain(symbol, "UpsideSkewAgent dark (flag off)")

        from core.options_skew import rr_percentile

        rr = state.get("risk_reversal")
        if rr is None:
            return self._abstain(symbol, "no risk-reversal for this symbol today")
        try:
            rr = float(rr)
        except (TypeError, ValueError):
            return self._abstain(symbol, f"risk-reversal not usable ({rr!r})")
        if not math.isfinite(rr):
            return self._abstain(symbol, "risk-reversal not finite")

        referenz = state.get("risk_reversal_reference")
        if not referenz or len(referenz) < 2:
            return self._abstain(
                symbol, "no reference distribution from a completed cycle yet"
            )

        perzentil = rr_percentile(rr, list(referenz))
        if perzentil is None:
            return self._abstain(
                symbol, "reference distribution carries no distinct values"
            )

        # HIGH risk-reversal (bullish skew) → HIGH score (unlike the risk-only,
        # inverted VIXAware). Direction, not magnitude.
        score = self._clamp(perzentil)
        datum = state.get("risk_reversal_reference_date") or "unknown"
        reasoning = (
            f"Options skew (25Δ risk-reversal) for this stock at {rr:+.4f} — rank "
            f"{perzentil:.3f} against the {len(referenz)} symbols of the previous "
            f"session ({datum}); a positive/high skew means the market pays up for "
            f"UPSIDE (score {score:.3f}). [src: 25Δ call/put implied vol, ref {datum}]"
        )
        return SignalCandidate(
            agent_name="UpsideSkewAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
