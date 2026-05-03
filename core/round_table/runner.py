# core/round_table/runner.py
# Epic 2.5 — Round Table V2: Haupt-Orchestrierungsfunktion
#
# run_round_table() ist der Einstiegspunkt für _run_strategy_node in graph.py.
# Führt alle 9 Voting-Agents parallel aus (asyncio.gather = LangGraph super-step),
# aggregiert via ConsensusEngine, prüft via ComplianceGatekeeper, loggt via SenateProtocol.
#
# Performance-Ziel: P99 ≤ 250ms bei 50 parallelen Symbolen (Bestätigung aus Epic 1.4)
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from core.round_table.agents import ALL_AGENTS
from core.round_table.consensus import ConsensusEngine
from core.round_table.gatekeeper import ComplianceGatekeeper
from core.round_table.senate_log import (
    SenateProtocol,
    DummyAuditLogger,
    SenateSession,
    make_session_id,
    IAuditLogger,
)
from core.round_table.registry import _global_registry
import os

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)

# Layer 1: Per-Agent Timeout (MiFID II Art. 17 — System Resilience)
_AGENT_VOTE_TIMEOUT_SEC = 15.0

# ML-Agents whose timeouts must bridge into MLWatchdog escalation chain
_ML_AGENT_NAMES = {"LSTMSignalAgent", "RLConfidenceAgent"}

# MLWatchdog bridge — module-level import to avoid import-in-loop overhead
try:
    from core.ml_watchdog import ml_watchdog as _ml_watchdog
except ImportError:
    _ml_watchdog = None

# Singletons initialized by boot_engine()
_consensus_engine: Optional[ConsensusEngine] = None
_gatekeeper: Optional[ComplianceGatekeeper] = None
_senate: Optional[IAuditLogger] = None
_active_agents: list = ALL_AGENTS


def boot_engine(license_key: Optional[str] = None) -> None:
    """
    Dependency Injection Factory für den Round Table.
    Konfiguriert die Engines und Agents abhängig von der Enterprise Lizenz.
    """
    global _consensus_engine, _gatekeeper, _senate, _active_agents

    _consensus_engine = ConsensusEngine()
    _gatekeeper = ComplianceGatekeeper()

    if license_key:
        logger.info("Enterprise License detected. Booting Premium Round Table Engine.")
        _senate = SenateProtocol()
        _active_agents = ALL_AGENTS
    else:
        logger.info("No Enterprise License detected. Booting OSS Community Engine.")
        _senate = DummyAuditLogger()

        plugins_dir = os.getenv("ROUND_TABLE_PLUGINS_DIR", "plugins/round_table")
        _global_registry.load_plugins_from_directory(plugins_dir)
        _active_agents = _global_registry.get_active_agents()

        # Fallback to ALL_AGENTS if no plugins found to prevent crash during transition
        if not _active_agents:
            _active_agents = ALL_AGENTS


# Auto-boot is removed to maintain DI isolation for tests and clean boot.
# boot_engine() should be called explicitly by BotEngine or tests.


async def run_round_table(state: "SymbolEvalState") -> "SymbolEvalState":
    """
    Haupt-Orchestrierungsfunktion des Round Table V2.

    Ablauf:
        1. Alle 9 Agents parallel (asyncio.gather) → VoteResult-Liste
        2. ConsensusEngine aggregiert → gewichteter Score
        3. ComplianceGatekeeper prüft → approved | vetoed
        4. SenateProtocol loggt (fire-and-forget, non-blocking)
        5. Signal aus Konsens ableiten → state[\"signal\"] setzen

    Args:
        state: SymbolEvalState (bereits validiert durch _fetch_context_node)

    Returns:
        Erweiterter SymbolEvalState mit signal, round_table_scores, consensus_ranking
    """
    if _consensus_engine is None or _senate is None:
        logger.error("run_round_table: boot_engine() was never called. Cannot execute.")
        return {
            **state,
            "error": "Round Table not initialized. Call boot_engine() first.",
        }

    if state.get("error"):
        return state

    symbol = state["symbol"]
    session_id = make_session_id()

    # --- Phase 1: Parallel Voting (LangGraph super-step via asyncio.gather) ---
    # Layer 1: Each agent.vote() wrapped in asyncio.wait_for (MiFID Art. 17)
    try:
        vote_results = await asyncio.gather(
            *[
                asyncio.wait_for(agent.vote(state), timeout=_AGENT_VOTE_TIMEOUT_SEC)
                for agent in _active_agents
            ],
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error("run_round_table: gather-Fehler für %s: %s", symbol, exc)
        return {**state, "error": str(exc)}

    # Exception handling from gather (isolating agent failures)
    valid_votes = []
    active_agent_count = len(_active_agents) if _active_agents else len(ALL_AGENTS)
    for i, result in enumerate(vote_results):
        _agent_name = _active_agents[i].__class__.__name__
        if isinstance(result, asyncio.TimeoutError):
            logger.warning(
                "MIFID_AUDIT[%s] agent=%s TIMEOUT after %.0fs — vote excluded",
                symbol,
                _agent_name,
                _AGENT_VOTE_TIMEOUT_SEC,
            )
            # Bridge: ML agent timeout → MLWatchdog escalation (60s→Slack, 300s→Kill)
            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:
                _ml_watchdog.record_error(_agent_name, result)
        elif isinstance(result, Exception):
            logger.warning(
                "run_round_table: Agent %s warf Exception für %s: %s",
                _agent_name,
                symbol,
                result,
            )
            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:
                _ml_watchdog.record_error(_agent_name, result)
        else:
            valid_votes.append(result)
            # ML agent success → reset escalation chain
            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:
                _ml_watchdog.record_success(_agent_name)

    if not valid_votes:
        logger.error("run_round_table: Alle Agents fehlgeschlagen für %s", symbol)
        return {**state, "error": "Alle Voting-Agents fehlgeschlagen", "signal": None}

    # --- Phase 2: Konsens-Aggregation + Pydantic V2 Validierung ---
    consensus_score = _consensus_engine.aggregate(valid_votes)

    # MiFID II / Observability: Jeder Agent-Vote einzeln loggen (unabhängig von DB)
    for vote in valid_votes:
        logger.info(
            "VOTE[%s] agent=%s score=%.3f weight=%.2f reasoning=%s",
            symbol,
            vote.agent_name,
            vote.score,
            vote.weight,
            vote.reasoning[:120] if vote.reasoning else "",
        )

    # --- Phase 3: Compliance Gate & Strict Local Dependency ---
    lstm_valid = any(
        v.agent_name == "LSTMSignalAgent" and v.weight > 0.0 for v in valid_votes
    )
    rl_valid = any(
        v.agent_name == "RLConfidenceAgent" and v.weight > 0.0 for v in valid_votes
    )

    if not lstm_valid or not rl_valid:
        from core.round_table.gatekeeper import GatekeeperDecision

        gatekeeper_decision = GatekeeperDecision(
            approved=False,
            reason="Missing core ML votes (LSTM/RL failed or excluded)",
            symbol=symbol,
        )
    else:
        # portfolio_context: In der Produktion kommt das aus dem LangGraph RunnableConfig
        # Im MVP: leerer Context (alle Checks bestehen) — wird in Epic 2.5/I-2 erweitert
        portfolio_context: dict = state.get("_portfolio_context") or {}  # type: ignore[call-overload]

        gatekeeper_decision = await _gatekeeper.check(
            symbol, consensus_score, portfolio_context
        )

    # Veto'd Votes markieren (für Senate Protocol / Audit)
    if not gatekeeper_decision.approved:
        for vote in valid_votes:
            vote.vetoed = True
        logger.info(
            "ComplianceGatekeeper: VETO für %s — %s",
            symbol,
            gatekeeper_decision.reason,
        )

    # --- Phase 4: Signal aus Konsens ableiten ---
    signal = None
    if gatekeeper_decision.approved:
        signal = _score_to_signal(state, consensus_score, valid_votes)

    # --- Phase 5: Senate Protocol (fire-and-forget) ---
    serialized_votes = [
        {
            "agent_name": v.agent_name,
            "score": v.score,
            "weight": v.weight,
            "reasoning": v.reasoning,
            "vetoed": v.vetoed,
        }
        for v in valid_votes
    ]
    session = SenateSession(
        session_id=session_id,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc).isoformat(),
        votes=serialized_votes,
        consensus_score=consensus_score,
        gatekeeper_approved=gatekeeper_decision.approved,
        gatekeeper_reason=gatekeeper_decision.reason,
        signal_action=getattr(signal, "action", None),
    )
    # asyncio.ensure_future: fire-and-forget (blockiert NICHT den LangGraph-Pfad)
    asyncio.ensure_future(_senate.log_session(session))

    logger.warning(
        "RoundTable[%s]: score=%.3f approved=%s signal=%s votes=%d/%d",
        symbol,
        consensus_score,
        gatekeeper_decision.approved,
        getattr(signal, "action", "NONE"),
        len(valid_votes),
        active_agent_count,
    )

    return {
        **state,
        "signal": signal,
        "round_table_scores": serialized_votes,
        "consensus_ranking": consensus_score,
        "session_id": session_id,
    }


def _score_to_signal(
    state: "SymbolEvalState",
    score: float,
    votes: list,
) -> Optional[object]:
    """
    Konvertiert den Konsens-Score in ein Signal-Event-ähnliches Objekt.
    Verwendet dasselbe Interface wie SignalEvent aus core/events.py.

    Thresholds:
        score > 0.65 → BUY
        score < 0.35 → SELL
        else         → HOLD
    """
    try:
        from core.events import SignalEvent

        if score > 0.65:
            action = "BUY"
        elif score < 0.35:
            action = "SELL"
        else:
            action = "HOLD"

        # Reasoning aus Top-3 Agents (nach Gewicht)
        top_votes = sorted(votes, key=lambda v: v.weight, reverse=True)[:3]
        reasoning = " | ".join(v.reasoning for v in top_votes if not v.vetoed)

        curr_price = state.get("ohlc", {}).get("close", 0.0)
        from core.cloud_logger import DecisionContext

        ctx = DecisionContext(
            symbol=state["symbol"],
            action=action,
            conviction_score=score,
            current_price=curr_price,
            reasoning_summary=f"RoundTableV2 consensus={score:.3f}: {reasoning[:200]}",
        )
        return SignalEvent(
            symbol=state["symbol"],
            action=action,
            decision_context=ctx,
        )
    except (ImportError, Exception) as exc:
        logger.warning("run_round_table: Signal-Erstellung fehlgeschlagen: %s", exc)
        return None
