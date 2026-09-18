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

# core/round_table/shadow_tft_recorder.py
# Fusion — Shadow-TFT-Vote (dormant, flag SHADOW_TFT_VOTE_ENABLED).
#
# Records what a TFT-only vote WOULD say vs the real Round Table consensus — recorded,
# NOT counted: zero order impact, zero LLM cost. Lets us decide empirically and risk-free
# whether the per-symbol TFT signal adds value BEFORE it is ever given weight
# (validate-before-activate). Append-only JSONL.
#
# TODO (Enterprise): Move to SenateProtocol or Cloud Storage for persistent cloud
# metrics — Cloud Run's local filesystem is ephemeral, so shadow votes are lost on a
# container restart. Acceptable for Phase-1 measure-before-activate data
# (implementation_plan 2026-06-09-tft-state-shadow-vote, Papa-audit P1).

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _tft_vote_from_direction(direction: Optional[str]) -> str:
    """Map the TFT directional forecast to a BUY/SELL/HOLD vote.

    Pure numbers in, no sentiment and no LLM: ``up`` → BUY, ``down`` → SELL, everything
    else (``neutral``, ``unavailable``, None) → HOLD.
    """
    if direction == "up":
        return "BUY"
    if direction == "down":
        return "SELL"
    return "HOLD"


def record_shadow_tft_vote(
    *,
    symbol: str,
    ml: Optional[Dict[str, Any]],
    consensus_score: float,
    real_action: Optional[str],
    chain_path: str,
) -> None:
    """Append one shadow-vote record to ``chain_path`` (JSONL).

    Fire-and-forget from the caller's perspective: this never raises into the order
    path. But it is NOT silent — any I/O failure is logged at WARNING (AGENTS.md Rule 5
    / §5.6) so CI and local debugging are not blind.
    """
    try:
        ml = ml or {}
        tft_direction = ml.get("tft_direction")
        tft_vote = _tft_vote_from_direction(tft_direction)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "tft_direction": tft_direction,
            "tft_confidence": ml.get("tft_confidence"),
            "tft_base_return_pct": ml.get("tft_base_return_pct"),
            "tft_vote": tft_vote,
            "real_consensus_score": consensus_score,
            "real_action": real_action,
            "agreement": (tft_vote == real_action) if real_action is not None else None,
        }
        path = Path(chain_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception as exc:  # never break the order path — but never silent (Rule 5)
        logger.warning(
            "record_shadow_tft_vote: could not record shadow vote for %s: %s",
            symbol,
            exc,
        )
