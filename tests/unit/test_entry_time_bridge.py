# tests/unit/test_entry_time_bridge.py
"""#2965 — durable entry-time reaches the strategies; reconcile reruns per cycle.

Proven live (HWM, 2026-08-19): a position without a known entry-time passes the
min-hold gate via the #1952 fail-open and gets rotation-sold immediately. The
#1994 reconcile writes pm._trade_history, but the strategies read their OWN
_entry_time map — the bridge was missing entirely — and the reconcile guards
run once per session, so mid-session foreign/executor fills stay unknown.

Contract pinned here (all flag-dark, ENTRY_TIME_RECONCILE_PER_CYCLE=False):
- bridge: own map first; durable pm._trade_history only with the flag ON;
  None when both are missing (existing defaults/fail-open untouched)
- trading_loop guard: flag OFF -> once per PM (today's behavior);
  flag ON -> reruns after the cooldown
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import allure

from core.entry_time_bridge import bridged_entry_time, durable_entry_time


def _pm(history=None):
    return SimpleNamespace(_trade_history=history or {})


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestDurableEntryTime:
    def test_reads_reconciled_history(self):
        ts = datetime(2026, 8, 17, 14, 0)
        assert durable_entry_time(_pm({"HWM": [ts]}), "HWM") == ts

    def test_none_on_missing_or_garbage(self):
        assert durable_entry_time(_pm(), "HWM") is None
        assert durable_entry_time(_pm({"HWM": []}), "HWM") is None
        assert durable_entry_time(_pm({"HWM": ["not-a-dt"]}), "HWM") is None
        assert durable_entry_time(None, "HWM") is None


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestBridgedEntryTime:
    def test_own_map_wins(self):
        own = datetime(2026, 8, 18, 10, 0)
        strat = SimpleNamespace(
            _entry_time={"HWM": own},
            portfolio_manager=_pm({"HWM": [datetime(2026, 8, 1)]}),
        )
        assert bridged_entry_time(strat, "HWM") == own

    def test_flag_off_never_bridges(self):
        strat = SimpleNamespace(
            _entry_time={},
            portfolio_manager=_pm({"HWM": [datetime(2026, 8, 17)]}),
        )
        with patch("config.ENTRY_TIME_RECONCILE_PER_CYCLE", False, create=True):
            assert (
                bridged_entry_time(strat, "HWM") is None
            ), "flag OFF must keep today's behavior byte-identical"

    def test_flag_on_bridges_to_durable_history(self):
        ts = datetime(2026, 8, 17, 14, 0)
        strat = SimpleNamespace(_entry_time={}, portfolio_manager=_pm({"HWM": [ts]}))
        with patch("config.ENTRY_TIME_RECONCILE_PER_CYCLE", True, create=True):
            assert bridged_entry_time(strat, "HWM") == ts, (
                "the HWM defect: a reconciled entry-time must reach the "
                "min-hold gate instead of the #1952 fail-open"
            )

    def test_flag_on_without_pm_stays_none(self):
        # LSTMDynamicStrategy has no portfolio_manager — the bridge must
        # degrade to None (existing fail-open), never raise.
        strat = SimpleNamespace(_entry_time={})
        with patch("config.ENTRY_TIME_RECONCILE_PER_CYCLE", True, create=True):
            assert bridged_entry_time(strat, "HWM") is None


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestPerCycleReconcileGuard:
    def _loop_self(self):
        pm = _pm()
        pm.client = object()
        strat = SimpleNamespace(portfolio_manager=pm)
        # duck-typed TradingLoopMixin self — only the attrs the method touches
        return SimpleNamespace(active_strategy=strat)

    def _run_twice(self, flag_on, cooldown_elapsed):
        from core.engine import trading_loop as tl

        fake = self._loop_self()

        mock_reconcile = AsyncMock(return_value=None)

        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            mock_reconcile,
        ), patch("config.ENTRY_TIME_RECONCILE_PER_CYCLE", flag_on, create=True):
            asyncio.run(tl.TradingLoopMixin._reconcile_active_strategy_entry_time(fake))
            if cooldown_elapsed and hasattr(fake, "_strategy_pm_reconcile_ts"):
                # age the recorded timestamp past the cooldown
                for k in fake._strategy_pm_reconcile_ts:
                    fake._strategy_pm_reconcile_ts[k] -= 305.0
            asyncio.run(tl.TradingLoopMixin._reconcile_active_strategy_entry_time(fake))

        return mock_reconcile.call_count

    def test_flag_off_runs_once_per_pm(self):
        assert self._run_twice(flag_on=False, cooldown_elapsed=True) == 1

    def test_fresh_boot_first_reconcile_runs(self):
        """CI-caught regression: time.monotonic() is seconds-since-boot — on a
        fresh container it is itself below the cooldown, and a 0.0 'never ran'
        sentinel skipped the very FIRST reconcile. Must run at monotonic()=10."""
        from core.engine import trading_loop as tl

        fake = self._loop_self()

        mock_reconcile = AsyncMock(return_value=None)

        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            mock_reconcile,
        ), patch("config.ENTRY_TIME_RECONCILE_PER_CYCLE", True, create=True), patch(
            "time.monotonic", return_value=10.0
        ):
            asyncio.run(tl.TradingLoopMixin._reconcile_active_strategy_entry_time(fake))
        assert (
            mock_reconcile.call_count == 1
        ), "fresh-boot first reconcile must never be skipped"

    def test_flag_on_reruns_after_cooldown(self):
        assert self._run_twice(flag_on=True, cooldown_elapsed=True) == 2

    def test_flag_on_respects_cooldown(self):
        assert self._run_twice(flag_on=True, cooldown_elapsed=False) == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestStrategyReadSitesUseBridge:
    def test_rl_smart_exit_uses_bridge_when_own_map_empty(self):
        """rl_execution._check_smart_exit defaulted to current_time (hours=0,
        rotation frozen). With the flag ON and a durable entry two days back,
        the gate must see ~48h."""
        from core.strategies.rl_execution import RLExecutionMixin

        now = datetime(2026, 8, 19, 14, 0)
        entry = now - timedelta(days=2)
        fake = SimpleNamespace(
            _entry_time={},
            portfolio_manager=_pm({"HWM": [entry]}),
            high_water_marks={},
            client=None,
        )
        captured = {}

        def _fake_should_sell(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(action="HOLD", reason="")

        with patch(
            "core.strategies.rl_execution.should_sell_smart",
            side_effect=_fake_should_sell,
        ), patch(
            "core.strategies.rl_execution._INTELLIGENT_EXIT_AVAILABLE", False
        ), patch(
            "config.ENTRY_TIME_RECONCILE_PER_CYCLE", True, create=True
        ):
            RLExecutionMixin._check_smart_exit(
                fake,
                symbol="HWM",
                in_position=True,
                qty=2.0,
                avg=287.0,
                curr=290.0,
                current_time=now,
                features=None,
            )
        assert captured.get("hours_held") is not None
        assert (
            abs(captured["hours_held"] - 48.0) < 0.01
        ), f"expected ~48h from the durable entry, got {captured['hours_held']}"
