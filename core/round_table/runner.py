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
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from core.round_table.agents import ALL_AGENTS, LSTMSignalAgent, RLConfidenceAgent
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    SIGNAL_SELL_THRESHOLD,
    ConsensusEngine,
)
from core.round_table.gatekeeper import ComplianceGatekeeper, GatekeeperDecision
from core.round_table.recent_decisions import record_round_table_decision
from core.round_table.registry import _global_registry
from core.round_table.senate_log import (
    IAuditLogger,
    LocalJSONAuditLogger,
    SenateProtocol,
    SenateSession,
    make_session_id,
    spawn_audit_task,
)

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)

# Layer 1: Per-Agent Timeout (MiFID II Art. 17 — System Resilience)
_AGENT_VOTE_TIMEOUT_SEC = 60.0  # Increased for CPU-bound PyTorch inference in Docker

# ML-Agents whose timeouts must bridge into MLWatchdog escalation chain
_ML_AGENT_NAMES = {"LSTMSignalAgent", "RLConfidenceAgent"}

# GAP9: the ComplianceGatekeeper runs per-symbol (up to ~200/cycle). A missing-context
# WARNING per symbol would flood the log, so throttle it to ~1×/cycle. monotonic()==0.0
# initial → the first call always warns.
_LAST_MISSING_CONTEXT_WARN_TS = 0.0
_MISSING_CONTEXT_WARN_INTERVAL_S = 60.0
# Lock makes the read-modify-write atomic so exactly 1 warning fires per window even
# when multiple ThreadPoolExecutor workers call this concurrently (P0-2, #1159).
_MISSING_CONTEXT_WARN_LOCK = threading.Lock()


def _warn_gatekeeper_missing_context() -> None:
    """Rate-limited WARNING (never DEBUG, §5.6) when the gatekeeper has no portfolio
    context — its concentration/PDT/daily-limit checks are inert that cycle."""
    global _LAST_MISSING_CONTEXT_WARN_TS
    now = time.monotonic()
    with _MISSING_CONTEXT_WARN_LOCK:
        if now - _LAST_MISSING_CONTEXT_WARN_TS < _MISSING_CONTEXT_WARN_INTERVAL_S:
            return
        _LAST_MISSING_CONTEXT_WARN_TS = now
    logger.warning(
        "ComplianceGatekeeper running WITHOUT portfolio context — concentration / PDT / "
        "daily-limit checks are inert this cycle. Set GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED "
        "to feed real context; GATEKEEPER_REQUIRE_CONTEXT to fail closed."
    )


async def _resolve_gatekeeper_decision(
    gatekeeper: "ComplianceGatekeeper",
    symbol: str,
    consensus_score: float,
    portfolio_context: dict,
    *,
    require_context: bool,
) -> "GatekeeperDecision":
    """Run the ComplianceGatekeeper, applying the GAP9 missing-context policy.

    - context present  → normal gatekeeper.check.
    - context missing  → rate-limited WARNING, then:
        * require_context AND a BUY score → fail CLOSED (no signal), short-circuit.
        * otherwise (SELL/HOLD, or fail-open default) → gatekeeper.check with the empty
          context = today's behaviour (gatekeeper approves; SELL/HOLD never blocked).
    """
    if not portfolio_context:
        _warn_gatekeeper_missing_context()
        if require_context and consensus_score > ComplianceGatekeeper.BUY_THRESHOLD:
            return GatekeeperDecision(
                approved=False,
                reason=(
                    "GatekeeperStrict: portfolio context unavailable — BUY blocked "
                    "(GATEKEEPER_REQUIRE_CONTEXT)."
                ),
                symbol=symbol,
            )
    return await gatekeeper.check(symbol, consensus_score, portfolio_context)


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


def get_audit_logger() -> Optional[IAuditLogger]:
    """The configured Round-Table audit logger, or None before ``boot_engine`` has run.

    The HITL gate (PR-0a-ii-4b) resolves the logger through this accessor so its Art-14
    events land on the SAME tamper-evident SHA-256 hash chain as Senate sessions in OSS mode
    — a second ``LocalJSONAuditLogger`` writing the shared daily file would split the chain.
    """
    return _senate


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
        _senate = LocalJSONAuditLogger()

        plugins_dir = os.getenv("ROUND_TABLE_PLUGINS_DIR", "plugins/round_table")
        _global_registry.load_plugins_from_directory(plugins_dir)

        # Plugins ergänzen die Basis-Agenten. Bei Namenskollision gewinnt ALL_AGENTS.
        # Verhindert, dass ein untrusted Plugin einen Basis-Agenten (z.B. RiskAgent)
        # unter gleichem Namen überschreibt und böswilligen Code injiziert.
        _active_agents = list(
            {
                # Plugins haben niedrigere Priorität: zuerst eintragen
                **{
                    a.__class__.__name__: a
                    for a in _global_registry.get_active_agents()
                },
                # Basis-Agenten überschreiben Kollisionen — nie auslassbar
                **{a.__class__.__name__: a for a in ALL_AGENTS},
            }.values()
        )


# Auto-boot is removed to maintain DI isolation for tests and clean boot.
# boot_engine() should be called explicitly by BotEngine or tests.


async def run_round_table(state: "SymbolEvalState") -> "SymbolEvalState":
    """
    Haupt-Orchestrierungsfunktion des Round Table V2.

    Ablauf:
        1. Alle 9 Agents parallel (asyncio.gather) → VoteResult-Liste
        1.5. Signal Integrity Check (ADR-SEC-01) → HIGH_CORRELATION alert
        2. ConsensusEngine aggregiert → gewichteter Score
        3. ComplianceGatekeeper prüft → approved | vetoed
        4. SenateProtocol loggt (fire-and-forget, non-blocking)
        5. Signal aus Konsens ableiten → state["signal"] setzen

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

    # --- Phase 1.5: Signal Integrity Check (ADR-SEC-01 / D6 Compliance Gap) ---
    # Detects suspiciously uniform vote distributions that may indicate correlated
    # data poisoning or feed manipulation (all 9 agents voting identically).
    # Alert-only in Phase A (2-week observation window before promoting to hard gate).
    _integrity_ok, _integrity_reason = _consensus_engine.check_distribution(valid_votes)
    if not _integrity_ok:
        logger.warning(
            "AI_SECURITY[runner]: Signal integrity check FAILED for %s. "
            "Reason: %s. "
            "Alert-only — no hard block (Phase A deployment, ADR-SEC-01). "
            "Escalation to hard-HOLD gate planned after 2-week observation.",
            symbol,
            _integrity_reason,
        )

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
    # Security (I-3 #944): Use isinstance() type-checks instead of agent_name string comparison.
    # A rogue plugin could set __class__.__name__ = "LSTMSignalAgent" to spoof a string check.
    # isinstance() verifies the actual class identity — name spoofing is not possible.
    # LSTMSignalAgent, RLConfidenceAgent imported at top-level (not here) per CODING_POLICY §5.3.

    # Build a name→agent map from _active_agents for O(1) lookup
    _agent_type_map = {agent.__class__.__name__: agent for agent in _active_agents}

    # SEC-01: Log when a vote's agent_name has no matching registered agent instance.
    # A rogue plugin spoofing __class__.__name__ would appear here with None lookup result.
    for v in valid_votes:
        if v.agent_name not in _agent_type_map:
            logger.warning(
                "SECURITY[runner]: VoteResult from unregistered agent_name=%r "
                "(not in _active_agents). Possible __class__.__name__ spoofing. "
                "Vote will be excluded from Strict Local Dependency check.",
                v.agent_name,
            )

    lstm_valid = any(
        isinstance(_agent_type_map.get(v.agent_name), LSTMSignalAgent)
        and v.weight > 0.0
        for v in valid_votes
    )
    rl_valid = any(
        isinstance(_agent_type_map.get(v.agent_name), RLConfidenceAgent)
        and v.weight > 0.0
        for v in valid_votes
    )

    if not lstm_valid or not rl_valid:
        gatekeeper_decision = GatekeeperDecision(
            approved=False,
            reason="Missing core ML votes (LSTM/RL failed or excluded)",
            symbol=symbol,
        )
    else:
        # GAP9: the trading loop injects a real per-cycle portfolio snapshot here when
        # GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED is on. When it's off (default) or the snapshot
        # failed, this is empty and the gatekeeper approves exactly as before — unless the
        # operator set GATEKEEPER_REQUIRE_CONTEXT (strict fail-closed). See _resolve_gatekeeper_decision.
        portfolio_context: dict = state.get("_portfolio_context") or {}  # type: ignore[call-overload]

        from config import get_config

        gatekeeper_decision = await _resolve_gatekeeper_decision(
            _gatekeeper,
            symbol,
            consensus_score,
            portfolio_context,
            require_context=get_config().GATEKEEPER_REQUIRE_CONTEXT,
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

    # --- Phase 4.5: Shadow-TFT-Vote (Fusion, dormant — flag SHADOW_TFT_VOTE_ENABLED) ---
    # Records what a TFT-only vote WOULD say vs the real consensus — NOT counted, never
    # touches the order path. No-op unless the flag is set.
    _maybe_record_shadow_tft_vote(state, symbol, consensus_score, signal)

    # --- Phase 5: Senate Protocol (fire-and-forget) ---
    serialized_votes = []
    for v in valid_votes:
        if v.score > SIGNAL_BUY_THRESHOLD:
            agent_signal = "BUY"
        elif v.score < SIGNAL_SELL_THRESHOLD:
            agent_signal = "SELL"
        else:
            agent_signal = "HOLD"
        serialized_votes.append(
            {
                "name": v.agent_name,
                "agent_name": v.agent_name,
                "score": v.score,
                "weight": v.weight,
                "reasoning": v.reasoning,
                "vetoed": v.vetoed,
                "signal": agent_signal,
            }
        )
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
    # Fire-and-forget (blockiert NICHT den LangGraph-Pfad), aber tracked: starke
    # Referenz gegen GC-Drop + Fehler werden geloggt statt verschluckt (#1253).
    spawn_audit_task(_senate.log_session(session))

    # G1a (#1050): same session into the in-memory display store for the
    # console routes — synchronous dict write, never raises (fail-safe),
    # read-only for the API layer. NOT a compliance record (that's the
    # protocol log above).
    record_round_table_decision(session)

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
        logger.warning("shadow-TFT-vote hook failed for %s: %s", symbol, exc)


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

        # Use imported thresholds (ADR-SEC-01: single source of truth in consensus.py)
        if score > SIGNAL_BUY_THRESHOLD:
            action = "BUY"
        elif score < SIGNAL_SELL_THRESHOLD:
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
    except Exception as exc:
        logger.warning("run_round_table: Signal-Erstellung fehlgeschlagen: %s", exc)
        return None
