"""INC-6 continuous-operation regression: the AI monitor must keep feeding
research symbols when the US market is closed (empty scanner result) *iff*
FULL_UNIVERSE_TRADING_ENABLED is armed.

Root cause (verified on origin/main):
``core/engine/monitor_loop.py::run_strategy_monitor_loop`` is the ONLY writer
of ``active_strategy.symbols``. When the market is closed the market scanner
returns ``None``/``{}`` (no intraday candidates). The pre-fix code did::

    if not scanner_recommendation:
        raise Exception("Scanner failed.")

which aborts the whole monitor cycle BEFORE the FULL_UNIVERSE bypass at
``target_stocks = list(universe)`` can run — so ``active_strategy.symbols`` is
never populated and the round table never evaluates anything (trading_loop
idles on "Waiting for symbols from scanner...").

The fix scopes the abort to the non-FULL_UNIVERSE case: with the flag armed an
empty scan logs a WARNING and proceeds with the full universe (order PLACEMENT
stays market-gated downstream in trading_loop/order_executor — unchanged).

Tests
-----
* GREEN (flag ON, empty scan): the cycle does NOT raise/abort and sets
  ``active_strategy.symbols`` to the full ``live_universe``. This test is RED on
  the pre-fix code (the raise wipes the symbols) and GREEN after the fix.
* GUARD (flag OFF, empty scan): the old ``raise Exception("Scanner failed.")``
  still fires and ``active_strategy.symbols`` is left untouched — byte-identical
  legacy behaviour for the default/OSS/cloud config.

CI-prevention rule R2: ``get_config`` is patched on ALL import paths via the
canonical ``_patch_get_config`` helper (config + ai_trading_bot.config +
the consumer module core.engine.monitor_loop).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import core.engine.monitor_loop as monitor_loop_mod
from core.engine.monitor_loop import MonitorLoopMixin
from tests.helpers.config_patch import _patch_get_config

_LIVE_UNIVERSE = ["AAA", "BBB", "CCC", "DDD"]
_INITIAL_SYMBOLS = ["__INIT_SENTINEL__"]


class _OneShotEvent:
    """Drives the monitor while-loop through exactly ONE cycle.

    * ``monitor_running`` uses this pre-set so ``is_set()`` is True on entry.
    * ``_shutdown_event`` uses this UNSET; its ``wait()`` (called once at the end
      of the cycle, monitor_loop.py:398) flips it set, so the next while-guard
      ``monitor_running.is_set() and not _shutdown_event.is_set()`` is False and
      the loop exits without a second iteration.
    """

    def __init__(self, *, initially_set: bool, terminate_on_wait: bool = False):
        self._set = initially_set
        self._terminate_on_wait = terminate_on_wait

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def clear(self) -> None:
        self._set = False

    def wait(self, timeout=None) -> bool:
        if self._terminate_on_wait:
            self._set = True
        return self._set


class _FakeEngine(MonitorLoopMixin):
    """Minimal BotEngine stand-in exercising the stable-strategy path (else
    branch at monitor_loop.py:361 → the ``active_strategy.symbols = target_stocks``
    write at :371). ``strategy_name`` is pinned to config.ACTIVE_STRATEGY so no
    strategy-switch/thread machinery is triggered."""


def _make_engine(slack_mock) -> _FakeEngine:
    eng = _FakeEngine()
    eng.monitor_running = _OneShotEvent(initially_set=True)
    eng._shutdown_event = _OneShotEvent(initially_set=False, terminate_on_wait=True)
    eng._skipped_symbols = set()
    eng._log_strategy_thought = MagicMock()
    eng.regime_model = MagicMock()
    eng.regime_model.get_market_regime.return_value = {
        "regime": "Neutral",
        "value": 20.0,
    }
    eng.current_market_data = {}
    # Market CLOSED -> scanner yields nothing (the trigger condition).
    eng.market_scanner = MagicMock()
    eng.market_scanner.scan_market = AsyncMock(return_value=None)
    eng.live_universe = list(_LIVE_UNIVERSE)
    # Live (not sim) with an api that owns no open positions -> the held-position
    # append loop runs but adds nothing, keeping target_stocks == full universe.
    eng.is_simulation = False
    eng.api = MagicMock()
    eng.api.get_all_positions.return_value = []
    eng.strategy_lock = threading.Lock()
    # Stable-strategy path: strategy_name must equal getattr(config,
    # "ACTIVE_STRATEGY", "RLAgent") so current == target and no switch occurs.
    active_name = getattr(config, "ACTIVE_STRATEGY", "RLAgent")
    eng.active_strategy = SimpleNamespace(
        strategy_name=active_name,
        symbols=list(_INITIAL_SYMBOLS),
        thought_callback=None,
        current_recommendation_confidence=None,
    )
    from datetime import datetime

    eng._last_live_equity_write_date = datetime.now().date()
    eng._append_live_equity_to_benchmark = MagicMock()
    eng._scans_completed = 0
    eng._last_scan_time = 0.0
    eng._last_top_picks = []
    return eng


def _run_one_cycle(monkeypatch, *, full_universe: bool):
    slack_mock = MagicMock()
    monkeypatch.setattr(monitor_loop_mod, "send_slack_alert", slack_mock)
    cfg = SimpleNamespace(FULL_UNIVERSE_TRADING_ENABLED=full_universe)
    # Rule R2: patch get_config on every import path (config, ai_trading_bot.config,
    # and the consumer module) so the flag the code reads is the flag we set.
    _patch_get_config(monkeypatch, cfg, consumer_modules=(monitor_loop_mod,))
    eng = _make_engine(slack_mock)
    eng.run_strategy_monitor_loop()
    return eng, slack_mock


def test_full_universe_armed_empty_scan_populates_full_universe(monkeypatch, caplog):
    """GREEN target / RED on pre-fix code.

    Flag ON + empty scan: the cycle must NOT abort; it must set
    active_strategy.symbols to the full live_universe so research/reports keep
    running while the market is closed.
    """
    with caplog.at_level("WARNING"):
        eng, slack_mock = _run_one_cycle(monkeypatch, full_universe=True)

    # The monitor did NOT abort: no "Monitor Cycle" warning alert was emitted.
    slack_mock.assert_not_called()
    # active_strategy.symbols is the FULL universe, not the empty/idle set.
    assert set(eng.active_strategy.symbols) == set(_LIVE_UNIVERSE)
    assert eng.active_strategy.symbols != _INITIAL_SYMBOLS
    # And the honest §5.6 WARNING was logged.
    assert any(
        "FULL_UNIVERSE" in rec.message and "scanner returned empty" in rec.message
        for rec in caplog.records
    )


def test_flag_off_empty_scan_still_raises_scanner_failed(monkeypatch):
    """GUARD: flag OFF + empty scan keeps the legacy abort (byte-identical).

    The Exception('Scanner failed.') is raised at monitor_loop.py:140, caught by
    the cycle's try/except which fires send_slack_alert, and active_strategy.symbols
    is left untouched (the round table stays idle exactly as before the fix).
    """
    eng, slack_mock = _run_one_cycle(monkeypatch, full_universe=False)

    # The abort path ran: the cycle-level warning alert was sent once.
    assert slack_mock.call_count == 1
    assert "Scanner failed." in str(slack_mock.call_args)
    # Symbols were NOT populated -> unchanged from the initial sentinel.
    assert eng.active_strategy.symbols == _INITIAL_SYMBOLS
