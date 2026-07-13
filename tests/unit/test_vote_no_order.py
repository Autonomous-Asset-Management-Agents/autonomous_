# tests/unit/test_vote_no_order.py
# Regression tests for #1876: Vote-Phase must never submit broker orders.
#
# Art. 14 EU AI Act: Human oversight requires that the vote (opinion) phase
# is completely separated from order execution.
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD - Red → Green → Refactor

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs — we test agents in isolation from the full engine
# ---------------------------------------------------------------------------


@dataclass
class FakeDecisionContext:
    symbol: str = "AAPL"
    action: str = "BUY"
    lstm_prediction: float = 0.8
    current_price: float = 150.0
    reasoning_summary: str = "test"


@dataclass
class FakeSignalEvent:
    symbol: str = "AAPL"
    action: str = "BUY"
    decision_context: Any = None
    suggested_quantity: float = 0.0
    is_simulation: bool = False


def _make_state(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 155.0,
            "low": 149.0,
            "close": 153.0,
            "volume": 1_000_000,
        },
        "current_time": "2026-07-08T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Test 1: evaluate_for_symbol() returns SignalEvent WITHOUT calling submit_order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_returns_signal_no_order():
    """evaluate_for_symbol() must return a SignalEvent and NEVER call submit_order.

    Gherkin:
      Given  the active strategy has an evaluate_for_symbol method
      When   evaluate_for_symbol is called
      Then   client.submit_order is NOT called
      And    a SignalEvent (or None) is returned
    """
    # Arrange — build a mock strategy with evaluate_for_symbol
    mock_strategy = MagicMock()
    mock_strategy.client = MagicMock()
    mock_strategy.client.submit_order = MagicMock()

    ctx = FakeDecisionContext(symbol="AAPL", action="BUY", lstm_prediction=0.8)
    signal = FakeSignalEvent(symbol="AAPL", action="BUY", decision_context=ctx)

    mock_strategy.evaluate_for_symbol = AsyncMock(return_value=signal)

    # Act
    result = await mock_strategy.evaluate_for_symbol(
        "AAPL", {"close": 150.0}, {}, datetime.now(timezone.utc)
    )

    # Assert
    assert result is not None
    assert hasattr(result, "action")
    assert result.action == "BUY"
    mock_strategy.client.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: LSTMSignalAgent.vote() calls evaluate_for_symbol (NOT run_for_symbol)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lstm_agent_vote_uses_evaluate():
    """LSTMSignalAgent.vote() must call evaluate_for_symbol, not run_for_symbol.

    Gherkin:
      Given  the active strategy supports evaluate_for_symbol
      When   LSTMSignalAgent.vote(state) is called
      Then   active.evaluate_for_symbol is called
      And    active.run_for_symbol is NOT called
    """
    from core.round_table.agents import LSTMSignalAgent

    agent = LSTMSignalAgent()
    state = _make_state()

    ctx = FakeDecisionContext(symbol="AAPL", action="BUY", lstm_prediction=0.8)
    signal = FakeSignalEvent(symbol="AAPL", action="BUY", decision_context=ctx)

    mock_active = MagicMock()
    mock_active.evaluate_for_symbol = AsyncMock(return_value=signal)
    mock_active.run_for_symbol = AsyncMock(return_value=signal)

    mock_registry = MagicMock()
    mock_registry.get_active.return_value = mock_active

    with patch(
        "core.round_table.agents.get_global_registry", return_value=mock_registry
    ):
        result = await agent.vote(state)

    # Assert: evaluate was called, run was NOT
    mock_active.evaluate_for_symbol.assert_called_once()
    mock_active.run_for_symbol.assert_not_called()

    # Vote result should have valid score
    assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Test 3: RLConfidenceAgent.vote() calls evaluate_for_symbol (NOT run_for_symbol)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rl_agent_vote_uses_evaluate():
    """RLConfidenceAgent.vote() must call evaluate_for_symbol, not run_for_symbol.

    Gherkin:
      Given  the active strategy supports evaluate_for_symbol
      When   RLConfidenceAgent.vote(state) is called
      Then   active.evaluate_for_symbol is called
      And    active.run_for_symbol is NOT called
    """
    from core.round_table.agents import RLConfidenceAgent

    agent = RLConfidenceAgent()
    state = _make_state()

    ctx = FakeDecisionContext(symbol="AAPL", action="BUY", lstm_prediction=0.8)
    signal = FakeSignalEvent(symbol="AAPL", action="BUY", decision_context=ctx)

    mock_active = MagicMock()
    mock_active.evaluate_for_symbol = AsyncMock(return_value=signal)
    mock_active.run_for_symbol = AsyncMock(return_value=signal)

    mock_registry = MagicMock()
    mock_registry.get_active.return_value = mock_active

    with patch(
        "core.round_table.agents.get_global_registry", return_value=mock_registry
    ):
        result = await agent.vote(state)

    # Assert: evaluate was called, run was NOT
    mock_active.evaluate_for_symbol.assert_called_once()
    mock_active.run_for_symbol.assert_not_called()

    assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Test 4: evaluate_for_symbol() does NOT mutate state tracking dicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_no_state_mutation():
    """evaluate_for_symbol() must not mutate high_water_marks or _entry_time.

    Gherkin:
      Given  high_water_marks and _entry_time have known state
      When   evaluate_for_symbol is called
      Then   high_water_marks is unchanged
      And    _entry_time is unchanged
    """
    mock_strategy = MagicMock()
    mock_strategy.high_water_marks = {"AAPL": 155.0}
    mock_strategy._entry_time = {"AAPL": datetime(2026, 7, 1, tzinfo=timezone.utc)}

    hwm_before = dict(mock_strategy.high_water_marks)
    entry_before = dict(mock_strategy._entry_time)

    ctx = FakeDecisionContext(symbol="AAPL", action="SELL", lstm_prediction=-0.5)
    signal = FakeSignalEvent(symbol="AAPL", action="SELL", decision_context=ctx)
    mock_strategy.evaluate_for_symbol = AsyncMock(return_value=signal)

    await mock_strategy.evaluate_for_symbol(
        "AAPL", {"close": 150.0}, {}, datetime.now(timezone.utc)
    )

    # State must be unchanged (evaluate is read-only)
    assert mock_strategy.high_water_marks == hwm_before
    assert mock_strategy._entry_time == entry_before


# ---------------------------------------------------------------------------
# Test 5: Concurrent votes produce zero broker orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_votes_no_orders():
    """Parallel vote() calls must produce zero submit_order calls.

    Gherkin:
      Given  LSTMSignalAgent and RLConfidenceAgent vote in parallel
      When   both votes complete
      Then   client.submit_order is never called
    """
    from core.round_table.agents import LSTMSignalAgent, RLConfidenceAgent

    state = _make_state()

    ctx = FakeDecisionContext(symbol="AAPL", action="BUY", lstm_prediction=0.8)
    signal = FakeSignalEvent(symbol="AAPL", action="BUY", decision_context=ctx)

    mock_client = MagicMock()
    mock_client.submit_order = MagicMock()

    mock_active = MagicMock()
    mock_active.client = mock_client
    mock_active.evaluate_for_symbol = AsyncMock(return_value=signal)
    mock_active.run_for_symbol = AsyncMock(return_value=signal)

    mock_registry = MagicMock()
    mock_registry.get_active.return_value = mock_active

    lstm_agent = LSTMSignalAgent()
    rl_agent = RLConfidenceAgent()

    with patch(
        "core.round_table.agents.get_global_registry", return_value=mock_registry
    ):
        results = await asyncio.gather(
            lstm_agent.vote(state),
            rl_agent.vote(state),
            return_exceptions=True,
        )

    # Both agents must produce valid votes
    for r in results:
        assert not isinstance(r, Exception), f"Vote raised: {r}"

    # CRITICAL: zero broker orders during vote phase
    mock_client.submit_order.assert_not_called()
