# core/round_table/agenten/rl_confidence.py
# #4084 (ARC-E6 G-6b): RLConfidenceAgent samt Gewichtshelfer, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck.
# get_global_registry liest der Agent zur Laufzeit ueber ``_ag`` (Zugriffsregel, agenten/).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table import agents as _ag
from core.round_table.agenten._basis import (
    _SHARED_UNSET,
    DependencyLostException,
    _agent_enabled,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


def _rl_confidence_weight() -> float:
    """RTR-0 (#1947) / Epic #1958: config-gated vote weight for RLConfidenceAgent.

    Default 0.40 == the historical hardcoded weight (byte-identical to today). The RL
    vote is currently direction-blind — its score is ``0.5 + (|pred|/2)*0.5`` on a BUY
    action, so a strongly-BEARISH prediction (large |pred|) still yields a max-conviction
    BUY. Set RL_CONFIDENCE_WEIGHT=0.0 to MUTE it as an interim guard until the sign-fix +
    RTR-0 attribution land (prove-it-or-kill-it). The os.environ read lives in
    config.py/config.oss.py (CODING_POLICY §2.10), not in the finance-core; this reads it
    via get_config(). Invalid values fall back to the 0.40 default.
    """
    try:
        return max(
            0.0, float(getattr(config.get_config(), "RL_CONFIDENCE_WEIGHT", 0.40))
        )
    except (TypeError, ValueError):
        return 0.40


# RTR-0 (#1947) / Epic #1958: resolve the RL vote weight ONCE at import (same reason as
# _SPECIALIST_ALPHA_WEIGHT, #1346). Default 0.40 = byte-identical; desktop arms 0.0 to mute the direction-blind
# RL vote as an interim guard. When muted, min/max clamp to 0 so it is fully excluded.
_RL_CONFIDENCE_WEIGHT = _rl_confidence_weight()


# ---------------------------------------------------------------------------
# 7. RLConfidenceAgent (w:0.40) — RL-Confidence aus aktiver Strategie
# ---------------------------------------------------------------------------


class RLConfidenceAgent(VotingAgent):
    """
    Liest RL-Konfidenz aus der aktiven Registry-Strategie.
    Fallback: 0.5 wenn nicht verfügbar.
    """

    # RTR-0 (#1947): config-gated. Default 0.40 = byte-identical (min 0.15 / max 1.50 as
    # before). RL_CONFIDENCE_WEIGHT=0.0 mutes the direction-blind RL vote — min/max clamp
    # to 0 so it is fully excluded from consensus (mirrors the SpecialistAlpha pattern).
    default_weight: float = _RL_CONFIDENCE_WEIGHT
    min_weight: float = 0.15 if _RL_CONFIDENCE_WEIGHT > 0.0 else 0.0
    max_weight: float = 1.50 if _RL_CONFIDENCE_WEIGHT > 0.0 else 0.0

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("RL_CONFIDENCE_AGENT_ENABLED"):
            return _disabled_abstain("RLConfidenceAgent", state["symbol"])

        symbol = state["symbol"]
        score = None
        weight = self.weight
        reasoning = (
            "Reinforcement-learning agent not connected — staying neutral (0.5)."
        )

        try:
            registry_fn = _ag.get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "RLConfidenceAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            # RTR / distinct-ML-sources (dark, default OFF): resolve the RL voice's OWN
            # registered trader BY NAME (ACTIVE_STRATEGY, default "RLAgent" — the key the
            # active trader registers under, rl_strategy.py:174 / monitor_loop.py:280) so
            # it is a DISTINCT source from the LSTM voice's get("LSTMDynamic"). OFF ->
            # get_active(), byte-identical to today.
            if getattr(config.get_config(), "ROUND_TABLE_DISTINCT_ML_SOURCES", False):
                _rl_name = getattr(config.get_config(), "ACTIVE_STRATEGY", "RLAgent")
                active = registry.get(_rl_name)
                if active is None:
                    # RL trader not registered under that name -> abstain (weight 0),
                    # never collapse back onto whatever get_active() happens to be.
                    return SignalCandidate(
                        agent_name="RLConfidenceAgent",
                        symbol=symbol,
                        score=None,
                        weight=0.0,
                        abstain_reason=AbstainReason.DEPENDENCY_LOST,
                        reasoning=(
                            "Reinforcement-learning agent source not registered — "
                            "abstention (vote not counted)"
                        ),
                    )
            else:
                active = registry.get_active()
                if active is None:
                    raise DependencyLostException(
                        f"RLConfidenceAgent: registry.get_active() returned None for {symbol} — "
                        "No active strategy registered. Kill Switch required."
                    )
            if hasattr(active, "evaluate_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    from core.composition.root import CompositionRoot

                    current_time = CompositionRoot.get_instance().clock_port.now()

                # Art. 14 EU AI Act / #1876: evaluate-only — no orders in vote phase.
                # INC (ML-gate outage): prefer the signal the runner evaluated ONCE and
                # shared, so both ML voices read the SAME result. Their own concurrent
                # per-voice eval returned inconsistent None/valid → the LSTM voice
                # abstained → the strict-ML gate blocked every decision. Fall back to our
                # own eval only when the runner did not share (distinct-ML-sources
                # routing, or an unresolvable active — the key stays absent there).
                _shared = state.get("_shared_active_signal", _SHARED_UNSET)  # type: ignore[call-overload]  # noqa: E501
                if _shared is not _SHARED_UNSET:
                    signal = _shared
                else:
                    signal = await active.evaluate_for_symbol(
                        symbol, state["ohlc"], {}, current_time
                    )

                # SignalEvent handling (V2 strategy returns SignalEvent)
                if signal is not None and hasattr(signal, "decision_context"):
                    ctx = signal.decision_context
                    action = str(getattr(signal, "action", "HOLD")).upper()
                    # Regression fix — reverses #656 (f5cc27da, "stop endless
                    # strategy-switch"). That commit switched this read to
                    # `conviction_score`, which rl_execution only sets on BUY (else 0.0),
                    # so every HOLD/SELL collapsed to a dead neutral 0.5 — the RL agent
                    # effectively stopped voting. `lstm_prediction` (= the strategy's
                    # `pred`) is the ALWAYS-set directional signal and the V2 equivalent
                    # of the pre-#656 `signal.confidence`.
                    pred = float(getattr(ctx, "lstm_prediction", 0.0))
                    # #2480: sign-coherent score across all actions (reverses direction-blindness).
                    # Uses continuous pred: pred > 0 => score > 0.5, pred < 0 => score < 0.5.
                    score = self._clamp(0.5 + 0.5 * math.tanh(pred / 2.0))
                    reasoning = (
                        f"Reinforcement-learning agent leans {action} "
                        f"(prediction {pred:+.2f}, score {score:.3f})."
                    )

                # Legacy handling (just in case)
                elif signal is not None and hasattr(signal, "confidence"):
                    confidence = float(getattr(signal, "confidence", 0.5))
                    action = getattr(signal, "action", "HOLD")
                    if str(action).upper() == "BUY":
                        score = self._clamp(0.5 + confidence * 0.5)
                    elif str(action).upper() == "SELL":
                        score = self._clamp(0.5 - confidence * 0.5)
                    else:
                        score = None
                    reasoning = (
                        f"Reinforcement-learning agent leans {action} "
                        f"(confidence {confidence:.2f}, score {score:.3f}). "
                        f"[src: RL model]"
                    )
                else:
                    score = None
                    weight = 0.0
                    reasoning = (
                        "Reinforcement-learning agent returned no signal this "
                        "cycle — abstention (vote not counted)"
                    )
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("RLConfidenceAgent", exc)
            return SignalCandidate(
                agent_name="RLConfidenceAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.DEPENDENCY_LOST,
                reasoning=(
                    f"Reinforcement-learning agent connection lost — staying "
                    f"neutral, vote not counted ({exc!s:.50})"
                ),
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "RLConfidenceAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("RLConfidenceAgent", exc)
            score = None
            reasoning = (
                f"Reinforcement-learning agent hit a temporary error — staying "
                f"neutral, vote not counted ({exc!s:.50})"
            )
            # Set weight=0.0 to exclude this vote from the consensus average
            return SignalCandidate(
                agent_name="RLConfidenceAgent",
                symbol=symbol,
                score=score,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning=reasoning,
            )

        from core.ml_watchdog import ml_watchdog

        ml_watchdog.record_success("RLConfidenceAgent")

        return SignalCandidate(
            agent_name="RLConfidenceAgent",
            symbol=symbol,
            score=score,
            weight=weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
