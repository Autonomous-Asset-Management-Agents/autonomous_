import allure
import pytest

from core.orchestration.graph import SymbolEvalState
from core.round_table.base_agent import VoteResult as AgentResult
from core.round_table.runner import _score_to_signal


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
def test_score_to_signal_populates_price():
    """Verify that current_price is extracted from state and passed to DecisionContext."""
    state = {
        "symbol": "AAPL",
        "ohlc": {
            "close": 150.0,
            "open": 149.0,
            "high": 151.0,
            "low": 148.0,
            "volume": 1000,
        },
        "current_time": "2026-04-01T10:00:00Z",
    }

    votes = [
        AgentResult(
            agent_name="Test", symbol="AAPL", score=0.8, weight=1.0, reasoning="Bullish"
        )
    ]

    signal = _score_to_signal(state, 0.8, votes)

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.decision_context.symbol == "AAPL"
    # This is expected to FAIL if the fix is not applied correctly
    assert signal.decision_context.current_price == 150.0
    assert "RoundTableV2" in signal.decision_context.reasoning_summary
