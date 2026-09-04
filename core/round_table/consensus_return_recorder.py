"""#3197 measurement instrument — consensus/vote → forward-return harness (DARK).  # noqa: E501

Observation-only, env-gated, fail-safe. When ``CONSENSUS_RETURN_HARNESS_PATH`` is set, every  # noqa: E501
round-table evaluation appends one JSONL row: the sim/eval date, the symbol, the blend  # noqa: E501
consensus, and each agent's raw score+weight. An offline analysis then joins these with corpus  # noqa: E501
forward returns to test — on the CURRENT round-table composition — whether the consensus LEVEL  # noqa: E501
(sweet-spot / shrinkage) or vote DIVERSITY predicts forward return. It NEVER touches a trading  # noqa: E501
decision and NEVER raises into the evaluation path (a recorder error must not affect a cycle).  # noqa: E501
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Iterable

from config import get_config

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState
    from core.round_table.base_agent import VoteResult

logger = logging.getLogger(__name__)

_ENV = "CONSENSUS_RETURN_HARNESS_PATH"


def _iso_date(state: "SymbolEvalState") -> str:
    """The sim/eval date (YYYY-MM-DD) from the graph state's ISO current_time, else ''."""  # noqa: E501
    try:
        t = ""
        if isinstance(state, dict):
            t = state.get("current_time", "") or ""
        return str(t)[:10]
    except Exception:  # noqa: BLE001 — never raise
        return ""


def record_consensus_observation(
    state: "SymbolEvalState",
    symbol: str,
    consensus: float,
    votes: Iterable["VoteResult"],
) -> None:
    """Append one observation to ``CONSENSUS_RETURN_HARNESS_PATH`` (no-op if unset).  # noqa: E501

    ``votes`` are the live VoteResults (``.agent_name``, ``.score``, ``.weight``). Fail-safe:  # noqa: E501
    any error is swallowed at WARNING — the harness must never perturb the trading path.  # noqa: E501
    """
    path = getattr(get_config(), "CONSENSUS_RETURN_HARNESS_PATH", "")
    if not path:
        return
    try:
        row = {
            "date": _iso_date(state),
            "symbol": str(symbol),
            "consensus": float(consensus),
            "votes": {
                str(getattr(v, "agent_name", "?")): {
                    "score": float(getattr(v, "score", 0.0)),
                    "weight": float(getattr(v, "weight", 0.0)),
                }
                for v in (votes or [])
            },
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as exc:  # noqa: BLE001, E501
        logger.warning(
            "[consensus-return-harness] record failed for %s: %s", symbol, exc
        )
