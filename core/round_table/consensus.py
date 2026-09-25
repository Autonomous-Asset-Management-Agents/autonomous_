# core/round_table/consensus.py
# Epic 2.5 — Round Table V2: ConsensusEngine + Pydantic V2 Validierung
#
# Weighted Score Aggregation:
#   weighted_score = Σ(score_i * weight_i) / Σ(weight_i)   [for non-vetoed votes]
#
# Pydantic V2 for ultra-fast validation of agent outputs before aggregation.
#
# Policy: CODING_POLICY.md §11.5 TDD

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.contracts.signal_candidate import SignalCandidate

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADR-SEC-01: Signal Integrity Thresholds (single source of truth)
# These mirror runner.py:_score_to_signal() BUY/SELL boundaries.
# Import these constants from runner.py instead of duplicating literals.
# Reviewed: 2026-05-12 (Security Governance Audit)
# ---------------------------------------------------------------------------


# BUY/SELL territory boundaries — must match runner.py:_score_to_signal()
# #2815 (TRD-8 T1): config-gated so the parameter sweep can vary them per RUN (fresh
# process; resolved once at import). Unset ⇒ the ADR-SEC-01 literals, byte-identical
# (pinned by tests/unit/test_consensus_weight_seam.py). Validation is fail-safe: any
# pair that is not 0 < sell < buy < 1 falls back to the defaults with a WARNING — a
# sweep typo must never invert the consensus. runner.py and attribution/metrics.py
# import THESE names, so the single source of truth stays here.
def _signal_thresholds() -> "tuple[float, float]":
    _DEFAULTS = (0.65, 0.35)  # ADR-SEC-01, reviewed 2026-05-12
    try:
        from config import get_config

        cfg = get_config()
        buy = float(getattr(cfg, "SIGNAL_BUY_THRESHOLD", _DEFAULTS[0]))
        sell = float(getattr(cfg, "SIGNAL_SELL_THRESHOLD", _DEFAULTS[1]))
    except Exception:  # config unavailable (isolated import) → literals
        return _DEFAULTS
    if not (0.0 < sell < buy < 1.0):
        logging.getLogger(__name__).warning(
            "SIGNAL thresholds invalid (buy=%s, sell=%s) — need 0 < sell < buy < 1; "
            "falling back to ADR-SEC-01 defaults %s",
            buy,
            sell,
            _DEFAULTS,
        )
        return _DEFAULTS
    return (buy, sell)


SIGNAL_BUY_THRESHOLD: float
SIGNAL_SELL_THRESHOLD: float
SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD = _signal_thresholds()

# std_dev floor for HIGH_CORRELATION detection (ADR-SEC-01)
# Legitimate 9-agent debate on strong signal produces std_dev ≈ 0.08–0.15.
# std_dev < 0.03 in BUY/SELL territory is statistically implausible without
# a shared, potentially compromised data source.
STD_DEV_UNIFORMITY_THRESHOLD: float = 0.03  # ADR-SEC-01: threshold reviewed 2026-05-12

if TYPE_CHECKING:
    from core.round_table.base_agent import VoteResult

# #3618: there is NO hand-kept list of "directional voters" any more. The damped set
# is DERIVED: every vote that remains in the direction mean after
# ``consensus_exclusions()`` (conditioners and, with the IV forecast on, VIXAware are
# out). The old list (Momentum/LSTM/RL/Specialist/UpsideSkew) damped only 5 of the
# mean's voters and thereby shifted the mean toward the undamped voices
# (Fundamentals/Valuation/News) — risk moving the SELECTION between alpha voices.


def _regime_conditioner_fusion_cfg() -> "tuple[bool, float]":
    """#1949 (RTR-2): (enabled, riskoff_damp_max) for the fusion-side conditioning.

    Flag OFF keeps the old binary block (regime score < 0.45 → MomentumAgent
    weight ×0.5) byte-identical. ON → gradual risk-off shrink of the direction
    consensus toward neutral (#3618): factor = 1 - damp_max * severity,
    severity = clamp((0.5 - score)/0.5, 0, 1); consensus' = 0.5 + factor * (consensus - 0.5)
    for a BUY-leaning consensus (upward only — SELL-leaning stays untouched).
    NB (#3004): the flag DEFAULT is True (config.py / config.oss.py) — the ON path
    is what production runs. The previous wording said "Default OFF", which had
    drifted from the configuration and read as if the binary block were live.
    The os.environ read lives in config.py / config.oss.py (CODING_POLICY §2.10);
    invalid values fall back to OFF.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "REGIME_CONDITIONER_ENABLED", False))
        damp_max = float(getattr(cfg, "REGIME_RISKOFF_DAMP_MAX", 0.5))
        return enabled, damp_max
    except (TypeError, ValueError):
        return False, 0.5


# --- ADR-OBS-01 / PR C: consensus-outcome instrumentation (PURE OBSERVATION) ---
# Fail-safe module-level counter of the round-table VERDICT distribution
# ({buy, sell, no_trade}) classified against the SIGNAL_*_THRESHOLD constants above.
# ``_bump_outcome`` swallows EVERY error so a counter failure can NEVER alter a
# consensus verdict or an agent vote — ``ConsensusEngine.aggregate`` / the runner's
# decision flow stay byte-identical. Aggregate counts only — never symbols, scores, or
# per-symbol verdicts. Read-only snapshot via ``get_decision_counters``.
_CONSENSUS_OUTCOMES: "dict[str, int]" = {"buy": 0, "sell": 0, "no_trade": 0}


def _bump_outcome(bucket: str) -> None:
    """Fail-safe consensus-outcome counter mutation — swallows EVERY error."""
    try:
        _CONSENSUS_OUTCOMES[bucket] = _CONSENSUS_OUTCOMES.get(bucket, 0) + 1
    except Exception:  # noqa: BLE001 — a broken counter must never alter a verdict
        pass


def record_consensus_outcome(score: float, approved: bool) -> None:
    """Classify a round-table verdict into {buy, sell, no_trade} and count it (fail-safe).

    A vetoed decision (``approved is False``) is always NO-TRADE. Otherwise the score is
    mapped against SIGNAL_BUY_THRESHOLD / SIGNAL_SELL_THRESHOLD (the same L27-28 boundaries
    ``_score_to_signal`` uses): > BUY → buy, < SELL → sell, else HOLD → no_trade. The whole
    body is guarded so it can NEVER raise into the calling decision path.
    """
    try:
        if not approved:
            bucket = "no_trade"
        elif score > SIGNAL_BUY_THRESHOLD:
            bucket = "buy"
        elif score < SIGNAL_SELL_THRESHOLD:
            bucket = "sell"
        else:
            bucket = "no_trade"
        _bump_outcome(bucket)
    except Exception:  # noqa: BLE001 — observation must never touch the decision
        pass


def get_decision_counters() -> "dict[str, dict[str, int]]":
    """Read-only snapshot of the consensus-outcome distribution (defensive copy)."""
    return {"consensus_outcomes": dict(_CONSENSUS_OUTCOMES)}


def reset_decision_counters() -> None:
    """Test/daily-reset helper — zeroes the consensus-outcome counter."""
    _CONSENSUS_OUTCOMES.update({"buy": 0, "sell": 0, "no_trade": 0})


# Pydantic V2 mit Fallback auf V1 oder None
try:
    from pydantic import BaseModel, Field

    class VoteResultValidator(BaseModel):
        """Pydantic V2 validation model for VoteResult (ultra-fast, __slots__-compatible)."""

        agent_name: str
        score: float = Field(ge=0.0, le=1.0)
        weight: float = Field(gt=0.0)
        vetoed: bool

    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False
    VoteResultValidator = None  # type: ignore[assignment,misc]


# #3084: THE one exclusion list for the directional mean. These agents vote
# (their VoteResult stays validator-compatible with weight > 0 so the agent-veto
# path keeps seeing them), but their score NEVER enters the weighted consensus —
# DrawdownGuard acts via VETO, RegimeDetection via weight-coupling. The runner's
# record serialization imports THIS constant to stamp their audit records with
# weight 0.0 + role, so no reader mistakes the inert class-weight (0.60/0.50)
# for a directional vote again (#1993, AGENT_CATALOG.md:158).
NON_DIRECTIONAL_AGENTS = ("RegimeDetectionAgent", "DrawdownGuardAgent")


def consensus_exclusions(iv_forecast_enabled: bool) -> tuple:
    """#3094 (a): the agents excluded from the weighted consensus (direction) mean.

    Base set = NON_DIRECTIONAL_AGENTS (conditioners, never in the mean). When
    IMPLIED_VOL_FORECAST_ENABLED is on, VIXAwareRiskAgent joins them: its per-name
    implied vol is redirected to the size-scaler (runner), so its directionless
    vote must leave the DIRECTION mean. Flag off → base set, byte-identical.
    """
    if iv_forecast_enabled:
        return NON_DIRECTIONAL_AGENTS + ("VIXAwareRiskAgent",)
    return NON_DIRECTIONAL_AGENTS


class CoverageMetric(float):
    """#3405: Float subclass carrying per-agent coverage breakdown."""

    breakdown: dict

    def __new__(cls, value: float, breakdown: dict | None = None):
        obj = super().__new__(cls, value)
        obj.breakdown = breakdown or {}
        return obj


def vote_coverage(valid_votes: list, armed_weights: dict) -> "CoverageMetric | None":
    """#3210 / #3405: fraction of the ARMED directional vote weight that actually voted.

    ``armed_weights`` maps ``agent_name -> configured weight`` for the agents that
    (a) enter the direction mean (NOT in ``consensus_exclusions()``) AND (b) are
    armed (enabled + configured weight > 0). Structurally muted agents (RL,
    UpsideSkew) are absent by construction, so they never drag the denominator.

        coverage = Σ weight(directional votes cast this round, weight>0, not vetoed)
                   ─────────────────────────────────────────────────────────────
                              Σ configured weight of the armed set

    A directional agent that ABSTAINS this round (weight 0 in its VoteResult — no
    market data OR "research report not ready") is in the denominator but not the
    numerator, so its missing input lowers coverage regardless of the cause.

    A disabled agent (abstain_reason == "disabled") is excluded from both numerator
    and denominator (#3405).

    Returns ``None`` when the denominator is ≤ 0 (no armed directional agents) —
    a FAIL-OPEN no-op so the sizer/audit discount stays 1.0. PURE (plain data in).
    """
    votes_by_agent = {}
    disabled_agents = set()
    for v in valid_votes:
        name = getattr(v, "agent_name", None)
        if name:
            votes_by_agent[name] = v
            reason = getattr(v, "abstain_reason", None)
            reason_val = getattr(reason, "value", reason)
            if reason_val and str(reason_val).upper() == "DISABLED":
                disabled_agents.add(name)

    den = sum(
        w
        for name, w in armed_weights.items()
        if name not in disabled_agents and w and w > 0.0
    )
    if den <= 0.0:
        return None

    num = 0.0
    breakdown = {}
    for name, _ in armed_weights.items():
        v = votes_by_agent.get(name)
        vetoed = bool(getattr(v, "vetoed", False)) if v else False
        vote_w = float(getattr(v, "weight", 0.0) or 0.0) if v else 0.0
        reason = getattr(v, "abstain_reason", None) if v else None
        reason_val = getattr(reason, "value", reason) if reason is not None else None

        has_data = vote_w > 0.0 and not vetoed and reason_val is None
        if has_data and name not in disabled_agents:
            num += vote_w

        breakdown[name] = {
            "has_data": has_data,
            "abstain_reason": reason_val,
        }

    for name, v in votes_by_agent.items():
        if name not in breakdown:
            vetoed = bool(getattr(v, "vetoed", False))
            vote_w = float(getattr(v, "weight", 0.0) or 0.0)
            reason = getattr(v, "abstain_reason", None)
            reason_val = (
                getattr(reason, "value", reason) if reason is not None else None
            )
            has_data = vote_w > 0.0 and not vetoed and reason_val is None
            breakdown[name] = {
                "has_data": has_data,
                "abstain_reason": reason_val,
            }

    return CoverageMetric(num / den, breakdown=breakdown)


class ConsensusEngine:
    """
    Aggregates VoteResult lists into a weighted consensus score.

    Methods:
        aggregate(votes) → float: Weighted average (vetoed votes excluded)
        check_distribution(votes) → (bool, str): AI Security signal integrity check
        rank(symbol_scores) → list[str]: Symbols sorted by score descending

    Pydantic V2 validates each VoteResult before aggregation (score ∈ [0,1], weight > 0).
    """

    # #3618: audit of the last aggregate() call's risk-off conditioning
    # ({severity, factor, undamped_consensus, consensus}); None = no damping.
    last_conditioning: dict | None = None

    def _validate_vote(self, vote: "VoteResult | SignalCandidate") -> bool:
        """
        Validates a single VoteResult or SignalCandidate.
        With Pydantic V2: strict validation. Without: manual check.
        Returns False if invalid (vote is excluded).
        """
        # If it's a SignalCandidate that explicitly abstains, it's valid but excluded.
        if hasattr(vote, "abstain_reason") and vote.abstain_reason is not None:
            logger.debug(
                "ConsensusEngine: %s abstained (%s) — excluded from consensus.",
                vote.agent_name,
                vote.abstain_reason,
            )
            return False

        # Legacy VoteResult abstention
        if vote.weight == 0.0 and not vote.vetoed:
            logger.debug(
                "ConsensusEngine: %s abstained (weight 0) — excluded from consensus.",
                vote.agent_name,
            )
            return False

        if _PYDANTIC_AVAILABLE and VoteResultValidator is not None:
            try:
                VoteResultValidator(
                    agent_name=vote.agent_name,
                    score=getattr(
                        vote, "score", 0.5
                    ),  # SignalCandidate has optional score, but if we reach here, it shouldn't be abstaining
                    weight=vote.weight,
                    vetoed=vote.vetoed,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "ConsensusEngine: Vote validation failed for %s: %s",
                    vote.agent_name,
                    exc,
                )
                return False
        else:
            # Manual fallback validation
            score = getattr(vote, "score", 0.5)
            if score is None:
                score = 0.5
            return 0.0 <= score <= 1.0 and vote.weight > 0.0

    def aggregate(self, votes: list["VoteResult"]) -> float:
        """
        Calculates the weighted consensus score.

        Excludes vetoed and invalid votes.
        Returns 0.0 if no active votes are present.

        Args:
            votes: List of VoteResult (from all agents)

        Returns:
            Weighted score ∈ [0.0, 1.0]
        """
        # #3618: per-call conditioning audit (None = no damping this call).
        self.last_conditioning = None
        _damp_factor = None
        _damp_severity = 0.0
        if not votes:
            logger.debug("ConsensusEngine: Empty vote list → 0.0")
            return 0.0

        validated_votes = [
            v
            for v in votes
            if v is not None
            and not getattr(v, "vetoed", False)
            and self._validate_vote(v)
        ]

        regime_vote = next(
            (v for v in validated_votes if v.agent_name == "RegimeDetectionAgent"), None
        )
        # #1968 (RT-BUG-2): with MOMENTUM_FALLBACK_ABSTAIN_ENABLED an insufficient-
        # history Momentum vote abstains (score 0.5, weight 0.0) and is already
        # filtered out of validated_votes by _validate_vote above — the bearish-regime
        # halving below therefore only ever touches a REAL 12-1M momentum vote, never
        # the regime's own one-bar echo (breaks the self-referential weight coupling).
        # #1949 (RTR-2): an abstaining conditioner (score 0.5, weight 0.0) is filtered
        # the same way — no data, no conditioning (fail-open).
        _regime_cond_enabled, _regime_damp_max = _regime_conditioner_fusion_cfg()
        if not _regime_cond_enabled:
            # Flag OFF (default): the old binary Momentum-only block — byte-identical.
            if regime_vote is not None and regime_vote.score < 0.45:
                for v in validated_votes:
                    if v.agent_name == "MomentumAgent":
                        v.weight *= 0.5
                        logger.debug(
                            "ConsensusEngine: Bearish regime detected. Scaled down MomentumAgent weight by 50%%."
                        )
        elif regime_vote is not None:
            # #1949 flag ON / #3618: gradual risk-off conditioning of the WHOLE
            # direction mean. severity 0 at neutral/bullish regime (score ≥ 0.5) →
            # factor 1 (no-op); severity 1 at full risk-off (score 0) → 1-damp_max.
            # Applied below on the consensus (== shrinking every remaining score by
            # the same factor with the weights untouched). Scaling the WEIGHTS of all
            # voters would cancel out of the weighted mean — the old 5-name list only
            # worked by tilting the mean toward the undamped voices.
            severity = max(0.0, min(1.0, (0.5 - regime_vote.score) / 0.5))
            if severity > 0.0:
                _damp_factor = 1.0 - _regime_damp_max * severity
                _damp_severity = severity

        # #3094 (a): when IV drives the size-scaler, VIXAware also leaves the
        # direction mean. Fail-safe: any config read error — flag off (byte-identical).
        try:
            _iv_forecast = bool(
                getattr(config.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", False)
            )
        except Exception:  # noqa: BLE001 — a config read must never break aggregation
            logger.exception(
                "Fehler beim Lesen von IMPLIED_VOL_FORECAST_ENABLED in aggregate"
            )
            _iv_forecast = False
        _excluded = consensus_exclusions(_iv_forecast)

        active_votes = [
            v
            for v in validated_votes
            if v.agent_name not in _excluded and v.weight > 0.0
        ]

        if not active_votes:
            logger.warning("ConsensusEngine: All votes vetoed or invalid → 0.0")
            return 0.0

        weighted_sum = sum(v.score * v.weight for v in active_votes)
        weight_total = sum(v.weight for v in active_votes)

        if weight_total <= 0:
            return 0.0

        result = weighted_sum / weight_total
        if _damp_factor is not None and result > 0.5:
            # #3618: upward-only shrink toward neutral. A BUY-leaning consensus loses
            # conviction in risk-off (0.72 → 0.61 at factor 0.5 = NO-TRADE); a
            # SELL-leaning consensus is left alone — exits must not get rarer under
            # stress (capital preservation). Audit: factor + undamped value are kept
            # on the engine for the runner (decision_outcomes.regime_damp_factor /
            # consensus_undamped).
            undamped = result
            result = 0.5 + _damp_factor * (result - 0.5)
            self.last_conditioning = {
                "severity": _damp_severity,
                "factor": _damp_factor,
                "undamped_consensus": undamped,
                "consensus": result,
            }
            logger.debug(
                "ConsensusEngine[#3618]: risk-off (severity=%.2f) — consensus "
                "shrunk ×%.3f toward neutral: %.4f → %.4f",
                _damp_severity,
                _damp_factor,
                undamped,
                result,
            )
        logger.debug(
            "ConsensusEngine: %d/%d active votes, weighted_score=%.4f",
            len(active_votes),
            len(votes),
            result,
        )
        return result

    def check_distribution(self, votes: list["VoteResult"]) -> tuple[bool, str]:
        """
        AI Security Control: Statistical anomaly detection on agent vote distributions.

        Closes D6 compliance gap (Security Governance Audit 2026-05-12):
        The ComplianceGatekeeper only checks Execution Risk (order value, PDT, wash trade).
        This method adds Signal Integrity checking — detecting suspiciously uniform votes
        that may indicate correlated data poisoning or feed manipulation.

        Algorithm (Stufe A — stdlib only, no new dependencies):
          1. Exclude vetoed votes (ComplianceGatekeeper decisions must not skew check).
          2. If fewer than 3 active votes: insufficient data, return pass with note.
          3. Compute std_dev and mean of active vote scores.
          4. HIGH_CORRELATION alert if:
             - std_dev < STD_DEV_MIN_THRESHOLD (suspiciously uniform)
             - AND mean is in BUY (>0.65) or SELL (<0.35) territory
             - HOLD zone (0.35–0.65) is exempt: agreement on neutrality is legitimate.

        ADR-SEC-01: MiFID II Art. 17 — pre-trade controls must cover signal integrity.
        Policy: CODING_POLICY.md §1 Compliance-First

        Args:
            votes: List of VoteResult (may include vetoed votes — they are excluded).

        Returns:
            (True, "ok")                   — distribution is normal
            (True, "insufficient_votes")   — fewer than 3 active votes, cannot check
            (False, "HIGH_CORRELATION: …") — suspicious uniformity detected
        """
        # Use module-level constants (ADR-SEC-01) — single source of truth.
        # See SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD, STD_DEV_UNIFORMITY_THRESHOLD
        # defined at module top-level. runner.py imports these instead of duplicating.
        _STD_DEV_MIN_THRESHOLD = STD_DEV_UNIFORMITY_THRESHOLD
        _BUY_THRESHOLD = SIGNAL_BUY_THRESHOLD
        _SELL_THRESHOLD = SIGNAL_SELL_THRESHOLD

        # Step 1: Exclude vetoed votes AND abstentions.
        #
        # ADR-SEC-01: this control asks whether the agents' OPINIONS are suspiciously
        # uniform. An abstention is score=0.5 at weight=0.0 by contract — the absence
        # of an opinion, not a neutral one. Left in the sample it is a CONSTANT, and
        # every constant pushes an extreme cluster's std_dev UP: reaching extreme
        # territory (mean > BUY / < SELL) requires the real votes to pull away from
        # 0.5, which only widens the spread. So each abstention drags the control
        # further from firing, and enough of them make it mathematically unreachable
        # — a poisoning alert that can never sound.
        #
        # Verified: 9 unanimous poisoned votes fire HIGH_CORRELATION; the same 9 plus
        # two permanently-abstaining modules return "ok".
        #
        # aggregate() (:180) has always filtered `v.weight > 0.0`. This makes the
        # integrity control agree with the aggregator: a vote nobody weighs is not
        # evidence — in either direction.
        active_votes = [
            v
            for v in votes
            if v is not None
            and not getattr(v, "vetoed", False)
            and self._validate_vote(v)
        ]

        # Step 2: Quorum check
        if len(active_votes) < 3:
            return True, "insufficient_votes_for_distribution_check"

        # Step 3: Compute distribution
        scores = [v.score for v in active_votes]
        try:
            std_dev = statistics.stdev(scores)
            mean = statistics.mean(scores)
        except statistics.StatisticsError as exc:
            logger.warning(
                "ConsensusEngine.check_distribution: statistics error: %s", exc
            )
            return True, "statistics_error"

        # Step 4: HIGH_CORRELATION check (only in extreme territory)
        in_extreme_territory = mean > _BUY_THRESHOLD or mean < _SELL_THRESHOLD
        if std_dev < _STD_DEV_MIN_THRESHOLD and in_extreme_territory:
            reason = (
                f"HIGH_CORRELATION: std_dev={std_dev:.4f} < {_STD_DEV_MIN_THRESHOLD} "
                f"mean={mean:.4f} n={len(active_votes)} "
                f"(BUY_THRESH={_BUY_THRESHOLD} SELL_THRESH={_SELL_THRESHOLD}) "
                "— suspiciously uniform agent votes in extreme territory. "
                "Possible correlated data poisoning."
            )
            logger.warning(
                "AI_SECURITY[ConsensusEngine]: %s",
                reason,
            )
            return False, reason

        return True, "ok"

    def rank(self, symbol_scores: dict[str, float]) -> list[str]:
        """
        Sorts symbols by their consensus score descending.

        Args:
            symbol_scores: {symbol → consensus_score}

        Returns:
            List of symbols, highest score first.
        """
        if not symbol_scores:
            return []
        return sorted(symbol_scores, key=lambda s: symbol_scores[s], reverse=True)
