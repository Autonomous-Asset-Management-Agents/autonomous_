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

"""Gate-Stufen des Round Table (Phase 3): Strict-ML-Gate, Agent-Veto, Meta-Label-Gate, Gatekeeper.

#4279 (H-4f), Teil von ARC-E6 (#3738): GAP9-Drossel, Strict-ML-Riegel,
``_strict_ml_gate_blocks``, ``_apply_agent_veto``, ``_apply_meta_label`` und
``_resolve_gatekeeper_decision`` wortgleich aus ``core/round_table/runner.py`` hierher
umgezogen (Schnitt-Entscheidung #4186 §2, §5 „H-4f“). Der Kern importiert die vier
Phase-3-Funktionen zurück (Weg (a), §3) und führt ``_warn_gatekeeper_missing_context``
wieder aus. Drossel und Riegel leben nur hier: Tests setzen sie an
``core.round_table.gate_stufen`` zurück (Weg (c)). Dieses Modul importiert den Kern
nicht (rückimport-frei, §3).
"""

from __future__ import annotations

import logging
import threading
import time

from core.round_table.consensus import SIGNAL_SELL_THRESHOLD
from core.round_table.gatekeeper import ComplianceGatekeeper, GatekeeperDecision

# Entscheidung #4186 §2: dasselbe Logger-Objekt wie der Kern (H-4b Regel 4).
logger = logging.getLogger("core.round_table.runner")

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


# #3781: once-per-session latch for the "LSTM disabled by operator" WARNING (same
# rationale as the RL-waiver latch above: per-process configuration fact, not per symbol).
_STRICT_ML_LSTM_DISABLED_WARNED = False
_STRICT_ML_LSTM_DISABLED_LOCK = threading.Lock()


def _warn_strict_ml_lstm_disabled() -> None:
    """Once-per-session WARNING (§5.6) when the Strict-ML-Gate passes without an LSTM
    vote because the operator switched the LSTM voter off (LSTM_SIGNAL_AGENT_ENABLED).
    """
    global _STRICT_ML_LSTM_DISABLED_WARNED
    with _STRICT_ML_LSTM_DISABLED_LOCK:
        if _STRICT_ML_LSTM_DISABLED_WARNED:
            return
        _STRICT_ML_LSTM_DISABLED_WARNED = True
    logger.warning(
        "StrictML-Gate: LSTM voter disabled by operator (LSTM_SIGNAL_AGENT_ENABLED=false) "
        "— gate passes without an LSTM vote; candidates still come from the LSTM "
        "pre-selection. Logged once per session."
    )


def _strict_ml_gate_blocks(
    lstm_valid: bool, rl_valid: bool, lstm_disabled: bool = False
) -> bool:
    """RTR-6 (#2410): does the Strict-ML-Gate veto this cycle?

    With ``GATEKEEPER_STRICT_ML_REQUIRES_RL=True`` BOTH core ML votes are required
    (LSTM and RL, each ``weight > 0``) — the pre-#2410 gate. The DEFAULT is False
    (ADR-018, owner 2026-07-26; #3261): a valid LSTM vote alone passes; the RL abstain is WARNING-logged
    once per session (§5.6). A cycle where the LSTM is invalid ALWAYS blocks — the
    "no ML voted yet approved" protection never lifts (issue #2410 scenario 3).

    #3781: ``lstm_disabled`` = this cycle's LSTM vote is the operator-disabled abstain
    (``abstain_reason == DISABLED``). That is a configuration choice, not an outage: the
    gate does not block on the missing LSTM vote (RL stays required if configured). An
    LSTM that is switched ON but has no valid vote still blocks — fail-closed.
    """
    from config import get_config

    if not lstm_valid:
        if not lstm_disabled:
            return True
        if get_config().GATEKEEPER_STRICT_ML_REQUIRES_RL and not rl_valid:
            return True
        _warn_strict_ml_lstm_disabled()
        return False
    if rl_valid:
        return False

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
