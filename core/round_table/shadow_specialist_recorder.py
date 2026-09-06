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

# core/round_table/shadow_specialist_recorder.py
# Shadow-SpecialistAlpha-Vote (dormant, flag SHADOW_SPECIALIST_VOTE_ENABLED) — the #76
# measurement output for the SpecialistAlpha activation runbook (addition 3).
#
# Records what the SpecialistAlpha WOULD vote vs the real Round Table consensus — recorded,
# NOT counted: zero order impact. Lets us decide empirically and risk-free whether the
# specialist signal adds net-of-cost alpha BEFORE it is ever given weight
# (validate-before-activate). Works in a Sim-Day run (offline, as-of via #2651/#2656) and in
# live paper (forward shadow); the record's ``ts`` is the engine clock (sim time under
# SIM_MODE) so an offline analyzer can join each record with the realized forward return.
# Append-only JSONL.
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _specialist_vote_from(
    sentiment_score: Optional[float], recommendation: Optional[str]
) -> str:
    """Map the specialist report to a BUY/SELL/HOLD would-be vote.

    Same rule as ``SpecialistAlphaAgent``: the explicit recommendation wins; when absent, the
    0-100 sentiment score falls back at the >=60 / <=40 thresholds. Pure; never raises.
    """
    rec = (recommendation or "").strip().lower()
    if rec == "buy":
        return "BUY"
    if rec == "sell":
        return "SELL"
    if rec == "hold":
        return "HOLD"
    try:
        if sentiment_score is not None:
            if sentiment_score >= 60:
                return "BUY"
            if sentiment_score <= 40:
                return "SELL"
    except (TypeError, ValueError):
        pass
    return "HOLD"


def _engine_ts() -> str:
    """ISO timestamp from the engine clock — sim time under SIM_MODE (so the record is joinable
    with the sim bars for the forward return), wall-clock otherwise. Fail-safe → wall-clock.
    """
    try:
        from core.sim.clock import engine_now

        return engine_now(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def record_shadow_specialist_vote(
    *,
    symbol: str,
    sentiment_score: Optional[float],
    recommendation: Optional[str],
    escalate: bool,
    consensus_score: float,
    real_action: Optional[str],
    chain_path: str,
) -> None:
    """Append one shadow-vote record to ``chain_path`` (JSONL).

    Fire-and-forget: never raises into the order path. But NOT silent — any I/O failure is
    logged at WARNING (AGENTS.md Rule 5 / §5.6) so CI and local debugging are not blind.
    """
    try:
        vote = _specialist_vote_from(sentiment_score, recommendation)
        record = {
            "ts": _engine_ts(),
            "symbol": symbol,
            "specialist_sentiment_score": sentiment_score,
            "specialist_recommendation": recommendation,
            "specialist_escalate": bool(escalate),
            "specialist_vote": vote,
            "real_consensus_score": consensus_score,
            "real_action": real_action,
            "agreement": (vote == real_action) if real_action is not None else None,
        }
        path = Path(chain_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception as exc:  # never break the order path — but never silent (Rule 5)
        logger.warning(
            "record_shadow_specialist_vote: could not record shadow vote for %s: %s",
            symbol,
            exc,
        )
