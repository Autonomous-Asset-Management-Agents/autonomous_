# tests/unit/test_round_table_consensus.py
# Epic 2.5 / Issue I-3 — TDD
# ConsensusEngine: Gewichtete Aggregation + Pydantic V2 Validierung

from __future__ import annotations

import allure
import pytest


def make_vote(agent_name: str, score: float, weight: float, vetoed: bool = False):
    from core.round_table.base_agent import VoteResult

    return VoteResult(
        agent_name=agent_name,
        symbol="AAPL",
        score=score,
        weight=weight,
        reasoning=f"Test reasoning for {agent_name}",
        vetoed=vetoed,
    )


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestConsensusImports:
    def test_consensus_engine_importable(self):
        from core.round_table.consensus import ConsensusEngine  # noqa: F401

        assert ConsensusEngine is not None

    def test_pydantic_validator_importable(self):
        from core.round_table.consensus import VoteResultValidator  # noqa: F401


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestConsensusAggregation:
    def test_aggregate_two_votes(self):
        """Gewichteter Durchschnitt von 2 Votes."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("AgentA", 0.8, 0.6),
            make_vote("AgentB", 0.4, 0.4),
        ]
        # Erwartet: (0.8*0.6 + 0.4*0.4) / (0.6+0.4) = (0.48+0.16)/1.0 = 0.64
        result = engine.aggregate(votes)
        assert abs(result - 0.64) < 1e-6, f"Aggregation falsch: {result}"

    def test_aggregate_returns_zero_for_empty(self):
        """Leere Liste → 0.0."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        assert engine.aggregate([]) == 0.0

    def test_aggregate_excludes_vetoed(self):
        """Veto'd Votes werden aus der Aggregation ausgeschlossen."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("Good", 0.8, 0.6, vetoed=False),
            make_vote("Vetoed", 0.0, 0.4, vetoed=True),  # Dieser soll ignoriert werden
        ]
        result = engine.aggregate(votes)
        # Nur Good zählt → score=0.8
        assert (
            abs(result - 0.8) < 1e-6
        ), f"Veto'd Vote soll ausgeschlossen sein: {result}"

    def test_aggregate_all_vetoed_returns_zero(self):
        """Alle Votes veto'd → 0.0."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("A", 0.9, 0.5, vetoed=True),
            make_vote("B", 0.8, 0.5, vetoed=True),
        ]
        assert engine.aggregate(votes) == 0.0


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestConsensusValidation:
    def test_invalid_score_rejected(self):
        """Pydantic V2: score > 1.0 → Vote ungültig (wird ausgeschlossen)."""
        from core.round_table.consensus import ConsensusEngine, VoteResultValidator

        if VoteResultValidator is None:
            pytest.skip("Pydantic nicht verfügbar")

        from pydantic import ValidationError

        with pytest.raises((ValidationError, ValueError)):
            VoteResultValidator(
                agent_name="BadAgent",
                score=1.5,  # > 1.0 → invalid
                weight=0.5,
                vetoed=False,
            )

    def test_valid_score_passes(self):
        """Score ∈ [0,1] und weight > 0 → gültig."""
        from core.round_table.consensus import VoteResultValidator

        if VoteResultValidator is None:
            pytest.skip("Pydantic nicht verfügbar")
        v = VoteResultValidator(agent_name="Good", score=0.75, weight=0.5, vetoed=False)
        assert v.score == 0.75


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestConsensusRank:
    def test_rank_descending(self):
        """rank() gibt Symbole nach Score absteigend zurück."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        scores = {"AAPL": 0.8, "TSLA": 0.3, "MSFT": 0.9, "NVDA": 0.6}
        ranked = engine.rank(scores)
        assert ranked == ["MSFT", "AAPL", "NVDA", "TSLA"]

    def test_rank_empty_returns_empty(self):
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        assert engine.rank({}) == []
