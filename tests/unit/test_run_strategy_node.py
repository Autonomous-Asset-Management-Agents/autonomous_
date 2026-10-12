import asyncio
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.events import SignalEvent
from core.orchestration.graph import (
    _fetch_context_node,
    _process_signal_node,
    _run_strategy_node,
)


def _make_state(symbol="AAPL", error=None):
    return {
        "symbol": symbol,
        "ohlc": None,
        "current_time": None,
        "signal": None,
        "error": error,
    }


pytestmark = [pytest.mark.vc0]


@allure.story("Core Utilities")
class TestRunStrategyNodeFallback:

    @pytest.fixture(autouse=True)
    def _no_round_table(self):
        with patch("core.orchestration.graph._ROUND_TABLE_AVAILABLE", False):
            yield

    def test_skips_when_state_already_has_error(self):
        state = _make_state(error="upstream failure")
        result = asyncio.run(_run_strategy_node(state))
        assert result["error"] == "upstream failure"

    def test_fallback_returns_abstain_signal(self):
        state = _make_state("AAPL")
        result = asyncio.run(_run_strategy_node(state))

        assert result.get("error") is None
        assert isinstance(result["signal"], SignalEvent)
        assert result["signal"].action == "HOLD"
        assert (
            result["signal"].decision_context.reasoning_summary
            == "abstain:council_unavailable"
        )


@allure.story("Core Utilities")
class TestProcessSignalNode:

    def test_passes_signal_through(self):
        mock_signal = MagicMock(spec=SignalEvent)
        state = {**_make_state(), "signal": mock_signal}
        result = asyncio.run(_process_signal_node(state))
        assert result["signal"] is mock_signal

    def test_skips_on_error_state(self):
        state = _make_state(error="bad upstream")
        result = asyncio.run(_process_signal_node(state))
        assert result["error"] == "bad upstream"

    def test_none_signal_no_crash(self):
        state = _make_state()
        result = asyncio.run(_process_signal_node(state))
        assert result["signal"] is None
        assert result.get("error") is None


@allure.story("Core Utilities")
class TestFullGraphPipeline:

    @pytest.fixture(autouse=True)
    def _no_round_table(self):
        with patch("core.orchestration.graph._ROUND_TABLE_AVAILABLE", False):
            yield

    def test_pipeline_propagates_signal_end_to_end(self):
        state = _make_state("AAPL")

        async def _run():
            s = {**state, "ohlc": {}}  # mocked context
            s = await _run_strategy_node(s)
            s = await _process_signal_node(s)
            return s

        result = asyncio.run(_run())
        assert isinstance(result["signal"], SignalEvent)
        assert result["signal"].action == "HOLD"
        assert result.get("error") is None
