# core/round_table/agenten/fundamentals.py
# #4086 (ARC-E6 G-6d): FundamentalsAgent samt Gewicht, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck. Die geteilten
# Fundamentaldaten-Helfer liest der Agent ueber ``_fd`` (agenten/_fundamentaldaten.py).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agenten import _fundamentaldaten as _fd
from core.round_table.agenten._basis import (
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")

# #3145(3)/#3146: consensus weights for the FundamentalsAgent / ValuationAgent. ACTIVATED
# at 0.35 as the shipped default (owner decision 2026-09-03). The config-missing fallback
# mirrors the config default so no path silently reverts to the old dormant 0.0.
_FUNDAMENTALS_AGENT_WEIGHT = _consensus_weight(
    "FUNDAMENTALS_AGENT_WEIGHT", 0.25
)  # #3698


class FundamentalsAgent(VotingAgent):
    """Beurteilt das GESCHAEFT: waechst der Umsatz, verdient die Firma Geld?

    Liest den PIT-Fundamentals-Cache (dieselbe Quelle wie der Report) und
    begruendet mit den Zahlen, auf denen das Urteil beruht.

    Gherkin (Architect):
      Given: Umsatz +65.5% YoY und Nettomarge 55.6%
      When:  FundamentalsAgent.vote()
      Then:  score > 0.5 und die Begruendung nennt beide Zahlen

      Given: kein Fundamentals-Eintrag im Cache
      When:  FundamentalsAgent.vote()
      Then:  weight == 0.0 (Abstinenz statt Rateschaetzung)
    """

    # ADR-RT-FUND-01: Schwellen sind redaktionelle Startkalibrierung, KEINE
    #   Modell-Ausgabe — sie gehoeren in den Walk-forward, nicht ins Bauchgefuehl.
    #   +15% YoY = waechst spuerbar; <=0% = schrumpft; Nettomarge >=20% = profitabel.
    GROWTH_STRONG_PCT: float = 15.0
    GROWTH_SHRINKING_PCT: float = 0.0
    MARGIN_STRONG_PCT: float = 20.0

    default_weight: float = (
        _FUNDAMENTALS_AGENT_WEIGHT  # #3145(3): config-gated, active default 0.35 (owner 2026-09-03)
    )
    min_weight: float = 0.0
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("FUNDAMENTALS_AGENT_ENABLED"):
            return _disabled_abstain("FundamentalsAgent", state["symbol"])

        import asyncio

        symbol = state["symbol"]
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(
            _fd._read_pit_fundamentals, symbol, close, state.get("current_time")
        )
        if not fund:
            logger.debug(
                "FundamentalsAgent: no PIT fundamentals for %s → abstention.", symbol
            )
            return SignalCandidate(
                agent_name="FundamentalsAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning="EXCLUDED — no verified fundamentals data yet",
            )

        # ⚠ The feed emits FRACTIONS (`revenue_yoy`, `net_margin`), not percent, and
        # there is no `*_pct` key. Reading the percent names returns None for every
        # symbol forever — verified against the real reader.
        growth = _fd._pct(fund.get("revenue_yoy"))
        margin = _fd._pct(fund.get("net_margin"))
        if growth is None and margin is None:
            return SignalCandidate(
                agent_name="FundamentalsAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning="EXCLUDED — no revenue or margin data available yet",
            )

        score, parts = 0.5, []
        if growth is not None:
            g = float(growth)
            if g >= self.GROWTH_STRONG_PCT:
                score += 0.2
                parts.append(f"revenue +{g:.1f}% year-over-year (growing)")
            elif g <= self.GROWTH_SHRINKING_PCT:
                score -= 0.2
                parts.append(f"revenue {g:.1f}% year-over-year (shrinking)")
            else:
                parts.append(f"revenue +{g:.1f}% year-over-year (modest)")
        if margin is not None:
            m = float(margin)
            if m >= self.MARGIN_STRONG_PCT:
                score += 0.2
                parts.append(f"net margin {m:.1f}% (profitable)")
            elif m <= 0:
                score -= 0.2
                parts.append(f"net margin {m:.1f}% (loss-making)")
            else:
                parts.append(f"net margin {m:.1f}% (thin)")

        return SignalCandidate(
            agent_name="FundamentalsAgent",
            symbol=symbol,
            score=self._clamp(score),
            weight=self.weight,
            reasoning="Business health: "
            + "; ".join(parts)
            + ". [src: audited annual filings]",
        )
