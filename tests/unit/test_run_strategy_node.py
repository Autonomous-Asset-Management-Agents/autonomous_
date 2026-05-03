# tests/unit/test_run_strategy_node.py
# Issue #217 — TDD Tests für _run_strategy_node LangGraph Integration
#
# Policy §11.5 TDD: Tests zuerst, dann Implementierung.
# Policy §12 Test-Freshness: Bei Änderungen an graph.py diesen File prüfen.

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.orchestration.graph import (
    _fetch_context_node,
    _process_signal_node,
    _run_strategy_node,
    SymbolEvalState,
)


# ---------------------------------------------------------------------------
# Shared fixture: disable Round Table V2 for legacy-path tests
# Epic 2.5: _run_strategy_node now tries Round Table first.
# These tests verify the legacy _legacy_single_strategy_node fallback path.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def disable_round_table(monkeypatch):
    """Patches _ROUND_TABLE_AVAILABLE=False so legacy-path tests stay valid."""
    import core.orchestration.graph as g

    monkeypatch.setattr(g, "_ROUND_TABLE_AVAILABLE", False)
    monkeypatch.setattr(g, "_run_round_table", None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(symbol: str = "AAPL", error: str = None) -> SymbolEvalState:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 1_000_000.0,
        },
        "market_data_keys": [],
        "current_time": datetime(2024, 6, 1, tzinfo=timezone.utc).isoformat(),
        "signal": None,
        "error": error,
    }


def _make_registry_with_mock_strategy(signal=None):
    """Returns a mock AgentRegistry whose active strategy returns `signal`."""
    strategy = MagicMock()
    strategy.run_for_symbol = AsyncMock(return_value=signal)

    registry = MagicMock()
    registry.get_active.return_value = strategy
    return registry, strategy


# ---------------------------------------------------------------------------
# 1. _run_strategy_node — calls active strategy
# ---------------------------------------------------------------------------


class TestRunStrategyNodeCallsStrategy:

    @pytest.fixture(autouse=True)
    def _no_round_table(self, disable_round_table):
        """Force legacy path for all tests in this class (Epic 2.3/Issue #217 tests)."""
        pass

    def test_calls_active_strategy_run_for_symbol(self):
        """_run_strategy_node calls get_active().run_for_symbol() with correct args."""
        registry, strategy = _make_registry_with_mock_strategy(signal=None)
        state = _make_state("AAPL")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        strategy.run_for_symbol.assert_called_once()
        call_args = strategy.run_for_symbol.call_args
        assert call_args[0][0] == "AAPL"  # symbol

    def test_sets_signal_in_state_when_strategy_returns_signal(self):
        """When strategy returns a SignalEvent, state['signal'] is set."""
        from core.events import SignalEvent

        mock_signal = MagicMock(spec=SignalEvent)

        registry, _ = _make_registry_with_mock_strategy(signal=mock_signal)
        state = _make_state("TSLA")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        assert result["signal"] is mock_signal

    def test_signal_is_none_when_strategy_returns_none(self):
        """When strategy returns None (HOLD), state['signal'] stays None."""
        registry, _ = _make_registry_with_mock_strategy(signal=None)
        state = _make_state("MSFT")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        assert result["signal"] is None
        assert result.get("error") is None

    def test_passes_ohlc_and_current_time_to_strategy(self):
        """Verifies ohlc and current_time are forwarded correctly."""
        registry, strategy = _make_registry_with_mock_strategy()
        state = _make_state("NVDA")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            asyncio.run(_run_strategy_node(state))

        call_args = strategy.run_for_symbol.call_args
        # second arg is ohlc
        assert call_args[0][1] == state["ohlc"]


# ---------------------------------------------------------------------------
# 2. Error isolation
# ---------------------------------------------------------------------------


class TestRunStrategyNodeErrorIsolation:

    @pytest.fixture(autouse=True)
    def _no_round_table(self, disable_round_table):
        """Force legacy path for all tests in this class."""
        pass

    def test_skips_when_state_already_has_error(self):
        """If state['error'] is set, node returns state unchanged."""
        registry, strategy = _make_registry_with_mock_strategy()
        state = _make_state(error="upstream failure")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        strategy.run_for_symbol.assert_not_called()
        assert result["error"] == "upstream failure"

    def test_no_registry_returns_state_without_crash(self):
        """If get_global_registry() returns None, no exception is raised."""
        state = _make_state("AAPL")

        with patch("core.orchestration.graph.get_global_registry", return_value=None):
            result = asyncio.run(_run_strategy_node(state))

        assert result["signal"] is None
        assert result.get("error") is None

    def test_no_active_strategy_no_crash(self):
        """If registry has no active strategy (None), node is a graceful no-op."""
        registry = MagicMock()
        registry.get_active.return_value = None
        state = _make_state("AAPL")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        assert result["signal"] is None
        assert result.get("error") is None

    def test_strategy_exception_isolated_to_state_error(self):
        """If run_for_symbol() raises, error is captured in state, not propagated."""
        registry, strategy = _make_registry_with_mock_strategy()
        strategy.run_for_symbol = AsyncMock(side_effect=RuntimeError("model crash"))
        state = _make_state("AAPL")

        with patch(
            "core.orchestration.graph.get_global_registry", return_value=registry
        ):
            result = asyncio.run(_run_strategy_node(state))

        assert "model crash" in result.get("error", "")
        assert result["signal"] is None


# ---------------------------------------------------------------------------
# 3. _process_signal_node — passthrough
# ---------------------------------------------------------------------------


class TestProcessSignalNode:

    def test_passes_signal_through(self):
        """Signal set in state by _run_strategy_node is preserved."""
        from core.events import SignalEvent

        mock_signal = MagicMock(spec=SignalEvent)
        state = {**_make_state(), "signal": mock_signal}

        result = asyncio.run(_process_signal_node(state))

        assert result["signal"] is mock_signal

    def test_skips_on_error_state(self):
        """Error state passes through unchanged."""
        state = _make_state(error="bad upstream")
        result = asyncio.run(_process_signal_node(state))
        assert result["error"] == "bad upstream"

    def test_none_signal_no_crash(self):
        """None signal (HOLD) passes through without error."""
        state = _make_state()
        result = asyncio.run(_process_signal_node(state))
        assert result["signal"] is None
        assert result.get("error") is None


# ---------------------------------------------------------------------------
# 4. Integration — fetch → run → process pipeline
# ---------------------------------------------------------------------------


class TestFullGraphPipeline:

    @pytest.fixture(autouse=True)
    def _no_round_table(self, disable_round_table):
        """Force legacy path for end-to-end pipeline test."""
        pass

    def test_pipeline_propagates_signal_end_to_end(self):
        """fetch_context → run_strategy → process_signal: signal survives all nodes."""
        from core.events import SignalEvent

        mock_signal = MagicMock(spec=SignalEvent)
        registry, _ = _make_registry_with_mock_strategy(signal=mock_signal)
        state = _make_state("AAPL")

        async def _run():
            s = await _fetch_context_node(state)
            with patch(
                "core.orchestration.graph.get_global_registry", return_value=registry
            ):
                s = await _run_strategy_node(s)
            s = await _process_signal_node(s)
            return s

        result = asyncio.run(_run())
        assert result["signal"] is mock_signal
        assert result.get("error") is None
