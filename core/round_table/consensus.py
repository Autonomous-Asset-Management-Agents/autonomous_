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
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from core.round_table.base_agent import VoteResult

# Pydantic V2 mit Fallback auf V1 oder None
try:
    from pydantic import BaseModel, Field

    class VoteResultValidator(BaseModel):
        """Pydantic V2 validation model for VoteResult (ultra-fast, __slots__-compatible)."""

        agent_name: str
        score: float = Field(ge=0.0, le=1.0)
        weight: float = Field(ge=0.0)
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
            return 0.0 <= vote.score <= 1.0 and vote.weight >= 0.0

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

        active_votes = [v for v in votes if not v.vetoed and self._validate_vote(v)]

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
