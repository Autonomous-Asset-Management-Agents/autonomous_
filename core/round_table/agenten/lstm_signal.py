# core/round_table/agenten/lstm_signal.py
# #4084 (ARC-E6 G-6b): LSTMSignalAgent samt Gewicht, Vote-Skala und Panel-Mindestbreite,
# unveraendert aus core/round_table/agents.py umgezogen. agents.py importiert jeden Namen
# zurueck. get_global_registry liest der Agent zur Laufzeit ueber ``_ag`` (Zugriffsregel,
# agenten/).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from datetime import timezone
from typing import TYPE_CHECKING, Tuple

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table import agents as _ag
from core.round_table.agenten._basis import (
    _SHARED_UNSET,
    DependencyLostException,
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")

_LSTM_SIGNAL_WEIGHT = _consensus_weight("LSTM_SIGNAL_WEIGHT", 0.15)  # #3698


# ---------------------------------------------------------------------------
# 6. LSTMSignalAgent (w:0.40) — Delegiert an aktive Registry-Strategie
# ---------------------------------------------------------------------------


# ADR-RT01 (#1969): LSTM vote scale for the continuous tanh score mapping.
# score = clamp(0.5 + 0.5 * tanh(pred / _LSTM_VOTE_SCALE)) — a MONOTONE, bounded
# transform of the raw LSTM prediction, replacing the old 3-bucket
# {BUY:0.75, HOLD:0.5, SELL:0.25} discretisation that destroyed the signal
# (raw pred cross-sectional IC = +0.067 t=20.4, collapsed to +0.011 with a
#  NEGATIVE consensus contribution — attribution harness #1947).
# Basis: 2-sigma of the empirical prediction distribution on the OOS holdout
# (2024-01..2026-05, N=285,445): sigma = 1.79, so 2*sigma ≈ 3.6. Calibrating to
# 2-sigma keeps the bulk of predictions in the responsive (non-saturated) region
# of tanh — a +/-2σ prediction maps to score ≈ 0.88 / 0.12. The Spearman IC is
# scale-invariant; the scale only sets the vote's spread inside the weighted
# consensus mean. Net-of-cost proof (#1947 harness, 10bps one-way): the fix
# lifts the LSTM leave-one-out delta-Sharpe from -0.013 to +0.78. Annual review.
_LSTM_VOTE_SCALE: float = 3.6

# ADR-RT-LSTM-02: Minimum panel breadth before a CROSS-SECTIONAL standing may be
# voted on (flag-gated path only — see LSTM_VOTE_USES_CROSS_SECTIONAL_RANK).
# Basis: the cross-sectional signal was validated across the FULL universe (~488-500
# names). A percentile computed against a handful of symbols is not that signal — at
# n=1 it is definitionally 0.0 (you beat nobody), and it stays degenerate while the
# panel is thin. 30 keeps percentile resolution at ~3.3pp, coarse but honest; below
# it the module abstains rather than dress a thin panel as a universe standing.
# Reviewed with the walk-forward that gates the flag itself. Annual review.
_MIN_PANEL_SYMBOLS: int = 30


class LSTMSignalAgent(VotingAgent):
    """
    Delegiert an die aktive Strategie in der AgentRegistry.

    Der Vote-Score ist eine MONOTONE KONTINUIERLICHE Funktion der LSTM-Prediction
    (`signal.decision_context.lstm_prediction`, das V2-Äquivalent des rohen `pred`),
    nicht mehr eine 3-Bucket-Diskretisierung (#1969). Fehlt die Prediction (None),
    abstiniert der Agent (weight=0.0) statt ein 0.5-Fake mit vollem Gewicht zu voten.
    """

    default_weight: float = (
        _LSTM_SIGNAL_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.15
    max_weight: float = 1.50

    def _vote_on_rank(
        self, symbol: str, pred: float, action: str
    ) -> Tuple[float, float, str]:
        """Vote on the stock's standing in the universe, not its isolated prediction.

        Aktiver Pfad: ``LSTM_VOTE_USES_CROSS_SECTIONAL_RANK`` hat seit ADR-018 die
        Vorgabe ``True``. Die Einzelprognose darunter ist der abschaltbare Rueckfall.
        (Bis 30.09.2026 stand hier "default OFF" — das war seit ADR-018 falsch.)

        Rationale (ADR-RT-LSTM-01): our own walk-forward refutes the raw per-symbol
        prediction — 0 of 488 symbols survive FDR correction, i.e. the isolated
        `pred` this agent votes on today is statistically indistinguishable from
        noise once you account for having tested 488 of them. What DID survive is
        the CROSS-SECTIONAL RANK (IC 0.067, t 20.4): the model is far better at
        ordering stocks against each other than at calling any one of them. So the
        honest question this module can answer is "how does this stock stand against
        the other ~500 today?", NOT "will this stock go up?".

        The standing comes from the same panel + the same `cross_section_standing()`
        the auditable report renders, so the board votes on exactly the number the
        report shows the user. Percentile maps directly to the [0,1] score — a stock
        in the top 10% scores 0.9. `pred` is kept only to explain the vote; it no
        longer drives it.

        Absent from the panel -> abstain (weight 0). The panel is only fed while the
        LSTM ranking cycle runs; without it this module has no validated basis to
        vote on, and a quiet 0.5 at full weight would drag every consensus toward
        neutral while looking like a real opinion.
        """
        from core.report.lstm_panel_store import (
            active_cross_section,
            cross_section_standing,
            get_store,
        )
        from core.sim.clock import engine_now

        # #2630: read the panel AS OF the engine's current day — the sim clock's date under
        # SIM_MODE, else byte-identical to datetime.now(). Was datetime.now(): the sim queried the
        # wall-clock date (no panel → n=0 → LSTM abstains → strict-ML-gate hard-blocks every symbol).
        as_of = engine_now(timezone.utc).date()
        # #2680: the ONE ranking seam. LSTM_RANK_SMOOTHING_ENABLED off (default) →
        # cross_section_at, byte-identical. ON → the trend (median) table, so a
        # one-day spike can no longer produce a confident BUY vote (the IVZ case:
        # bought point-in-time top-10, rank 242 three hours later).
        xsec = active_cross_section(get_store(), as_of)

        # ADR-RT-LSTM-02: a standing is only meaningful against a BROAD, DISPERSED
        # panel. Two degenerate panels the live engine can genuinely produce would
        # otherwise turn into confident bearishness:
        #   * a thin panel (n=1): the symbol beats nobody -> 0th percentile -> score
        #     0.0, a maximally BEARISH vote drawn from no information at all;
        #   * a collapsed model (every score identical): NO symbol beats any other,
        #     so EVERY symbol lands at the 0th percentile at once -> the board reads
        #     the entire universe as "sell" off a model failure.
        # Both are absence of signal, and absence of signal is an abstention.
        n_panel = len(xsec)
        if n_panel < _MIN_PANEL_SYMBOLS or len(set(xsec.values())) < 2:
            logger.warning(
                "LSTMSignalAgent: panel @ %s unusable for a standing "
                "(n=%d, distinct scores=%d) → abstention",
                as_of.isoformat(),
                n_panel,
                len(set(xsec.values())),
            )
            return (
                0.5,
                0.0,
                f"EXCLUDED — today's AI model ranking does not have enough "
                f"usable data to place this stock ({n_panel} stocks, "
                f"{len(set(xsec.values()))} distinct scores as of "
                f"{as_of.isoformat()})",
            )

        pct, rank, n = cross_section_standing(xsec, symbol)
        if pct is None:
            logger.warning(
                "LSTMSignalAgent: %s absent from the LSTM panel @ %s → abstention "
                "(cross-sectional mode needs the ranking cycle to have run)",
                symbol,
                as_of.isoformat(),
            )
            return (
                None,
                0.0,
                f"EXCLUDED — {symbol} is not in today's AI model ranking "
                f"({as_of.isoformat()}), so there is no validated basis to vote on",
            )
        score = self._clamp(pct / 100.0)
        return (
            score,
            self.weight,
            f"AI price model puts this stock at rank {rank} of {n} in today's "
            f"universe ({pct:.1f}th percentile, score {score:.3f}). The vote rests "
            f"on this cross-sectional standing — how the stock ranks against the "
            f"others, which is what the model is validated for — not on its raw "
            f"single-stock prediction ({pred:+.3f}, {action}), which failed "
            f"per-stock validation (0/488 passed). [src: AI model daily ranking @ "
            f"{as_of.isoformat()}]",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("LSTM_SIGNAL_AGENT_ENABLED"):
            return _disabled_abstain("LSTMSignalAgent", symbol)

        score = None
        weight = self.weight
        reasoning = "AI price model not connected — staying neutral (0.5)."

        try:
            registry_fn = _ag.get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "LSTMSignalAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            # RTR / distinct-ML-sources (dark, default OFF): resolve the LSTM voice's
            # OWN registered model (the standby "LSTMDynamic" ranker, monitor_loop.py:77)
            # instead of the shared get_active() — which is the RL trader under the
            # default ACTIVE_STRATEGY="RLAgent", the LSTM/RL collapse documented in
            # core/agent_registry.py::get(). OFF -> get_active(), byte-identical to today.
            if getattr(config.get_config(), "ROUND_TABLE_DISTINCT_ML_SOURCES", False):
                active = registry.get("LSTMDynamic")
                if active is None:
                    # Producer not registered yet -> abstain (weight 0), NEVER a
                    # 0.5-fake at full weight that pollutes the consensus mean.
                    return SignalCandidate(
                        agent_name="LSTMSignalAgent",
                        symbol=symbol,
                        score=None,
                        weight=0.0,
                        abstain_reason=AbstainReason.NO_DATA,
                        reasoning=(
                            "AI price model (LSTMDynamic) not registered yet — "
                            "abstention (vote not counted)"
                        ),
                    )
            else:
                active = registry.get_active()
                if active is None:
                    raise DependencyLostException(
                        f"LSTMSignalAgent: registry.get_active() returned None for {symbol} — "
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
                elif config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:
                    # INC (ML-gate outage) robustness: in rank mode the vote is driven by
                    # the PANEL standing; this per-symbol evaluate is best-effort
                    # ENRICHMENT ONLY (it colours the reasoning string, never the
                    # score/weight — see the rank branch below). The live standby
                    # "LSTMDynamic" ranker can RAISE for a symbol not in its per-symbol
                    # cache (a "no bars"/KeyError inside the strategy), not just return
                    # None. Letting that raise propagate to the outer except would abort
                    # the whole vote into a weight-0 abstain — the LSTM voice would still
                    # abstain on every symbol and the strict-ML gate would still block
                    # every decision, i.e. the exact ML-gate outage this decoupling exists
                    # to remove. So swallow it (WARNING, §5.6) and vote on the panel
                    # regardless. The flag-OFF branch below is UNCHANGED — a raise there
                    # still propagates to the outer except, byte-identical to today.
                    try:
                        signal = await active.evaluate_for_symbol(
                            symbol, state["ohlc"], {}, current_time
                        )
                    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                        logger.warning(
                            "LSTMSignalAgent: best-effort evaluate raised in rank mode "
                            "for %s (%s) — voting on the cross-sectional panel standing "
                            "regardless.",
                            symbol,
                            exc,
                        )
                        signal = None
                else:
                    signal = await active.evaluate_for_symbol(
                        symbol, state["ohlc"], {}, current_time
                    )

                # INC (ML-gate outage): the cross-sectional RANK is a PANEL property
                # (core.report.lstm_panel_store), NOT a per-symbol evaluate signal. Under
                # distinct-ML-sources the resolved "LSTMDynamic" ranker returns None per
                # symbol (the symbol is not in its per-symbol cache), which — before this
                # decoupling — fell into the "no signal" abstain BELOW, UPSTREAM of the
                # rank branch → the LSTM voice abstained → the strict-ML gate blocked
                # EVERY decision. When the rank flag is ON we must vote on the panel
                # standing REGARDLESS of the per-symbol signal; the signal only enriches
                # the reasoning string. All abstain guards (thin panel / symbol absent →
                # weight 0) live inside _vote_on_rank and are preserved. The flag-OFF
                # (per-symbol) path below is byte-identical to before.
                if config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:
                    # Best-effort: if a signal with a prediction is present, pass it as
                    # `pred`/`action` for a richer reasoning string; otherwise a neutral
                    # (0.0, "HOLD") — the score/weight come from the panel, not from these.
                    pred = 0.0
                    action = "HOLD"
                    if signal is not None and hasattr(signal, "decision_context"):
                        raw_pred = getattr(
                            signal.decision_context, "lstm_prediction", None
                        )
                        if raw_pred is not None:
                            pred = float(raw_pred)
                            action = getattr(signal, "action", "HOLD")
                    score, weight, reasoning = self._vote_on_rank(symbol, pred, action)
                    if weight == 0.0:
                        score = None
                elif signal is not None and hasattr(signal, "decision_context"):
                    # #1969: continuous, monotone score from the raw LSTM prediction
                    # (the same ALWAYS-set field RLConfidenceAgent reads), NOT a
                    # 3-bucket {BUY:0.75, HOLD:0.5, SELL:0.25} discretisation.
                    ctx = signal.decision_context
                    raw_pred = getattr(ctx, "lstm_prediction", None)
                    if raw_pred is None:
                        # Prediction genuinely missing → abstain (weight 0), never a
                        # 0.5-fake with full weight that pollutes the consensus mean.
                        score = None
                        weight = 0.0
                        reasoning = (
                            "AI price model has no prediction right now — "
                            "abstention (vote not counted)"
                        )
                    else:
                        pred = float(raw_pred)
                        action = getattr(signal, "action", "HOLD")
                        score = self._clamp(
                            0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)
                        )
                        reasoning = (
                            f"AI price model (LSTM) signals {action} "
                            f"(prediction {pred:+.3f}, score {score:.3f}). "
                            f"[src: LSTM model output]"
                        )
                else:
                    score = None
                    weight = 0.0
                    reasoning = (
                        "AI price model returned no signal this cycle — "
                        "abstention (vote not counted)"
                    )
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("LSTMSignalAgent", exc)
            return SignalCandidate(
                agent_name="LSTMSignalAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.DEPENDENCY_LOST,
                reasoning=(
                    f"AI price model connection lost — staying neutral, "
                    f"vote not counted ({exc!s:.50})"
                ),
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "LSTMSignalAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("LSTMSignalAgent", exc)
            score = None
            reasoning = (
                f"AI price model hit a temporary error — staying neutral, "
                f"vote not counted ({exc!s:.50})"
            )
            # Set weight=0.0 to exclude this vote from the consensus average
            return SignalCandidate(
                agent_name="LSTMSignalAgent",
                symbol=symbol,
                score=score,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning=reasoning,
            )

        from core.ml_watchdog import ml_watchdog

        ml_watchdog.record_success("LSTMSignalAgent")

        return SignalCandidate(
            agent_name="LSTMSignalAgent",
            symbol=symbol,
            score=score,
            weight=weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
