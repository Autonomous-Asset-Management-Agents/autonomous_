# core/round_table/agenten/valuation.py
# #4086 (ARC-E6 G-6d): ValuationAgent samt Gewicht und Multimetrik-Schalter, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck. Die geteilten
# Fundamentaldaten-Helfer liest der Agent ueber ``_fd`` (agenten/_fundamentaldaten.py).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import config
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
_VALUATION_AGENT_WEIGHT = _consensus_weight("VALUATION_AGENT_WEIGHT", 0.0)  # #3698


def _valuation_multimetric_enabled() -> bool:
    """#3146: gate for the ValuationAgent PEG -> P/S / P/B fallback. Default OFF (dark)
    — the agent is weight=0 anyway; ON only widens which names it can speak on."""
    try:
        return bool(
            getattr(config.get_config(), "VALUATION_MULTIMETRIC_ENABLED", False)
        )
    except Exception:  # noqa: BLE001 — a config read must never break a vote
        logger.exception("Fehler beim Lesen der VALUATION_MULTIMETRIC_ENABLED Config")
        return False


class ValuationAgent(VotingAgent):
    """Beurteilt den PREIS: ist das Multiple relativ zum Gewinnwachstum gestreckt?

    Trailing PEG (KGV / GEWINNwachstum) + Verschuldungsgrad aus derselben PIT-Quelle
    wie der Report. Kein Forward-KGV — dafuer fehlen lokale Schaetzungen, und eine
    geratene Schaetzung waere genau die Fabrikation, die wir verbieten.

    ⚠ PEG ist auf EARNINGS growth definiert, nicht auf Umsatzwachstum. Eine fruehere
    Fassung teilte das KGV durch `revenue_yoy` und nannte das Ergebnis trotzdem
    "PEG" — ein Etikett, das dem Leser eine andere Kennzahl vorspiegelt als die
    gerechnete. Hier wird `eps_yoy` gelesen; fehlt es, wird abstiniert, statt die
    naechstbeste Zahl unter dem bekannten Namen zu servieren.

    Gherkin (Architect):
      Given: KGV 40.5 auf +65.5% GEWINNwachstum (PEG 0.62) und Verb/EK 0.31
      When:  ValuationAgent.vote()
      Then:  score > 0.5 und die Begruendung nennt PEG und Verschuldungsgrad

      Given: Gewinnwachstum <= 0 (PEG nicht bildbar)
      When:  ValuationAgent.vote()
      Then:  weight == 0.0 (Abstinenz statt Division durch ~0)
    """

    # ADR-RT-VAL-01: redaktionelle Startkalibrierung, KEINE Modell-Ausgabe.
    #   PEG < 1 = Wachstum nicht eingepreist; PEG > 3 = gestreckt;
    #   Verb/EK < 1 = schuldenarme Bilanz.
    PEG_CHEAP: float = 1.0
    PEG_STRETCHED: float = 3.0
    DEBT_HEALTHY: float = 1.0

    # ADR-RT-VAL-02 (#3146): PEG-fallback multiples — redaktionelle Startkalibrierung,
    # KEINE Modell-Ausgabe (gehört in den Walk-forward, wie VAL-01). P/S<1 / P/B<1 =
    # günstig; über der oberen Grenze = gestreckt.
    PS_CHEAP: float = 1.0
    PS_STRETCHED: float = 10.0
    PB_CHEAP: float = 1.0
    PB_STRETCHED: float = 3.0

    default_weight: float = (
        _VALUATION_AGENT_WEIGHT  # #3146: config-gated, active default 0.35 (owner 2026-09-03)
    )
    min_weight: float = 0.0
    max_weight: float = 1.50

    def _multimetric_vote(self, fund, symbol, as_of_txt: str):
        """#3146: entity-appropriate PEG fallback. Returns a VoteResult or None.

        P/S for names without positive earnings (unprofitable growth); P/B where a
        book value exists (asset-heavy / financials). P/FFO (REITs) is Phase 2. Only
        a metric that is actually present produces a vote — never a fabricated one.
        """
        revenue = (fund or {}).get("revenue")
        ps = (fund or {}).get("ps")
        pb = (fund or {}).get("pb")
        if ps is not None and revenue:
            s = float(ps)
            score = (
                0.65 if s < self.PS_CHEAP else (0.35 if s > self.PS_STRETCHED else 0.5)
            )
            return SignalCandidate(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=self._clamp(score),
                weight=self.weight,
                reasoning=(
                    f"Valuation (no positive earnings) — price-to-sales {s:.2f}"
                    f"{as_of_txt}. [src: audited annual filings]"
                ),
            )
        if pb is not None:
            b = float(pb)
            score = (
                0.65 if b < self.PB_CHEAP else (0.35 if b > self.PB_STRETCHED else 0.5)
            )
            return SignalCandidate(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=self._clamp(score),
                weight=self.weight,
                reasoning=(
                    f"Valuation (book-value basis) — price-to-book {b:.2f}"
                    f"{as_of_txt}. [src: audited annual filings]"
                ),
            )
        return None

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("VALUATION_AGENT_ENABLED"):
            return _disabled_abstain("ValuationAgent", state["symbol"])

        import asyncio

        symbol = state["symbol"]
        # The price is what makes `pe` exist at all — the feed derives it from
        # price ÷ per-share earnings, so without it every price ratio is None.
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(
            _fd._read_pit_fundamentals, symbol, close, state.get("current_time")
        )
        pe = (fund or {}).get("pe")
        # PEG is defined on EARNINGS growth: `eps_yoy`, a FRACTION in the feed.
        growth = _fd._pct((fund or {}).get("eps_yoy"))
        as_of_txt = ""
        if fund:
            as_of = fund.get("filing_date") or fund.get("as_of")
            if as_of:
                as_of_txt = f" (as of {as_of})"

        if not fund or pe is None or growth is None or float(growth) <= 0:
            # #3146: PEG is undefined (loss-makers / financials / REITs). Fall back to
            # an entity-appropriate multiple where the data supports one, instead of
            # abstaining. Flag OFF (default) => byte-identical PEG-only behaviour.
            if _valuation_multimetric_enabled():
                mm = self._multimetric_vote(fund, symbol, as_of_txt)
                if mm is not None:
                    return mm
            logger.debug(
                "ValuationAgent: no usable P/E or earnings growth for %s → abstention.",
                symbol,
            )
            return SignalCandidate(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning=(
                    f"EXCLUDED — no reliable price-to-earnings or "
                    f"earnings-growth data yet{as_of_txt}"
                ),
            )

        peg = float(pe) / float(growth)
        debt = _fd._debt_to_equity(fund)
        debt_txt = "n/a" if debt is None else f"{float(debt):.2f}"
        if peg < self.PEG_CHEAP and (debt is None or float(debt) < self.DEBT_HEALTHY):
            score = 0.7
            verdict = "the market has not yet priced in its growth"
        elif peg > self.PEG_STRETCHED:
            score = 0.3
            verdict = "the price looks stretched relative to growth"
        else:
            score = None
            verdict = "neither cheap nor stretched"

        return SignalCandidate(
            agent_name="ValuationAgent",
            symbol=symbol,
            score=self._clamp(score),
            weight=self.weight,
            reasoning=(
                f"Valuation: {verdict} — PEG {peg:.2f} "
                f"(price-to-earnings {float(pe):.1f} vs +{float(growth):.1f}% "
                f"earnings growth), debt-to-equity {debt_txt}{as_of_txt}. "
                f"[src: audited annual filings]"
            ),
        )
