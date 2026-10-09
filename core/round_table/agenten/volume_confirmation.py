# core/round_table/agenten/volume_confirmation.py
# #4110 (ARC-E6 G-6e): VolumeConfirmationAgent samt Enable-Gate, Gewicht und Volumen-Skala, unveraendert aus
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


# #3250: TA-Feature-Voter, Gegenstueck zum TrendAgent (agenten/trend.py).
_VOLUME_CONFIRM_AGENT_WEIGHT = _consensus_weight("VOLUME_CONFIRM_AGENT_WEIGHT", 0.0)


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

# Redaktionelle Startkalibrierung (KEIN Modell-Fit) fuer die Volumenstimme: die Skala
# haelt den Score in (0,1), streng monoton, 0.5 neutral. Der TrendAgent braucht seit #3790
# keine Skala mehr (trend_baz ist bereits normiert und gesaettigt).
_VOLUME_RATIO_SCALE = 1.0


def _volume_confirm_agent_enabled() -> bool:
    """#3250: master gate for VolumeConfirmationAgent. Default OFF (dark)."""
    try:
        return bool(getattr(config.get_config(), "VOLUME_CONFIRM_AGENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — ein Config-Read darf den Vote nie brechen
        logger.exception("Fehler beim Lesen der VOLUME_CONFIRM_AGENT_ENABLED Config")
        return False


class VolumeConfirmationAgent(VotingAgent):
    """#3250: Volumen-Bestätigungsstimme aus dem 5d/20d-Volumenverhältnis.

    Liest ``state["features"]["vol_ratio_5_20"]`` (5-Tage-Durchschnittsvolumen /
    20-Tage-Durchschnittsvolumen; ``core/round_table/features.py``). Hohes jüngeres
    Volumen bestätigt die Richtungsüberzeugung — der Score steigt monoton mit dem
    Verhältnis um den neutralen Punkt 1.0 (5d == 20d):
    ``score = 0.5 + 0.5*tanh((vol_ratio - 1.0) / _VOLUME_RATIO_SCALE)``.

    Fehlt ``features`` / der Schlüssel / ist der Wert nicht endlich oder ≤ 0 →
    Enthaltung (Gewicht 0.0 direkt). Flag OFF (Default) → Enthaltung (dark).
    """

    default_weight: float = _VOLUME_CONFIRM_AGENT_WEIGHT
    min_weight: float = 0.0
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> SignalCandidate:
        return SignalCandidate(
            agent_name="VolumeConfirmationAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.NO_DATA,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]
        if not _volume_confirm_agent_enabled():
            return self._abstain(symbol, "VolumeConfirmationAgent dark (flag off)")

        features = state.get("features")
        if not features:
            return self._abstain(symbol, "no TA feature snapshot for this symbol today")
        vol_ratio = features.get("vol_ratio_5_20")
        if vol_ratio is None:
            return self._abstain(symbol, "no vol_ratio_5_20 in the feature snapshot")
        try:
            vol_ratio = float(vol_ratio)
        except (TypeError, ValueError):
            return self._abstain(symbol, f"vol_ratio_5_20 not usable ({vol_ratio!r})")
        if not math.isfinite(vol_ratio) or vol_ratio <= 0:
            return self._abstain(symbol, "vol_ratio_5_20 not finite or non-positive")

        score = self._clamp(
            0.5 + 0.5 * math.tanh((vol_ratio - 1.0) / _VOLUME_RATIO_SCALE)
        )
        reasoning = (
            f"Recent volume {vol_ratio:.2f}× its 20-day average — "
            f"{'above' if vol_ratio > 1.0 else 'below'} normal, "
            f"{'confirming' if vol_ratio > 1.0 else 'weak'} conviction "
            f"(score {score:.3f}). [src: 5d/20d average volume]"
        )
        return SignalCandidate(
            agent_name="VolumeConfirmationAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
