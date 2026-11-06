# core/round_table/agenten/specialist_alpha.py
# #4084 (ARC-E6 G-6b): SpecialistAlphaAgent samt Gewichtshelfer, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck.
# Die Registry (_specialist_registry_instance, gesetzt von agents.set_specialist_registry)
# und die Warmup-Drossel liest der Agent zur Laufzeit ueber ``_ag`` (Zugriffsregel, agenten/).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table import agents as _ag
from core.round_table.agenten._basis import _agent_enabled, _disabled_abstain
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


def _specialist_alpha_weight() -> float:
    """#1346 / PR #2839: config-gated vote weight for SpecialistAlphaAgent.

    Default 0.40 keeps the specialist ACTIVE. The agent leverages local LLMs (Ollama)
    to enable deep specialist research without incurring expensive Cloud API costs.
    The os.environ read lives in config.py/config.oss.py (CODING_POLICY §2.10),
    not in the finance-core. Invalid values fall back to dormant.
    """
    try:
        return max(
            0.0, float(getattr(config.get_config(), "SPECIALIST_ALPHA_WEIGHT", 0.0))
        )
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 2. SpecialistAlphaAgent (w:0.55) — Stock Specialist System (Epic 3.3)
# ---------------------------------------------------------------------------

# #1346: resolve the config-gated weight ONCE at import — not twice in the class body
# (avoids a monkeypatch race between default_weight and max_weight reading different env).
_SPECIALIST_ALPHA_WEIGHT = _specialist_alpha_weight()


class SpecialistAlphaAgent(VotingAgent):
    """
    Liest Sentiment-Score aus dem StockSpecialistRegistry (Epic 3.3).
    Konvertiert sentiment_score (0-100) in einen normalisierten Score (0.0-1.0).

    Fallback: 0.5 (neutral) wenn Registry nicht verfügbar oder kein Report gecacht.
    Empfehlung: "buy" → ≥0.6 | "sell" → ≤0.4 | "hold" → ~0.5
    """

    # #1346: config-gated. Default 0.0 keeps the specialist DORMANT (excluded from
    # consensus, byte-identical to today). SPECIALIST_ALPHA_WEIGHT (e.g. 0.55) restores
    # a real weighted vote — resolves the stale "w:0.55" header comment above.
    default_weight: float = _SPECIALIST_ALPHA_WEIGHT
    min_weight: float = 0.0
    max_weight: float = 2.0 if _SPECIALIST_ALPHA_WEIGHT > 0.0 else 0.0

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("SPECIALIST_ALPHA_AGENT_ENABLED"):
            return _disabled_abstain("SpecialistAlphaAgent", symbol)

        if _ag._specialist_registry_instance is not None:
            try:
                report = _ag._specialist_registry_instance.get_report(symbol)
                if report is not None:
                    # Map 0-100 sentiment_score to 0.0-1.0
                    raw_score = report.sentiment_score / 100.0
                    # Recommendation nudge: buy/sell shift score ±0.05
                    rec = getattr(report, "recommendation", "hold")
                    if rec == "buy":
                        raw_score = min(1.0, raw_score + 0.05)
                    elif rec == "sell":
                        raw_score = max(0.0, raw_score - 0.05)
                    score = self._clamp(raw_score)
                    escalation = (
                        " [ESCALATED]" if getattr(report, "escalate", False) else ""
                    )
                    reasoning = (
                        f"Our stock specialist rates {symbol} at "
                        f"{report.sentiment_score:.0f}/100 with a {rec} "
                        f"recommendation{escalation} (score {score:.3f})."
                    )
                    return SignalCandidate(
                        agent_name="SpecialistAlphaAgent",
                        symbol=symbol,
                        score=score,
                        weight=self.weight,
                        abstain_reason=AbstainReason.NO_DATA if score is None else None,
                        reasoning=reasoning,
                    )
            except Exception as exc:
                logger.debug(
                    "SpecialistAlphaAgent: Registry error for %s: %s", symbol, exc
                )

        # Kein Report gecacht (Registry nicht bereit oder Warmup läuft):
        # weight=0.0 → Pydantic Field(gt=0.0) schließt diesen Vote aus dem Konsens aus.
        # Besser ausgeschlossen als 0.5 mit vollem Gewicht (w:0.55) den Konsens zu zerren!
        # RTR-3 (#1950): Bei AKTIVEM Gewicht ist ein dauerhaft fehlender Report ein
        # Betriebsproblem — der gearmte Voter zahlt kein Signal ein → WARNING (§5.6,
        # nie DEBUG), gedrosselt auf 1×/Symbol/Prozess. Dormant bleibt der heutige
        # DEBUG-Pfad byte-identisch; `max_weight > 0` short-circuits davor, damit der
        # dormante Pfad keinen zusätzlichen Redis-Read (weight-Property) bekommt.
        live_weight = self.weight if self.max_weight > 0.0 else 0.0
        if live_weight > 0.0 and symbol not in _ag._warmup_warned_symbols:
            _ag._warmup_warned_symbols.add(symbol)
            logger.warning(
                "SpecialistAlphaAgent: Gewicht aktiv (%.2f), aber KEIN Report für %s — "
                "Vote aus Konsens AUSGESCHLOSSEN (w=0; Registry im Warmup oder nicht "
                "verbunden/injiziert).",
                live_weight,
                symbol,
            )
        else:
            logger.debug(
                "SpecialistAlphaAgent: kein Report für %s — Vote aus Konsens AUSGESCHLOSSEN (w=0)",
                symbol,
            )
        return SignalCandidate(
            agent_name="SpecialistAlphaAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.WARMUP,  # ← EXCLUDED: Pydantic gt=0.0 schlägt fehl → aus active_votes entfernt
            reasoning="EXCLUDED — research report not ready yet (warming up)",
        )
