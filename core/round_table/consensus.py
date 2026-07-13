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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADR-SEC-01: Signal Integrity Thresholds (single source of truth)
# These mirror runner.py:_score_to_signal() BUY/SELL boundaries.
# Import these constants from runner.py instead of duplicating literals.
# Reviewed: 2026-05-12 (Security Governance Audit)
# ---------------------------------------------------------------------------

# BUY/SELL territory boundaries — must match runner.py:_score_to_signal()
SIGNAL_BUY_THRESHOLD: float = 0.65  # ADR-SEC-01: score > threshold → BUY territory
SIGNAL_SELL_THRESHOLD: float = 0.35  # ADR-SEC-01: score < threshold → SELL territory

# std_dev floor for HIGH_CORRELATION detection (ADR-SEC-01)
# Legitimate 9-agent debate on strong signal produces std_dev ≈ 0.08–0.15.
# std_dev < 0.03 in BUY/SELL territory is statistically implausible without
# a shared, potentially compromised data source.
STD_DEV_UNIFORMITY_THRESHOLD: float = 0.03  # ADR-SEC-01: threshold reviewed 2026-05-12

if TYPE_CHECKING:
    from core.round_table.base_agent import VoteResult

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
        if regime_vote is not None and regime_vote.score < 0.45:
            for v in validated_votes:
                if v.agent_name == "MomentumAgent":
                    v.weight *= 0.5
                    logger.debug(
                        "ConsensusEngine: Bearish regime detected. Scaled down MomentumAgent weight by 50%%."
                    )

        active_votes = [
            v
            for v in validated_votes
            if v.agent_name not in ("RegimeDetectionAgent", "DrawdownGuardAgent")
            and v.weight > 0.0
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

        # Step 1: Exclude vetoed votes
        active_votes = [v for v in votes if not v.vetoed]

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
