# tests/unit/test_graceful_handover.py
# Epic 2.3-Pre / PR-A — TDD Red-Phase
# Graceful Handover: Cycle-Boundary-Swap, Position-Transfer
#
# Policy: Tests nutzen AgentRegistry + TradingLoopMixin via Mocks.
# Alle Tests ROT bis _perform_graceful_handover() in trading_loop.py existiert.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_with_registry(active_name="RLAgent", pending_name="LSTMDynamic"):
    """
    Minimaler BotEngine-Mock mit AgentRegistry.
    Umgeht echte Alpaca- und Redis-Verbindungen.
    """
    from core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    s_active = MagicMock()
    s_active.strategy_name = active_name
    s_active.symbols = ["AAPL", "MSFT"]

    s_pending = MagicMock()
    s_pending.strategy_name = pending_name
    s_pending.symbols = ["AAPL", "MSFT"]

    registry.register(active_name, s_active, set_active=True)
    registry.register(pending_name, s_pending, set_active=False)
    registry.swap(pending_name)

    engine = MagicMock()
    engine.agent_registry = registry
    engine._shutdown_event = MagicMock()
    engine._shutdown_event.is_set.return_value = False
    engine.api = MagicMock()
    engine.api.get_all_positions.return_value = [
        MagicMock(symbol="AAPL", qty="10", avg_entry_price="150.0"),
    ]
    engine._log_strategy_thought = MagicMock()

    return engine, registry, s_active, s_pending


# ---------------------------------------------------------------------------
# 1. Graceful Handover — Timing
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestGracefulHandoverTiming:
    @pytest.mark.anyio
    async def test_handover_does_not_occur_mid_cycle(self):
        """
        swap() setzt nur den Pending-Flag.
        _perform_graceful_handover() wird NICHT während des laufenden Zyklus aufgerufen.
        Es darf keine sofortige Strategie-Änderung geben.
        """
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = MagicMock(strategy_name="RLAgent")
        s2 = MagicMock(strategy_name="LSTMDynamic")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)

        # Swap aufrufen — darf die aktive Strategy NICHT sofort wechseln
        registry.swap("LSTMDynamic")

        assert (
            registry.get_active() is s1
        ), "Active strategy must not change before commit"

    @pytest.mark.anyio
    async def test_handover_commits_after_cycle_end(self):
        """
        commit_swap() nach Cycle-Ende wechselt die aktive Strategy.
        """
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = MagicMock(strategy_name="RLAgent")
        s2 = MagicMock(strategy_name="LSTMDynamic")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)
        registry.swap("LSTMDynamic")

        # Simuliert: Cycle endet, commit wird aufgerufen
        registry.commit_swap()

        assert registry.get_active() is s2


# ---------------------------------------------------------------------------
# 2. Graceful Handover — Position Transfer
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestGracefulHandoverPositionTransfer:
    @pytest.mark.anyio
    async def test_pending_positions_transferred_to_new_strategy(self):
        """
        _perform_graceful_handover() übergibt offene Positionen
        (als open_positions-Liste) an die neue Strategy.
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine, registry, s_active, s_pending = _make_engine_with_registry()

        # Direkt _perform_graceful_handover aufrufen
        await TradingLoopMixin._perform_graceful_handover(engine)

        # Neue Strategie sollte mit den offenen Positionen informiert worden sein
        s_pending.on_positions_received.assert_called_once()
        call_args = s_pending.on_positions_received.call_args[0][0]
        assert any(p.symbol == "AAPL" for p in call_args)

    @pytest.mark.anyio
    async def test_handover_logs_swap_event(self):
        """
        _perform_graceful_handover() loggt den Swap-Vorgang.
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine, registry, s_active, s_pending = _make_engine_with_registry()
        await TradingLoopMixin._perform_graceful_handover(engine)

        engine._log_strategy_thought.assert_called()
        logged_messages = [str(c) for c in engine._log_strategy_thought.call_args_list]
        assert any(
            "swap" in m.lower() or "handover" in m.lower() for m in logged_messages
        )


# ---------------------------------------------------------------------------
# 3. Exceptions in core/exceptions.py
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSwapExceptions:
    def test_swap_in_progress_error_exists(self):
        """SwapInProgressError muss in core/exceptions.py definiert sein."""
        from core.exceptions import SwapInProgressError

        assert issubclass(SwapInProgressError, Exception)

    def test_double_swap_raises_swap_in_progress_error(self):
        """swap() während eines laufenden Swaps wirft SwapInProgressError."""
        from core.agent_registry import AgentRegistry
        from core.exceptions import SwapInProgressError

        registry = AgentRegistry()
        s1 = MagicMock(strategy_name="RLAgent")
        s2 = MagicMock(strategy_name="LSTMDynamic")
        s3 = MagicMock(strategy_name="MLPAgent")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)
        registry.register("MLPAgent", s3, set_active=False)

        registry.swap("LSTMDynamic")  # Erster Swap — ok

        with pytest.raises(SwapInProgressError):
            registry.swap("MLPAgent")  # Zweiter Swap während pending — Fehler
