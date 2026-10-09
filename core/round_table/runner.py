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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import config
from core.composition.root import CompositionRoot

try:  # module attribute so tests can patch it; matches agents.py's lazy import
    from core.agent_registry import get_global_registry
except ImportError:  # pragma: no cover — registry always present in the live engine
    get_global_registry = None  # type: ignore[assignment]

from core.contracts.signal_candidate import AbstainReason
from core.round_table.agents import ALL_AGENTS, LSTMSignalAgent, RLConfidenceAgent
from core.round_table.aufzeichnung import (  # noqa: F401 — #4280 Rückimport/Wiederausfuhr
    _AGENT_RECORD_ROLES,
    _maybe_record_shadow_specialist_vote,
    _maybe_record_shadow_tft_vote,
    _serialize_votes,
)
from core.round_table.consensus import ConsensusEngine
from core.round_table.consensus import (
    record_consensus_outcome as _record_consensus_outcome,
)
from core.round_table.entscheidungs_zaehler import (  # noqa: F401 — #4276 Wiederausfuhr
    _bump_agent_failure,
    _bump_run,
    get_decision_counters,
    reset_decision_counters,
)
from core.round_table.gate_stufen import (  # noqa: F401 — #4279 Wiederausfuhr
    _apply_agent_veto,
    _apply_meta_label,
    _resolve_gatekeeper_decision,
    _strict_ml_gate_blocks,
    _warn_gatekeeper_missing_context,
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
from core.round_table.signal_bau import (  # noqa: F401 — #4277 Wiederausfuhr
    _position_fields_from_state,
    _score_to_signal,
)

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)


# Layer 1: Per-Agent Timeout (MiFID II Art. 17 — System Resilience)
# #3381: der Wert (60,0 s, erhoeht fuer CPU-gebundene PyTorch-Inferenz in Docker) ist
# unveraendert, kommt aber aus derselben geordneten Staffelung wie Symbol und Zyklus —
# sonst kann die innere Schicht wieder nach der aeusseren aufgeben.
def _agent_vote_timeout() -> float:
    """Layer 1 der Staffelung (#3381)."""
    from core.engine.time_budget import current_time_budget

    return current_time_budget()[0]


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
            current_time = CompositionRoot.get_instance().clock_port.now()
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
            exc_info=True,
        )
        return state


async def _abstimmen(
    state: "SymbolEvalState", symbol: str
) -> "tuple[list, Optional[SymbolEvalState]]":
    """Phase 1 (#4281, H-4h): alle Agents parallel, Ausfälle isoliert und gezählt.

    Liefert ``(valid_votes, abbruch)``. ``abbruch`` ist ``None`` oder das Fehler-State-dict,
    das der Dirigent unverändert zurückgibt (gather-Fehler, keine gültige Stimme) —
    Abbruch-Ergebnis nach Muster G-1b statt ``return`` aus der Mitte des Dirigenten.
    """
    # --- Phase 1: Parallel Voting (LangGraph super-step via asyncio.gather) ---
    # Layer 1: Each agent.vote() wrapped in asyncio.wait_for (MiFID Art. 17)
    try:
        vote_results = await asyncio.gather(
            *[
                asyncio.wait_for(agent.vote(state), timeout=_agent_vote_timeout())
                for agent in _active_agents
            ],
            return_exceptions=True,
        )
    except Exception as exc:
        logger.exception("run_round_table: gather-Fehler für %s: %s", symbol, exc)
        return [], {**state, "error": str(exc)}

    # Exception handling from gather (isolating agent failures)
    valid_votes = []
    for i, result in enumerate(vote_results):
        _agent_name = _active_agents[i].__class__.__name__
        if isinstance(result, asyncio.TimeoutError):
            logger.warning(
                "MIFID_AUDIT[%s] agent=%s TIMEOUT after %.0fs — vote excluded",
                symbol,
                _agent_name,
                _agent_vote_timeout(),
            )
            # PR C (PURE OBSERVATION): a timed-out vote is a vote failure. Fail-safe bump
            # keyed on the agent CLASS name (never order content); wrapped so it can never
            # perturb the existing MLWatchdog escalation below.
            try:
                _bump_agent_failure(_agent_name)
            except Exception as exc:  # noqa: BLE001 — observation never alters the flow
                logger.warning("agent-failure counter failed: %s", exc, exc_info=True)
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
            except Exception as exc:  # noqa: BLE001 — observation never alters the flow
                logger.warning("agent-failure counter failed: %s", exc, exc_info=True)
            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:
                _ml_watchdog.record_error(_agent_name, result)
        elif result is None:
            logger.warning(
                "run_round_table: Agent %s returned None for %s — vote excluded",
                _agent_name,
                symbol,
            )
        else:
            valid_votes.append(result)
            # ML agent success → reset escalation chain
            if _agent_name in _ML_AGENT_NAMES and _ml_watchdog:
                _ml_watchdog.record_success(_agent_name)

    if not valid_votes:
        logger.error("run_round_table: Alle Agents fehlgeschlagen für %s", symbol)
        return [], {
            **state,
            "error": "Alle Voting-Agents fehlgeschlagen",
            "signal": None,
        }

    return valid_votes, None


def _konsens_bilden(
    state: "SymbolEvalState", symbol: str, valid_votes: list
) -> tuple[float, Optional[dict]]:
    """Phase 1.5 und 2 (#4281, H-4h): Integrität, Aggregation, Abdeckung, ``VOTE[…]``-Log.

    Schreibt ``state["regime_conditioning"]`` in place — dasselbe dict gibt der Dirigent aus.
    """
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
    # #3618: hand the risk-off conditioning audit (factor, undamped consensus) to the
    # signal builder; None when nothing was damped this cycle.
    state["regime_conditioning"] = getattr(_consensus_engine, "last_conditioning", None)

    # #3407: Die Beobachtung laeuft immer, getrennt von der Wirkung im Sizing
    _vote_coverage = None
    try:
        import config as _cfg_cov
        from core.round_table.consensus import consensus_exclusions, vote_coverage

        _iv_on = bool(
            getattr(_cfg_cov.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", False)
        )
        _excl = set(consensus_exclusions(_iv_on))
        _armed = {
            a.__class__.__name__: float(a.weight)
            for a in (_active_agents or [])
            if a.__class__.__name__ not in _excl and float(a.weight) > 0.0
        }
        _vote_coverage = vote_coverage(valid_votes, _armed)
    except Exception as exc:  # noqa: BLE001 — observation must never break the path
        logger.warning("coverage compute failed for %s: %s", symbol, exc, exc_info=True)

    # MiFID II / Observability: Jeder Agent-Vote einzeln loggen (unabhängig von DB)
    for vote in valid_votes:
        score_repr = (
            f"{vote.score:.3f}"
            if getattr(vote, "score", None) is not None
            else "ABSTAIN"
        )
        logger.info(
            "VOTE[%s] agent=%s score=%s weight=%.2f reasoning=%s",
            symbol,
            vote.agent_name,
            score_repr,
            vote.weight,
            vote.reasoning[:120] if vote.reasoning else "",
        )

    return consensus_score, _vote_coverage


async def _compliance_pruefen(
    state: "SymbolEvalState", symbol: str, valid_votes: list, consensus_score: float
) -> GatekeeperDecision:
    """Phase 3 (#4281, H-4h): Strict-ML, Gatekeeper, Veto, Meta-Label, Veto-Markierung."""
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

    # #3781: the LSTM vote of this cycle is the operator-disabled abstain (switch off),
    # not an outage — read from the vote record itself (isinstance-checked, I-3 #944).
    lstm_disabled = not lstm_valid and any(
        isinstance(_agent_type_map.get(v.agent_name), LSTMSignalAgent)
        and getattr(v, "abstain_reason", None) == AbstainReason.DISABLED
        for v in valid_votes
    )

    # RTR-6 (#2410): flag-gated Strict-ML-Gate — default (GATEKEEPER_STRICT_ML_REQUIRES_RL
    # =True) is byte-identical to the pre-#2410 "both ML votes required" gate.
    if _strict_ml_gate_blocks(lstm_valid, rl_valid, lstm_disabled=lstm_disabled):
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

        if lstm_disabled:
            # #3781: name the mode in the audit reasoning (MiFID II trail) — this
            # decision cleared the Strict-ML-Gate without an LSTM vote by operator choice.
            gatekeeper_decision = GatekeeperDecision(
                approved=gatekeeper_decision.approved,
                reason=(
                    f"{gatekeeper_decision.reason} | StrictML: LSTM disabled by operator "
                    "(flag) — no LSTM vote required"
                ),
                symbol=gatekeeper_decision.symbol,
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
            if vote is not None:
                vote.vetoed = True
        logger.info(
            "ComplianceGatekeeper: VETO für %s — %s",
            symbol,
            gatekeeper_decision.reason,
        )

    return gatekeeper_decision


def _signal_ableiten(
    state: "SymbolEvalState",
    symbol: str,
    valid_votes: list,
    consensus_score: float,
    gatekeeper_decision: GatekeeperDecision,
    vote_coverage: Optional[dict],
):
    """Phase 4 (#4281, H-4h): Signal (oder ``None``), Coverage-Anhang, Verdikt-Zähler."""
    # --- Phase 4: Signal aus Konsens ableiten ---
    signal = None
    if gatekeeper_decision.approved:
        signal = _score_to_signal(state, consensus_score, valid_votes)

        # #3210: attach the directional-vote coverage to the DecisionContext so the
        # sizer/audit can apply the prudence discount (fail-safe; None/dark ⇒ unused,
        # byte-identical). Mirrors the decision-capture attach hook below.
        try:
            _dc_cov = getattr(signal, "decision_context", None) if signal else None
            if _dc_cov is not None and vote_coverage is not None:
                _dc_cov.vote_coverage = vote_coverage
        except Exception as exc:  # noqa: BLE001 — never break the decision path
            logger.warning(
                "coverage attach failed for %s: %s", symbol, exc, exc_info=True
            )

    # PR C (PURE OBSERVATION): count the VERDICT distribution ({buy, sell, no_trade}).
    # Classified from consensus_score + gatekeeper approval — the SAME inputs that produced
    # ``signal`` above — so the count mirrors the real decision. record_consensus_outcome is
    # itself fail-safe; the extra call-site guard is defense-in-depth so a broken counter can
    # never touch the order path derived from ``signal``.
    try:
        _record_consensus_outcome(consensus_score, gatekeeper_decision.approved)
    except Exception as exc:  # noqa: BLE001 — observation must never alter the verdict
        logger.warning("consensus-outcome counter failed: %s", exc, exc_info=True)

    return signal


def _beobachten(
    state: "SymbolEvalState",
    symbol: str,
    consensus_score: float,
    valid_votes: list,
    signal,
) -> None:
    """Phase 4.5 (#4281, H-4h): Schatten-Haken und Harness #3197 — nur Beobachtung."""
    # --- Phase 4.5: Shadow-TFT-Vote (Fusion, dormant — flag SHADOW_TFT_VOTE_ENABLED) ---
    # Records what a TFT-only vote WOULD say vs the real consensus — NOT counted, never
    # touches the order path. No-op unless the flag is set.
    _maybe_record_shadow_tft_vote(state, symbol, consensus_score, signal)
    _maybe_record_shadow_specialist_vote(
        symbol, consensus_score, getattr(signal, "action", None)
    )
    # #3197 measurement harness (DARK, env-gated, fail-safe): dump this evaluation's
    # consensus + per-agent votes + date so an offline analysis can test consensus-level
    # (sweet-spot / shrinkage) and vote-diversity against forward returns. Never trades.
    try:
        from core.round_table.consensus_return_recorder import (
            record_consensus_observation,
        )

        record_consensus_observation(state, symbol, consensus_score, valid_votes)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — observation must never break the evaluation
        logger.warning(
            "[runner] consensus_return_recorder hook failed: %s", exc, exc_info=True
        )


def _protokollieren(
    state: "SymbolEvalState",
    session_id: str,
    symbol: str,
    valid_votes: list,
    consensus_score: float,
    gatekeeper_decision: GatekeeperDecision,
    signal,
) -> list:
    """Phase 5 (#4281, H-4h): Serialisierung, Senate, Anzeige-Speicher, Capture."""
    # --- Phase 5: Senate Protocol (fire-and-forget) ---
    serialized_votes = _serialize_votes(valid_votes)
    session = SenateSession(
        session_id=session_id,
        symbol=symbol,
        timestamp=CompositionRoot.get_instance().clock_port.now().isoformat(),
        votes=serialized_votes,
        consensus_score=consensus_score,
        gatekeeper_approved=gatekeeper_decision.approved,
        gatekeeper_reason=gatekeeper_decision.reason,
        signal_action=getattr(signal, "action", None),
        # #2783 Inkrement 1: derselbe Wert, den die Erfassungszeile traegt
        # (`capture.py::build_outcome_row` liest `ctx.decision_id`) — EIN Erzeuger,
        # EIN Wert, keine Abstimmung zwischen zwei Senken noetig.
        #
        # Defensiv wie die Capture-Anbindung dreissig Zeilen tiefer: Fehlt der
        # Kontext, bleibt das Feld `None` und der Datensatz wird trotzdem
        # geschrieben. Der Audit-Pfad darf den Handelspfad nie zum Stehen bringen.
        decision_id=getattr(
            getattr(signal, "decision_context", None) if signal else None,
            "decision_id",
            None,
        ),
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
        logger.warning(
            "decision-capture attach hook failed for %s: %s", symbol, exc, exc_info=True
        )

    return serialized_votes


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
    except (
        Exception
    ) as exc:  # noqa: BLE001 — observation must never alter the round table
        logger.warning("round-table run counter failed: %s", exc, exc_info=True)

    symbol = state["symbol"]
    session_id = make_session_id()

    # INC (ML-gate outage): de-duplicate the heavy per-symbol model call — evaluate the
    # active strategy ONCE and share it so both ML voices read one consistent signal,
    # instead of each firing its own concurrent evaluate_for_symbol inside the gather.
    state = await _maybe_share_active_signal(state)

    # --- Phase 1: Parallel Voting (Abbruch-Ergebnis statt return aus der Mitte) ---
    valid_votes, abbruch = await _abstimmen(state, symbol)
    if abbruch is not None:
        return abbruch
    active_agent_count = len(_active_agents) if _active_agents else len(ALL_AGENTS)

    # --- Phase 1.5 + 2: Integrität, Konsens, Abdeckung ---
    consensus_score, vote_coverage = _konsens_bilden(state, symbol, valid_votes)

    # --- Phase 3: Compliance Gate (Veto-Markierung vor dem Signal) ---
    gatekeeper_decision = await _compliance_pruefen(
        state, symbol, valid_votes, consensus_score
    )

    # --- Phase 4: Signal + Verdikt-Zähler (vor den Schatten-Haken) ---
    signal = _signal_ableiten(
        state, symbol, valid_votes, consensus_score, gatekeeper_decision, vote_coverage
    )

    # --- Phase 4.5: Beobachtung ---
    _beobachten(state, symbol, consensus_score, valid_votes, signal)

    # --- Phase 5: Senate vor Anzeige-Speicher vor Capture ---
    serialized_votes = _protokollieren(
        state,
        session_id,
        symbol,
        valid_votes,
        consensus_score,
        gatekeeper_decision,
        signal,
    )

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
