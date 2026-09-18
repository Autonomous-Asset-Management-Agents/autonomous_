"""#2978 (Epic #1891) — Live-wiring of the account-wide drawdown breaker.

Proves that the live trading loop feeds ``RiskManager.update_account_equity`` on
every cycle (via the ``_update_live_account_equity`` mixin helper) BEFORE the
existing ``trading_halted`` gate, so the 7% portfolio-stop and the daily
drawdown circuit-breaker actually fire in live trading — and that the live path
is halt-only (``allow_unlock=False`` → never auto-unlocks; EU AI Act Art. 14).

The helper reads equity through the fail-safe house helper ``resolve_equity``
off the event loop, so a broker read failure warns and continues (never crashes
the loop). Async interface exercised with ``AsyncMock``-compatible fakes.
"""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from core.engine.trading_loop import TradingLoopMixin
from core.risk_manager import RiskManager

pytestmark = pytest.mark.iron_dome


@pytest.fixture(autouse=True)
def reset_kill_switch_fixture():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


class _Harness(TradingLoopMixin):
    """Minimal TradingLoopMixin instance exposing only what the helper touches."""

    def __init__(self, api):
        self.api = api


def _make_api(equity):
    """Broker client double whose ``get_account().equity`` returns ``equity``."""
    api = MagicMock()
    api.get_account.return_value = MagicMock(equity=equity)
    return api


def _make_strategy(total_capital=100_000.0, client=None):
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        rm = RiskManager(client=client or MagicMock(), total_capital=total_capital)
    strat = MagicMock()
    strat.risk_manager = rm
    return strat, rm


def test_live_cycle_halts_on_portfolio_stop():
    """Equity -7.5% from session start ⇒ portfolio-stop latches trading_halted."""
    api = _make_api(92_500.0)  # -7.5% of 100k
    strat, rm = _make_strategy(client=api)
    harness = _Harness(api)

    assert rm.trading_halted is False  # sanity: not yet fed

    asyncio.run(harness._update_live_account_equity(strat))

    assert rm.trading_halted is True
    assert rm._portfolio_stop_triggered is True


def test_live_cycle_halts_on_daily_drawdown():
    """Equity -18% from peak ⇒ Tier-2 circuit breaker halts."""
    api = _make_api(82_000.0)  # -18% of 100k peak
    strat, rm = _make_strategy(client=api)
    harness = _Harness(api)

    asyncio.run(harness._update_live_account_equity(strat))

    assert rm.trading_halted is True


def test_no_false_halt_above_thresholds():
    """Equity -3% stays below both thresholds ⇒ no halt."""
    api = _make_api(97_000.0)  # -3%
    strat, rm = _make_strategy(client=api)
    harness = _Harness(api)

    asyncio.run(harness._update_live_account_equity(strat))

    assert rm.trading_halted is False


def test_live_path_never_auto_unlocks():
    """After a drawdown trip, an equity recovery must NOT auto-unlock on the live
    path (allow_unlock=False enforces the halt-only guarantee).

    Peak is raised to 130k first so the -20% drop from peak trips the daily
    circuit breaker WITHOUT tripping the (permanently latching) 7% portfolio-stop
    from session start — otherwise no unlock path would exist to prove."""
    strat, rm = _make_strategy(client=MagicMock())

    rm.update_account_equity(130_000.0)  # raise peak; no halt
    rm.update_account_equity(104_000.0)  # -20% from peak → circuit breaker
    assert rm.trading_halted is True
    assert rm._portfolio_stop_triggered is False  # portfolio-stop NOT latched

    # Equity recovers back near peak; the live helper re-feeds via the breaker.
    api = _make_api(129_000.0)
    harness = _Harness(api)
    asyncio.run(harness._update_live_account_equity(strat))

    # Halt-only: still halted, because allow_unlock=False skips the unlock block.
    assert rm.trading_halted is True


def test_none_risk_manager_skips_cleanly():
    """A strategy without a RiskManager must not crash the loop."""
    api = _make_api(50_000.0)
    strat = MagicMock()
    strat.risk_manager = None
    harness = _Harness(api)

    # Must simply return without raising.
    asyncio.run(harness._update_live_account_equity(strat))


def test_account_read_failure_warns_and_continues(caplog):
    """api.get_account() raising ⇒ resolve_equity warns + falls back to
    DEFAULT_EQUITY; the helper must not raise and must not falsely halt."""
    api = MagicMock()
    api.get_account.side_effect = RuntimeError("broker down")
    strat, rm = _make_strategy(client=api)
    harness = _Harness(api)

    with caplog.at_level(logging.WARNING):
        asyncio.run(harness._update_live_account_equity(strat))

    # DEFAULT_EQUITY (100k) ≈ session start ⇒ no drawdown ⇒ no halt, no crash.
    assert rm.trading_halted is False
    assert any("equity" in r.message.lower() for r in caplog.records)


def test_default_allow_unlock_is_true_regression():
    """Sim/tests call update_account_equity WITHOUT allow_unlock → default True →
    the adaptive unlock logic stays byte-identically active (Sim parity)."""
    strat, rm = _make_strategy(client=MagicMock())
    rm.update_account_equity(130_000.0)  # raise peak
    rm.update_account_equity(104_000.0)  # -20% from peak → circuit breaker
    assert rm.trading_halted is True
    # Full recovery with default (allow_unlock=True) → unlock path re-enables.
    rm.update_account_equity(129_000.0)
    assert rm.trading_halted is False
