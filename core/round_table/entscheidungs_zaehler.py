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

"""ADR-OBS-01: Entscheidungs-Zähler des Round-Table-Dirigenten (reine Beobachtung).

#4276 (H-4c), Teil von ARC-E6 (#3738): wortgleich aus ``core/round_table/runner.py``
hierher umgezogen (Schnitt-Entscheidung #4186 §2, §5 „H-4c“). Der Kern führt
``_bump_run``, ``_bump_agent_failure``, ``get_decision_counters`` und
``reset_decision_counters`` per Rückimport wieder aus (Weg (a), §3): ``run_round_table``
liest die Namen aus dem Kern, Patches auf ``core.round_table.runner`` treffen weiter.
Dieses Modul importiert den Kern nicht (rückimport-frei, §3).
"""

from __future__ import annotations

import logging

from core.composition.root import CompositionRoot

# Entscheidung #4186 §2: dasselbe Logger-Objekt wie der Kern (H-4b Regel 4).
logger = logging.getLogger("core.round_table.runner")

# --- ADR-OBS-01 / PR C: decision-health instrumentation (PURE OBSERVATION) -----
# Fail-safe module-level counters over the VC-2 decision path (BEFORE order execution):
#   * round_tables_run   — one bump per run_round_table execution
#   * last_consensus_ts  — wall-clock of the most recent run (age derived at the surface)
#   * agent_vote_failures — {agent_class_name: count} bounded at _MAX_AGENT_FAILURE_KEYS
# Every helper swallows EVERY error so a counter failure can NEVER alter a consensus
# verdict, an agent vote, or the round-table flow — the decision logic stays byte-identical.
# MACHINE-only: aggregate counts + agent CLASS names + a timestamp; never symbols/orders.
_DECISION_COUNTERS: "dict[str, object]" = {
    "round_tables_run": 0,
    "last_consensus_ts": None,
}
_AGENT_VOTE_FAILURES: "dict[str, int]" = {}
_MAX_AGENT_FAILURE_KEYS = 32  # bound the agent-name cardinality (rogue-plugin safety)


def _bump_run() -> None:
    """Fail-safe round-table run counter + last-consensus timestamp — swallows EVERY error."""
    try:
        _DECISION_COUNTERS["round_tables_run"] = (
            int(_DECISION_COUNTERS.get("round_tables_run", 0) or 0) + 1
        )
        _DECISION_COUNTERS["last_consensus_ts"] = (
            CompositionRoot.get_instance().clock_port.time()
        )
    except (
        Exception
    ):  # noqa: BLE001 — a broken counter must never break the round table
        pass


def _bump_agent_failure(agent_name: str) -> None:
    """Fail-safe per-agent vote-failure counter (bounded) — swallows EVERY error.

    ``agent_name`` is the code CLASS identifier, never order content. New names are only
    admitted while under the cap so a rogue plugin cannot blow up the map cardinality.
    """
    try:
        if agent_name in _AGENT_VOTE_FAILURES or (
            len(_AGENT_VOTE_FAILURES) < _MAX_AGENT_FAILURE_KEYS
        ):
            _AGENT_VOTE_FAILURES[agent_name] = (
                _AGENT_VOTE_FAILURES.get(agent_name, 0) + 1
            )
    except Exception:  # noqa: BLE001 — observation must never break the round table
        pass


def get_decision_counters() -> dict:
    """Read-only snapshot merging the runner + consensus decision counters.

    Reused by ``_collect_decision`` in api_routes. Fail-safe: a broken consensus accessor
    degrades its slice to empty, never raising out of the diagnostics surface.
    """
    try:
        from core.round_table.consensus import (
            get_decision_counters as _get_consensus_counters,
        )

        outcomes = _get_consensus_counters().get("consensus_outcomes", {})
    except Exception:  # noqa: BLE001
        outcomes = {}
    return {
        "consensus_outcomes": dict(outcomes),
        "round_tables_run": int(_DECISION_COUNTERS.get("round_tables_run", 0) or 0),
        "last_consensus_ts": _DECISION_COUNTERS.get("last_consensus_ts"),
        "agent_vote_failures": dict(_AGENT_VOTE_FAILURES),
    }


def reset_decision_counters() -> None:
    """Test/daily-reset helper — zeroes the runner + consensus decision counters."""
    _DECISION_COUNTERS.update({"round_tables_run": 0, "last_consensus_ts": None})
    _AGENT_VOTE_FAILURES.clear()
    try:
        from core.round_table.consensus import (
            reset_decision_counters as _reset_consensus_counters,
        )

        _reset_consensus_counters()
    except Exception:  # noqa: BLE001
        pass
