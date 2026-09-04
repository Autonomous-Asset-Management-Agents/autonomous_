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

# #1949 (RTR-2): the directional voters that remain in the weighted mean and are
# scaled by the regime conditioner in risk-off. Deliberately EXCLUDES the
# conditioners (RegimeDetection/DrawdownGuard — never in the mean) and the
# non-directional risk/context voices (VIXAware/NewsSentiment/Fundamentals/
# Valuation). NOTE: the #1949 plan names "RLExecutionAgent" — the actual roster
# name is RLConfidenceAgent (agents.py), documented deviation.
_DIRECTIONAL_VOTER_NAMES = (
    "MomentumAgent",
    "LSTMSignalAgent",
    "RLConfidenceAgent",
    "SpecialistAlphaAgent",
    # #3095 (b): UpsideSkewAgent is directional (25Δ risk-reversal) → damped in
    # risk-off like the other direction voters. Inert until registered (Step-2).
    "UpsideSkewAgent",
)


def _regime_conditioner_fusion_cfg() -> "tuple[bool, float]":
    """#1949 (RTR-2): (enabled, riskoff_damp_max) for the fusion-side conditioning.

    Flag OFF keeps the old binary block (regime score < 0.45 → MomentumAgent
    weight ×0.5) byte-identical. ON → gradual scaling of ALL directional voters:
    factor = 1 - damp_max * severity, severity = clamp((0.5 - score)/0.5, 0, 1).
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


def vote_coverage(valid_votes: list, armed_weights: dict) -> "float | None":
    """#3210: fraction of the ARMED directional vote weight that actually voted.

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

    Returns ``None`` when the denominator is ≤ 0 (no armed directional agents) —
    a FAIL-OPEN no-op so the sizer/audit discount stays 1.0. PURE (plain data in).
    """
    den = sum(w for w in armed_weights.values() if w and w > 0.0)
    if den <= 0.0:
        return None
    num = 0.0
    for v in valid_votes:
        name = getattr(v, "agent_name", None)
        if name in armed_weights and not getattr(v, "vetoed", False):
            w = float(getattr(v, "weight", 0.0) or 0.0)
            if w > 0.0:
                num += w
    return num / den


class ConsensusEngine:
    """
    Aggregates VoteResult lists into a weighted consensus score.

    Methods:
        aggregate(votes) → float: Weighted average (vetoed votes excluded)
        check_distribution(votes) → (bool, str): AI Security signal integrity check
        rank(symbol_scores) → list[str]: Symbols sorted by score descending

    Pydantic V2 validates each VoteResult before aggregation (score ∈ [0,1], weight > 0).
    """

    def _validate_vote(self, vote: "VoteResult") -> bool:
        """
        Validates a single VoteResult.
        With Pydantic V2: strict validation. Without: manual check.
        Returns False if invalid (vote is excluded).
        """
        # An ABSTENTION is not a malformed vote. The contract across the gremium is
        # "no data -> score 0.5 at weight 0.0", so a module with nothing to say is
        # excluded — correct — but calling it "invalid" and shouting about it at
        # WARNING is not: with ~500 symbols per cycle and several modules that
        # legitimately abstain (cold cache, missing prediction, no VIX), the real
        # WARNINGs drown in thousands of routine ones. Excluded either way; logged
        # for what it is. Anything ELSE malformed still warns loudly.
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
                    score=vote.score,
                    weight=vote.weight,
                    vetoed=vote.vetoed,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "ConsensusEngine: Vote from %s invalid (score=%.3f): %s",
                    vote.agent_name,
                    vote.score,
                    exc,
                )
                return False
        else:
            # Manual fallback validation
            return 0.0 <= vote.score <= 1.0 and vote.weight > 0.0

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
        if not votes:
            logger.debug("ConsensusEngine: Empty vote list → 0.0")
            return 0.0

        validated_votes = [v for v in votes if not v.vetoed and self._validate_vote(v)]

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
            # #1949 flag ON: gradual risk-off conditioning of ALL directional voters.
            # severity 0 at neutral/bullish regime (score ≥ 0.5) → factor 1 (no-op);
            # severity 1 at full risk-off (score 0) → factor 1-damp_max.
            severity = max(0.0, min(1.0, (0.5 - regime_vote.score) / 0.5))
            if severity > 0.0:
                factor = 1.0 - _regime_damp_max * severity
                for v in validated_votes:
                    if v.agent_name in _DIRECTIONAL_VOTER_NAMES:
                        v.weight *= factor
                        # Audit visibility (plan #1949 §12.4): which voter, which factor.
                        logger.debug(
                            "ConsensusEngine[#1949]: risk-off regime (score=%.3f, "
                            "severity=%.2f) — %s weight scaled ×%.3f → %.3f",
                            regime_vote.score,
                            severity,
                            v.agent_name,
                            factor,
                            v.weight,
                        )

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
        active_votes = [v for v in votes if not v.vetoed and v.weight > 0.0]

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
