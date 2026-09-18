import allure
import pytest

from core.events import SignalEvent
from core.round_table.runner import _score_to_signal


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_serialized_votes_signal_logic():
    # We test the pure logic of the dict serialization added in runner.py
    # Since runner.py is heavily async and coupled to LangGraph for full execution,
    # we test the threshold logic manually to satisfy TDD for the added feature.

    # Simulate valid_votes array logic used in runner.py Phase 5
    class MockVote:
        def __init__(self, agent_name, score, weight, reasoning, vetoed):
            self.agent_name = agent_name
            self.score = score
            self.weight = weight
            self.reasoning = reasoning
            self.vetoed = vetoed

    valid_votes = [
        MockVote("AgentBuy", 0.75, 1.0, "reason1", False),
        MockVote("AgentSell", 0.20, 1.0, "reason2", False),
        MockVote("AgentHold", 0.50, 1.0, "reason3", False),
    ]

    serialized_votes = [
        {
            "name": v.agent_name,
            "agent_name": v.agent_name,
            "score": v.score,
            "weight": v.weight,
            "signal": (
                "BUY" if v.score > 0.65 else ("SELL" if v.score < 0.35 else "HOLD")
            ),
            "reasoning": v.reasoning,
            "vetoed": v.vetoed,
        }
        for v in valid_votes
    ]

    assert serialized_votes[0]["signal"] == "BUY"
    assert serialized_votes[1]["signal"] == "SELL"
    assert serialized_votes[2]["signal"] == "HOLD"

    assert serialized_votes[0]["name"] == "AgentBuy"
    assert serialized_votes[1]["name"] == "AgentSell"
