# core/round_table/agenten/trend.py
# #4110 (ARC-E6 G-6e): TrendAgent samt Enable-Gate und Gewicht, unveraendert aus
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


# #3250: TA-Feature-Voter (TrendAgent / VolumeConfirmationAgent). DARK-geshippt —
# Default-Gewicht 0.0 ⇒ SignalCandidate(weight=0.0) ⇒ Field(gt=0.0) schließt sie aus dem
# Konsens aus (byte-identisch). Beide lesen die bereits berechneten Last-Row-Skalare
# aus state["features"] (core/round_table/features.py via _compute_features_node),
# kein neuer Datenpfad. Env-Naht wie die übrigen Gewichte (config.py/config.oss.py).
_TREND_AGENT_WEIGHT = _consensus_weight("TREND_AGENT_WEIGHT", 0.0)


# ---------------------------------------------------------------------------
# TrendAgent + VolumeConfirmationAgent (#3250) — TA-Features → Richtungsstimmen
# ---------------------------------------------------------------------------
#
# Beide lesen NUR die bereits berechneten Last-Row-Skalare aus state["features"]
# (compute_technical_features in core/round_table/features.py, via
# _compute_features_node). DARK-geshippt: Default-Gewicht 0.0 UND Enable-Flag
# false ⇒ Enthaltung, Konsens byte-identisch. Abstain-Pfad exakt wie
# UpsideSkewAgent._abstain (weight 0.0 DIREKT im VoteResult, nie via self.weight —
# base_agent.py würde auf min_weight hochklemmen).


def _trend_agent_enabled() -> bool:
    """#3250: master gate for TrendAgent. Default OFF (dark) — der Agent enthält
    sich byte-identisch, bis ein Owner ihn armt (Muster _upside_skew_enabled)."""
    try:
        return bool(getattr(config.get_config(), "TREND_AGENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — ein Config-Read darf den Vote nie brechen
        logger.exception("Fehler beim Lesen der TREND_AGENT_ENABLED Config")
        return False


class TrendAgent(VotingAgent):
    """#3790 (war #3250): DIREKTIONALE Trendstimme aus dem Mehrhorizont-Trend nach Baz et al.

    Liest ``state["features"]["trend_baz"]`` (``core/round_table/features.py``,
    ``trend_baz_series``): Mittel dreier normierter, gesaettigter MACD-Signale
    (8/24, 16/48, 32/96), preisniveau-unabhaengig, Wert in (-0.97, 0.97).
    Score = ``0.5 + 0.5*trend_baz`` — neutral 0.5 bei 0, streng monoton im Eingang.

    Bewusst NICHT mehr genutzt: das MACD(12,26,9)-Histogramm (Dollar-skaliert, nicht
    zwischen Titeln vergleichbar; Signallinien-Kreuzungen ohne Prognosekraft laut
    Chong/Ng/Liew 2014) — ``macd_hist`` bleibt als Merkmal fuer den Decision-Record.
    Fehlt ``trend_baz`` (Historie < TREND_BAZ_MIN_BARS) oder ist er nicht endlich →
    Enthaltung (Gewicht 0.0 direkt im VoteResult). Flag OFF (Default) → Enthaltung
    (dark, byte-identisch). Ergaenzung zu Momentum; Startwert beim Einschalten 0.30 neben
    Momentum 0.45 (Owner 30.09.2026: Kapitalerhalt vor Rendite, 20-Tage-Horizont).
    """

    default_weight: float = _TREND_AGENT_WEIGHT
    min_weight: float = 0.0
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> SignalCandidate:
        """Enthaltung mit Gewicht 0.0 DIREKT im VoteResult (nie via self.weight —
        base_agent würde auf min_weight hochklemmen, Muster UpsideSkewAgent)."""
        return SignalCandidate(
            agent_name="TrendAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.NO_DATA,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]
        if not _trend_agent_enabled():
            return self._abstain(symbol, "TrendAgent dark (flag off)")

        features = state.get("features")
        if not features:
            return self._abstain(symbol, "no TA feature snapshot for this symbol today")
        trend = features.get("trend_baz")
        if trend is None:
            return self._abstain(
                symbol, "history too short for the multi-horizon trend (trend_baz)"
            )
        try:
            trend = float(trend)
        except (TypeError, ValueError):
            return self._abstain(symbol, f"trend_baz not usable ({trend!r})")
        if not math.isfinite(trend):
            return self._abstain(symbol, "trend_baz not finite")

        score = self._clamp(0.5 + 0.5 * trend)
        direction = "rising" if trend > 0 else ("falling" if trend < 0 else "flat")
        reasoning = (
            f"Multi-horizon trend {trend:+.3f} — {direction} over weeks to months "
            f"(score {score:.3f}). [src: normalised MACD 8/24, 16/48, 32/96 on daily closes]"
        )
        return SignalCandidate(
            agent_name="TrendAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=None,
            reasoning=reasoning,
        )
