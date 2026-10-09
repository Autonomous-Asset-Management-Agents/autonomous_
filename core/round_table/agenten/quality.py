# core/round_table/agenten/quality.py
# #4110 (ARC-E6 G-6e): QualityAgent samt Enable-Gate und Gewicht, unveraendert aus
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


# #3275: QualityAgent (Composite-Quality-Richtungsstimme). DARK über das ENABLE-Flag
# (QUALITY_AGENT_ENABLED default false ⇒ Enthaltung ⇒ SignalCandidate(weight=0.0) ⇒ aus dem
# Konsens ausgeschlossen, byte-identisch) — exakt das UpsideSkew-Muster. Das Gewicht ist
# ein DORMANTES 0.30 (min 0.10, wie UpsideSkew): inert in Produktion (der Agent enthält
# sich), aber > 0, sobald die RTR-0-Attribution das Flag armt, damit sein Score offline
# ATTRIBUIERBAR ist (ein Vote mit Gewicht 0.0 wäre von einer Enthaltung nicht
# unterscheidbar). Liest keinen neuen Datenbezug. BORA-parallel zu config.oss.py.
_QUALITY_AGENT_WEIGHT = _consensus_weight("QUALITY_AGENT_WEIGHT", 0.30)


# ---------------------------------------------------------------------------
# QualityAgent (#3275) — Composite-Quality-Richtungsstimme (AC-7, dark)
# ---------------------------------------------------------------------------
#
# Der belegte Quality-Faktor (Novy-Marx Gross-Profitability + voller 9-Punkte-
# Piotroski) hatte im Gremium keine eigene Stimme. Der Composite wird AUSSCHLIESSLICH
# aus dem vorhandenen PIT-Fundamentals-Feed abgeleitet (kein neuer Ingest) und lebt in
# ``core/quality_score.py`` (reine, unit-getestete Rechnung). BEWUSST OHNE Nettomarge
# (FundamentalsAgent) und Verschuldung als eigener Term (ValuationAgent) — Piotroski
# subsumiert ROA/Accruals/Margen-/Leverage-Δ, keine Doppelzählung.
#
# AC-7 (kein Look-ahead) — exakt das VIXAware/UpsideSkew-Muster: der Agent liest die
# vom Producer gefüllten State-Kanäle ``quality_score`` (dieser Titel, heute) und
# ``quality_reference`` (Querschnitt der VORSESSION) und votet den Perzentilrang. Er
# rechnet den Composite NICHT selbst — die einzige Quelle ist der Producer
# (trading_loop._quality_state_keys live / replay-Injektion offline). Fehlt ein Kanal
# → Enthaltung (Gewicht 0.0 DIREKT im VoteResult). Flag OFF (Default) → der Producer
# emittiert None ⇒ Enthaltung (dark, byte-identisch).


def _quality_agent_enabled() -> bool:
    """#3275: Master-Gate für QualityAgent. Default OFF (dark) — der Agent enthält
    sich byte-identisch, bis ein Owner ihn armt (Hausmuster _upside_skew_enabled /
    _trend_agent_enabled; Lesefehler ⇒ Default false = fail-open-to-dark)."""
    try:
        return bool(getattr(config.get_config(), "QUALITY_AGENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — ein Config-Read darf den Vote nie brechen
        logger.exception("Fehler beim Lesen der QUALITY_AGENT_ENABLED Config")
        return False


class QualityAgent(VotingAgent):
    """#3275: Composite-Quality-Richtungsstimme (dark), cross-sektional (AC-7).

    Rankt den Composite-Quality-Score dieses Titels gegen den Querschnitt der
    Vorsession (``quality_reference``, vom Producer geliefert): hohe Qualität → hoher
    Rang → BUY-Neigung. Spiegelt ``VIXAwareRiskAgent._vote_implied_vol`` /
    ``UpsideSkewAgent``. Enthaltung (Gewicht 0.0 DIREKT im VoteResult, wie
    ``UpsideSkewAgent._abstain``) ohne Composite/Referenz. Flag OFF → dark.

    Gherkin:
      Given QUALITY_AGENT_ENABLED=false (default)
      Then enthält sich der Agent (weight 0.0), Konsens byte-identisch

      Given quality_score + quality_reference (≥2 distinkte Werte) im State
      When QualityAgent.vote() bei aktivem Flag
      Then votet er den Perzentilrang seines Composites gegen die Vorsession.
    """

    default_weight: float = _QUALITY_AGENT_WEIGHT
    min_weight: float = 0.10  # wie UpsideSkew: eine ECHTE Stimme ist attribuierbar (>0)
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> SignalCandidate:
        # Gewicht 0.0 DIREKT im VoteResult (nie via self.weight — base_agent würde auf
        # min_weight=0.10 hochklemmen = eine echte Stimme). So bleibt der dark-Pfad
        # byte-identisch (Field(gt=0.0) filtert die Enthaltung aus dem Konsens).
        return SignalCandidate(
            agent_name="QualityAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.NO_DATA,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]
        if not _quality_agent_enabled():
            return self._abstain(symbol, "QualityAgent dark (flag off)")

        from core.quality_score import quality_percentile

        score = state.get("quality_score")
        if score is None:
            return self._abstain(
                symbol, "no composite quality score for this symbol today"
            )
        try:
            score = float(score)
        except (TypeError, ValueError):
            return self._abstain(symbol, f"quality score not usable ({score!r})")
        if not math.isfinite(score):
            return self._abstain(symbol, "quality score not finite")

        referenz = state.get("quality_reference")
        if not referenz or len(referenz) < 2:
            return self._abstain(
                symbol, "no reference distribution from a completed cycle yet"
            )

        perzentil = quality_percentile(score, list(referenz))
        if perzentil is None:
            return self._abstain(
                symbol, "reference distribution carries no distinct values"
            )

        # HOHE Qualität (hoher Composite) → HOHER Rang → HOHER Score (BUY-Neigung).
        rang = self._clamp(perzentil)
        datum = state.get("quality_reference_date") or "unknown"
        reasoning = (
            f"Business quality (Novy-Marx gross profitability + 9-point Piotroski) "
            f"composite {score:.3f} — rank {perzentil:.3f} against the "
            f"{len(referenz)} symbols of the previous session ({datum}); a higher-"
            f"quality business leans buy (score {rang:.3f}). "
            f"[src: audited annual filings, reference {datum}]"
        )
        return SignalCandidate(
            agent_name="QualityAgent",
            symbol=symbol,
            score=rang,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
