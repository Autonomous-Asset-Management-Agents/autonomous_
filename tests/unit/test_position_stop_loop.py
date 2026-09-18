"""#2066 Increment 2 — pre-loop per-position stop hook.

Tests TradingLoopMixin._run_position_stop_checks: it fetches held positions, dispatches a
SELL SignalEvent (triggered_by_stop) through the compliance-gated executor for each stop,
and returns the set of stopped symbols to exclude from the consensus that cycle.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest

from core.engine.trading_loop import TradingLoopMixin

_NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _pos(symbol, qty, avg, current):
    return SimpleNamespace(
        symbol=symbol, qty=qty, avg_entry_price=avg, current_price=current
    )


def _make_engine(positions, trade_history):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.api = MagicMock()
    eng.api.get_all_positions = MagicMock(return_value=positions)
    eng.active_strategy = SimpleNamespace(
        portfolio_manager=SimpleNamespace(_trade_history=trade_history)
    )
    eng._process_signal_event = AsyncMock()
    return eng


@allure.feature("VC-3 Trading & Execution")
@allure.story("Per-Position Stop-Loss")
class TestPreLoopStopHook:
    @pytest.mark.anyio
    async def test_dispatches_sell_for_loser_and_returns_symbol(self):
        positions = [
            _pos("HOOD", 381.0, 138.0, 110.0),  # -20% → hard stop
            _pos("AAPL", 10.0, 100.0, 115.0),  # winner → no stop
        ]
        th = {
            "HOOD": [_NOW - timedelta(hours=1)],
            "AAPL": [_NOW - timedelta(hours=1)],
        }
        eng = _make_engine(positions, th)

        stopped = await eng._run_position_stop_checks()

        assert stopped == {"HOOD"}
        eng._process_signal_event.assert_awaited_once()
        event = eng._process_signal_event.call_args[0][0]
        assert event.symbol == "HOOD"
        assert event.action == "SELL"
        assert event.decision_context.triggered_by_stop is True
        assert event.decision_context.position_qty == 381.0
        assert event.decision_context.stop_type  # non-empty

    @pytest.mark.anyio
    async def test_no_stop_dispatches_nothing(self):
        eng = _make_engine(
            [_pos("AAPL", 10.0, 100.0, 115.0)],  # winner
            {"AAPL": [_NOW - timedelta(hours=1)]},
        )
        stopped = await eng._run_position_stop_checks()
        assert stopped == set()
        eng._process_signal_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_fetch_failure_is_failsafe(self):
        eng = _make_engine([], {})
        eng.api.get_all_positions = MagicMock(side_effect=RuntimeError("boom"))
        stopped = await eng._run_position_stop_checks()
        assert stopped == set()
        eng._process_signal_event.assert_not_awaited()


@allure.feature("VC-3 Trading & Execution")
@allure.story("#3180 consensus retention gate — position-stop OPINION exits")
class TestPositionStopConsensusGate:
    """An OPINION exit (take-profit / weighted-composite) that leaks through the
    position-stop pass is gate-eligible; RISK stops (hard/trailing/loss) never are.
    Default OFF ⇒ byte-identical; fail-open on a missing consensus."""

    def _plan(self, symbol, tier, current):
        return {
            "symbol": symbol,
            "qty": 10.0,
            "avg_entry_price": 100.0,
            "current_price": current,
            "reason": f"{tier} exit",
            "tier": tier,
        }

    @pytest.mark.anyio
    async def test_opinion_exit_suppressed_when_consensus_high(self, monkeypatch):
        import core.engine.trading_loop as tl

        eng = _make_engine(
            [_pos("AAPL", 10.0, 100.0, 130.0)],
            {"AAPL": [_NOW - timedelta(hours=5)]},
        )
        eng.active_strategy.portfolio_manager.get_live_consensus = lambda s: 0.85
        monkeypatch.setattr(
            tl,
            "plan_position_stops",
            lambda *a, **k: [self._plan("AAPL", "opinion", 130.0)],
        )
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        stopped = await eng._run_position_stop_checks()
        assert stopped == set()  # opinion take-profit RETAINED
        eng._process_signal_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_risk_stop_never_gated(self, monkeypatch):
        import core.engine.trading_loop as tl

        eng = _make_engine(
            [_pos("HOOD", 10.0, 140.0, 110.0)],
            {"HOOD": [_NOW - timedelta(hours=5)]},
        )
        eng.active_strategy.portfolio_manager.get_live_consensus = lambda s: 0.99
        monkeypatch.setattr(
            tl,
            "plan_position_stops",
            lambda *a, **k: [self._plan("HOOD", "risk", 110.0)],
        )
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        stopped = await eng._run_position_stop_checks()
        assert stopped == {"HOOD"}  # hard stop fires even at max consensus
        eng._process_signal_event.assert_awaited_once()

    @pytest.mark.anyio
    async def test_gate_off_default_opinion_exit_fires(self, monkeypatch):
        import core.engine.trading_loop as tl

        eng = _make_engine(
            [_pos("AAPL", 10.0, 100.0, 130.0)],
            {"AAPL": [_NOW - timedelta(hours=5)]},
        )
        eng.active_strategy.portfolio_manager.get_live_consensus = lambda s: 0.99
        monkeypatch.setattr(
            tl,
            "plan_position_stops",
            lambda *a, **k: [self._plan("AAPL", "opinion", 130.0)],
        )
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.0)

        stopped = await eng._run_position_stop_checks()
        assert stopped == {"AAPL"}  # gate OFF → byte-identical, fires
        eng._process_signal_event.assert_awaited_once()
