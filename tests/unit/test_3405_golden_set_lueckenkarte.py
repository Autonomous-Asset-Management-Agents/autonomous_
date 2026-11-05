import pytest

from core.contracts.signal_candidate import AbstainReason
from core.round_table.consensus import CoverageMetric, vote_coverage

pytestmark = [pytest.mark.vc6]


class DummyVote:
    def __init__(self, agent_name, weight, vetoed=False, abstain_reason=None):
        self.agent_name = agent_name
        self.weight = weight
        self.vetoed = vetoed
        self.abstain_reason = abstain_reason


def test_3405_coverage_agent_breakdown():
    """
    TDD Step 2: Test, der die gemessene Abdeckung je Agent gegen die hinterlegte Erwartung prüft.
    """
    votes = [
        DummyVote("FundamentalsAgent", 0.35, abstain_reason=None),
        DummyVote("LSTMSignalAgent", 0.0, abstain_reason=AbstainReason.NO_DATA),
    ]
    armed_weights = {
        "FundamentalsAgent": 0.35,
        "LSTMSignalAgent": 0.40,
    }

    cov = vote_coverage(votes, armed_weights)
    assert cov is not None
    assert isinstance(cov, CoverageMetric)
    assert cov == pytest.approx(0.35 / 0.75)

    assert cov.breakdown["FundamentalsAgent"]["has_data"] is True
    assert cov.breakdown["FundamentalsAgent"]["abstain_reason"] is None

    assert cov.breakdown["LSTMSignalAgent"]["has_data"] is False
    assert (
        cov.breakdown["LSTMSignalAgent"]["abstain_reason"]
        == AbstainReason.NO_DATA.value
    )


def test_3405_disabled_agent_ignored():
    """
    TDD Step 3: Test, dass ein stillgelegter Agent weder Zähler noch Nenner berührt.
    """
    votes = [
        DummyVote("FundamentalsAgent", 0.35, abstain_reason=None),
        DummyVote("NewsSentimentAgent", 0.0, abstain_reason=AbstainReason.DISABLED),
    ]
    armed_weights = {
        "FundamentalsAgent": 0.35,
        "NewsSentimentAgent": 0.15,
    }

    cov = vote_coverage(votes, armed_weights)
    assert cov is not None

    # Der Nenner sollte nur 0.35 sein, nicht 0.50!
    assert cov == pytest.approx(0.35 / 0.35)
    assert cov == 1.0

    assert cov.breakdown["NewsSentimentAgent"]["has_data"] is False
    assert (
        cov.breakdown["NewsSentimentAgent"]["abstain_reason"]
        == AbstainReason.DISABLED.value
    )
