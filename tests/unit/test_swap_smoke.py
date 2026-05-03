# tests/unit/test_swap_smoke.py
# Epic 2.3 / I-7 — Swap Smoke Test: Synthetic Forward Pass
# Issue #243 — Ziel: Nach swap() + commit_swap() läuft run_for_symbol() ohne Exception.
#
# § 12 Test-Freshness: Bei Änderungen an agent_registry.py / lstm_strategy.py /
#   rl_strategy.py immer diesen File prüfen.
#
# Strategie (kein echtes Modell nötig):
#   LSTMDynamicStrategy.__init__ setzt self.torch_model = None,
#   _get_torch_prediction() gibt dann (0.0, None) zurück → sicherer No-Op Pfad.
#   run_for_symbol() muss dann vollständig durchlaufen ohne Exception.

import asyncio
import threading
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal mocked deps so strategies can be instantiated
# ---------------------------------------------------------------------------


def _make_mock_client():
    """Minimal IBKR/Alpaca-ähnlicher Client-Mock."""
    client = MagicMock()
    client.get_bars.return_value = pd.DataFrame()
    client.get_clock.return_value = MagicMock(is_open=False)
    return client


def _make_mock_data_provider():
    dp = MagicMock()
    dp.get_data.return_value = pd.DataFrame()
    return dp


def _make_mock_risk_manager():
    rm = MagicMock()
    rm.should_trade.return_value = (True, "OK")
    rm.approve_order.return_value = (True, "OK")
    return rm


def _make_registry():
    """AgentRegistry with Mocked RLAgent + LSTMDynamic (no real models)."""
    from core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    # Use MagicMock for both strategies to avoid loading real model files
    rl = MagicMock()
    rl.strategy_name = "RLAgent"
    lstm = MagicMock()
    lstm.strategy_name = "LSTMDynamic"
    registry.register("RLAgent", rl, set_active=True)
    registry.register("LSTMDynamic", lstm, set_active=False)
    return registry, rl, lstm


def _make_registry_with_real_lstm(tmp_path):
    """
    Registry where LSTMDynamic is a real (but model-less) instance.
    torch_model=None → _get_torch_prediction returns (0.0, None).
    """
    from core.agent_registry import AgentRegistry

    client = _make_mock_client()
    dp = _make_mock_data_provider()
    rm = _make_mock_risk_manager()
    running_event = threading.Event()
    running_event.set()

    with patch(
        "core.strategies.lstm_strategy.LSTMDynamicStrategy._load_torch_model_assets"
    ):
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        lstm = LSTMDynamicStrategy(
            client=client,
            symbols=["AAPL"],
            running_event=running_event,
            total_capital=100_000.0,
            risk_manager=rm,
            data_provider=dp,
        )

    # Confirm model is None (our safe graceful-degradation path)
    assert lstm.torch_model is None

    rl_mock = MagicMock()
    rl_mock.strategy_name = "RLAgent"

    registry = AgentRegistry()
    registry.register("RLAgent", rl_mock, set_active=True)
    registry.register("LSTMDynamic", lstm, set_active=False)
    return registry, rl_mock, lstm


# ---------------------------------------------------------------------------
# 1. Swap Lifecycle
# ---------------------------------------------------------------------------


class TestSwapLifecycle:

    def test_swap_sets_pending_flag(self):
        registry, _, _ = _make_registry()
        result = registry.swap("LSTMDynamic")
        assert result is True
        assert registry.has_pending_swap() is True

    def test_swap_unknown_name_returns_false(self):
        registry, _, _ = _make_registry()
        result = registry.swap("UnknownStrategy")
        assert result is False
        assert not registry.has_pending_swap()

    def test_commit_swap_changes_active(self):
        registry, rl, lstm = _make_registry()
        assert registry.get_active() is rl
        registry.swap("LSTMDynamic")
        registry.commit_swap()
        assert registry.get_active() is lstm

    def test_commit_swap_clears_pending(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic")
        registry.commit_swap()
        assert not registry.has_pending_swap()

    def test_double_swap_raises_swap_in_progress_error(self):
        from core.exceptions import SwapInProgressError

        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic")
        with pytest.raises(SwapInProgressError):
            registry.swap("LSTMDynamic")

    def test_commit_swap_noop_when_no_pending(self):
        registry, rl, _ = _make_registry()
        registry.commit_swap()  # No-Op — should not raise
        assert registry.get_active() is rl

    def test_swap_rl_to_lstm_and_back(self):
        registry, rl, lstm = _make_registry()
        # RL → LSTM
        registry.swap("LSTMDynamic")
        registry.commit_swap()
        assert registry.get_active() is lstm
        # LSTM → RL
        registry.swap("RLAgent")
        registry.commit_swap()
        assert registry.get_active() is rl


# ---------------------------------------------------------------------------
# 2. Shadow Mode
# ---------------------------------------------------------------------------


class TestSwapSmokeShadowMode:

    def test_shadow_swap_sets_shadow_flag(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic", shadow_mode=True)
        assert registry.is_shadow_mode() is True

    def test_normal_swap_no_shadow_flag(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic", shadow_mode=False)
        assert registry.is_shadow_mode() is False

    def test_shadow_flag_cleared_after_commit(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic", shadow_mode=True)
        registry.commit_swap()
        assert registry.is_shadow_mode() is False

    def test_shadow_swap_pending_flag_set(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic", shadow_mode=True)
        assert registry.has_pending_swap() is True

    def test_shadow_swap_active_unchanged_before_commit(self):
        registry, rl, _ = _make_registry()
        registry.swap("LSTMDynamic", shadow_mode=True)
        assert registry.get_active() is rl  # Not changed until commit_swap()


# ---------------------------------------------------------------------------
# 3. Synthetic Forward Pass — real LSTM instance (model=None graceful path)
# ---------------------------------------------------------------------------


class TestSwapSmokeForwardPass:

    def test_lstm_strategy_instantiates_without_model(self, tmp_path):
        """LSTMDynamic can be created with torch_model=None."""
        _, _, lstm = _make_registry_with_real_lstm(tmp_path)
        assert lstm.torch_model is None
        assert lstm.strategy_name == "LSTMDynamic"

    def test_run_for_symbol_no_crash_after_swap(self, tmp_path):
        """
        After registry.swap() + commit_swap() the new strategy's run_for_symbol()
        must not raise — even with empty data (graceful degradation).
        """
        registry, _, lstm = _make_registry_with_real_lstm(tmp_path)
        registry.swap("LSTMDynamic")
        registry.commit_swap()

        active = registry.get_active()
        assert active is lstm

        ohlc = {
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 1_000_000,
        }
        market_data = {"vix": 18.0, "latest_news_sentiment": 0.1}

        # run_for_symbol is async — run in event loop
        async def _run():
            await active.run_for_symbol("AAPL", ohlc, market_data, datetime(2024, 6, 1))

        # Must not raise
        asyncio.run(_run())

    def test_run_for_symbol_does_not_raise_on_empty_hist(self, tmp_path):
        """Even with completely empty market history, no exception bubbles up."""
        _, _, lstm = _make_registry_with_real_lstm(tmp_path)

        async def _run():
            await lstm.run_for_symbol(
                "TSLA",
                {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
                {},
                datetime(2024, 1, 1),
            )

        asyncio.run(_run())

    def test_run_for_symbol_returns_none_not_error(self, tmp_path):
        """run_for_symbol() returns None (no trade) when model is absent."""
        _, _, lstm = _make_registry_with_real_lstm(tmp_path)

        result = None

        async def _run():
            nonlocal result
            result = await lstm.run_for_symbol(
                "MSFT",
                {
                    "open": 300.0,
                    "high": 305.0,
                    "low": 298.0,
                    "close": 302.0,
                    "volume": 500_000,
                },
                {"vix": 20.0},
                datetime(2024, 6, 1),
            )

        asyncio.run(_run())
        # Result is None or a dict/bool — just not a raised exception
        assert result is None or isinstance(result, (dict, bool, type(None)))


# ---------------------------------------------------------------------------
# 4. Edge Cases
# ---------------------------------------------------------------------------


class TestSwapSmokeEdgeCases:

    def test_swap_to_same_strategy_sets_pending(self):
        """Swap to the currently active strategy is allowed (sets pending)."""
        registry, _, _ = _make_registry()
        result = registry.swap("RLAgent")  # same as current active
        assert result is True

    def test_swap_with_empty_registry_returns_false(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        result = registry.swap("LSTMDynamic")
        assert result is False

    def test_list_registered_after_swap(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic")
        names = registry.list_registered()
        assert "RLAgent" in names
        assert "LSTMDynamic" in names

    def test_multiple_commits_are_safe(self):
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic")
        registry.commit_swap()
        registry.commit_swap()  # Second commit: no-op, must not raise
        assert not registry.has_pending_swap()


# ---------------------------------------------------------------------------
# 5. Thread safety
# ---------------------------------------------------------------------------


class TestSwapThreadSafety:

    def test_commit_swap_thread_safe(self):
        """Multiple threads calling commit_swap() simultaneously must not crash."""
        registry, _, _ = _make_registry()
        registry.swap("LSTMDynamic")

        errors = []

        def commit():
            try:
                registry.commit_swap()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=commit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert not registry.has_pending_swap()
