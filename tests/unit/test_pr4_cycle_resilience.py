# tests/unit/test_pr4_cycle_resilience.py
# PR-4 INC-3: (1) a single wedged/failing SOURCE must not stop the strategy — the per-cycle
# try/except (trading_loop.py:880-900) never clears strategy_running (only startup-health-fail
# and the kill-switch may). (2) SAFETY: Inc3 must NOT mask a genuinely wedged loop — the
# independent stall watchdog (base.py:561 → cycle_watchdog.evaluate_stall) must still fire.

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

# ── SAFETY: the stall watchdog still fires on a genuinely wedged loop ─────────


def test_stall_watchdog_still_fires_on_a_genuinely_wedged_loop():
    """A genuinely dead loop (running + market open + last cycle far past the threshold)
    MUST be flagged stalled — Inc3's cycle-level resilience must never mask a real stall.
    The negative controls prove it does not misfire on healthy / stopped / closed states.
    """
    from core.cycle_watchdog import evaluate_stall

    now = 1_000_000.0
    threshold = 5400.0

    wedged = evaluate_stall(
        now=now,
        last_cycle_ts=now - 6000,  # > threshold since the last completed cycle
        market_open=True,
        strategy_running=True,
        stall_after_seconds=threshold,
    )
    assert (
        wedged["stalled"] is True
    ), "a genuinely wedged loop must still trip the stall watchdog"

    # Negative controls — none of these is a stall (no false positive that hides nothing).
    healthy = evaluate_stall(
        now=now,
        last_cycle_ts=now - 60,
        market_open=True,
        strategy_running=True,
        stall_after_seconds=threshold,
    )
    assert healthy["stalled"] is False
    stopped = evaluate_stall(
        now=now,
        last_cycle_ts=now - 6000,
        market_open=True,
        strategy_running=False,
        stall_after_seconds=threshold,
    )
    assert stopped["stalled"] is False
    closed = evaluate_stall(
        now=now,
        last_cycle_ts=now - 6000,
        market_open=False,
        strategy_running=True,
        stall_after_seconds=threshold,
    )
    assert closed["stalled"] is False


# ── Resilience: a bad source keeps the strategy running ──────────────────────


def _make_loop():
    from core.engine.trading_loop import TradingLoopMixin

    obj = TradingLoopMixin.__new__(TradingLoopMixin)
    obj.api = MagicMock()
    clock = MagicMock()
    clock.is_open = (
        True  # market open → exercise the trading branch, not the closed-sleep
    )
    obj.api.get_clock.return_value = clock
    obj.data_api = MagicMock()
    obj._log_strategy_thought = MagicMock()
    obj._skipped_symbols = set()
    obj.strategy_running = threading.Event()
    obj.strategy_running.set()
    obj._shutdown_event = threading.Event()
    obj._startup_health_check = AsyncMock()
    obj._hitl_day_rollover = AsyncMock()
    obj._drain_hitl_approvals = AsyncMock(return_value=0)
    obj._reconcile_active_strategy_entry_time = AsyncMock()
    obj._run_position_stop_checks = AsyncMock(return_value=set())
    obj._hitl_symbol_pending = AsyncMock(return_value=False)
    obj.compliance_guardian = None
    obj._last_trading_day = None
    obj.current_market_data = {}
    obj.strategy_lock = threading.Lock()
    obj._last_market_open = True
    obj._rank_panel_producer = None
    obj._cycle_watchdog = None

    strat = MagicMock()
    strat.symbols = ["AAPL"]
    strat.strategy_name = "RLAgent"
    strat.risk_manager = None
    strat.update_lstm_rankings = AsyncMock()
    obj.active_strategy = strat
    return obj


async def test_transient_bad_source_does_not_clear_strategy_running():
    """A transient data-source failure (snapshot fetch raises) is caught inside the cycle;
    the loop continues and strategy_running stays SET (never cleared by a per-source error).
    """
    obj = _make_loop()

    def _boom(*_a, **_k):
        obj._shutdown_event.set()  # let the loop exit cleanly after this failed fetch
        raise RuntimeError("transient data-feed outage")

    obj.data_api.get_stock_snapshot.side_effect = _boom

    with patch("core.engine.trading_loop.kill_switch", None), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=None),
    ):
        await obj.live_trading_loop()

    assert (
        obj.strategy_running.is_set()
    ), "a transient bad source must NOT clear strategy_running"


async def test_outer_cycle_exception_does_not_clear_strategy_running():
    """A wedged source that raises past the inner guards hits the OUTER cycle except
    (trading_loop.py:894). It sleeps and continues — it must NEVER clear strategy_running.
    """
    obj = _make_loop()
    obj.data_api.get_stock_snapshot.return_value = {}  # fetch ok, but…
    obj.active_strategy.update_lstm_rankings = AsyncMock(
        side_effect=RuntimeError("wedged source in the strategy step")
    )

    async def _fast_sleep(*_a, **_k):
        obj._shutdown_event.set()  # the outer-except sleep(30) → set shutdown → loop breaks
        return None

    with patch("core.engine.trading_loop.kill_switch", None), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=None),
    ), patch("asyncio.sleep", new=_fast_sleep):
        await obj.live_trading_loop()

    assert (
        obj.strategy_running.is_set()
    ), "the outer cycle except must NOT clear strategy_running"
