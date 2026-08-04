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

import config

try:  # module attribute so tests can patch it; matches agents.py's lazy import
    from core.agent_registry import get_global_registry
except ImportError:  # pragma: no cover — registry always present in the live engine
    get_global_registry = None  # type: ignore[assignment]

from core.round_table.agents import ALL_AGENTS, LSTMSignalAgent, RLConfidenceAgent
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    SIGNAL_SELL_THRESHOLD,
    ConsensusEngine,
)
from core.round_table.consensus import (
    record_consensus_outcome as _record_consensus_outcome,
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
        _DECISION_COUNTERS["last_consensus_ts"] = time.time()
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


# RTR-6 (#2410): once-per-session latch for the LSTM-only Strict-ML-Gate WARNING.
# The gate runs per-symbol (up to ~200/cycle) — an RL-abstain WARNING per symbol would
# flood the log, and the waived-RL posture is a per-process configuration fact, so the
# §5.6 WARNING fires exactly once per engine session. Lock: same rationale as
# _MISSING_CONTEXT_WARN_LOCK — atomic check-and-set under concurrent workers.
_STRICT_ML_RL_WAIVED_WARNED = False
_STRICT_ML_RL_WAIVED_LOCK = threading.Lock()


def _warn_strict_ml_rl_not_required() -> None:
    """Once-per-session WARNING (§5.6, never DEBUG) when the Strict-ML-Gate passes on
    the LSTM vote alone because GATEKEEPER_STRICT_ML_REQUIRES_RL=False waived RL."""
    global _STRICT_ML_RL_WAIVED_WARNED
    with _STRICT_ML_RL_WAIVED_LOCK:
        if _STRICT_ML_RL_WAIVED_WARNED:
            return
        _STRICT_ML_RL_WAIVED_WARNED = True
    logger.warning(
        "StrictML-Gate: RL vote abstained/absent but RL not required "
        "(GATEKEEPER_STRICT_ML_REQUIRES_RL=False) — gate passes on the valid LSTM "
        "vote alone. Logged once per session."
    )


def _strict_ml_gate_blocks(lstm_valid: bool, rl_valid: bool) -> bool:
    """RTR-6 (#2410): does the Strict-ML-Gate veto this cycle?

    Default (``GATEKEEPER_STRICT_ML_REQUIRES_RL=True``) requires BOTH core ML votes
    (LSTM and RL, each ``weight > 0``) — byte-identical to the pre-#2410 gate. With
    the flag False a valid LSTM vote alone passes; the RL abstain is WARNING-logged
    once per session (§5.6). A cycle where the LSTM is invalid ALWAYS blocks — the
    "no ML voted yet approved" protection never lifts (issue #2410 scenario 3).
    """
    if not lstm_valid:
        return True
    if rl_valid:
        return False
    from config import get_config

    # Direct-chain read (neighbour-flag idiom, e.g. GATEKEEPER_PDT_CROSSDAY_EXEMPTION in
    # portfolio_context.py) — scan-compatible, so the auto-scan parity guard
    # (test_oss_get_config_mirrors_all_engine_reads) covers this key. The safe default
    # (True = today's gate) lives in the config definition of BOTH editions; existence
    # is pinned by _REQUIRED_GET_CONFIG_FLAGS + test_strict_ml_flag_covered_by_auto_scan.
    if get_config().GATEKEEPER_STRICT_ML_REQUIRES_RL:
        return True
    _warn_strict_ml_rl_not_required()
    return False


def _apply_agent_veto(gatekeeper_decision, valid_votes, consensus_score, symbol):
    """Apply a vote-level veto (e.g. DrawdownGuardAgent) over the gatekeeper decision.

    Direction-aware (#2031): a veto must NOT block a risk-reducing SELL — freezing the
    exit of a drawdown name is the exact harm the guard is meant to prevent. The veto
    therefore only overrides the decision when the consensus is NOT a SELL (score at or
    above SIGNAL_SELL_THRESHOLD, i.e. HOLD/BUY). A SELL under a bearish veto passes
    through unchanged; the execution-layer min-hold (can_sell_position) remains the
    anti-churn guard.
    """
    agent_veto = next((v for v in valid_votes if getattr(v, "vetoed", False)), None)
    if agent_veto is not None and consensus_score >= SIGNAL_SELL_THRESHOLD:
        return GatekeeperDecision(
            approved=False,
            reason=f"Agent VETO ({agent_veto.agent_name}): {agent_veto.reasoning}",
            symbol=symbol,
        )
    return gatekeeper_decision


def _apply_meta_label(gatekeeper_decision, state, valid_votes, consensus_score, symbol):
    """#1955 Meta-Label precision gate (López de Prado, AFML Ch. 3.6) over REAL BUYs.

    The primary model (consensus > BUY_THRESHOLD) fixes the DIRECTION; the
    secondary meta-label model decides whether THIS BUY has net-of-cost edge.
    Dropping a BUY reuses the EXISTING approved=False plumbing (votes marked
    vetoed, reason in gatekeeper_reason → Senate trail) so the block reason is
    audited — exactly the forensic gap ("Block-Grund nirgends erfasst") this
    gate heals. No-op when:
      * META_LABEL_FILTER_ENABLED is off (default — byte-identical, BORA §9),
      * the decision is already blocked (gatekeeper / agent veto), or
      * the consensus is not a BUY (SELL/HOLD carve-out — the filter may only
        ever DROP a BUY, never touch an exit; mirrors the #2031 symmetry).
    When META_LABEL_REQUIRE_MODEL is True, any exception or missing model fails
    CLOSED (approved=False). Otherwise, in unconfigured observation mode, the BUY
    passes through.
    """
    try:
        from config import get_config

        cfg = get_config()
        if not getattr(cfg, "META_LABEL_FILTER_ENABLED", False):
            return gatekeeper_decision
        if not gatekeeper_decision.approved:
            return gatekeeper_decision
        if consensus_score <= ComplianceGatekeeper.BUY_THRESHOLD:
            return gatekeeper_decision

        import core.round_table.meta_label as _meta_label

        trade, _proba, reason = _meta_label.get_meta_label_filter().should_trade(
            state, valid_votes, consensus_score
        )
        if not trade:
            return GatekeeperDecision(
                approved=False,
                reason=f"MetaLabel: {reason}",
                symbol=symbol,
            )
        return gatekeeper_decision
    except Exception as exc:  # noqa: BLE001 — handle gate evaluation error
        from config import get_config

        # #2418 review P2: exc_info=True for the stack trace; WARNING kept (§5.6).
        logger.warning("MetaLabel gate error for %s: %s", symbol, exc, exc_info=True)
        if getattr(get_config(), "META_LABEL_REQUIRE_MODEL", False):
            return GatekeeperDecision(
                approved=False,
                reason=f"MetaLabel: error ({exc}) — fail-closed (META_LABEL_REQUIRE_MODEL)",
                symbol=symbol,
            )
        return gatekeeper_decision


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

    # GTM-1 (#1800) Brick-3: signed Tier-Entitlement agent gate — desktop-only.
    # resolve_entitlement() returns the FULL bundle for non-LOCAL deployments, so this
    # filter is a no-op for Cloud/Dev/CI (byte-identical behaviour). On the LOCAL desktop
    # it narrows the active set to the tier's licensed agents, matched by CLASS NAME
    # (never a slice) so the DrawdownGuard invariant survives regardless of order.
    # Iron Dome / risk / kill-switch are NOT agents in this list and stay ungated.
    if os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL":
        from core.entitlement import resolve_entitlement

        ent = resolve_entitlement()
        _active_agents = [
            a for a in _active_agents if a.__class__.__name__ in ent.agent_names
        ]
        logger.info(
            "[Entitlement] LOCAL tier=%s → %d/%d Round-Table agents active.",
            ent.tier.value,
            len(_active_agents),
            len(ALL_AGENTS),
        )


# Auto-boot is removed to maintain DI isolation for tests and clean boot.
# boot_engine() should be called explicitly by BotEngine or tests.


async def _maybe_share_active_signal(state: "SymbolEvalState") -> "SymbolEvalState":
    """Evaluate the active strategy ONCE and share it with both ML voices.

    LSTMSignalAgent and RLConfidenceAgent each call ``active.evaluate_for_symbol`` (a
    heavy get_bars + torch inference on ONE model). Inside the vote gather they ran
    concurrently with no shared cache, so the two calls returned inconsistent results —
    the LSTM voice's copy came back ``None`` ("AI price model returned no signal this
    cycle") while RL's succeeded, and the strict-ML gate then blocked every decision.
    Evaluating once, sequentially, BEFORE the gather and stashing the result in
    ``state["_shared_active_signal"]`` removes the race and halves the ML load.

    Shared ONLY in the default ``get_active()`` routing. When
    ``ROUND_TABLE_DISTINCT_ML_SOURCES`` splits the voices onto distinct sources, or when
    the active strategy can't be resolved (no registry / no active / eval error), the key
    stays ABSENT so each voice keeps its own path — preserving the DependencyLost /
    kill-switch contract on a missing registry. A resolvable active that genuinely
    returns ``None`` sets the key to ``None`` → both voices abstain CONSISTENTLY.
    """
    try:
        if getattr(config.get_config(), "ROUND_TABLE_DISTINCT_ML_SOURCES", False):
            return state
        registry = get_global_registry() if callable(get_global_registry) else None
        if registry is None:
            return state
        active = registry.get_active()
        if active is None or not hasattr(active, "evaluate_for_symbol"):
            return state
        time_str = state.get("current_time", "")
        try:
            current_time = datetime.fromisoformat(time_str)
        except (ValueError, TypeError):
            current_time = datetime.now(timezone.utc)
        # Art. 14 EU AI Act / #1876: evaluate-only — no orders in the vote phase.
        signal = await active.evaluate_for_symbol(
            state["symbol"], state["ohlc"], {}, current_time
        )
        return {**state, "_shared_active_signal": signal}
    except Exception as exc:  # noqa: BLE001 — sharing is an optimisation, never fatal
        logger.warning(
            "run_round_table: shared active-signal eval failed for %s (%s) — the ML "
            "voices will each resolve their own.",
            state.get("symbol"),
            exc,
        )
        return state


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

    # PR C (PURE OBSERVATION): count this round-table execution + stamp the last-consensus
    # time. Double-guarded (call site + inside _bump_run) so a broken counter can NEVER
    # perturb the decision flow below.
    try:
        _bump_run()
    except Exception:  # noqa: BLE001 — observation must never alter the round table
        pass

    symbol = state["symbol"]
    session_id = make_session_id()

    # INC (ML-gate outage): de-duplicate the heavy per-symbol model call — evaluate the
    # active strategy ONCE and share it so both ML voices read one consistent signal,
    # instead of each firing its own concurrent evaluate_for_symbol inside the gather.
    state = await _maybe_share_active_signal(state)

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
            # PR C (PURE OBSERVATION): a timed-out vote is a vote failure. Fail-safe bump
            # keyed on the agent CLASS name (never order content); wrapped so it can never
            # perturb the existing MLWatchdog escalation below.
            try:
                _bump_agent_failure(_agent_name)
            except Exception:  # noqa: BLE001 — observation never alters the flow
                pass
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
            # PR C (PURE OBSERVATION): count the raised agent vote (fail-safe).
            try:
                _bump_agent_failure(_agent_name)
            except Exception:  # noqa: BLE001 — observation never alters the flow
                pass
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

    # RTR-6 (#2410): flag-gated Strict-ML-Gate — default (GATEKEEPER_STRICT_ML_REQUIRES_RL
    # =True) is byte-identical to the pre-#2410 "both ML votes required" gate.
    if _strict_ml_gate_blocks(lstm_valid, rl_valid):
        gatekeeper_decision = GatekeeperDecision(
            approved=False,
            reason="Missing core ML votes (LSTM/RL failed or excluded)",
            symbol=symbol,
        )
    else:
        # GAP9: the trading loop injects a real per-cycle portfolio snapshot here when
        # GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED is on (default since #1962). When it is OFF or
        # the snapshot failed, this is empty and the gatekeeper approves exactly as before —
        # unless the operator set GATEKEEPER_REQUIRE_CONTEXT (strict fail-closed). See _resolve_gatekeeper_decision.
        portfolio_context: dict = state.get("_portfolio_context") or {}  # type: ignore[call-overload]

        from config import get_config

        gatekeeper_decision = await _resolve_gatekeeper_decision(
            _gatekeeper,
            symbol,
            consensus_score,
            portfolio_context,
            require_context=get_config().GATEKEEPER_REQUIRE_CONTEXT,
        )

        if not rl_valid:
            # RTR-6 (#2410): reachable only because the flag waived the RL requirement
            # (an invalid LSTM never passes the gate above). Name the mode explicitly
            # in the audit reasoning (MiFID II trail): this decision cleared the
            # Strict-ML-Gate on the LSTM vote alone.
            gatekeeper_decision = GatekeeperDecision(
                approved=gatekeeper_decision.approved,
                reason=(
                    f"{gatekeeper_decision.reason} | StrictML: RL abstained — "
                    "RL not required (flag)"
                ),
                symbol=gatekeeper_decision.symbol,
            )

    # Agent-Veto (z.B. DrawdownGuardAgent) — richtungsbewusst (#2031): ein Veto blockt
    # KEINE risikoreduzierende SELL, sonst wird der Verlierer eingefroren (Exit gesperrt).
    gatekeeper_decision = _apply_agent_veto(
        gatekeeper_decision, valid_votes, consensus_score, symbol
    )

    # #1955 Meta-Label-Gate (dormant, META_LABEL_FILTER_ENABLED default OFF):
    # Precision-Gate NUR über echte BUYs (score > BUY_THRESHOLD) — downgraded
    # einen approved BUY ohne net-of-cost-Edge zu einem auditierten no_trade
    # über den BESTEHENDEN approved=False-Pfad (Votes vetoed + Grund im
    # Senate-Trail). SELL/HOLD nie berührt; Flag off ⇒ No-op (byte-identisch).
    gatekeeper_decision = _apply_meta_label(
        gatekeeper_decision, state, valid_votes, consensus_score, symbol
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

    # PR C (PURE OBSERVATION): count the VERDICT distribution ({buy, sell, no_trade}).
    # Classified from consensus_score + gatekeeper approval — the SAME inputs that produced
    # ``signal`` above — so the count mirrors the real decision. record_consensus_outcome is
    # itself fail-safe; the extra call-site guard is defense-in-depth so a broken counter can
    # never touch the order path derived from ``signal``.
    try:
        _record_consensus_outcome(consensus_score, gatekeeper_decision.approved)
    except Exception:  # noqa: BLE001 — observation must never alter the verdict
        pass

    # --- Phase 4.5: Shadow-TFT-Vote (Fusion, dormant — flag SHADOW_TFT_VOTE_ENABLED) ---
    # Records what a TFT-only vote WOULD say vs the real consensus — NOT counted, never
    # touches the order path. No-op unless the flag is set.
    _maybe_record_shadow_tft_vote(state, symbol, consensus_score, signal)
    _maybe_record_shadow_specialist_vote(
        symbol, consensus_score, getattr(signal, "action", None)
    )

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

    # #2113 Decision-Outcome-Capture (flag-gated, default OFF): attach the
    # already-serialized round-table detail + per-cycle eval inputs to the
    # DecisionContext so the durable decision_outcomes row can persist
    # votes_json/session_id per decision_id (plan §5.3 — R-A umgangen, nicht
    # umgebaut: the SenateProtocol/LocalJSONAuditLogger split stays untouched).
    # PURE OBSERVATION: fail-safe inside, never alters the verdict; flag OFF ⇒
    # the context is not touched (decisions payload byte-identical).
    try:
        from core.decision_capture.capture import attach_round_table_capture_fields

        attach_round_table_capture_fields(
            getattr(signal, "decision_context", None) if signal else None,
            session_id=session_id,
            votes=serialized_votes,
            consensus_score=consensus_score,
            gatekeeper_approved=gatekeeper_decision.approved,
            gatekeeper_reason=gatekeeper_decision.reason,
            ohlc=state.get("ohlc"),
        )
    except Exception as exc:  # noqa: BLE001 — observation must never break the path
        logger.warning("decision-capture attach hook failed for %s: %s", symbol, exc)

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
        logger.warning("shadow-specialist-vote hook failed for %s: %s", symbol, exc)


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

        # #1951 / plan #2205 (Hybrid B+C) — DrawdownGuard as a CONDITIONER on BUYs:
        # the DrawdownGuard's severity DAMPENS the conviction (→ position size), and
        # the DAMPED conviction gates the action: if it falls below the buy threshold
        # the BUY flips to HOLD (audited). Live forensics (#1951, 13.–22.07.2026)
        # proved the previous raw-score-only direction was cosmetic — 708 BUYs at
        # ~0.1x damped conviction still executed on risk_manager's 2% MIN_POSITION
        # floor. SELL/HOLD are never touched (risk-reducing SELLs must not freeze).
        # Default OFF ⇒ conviction == score (byte-identical old hard-veto posture).
        conviction = score
        damp_note = ""
        if action == "BUY":
            from config import get_config

            cfg = get_config()
            if getattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False):
                dg = next(
                    (
                        v
                        for v in votes
                        if getattr(v, "agent_name", "") == "DrawdownGuardAgent"
                    ),
                    None,
                )
                if dg is None:
                    # Archon R3: fail-OPEN (no damping — damping only ever REDUCES size,
                    # so its absence never over-sizes) + WARNING, never DEBUG (§5.6).
                    logger.warning(
                        "DrawdownGuard conditioner ON but no DrawdownGuardAgent vote for "
                        "%s — BUY conviction NOT dampened (fail-open).",
                        state.get("symbol"),
                    )
                else:
                    try:
                        damp_max = float(
                            getattr(cfg, "DRAWDOWN_CONVICTION_DAMP_MAX", 0.8)
                        )
                    except (TypeError, ValueError):
                        damp_max = 0.8
                    severity = max(0.0, min(1.0, 1.0 - float(dg.score)))
                    conviction = score * (1.0 - damp_max * severity)
                    # Archon R2 (MiFID pre-trade transparency): preserve BOTH the raw
                    # consensus AND the damped conviction in the audit trail — the raw
                    # consensus is NEVER overwritten by the damped value.
                    dd_pct = (
                        severity / 5.0 * 100.0
                    )  # approx; DG score clamps at ~20% DD
                    if conviction < SIGNAL_BUY_THRESHOLD:
                        # #1951 conviction gate — the damped conviction is below the
                        # buy threshold: no trade. The raw consensus stays visible in
                        # the audit trail (§5.4 MiFID pre-trade transparency); only
                        # the action is downgraded, never the recorded scores.
                        action = "HOLD"
                        damp_note = (
                            f" (damped to {conviction:.3f} → HOLD: "
                            f"{state.get('symbol')} ~{dd_pct:.0f}% drawdown "
                            f"below buy gate)"
                        )
                    else:
                        damp_note = (
                            f" (damped to {conviction:.3f} due to "
                            f"{state.get('symbol')} ~{dd_pct:.0f}% drawdown)"
                        )

        curr_price = state.get("ohlc", {}).get("close", 0.0)
        from core.cloud_logger import DecisionContext

        # #2389: real TA snapshot for the MiFID II Art. 25 decision record. The
        # flag-gated _compute_features_node (graph.py) puts the last-row TA SCALARS
        # into state["features"]; missing/partial features (flag off, no history,
        # node fail-open) keep today's DecisionContext defaults — field-by-field.
        feats = state.get("features") or {}

        def _feat(key: str, default: float) -> float:
            value = feats.get(key, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        # #1953 (RC-2): plumb the HAR-RV forward-vol from the specialist scalars
        # (graph.py _attach_specialist_scalars -> state["ml"]) into the decision
        # record — the docking input for the flag-gated vol-targeting sizing.
        # state["ml"] is None whenever no registry/report/history exists; the
        # context then keeps its None default (fail-safe, byte-identical).
        _ml = state.get("ml")
        _forecast_vol = _ml.get("forecast_vol") if isinstance(_ml, dict) else None

        ctx = DecisionContext(
            symbol=state["symbol"],
            action=action,
            conviction_score=conviction,
            current_price=curr_price,
            rsi_14=_feat("rsi_14", 50.0),
            # features.py exposes the MACD histogram (macd - signal line) — the
            # standard momentum reading; the raw lines are not computed there.
            macd=_feat("macd_hist", 0.0),
            # bb_position ∈ ~[-1, +1] (lower..upper band) → %B ∈ ~[0, 1].
            bb_pct=0.5 + _feat("bb_position", 0.0) / 2.0,
            volume_ratio=_feat("vol_anomaly", 1.0),
            atr_14d=_feat("atr_14d", 0.0),
            forecast_vol=_forecast_vol,
            reasoning_summary=(
                f"RoundTableV2 consensus={score:.3f}{damp_note}: {reasoning[:200]}"
            ),
        )
        return SignalEvent(
            symbol=state["symbol"],
            action=action,
            decision_context=ctx,
        )
    except Exception as exc:
        logger.warning("run_round_table: Signal-Erstellung fehlgeschlagen: %s", exc)
        return None
