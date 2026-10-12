import pytest

from core.contracts.signal_candidate import AbstainReason, SignalCandidate


def test_signal_candidate_must_have_either_score_or_abstain():
    with pytest.raises(ValueError):
        SignalCandidate(
            agent_name="Test",
            symbol="AAPL",
            weight=1.0,
            reasoning="test",
            score=0.5,
            abstain_reason=AbstainReason.NO_DATA,
        )

    with pytest.raises(ValueError):
        SignalCandidate(agent_name="Test", symbol="AAPL", weight=1.0, reasoning="test")

    sc1 = SignalCandidate(
        agent_name="Test", symbol="AAPL", weight=1.0, reasoning="Bullish", score=0.8
    )
    assert sc1.score == 0.8
    assert sc1.abstain_reason is None

    sc2 = SignalCandidate(
        agent_name="Test",
        symbol="AAPL",
        weight=0.0,
        reasoning="No data",
        abstain_reason=AbstainReason.NO_DATA,
    )
    assert sc2.score is None
    assert sc2.abstain_reason == AbstainReason.NO_DATA


def test_signal_candidate_closed_reason_list():
    with pytest.raises(ValueError):
        SignalCandidate(
            agent_name="Test",
            symbol="AAPL",
            weight=0.0,
            reasoning="test",
            abstain_reason="INVALID_REASON",
        )


def test_signal_candidate_serialization():
    sc = SignalCandidate(
        agent_name="Test",
        symbol="AAPL",
        weight=1.0,
        score=0.8,
        reasoning="Bullish",
        data_source="Source",
    )
    d = sc.model_dump()
    assert d["agent_name"] == "Test"
    assert "abstain_reason" in d


pytestmark = pytest.mark.vc4
