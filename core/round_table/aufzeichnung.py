# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Aufzeichnung des Round Table: Stimmen → Senate-/``votes_json``-Zeilen, Schatten-Stimmen.

#4280 (H-4g), Teil von ARC-E6 (#3738): ``_AGENT_RECORD_ROLES`` (samt #3084-Block),
``_serialize_votes``, ``_maybe_record_shadow_tft_vote`` und
``_maybe_record_shadow_specialist_vote`` wortgleich aus ``core/round_table/runner.py``
hierher umgezogen (Schnitt-Entscheidung #4186 §2, §5 „H-4g“). Alles hier zeichnet auf, was
nach Phase 4 feststeht, und ändert das Verdikt nicht. Der Kern importiert die drei
Funktionen zurück und ruft sie über seinen Namensraum auf (Weg (a)); ein Patch auf
``core.round_table.runner._maybe_record_shadow_tft_vote`` trifft deshalb weiter. Dieses
Modul importiert den Kern nicht (rückimport-frei, §3).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import config
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    SIGNAL_SELL_THRESHOLD,
    consensus_exclusions,
)

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Entscheidung #4186 §2: dasselbe Logger-Objekt wie der Kern (H-4b Regel 4).
logger = logging.getLogger("core.round_table.runner")


#: #3084: audit-record role of each vote — "directional" votes carry their real
#: consensus weight; the two NON_DIRECTIONAL_AGENTS act via veto / weight-coupling.
_AGENT_RECORD_ROLES = {
    "DrawdownGuardAgent": "veto_guard",
    "RegimeDetectionAgent": "conditioner",
}


def _serialize_votes(valid_votes) -> list:
    """#3084: map live VoteResults into the Senate-/votes_json record rows.

    HONEST WEIGHTS + ROLES in the audit trail: an agent recorded here must
    reflect the SAME exclusion the directional mean applied — ``consensus_exclusions``
    (consensus.py), NOT a static list. That set is:
      • the base conditioners (RegimeDetection via weight-coupling, DrawdownGuard
        via VETO) — never in the mean; and
      • #3094: VIXAwareRiskAgent WHEN ``IMPLIED_VOL_FORECAST_ENABLED`` is on — its
        per-name implied vol is redirected to the size-scaler (runner), so its
        directionless vote leaves the DIRECTION mean and it acts as a SIZER.
    Every excluded agent is recorded with ``weight: 0.0`` (its class-weight NEVER
    enters the directional mean — recording it misled readers repeatedly: #1993,
    AGENT_CATALOG.md:158, owner query 2026-08-28) + its ``role`` (``veto_guard`` /
    ``conditioner`` / ``sizer``). Recording VIXAware as ``directional`` weight 0.45
    while the gate excluded it drove the console Panel-vs-Gate divergence (64 % vs
    68 %, live 2026-09-08). Flag off → VIXAware is a directional voter again,
    byte-identical to pre-#3094. Every vote STAYS in the record (the console keeps
    showing the full board — owner directive); the LIVE VoteResult is not mutated
    (its weight must stay > 0 for the validator + agent-veto path). Directional
    voters record their real weight + role "directional".
    """
    _iv_on = bool(getattr(config.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", False))
    _excluded = consensus_exclusions(_iv_on)
    serialized_votes = []
    for v in valid_votes:
        if v is None:
            continue
        v_score = getattr(v, "score", None)
        if v_score is None:
            agent_signal = "ABSTAIN"
        elif v_score > SIGNAL_BUY_THRESHOLD:
            agent_signal = "BUY"
        elif v_score < SIGNAL_SELL_THRESHOLD:
            agent_signal = "SELL"
        else:
            agent_signal = "HOLD"
        excluded = v.agent_name in _excluded
        # Role reflects the ACTUAL gate treatment: the two base conditioners keep
        # their fixed audit role; any OTHER excluded agent (today VIXAware when
        # IV-forecast on) is a size-input, tagged "sizer"; the rest are directional.
        if v.agent_name in _AGENT_RECORD_ROLES:
            role = _AGENT_RECORD_ROLES[v.agent_name]
        elif excluded:
            role = "sizer"
        else:
            role = "directional"
        serialized_votes.append(
            {
                "name": v.agent_name,
                "agent_name": v.agent_name,
                "score": v_score,
                "weight": 0.0 if excluded else v.weight,
                "role": role,
                "reasoning": v.reasoning,
                "vetoed": getattr(v, "vetoed", False),
                "signal": agent_signal,
            }
        )
    return serialized_votes


def _maybe_record_shadow_tft_vote(
    state: "SymbolEvalState",
    symbol: str,
    consensus_score: float,
    signal: object,
) -> None:
    """Fusion (dormant, flag-gated): record what a TFT-only vote WOULD say vs the real
    consensus — recorded, NOT counted. Never touches the order path; on any failure the
    recorder logs at WARNING (AGENTS.md Rule 5 — never silent). No-op unless
    ``SHADOW_TFT_VOTE_ENABLED`` is set. See implementation_plan
    2026-06-09-tft-state-shadow-vote.
    """
    try:
        from config import get_config

        cfg = get_config()
        if not cfg.SHADOW_TFT_VOTE_ENABLED:
            return
        from core.round_table.shadow_tft_recorder import record_shadow_tft_vote

        record_shadow_tft_vote(
            symbol=symbol,
            ml=state.get("ml"),
            consensus_score=consensus_score,
            real_action=getattr(signal, "action", None),
            chain_path=cfg.SHADOW_TFT_VOTE_CHAIN_PATH,
        )
    except Exception as exc:  # never break the order path — but never silent
        logger.warning(
            "shadow-TFT-vote hook failed for %s: %s", symbol, exc, exc_info=True
        )


def _maybe_record_shadow_specialist_vote(
    symbol: str,
    consensus_score: float,
    real_action: Optional[str],
) -> None:
    """Shadow SpecialistAlpha vote (dormant, flag ``SHADOW_SPECIALIST_VOTE_ENABLED``): records what
    the specialist WOULD vote vs the real consensus — recorded, NOT counted, zero order impact.
    Reads the report from the SAME registry the agent uses (no re-fetch). Never touches the order
    path; WARNING on failure (Rule 5). No-op unless the flag is set and a report exists (so it is
    a no-op today, since the registry is default-OFF)."""
    try:
        from config import get_config

        cfg = get_config()
        if not getattr(cfg, "SHADOW_SPECIALIST_VOTE_ENABLED", False):
            return
        from core.round_table import agents as _agents

        registry = getattr(_agents, "_specialist_registry_instance", None)
        if registry is None:
            return
        report = registry.get_report(symbol)
        if report is None:
            return
        from core.round_table.shadow_specialist_recorder import (
            record_shadow_specialist_vote,
        )

        record_shadow_specialist_vote(
            symbol=symbol,
            sentiment_score=getattr(report, "sentiment_score", None),
            recommendation=getattr(report, "recommendation", None),
            escalate=getattr(report, "escalate", False),
            consensus_score=consensus_score,
            real_action=real_action,
            chain_path=getattr(
                cfg,
                "SHADOW_SPECIALIST_VOTE_CHAIN_PATH",
                "shadow_specialist_votes.jsonl",
            ),
        )
    except Exception as exc:  # never break the order path — but never silent
        logger.warning(
            "shadow-specialist-vote hook failed for %s: %s", symbol, exc, exc_info=True
        )
