"""Ein Halt stoppt neue Einstiege, nicht den Schutz offener Positionen (#3380, ARC-E1.4).

Der Halt wirkt heute an ZWEI Stellen, und beide muessen fallen — sonst ist die Aenderung
eine Scheinloesung:

1. **Die Zyklus-Schleife** (`core/engine/trading_loop.py`): die Halt-Pruefung steht vor
   `_run_position_stop_checks()` und schlaeft 60 Sekunden. Solange der Halt steht, laufen
   die Stops offener Positionen nicht.
2. **Das Kill-Switch-Tor im Order-Pfad** (`core/engine/order_executor.py`): selbst wenn
   die Schleife den Stop-Exit erreicht, weist `kill_switch.check_halt` ihn ab —
   `risk_manager.py:751` setzt `trading_halted` und loest im selben Block den Kill-Switch
   aus (`:754`).

Die Freistellung ist eng: nur ein SELL, der als Schutz-Exit ausgewiesen ist
(`DecisionContext.triggered_by_stop`, gesetzt vom Stop-Pfad in `trading_loop.py:1029`).
Ein Einstieg bleibt geblockt. Und sie ist nicht still: sie wird protokolliert.
"""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tor 1 — die Zyklus-Schleife
# ---------------------------------------------------------------------------


def _halted_mixin():
    """Minimaler Loop-Mixin mit ausgeloestem Drawdown-Halt."""
    from core.engine.trading_loop import TradingLoopMixin

    strategy = MagicMock()
    strategy.strategy_name = "RLAgent"
    strategy.symbols = ["AAPL"]
    strategy.risk_manager = MagicMock()
    strategy.risk_manager.trading_halted = True  # <- der Halt steht
    strategy.update_lstm_rankings = AsyncMock()

    shutdown_flags = [False]
    shutdown = MagicMock()
    shutdown.is_set.side_effect = lambda: shutdown_flags[0]
    running = MagicMock()
    running.is_set.return_value = True

    api = MagicMock()
    clock = type(
        "MockClock",
        (),
        {
            "now": lambda self: __import__("datetime").datetime(
                2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
            ),
            "time": lambda self: 1735732800.0,
        },
    )()
    clock.is_open = True
    api.get_clock.return_value = clock

    mixin = TradingLoopMixin.__new__(TradingLoopMixin)
    mixin.active_strategy = strategy
    mixin.specialist_registry = MagicMock()
    mixin._shutdown_event = shutdown
    mixin.strategy_running = running
    mixin.strategy_lock = threading.Lock()
    mixin._skipped_symbols = set()
    mixin._cycle_latencies = []
    mixin._last_cycle_details = {}
    mixin.cloud_logger = MagicMock()
    mixin.cloud_logger.log_latency_metric = MagicMock()
    mixin._log_strategy_thought = MagicMock()
    mixin._send_update_threadsafe = MagicMock()
    mixin.api = api
    mixin.data_api = MagicMock()
    mixin.current_market_data = {}
    mixin._process_signal_event = AsyncMock()
    mixin._update_live_account_equity = AsyncMock()
    mixin._run_position_stop_checks = AsyncMock(return_value=set())
    mixin._startup_health_check = AsyncMock(return_value=None)
    return mixin, shutdown_flags


@pytest.mark.anyio
@pytest.mark.mutates_global_state
async def test_position_stops_run_while_trading_is_halted():
    """Der Halt darf den Schutz offener Positionen nicht aussetzen."""
    mixin, shutdown_flags = _halted_mixin()

    def _sleep(seconds):
        # Nach dem ersten Halt-Schlaf ist der Zyklus durch — Schleife beenden.
        if seconds >= 60:
            shutdown_flags[0] = True

    with (
        patch("core.engine.trading_loop.asyncio.sleep", AsyncMock(side_effect=_sleep)),
        patch("core.engine.trading_loop.kill_switch", create=True) as ks,
    ):
        ks.is_halted.return_value = False
        await mixin.live_trading_loop()

    assert mixin._run_position_stop_checks.await_count >= 1, (
        "Bei gesetztem Halt wurden die Positions-Stops nicht abgearbeitet. "
        "Die Halt-Pruefung steht vor _run_position_stop_checks() und schlaeft."
    )


@pytest.mark.anyio
@pytest.mark.mutates_global_state
async def test_halt_still_blocks_new_entries():
    """Gegenprobe: der Halt bleibt wirksam — es entsteht kein neuer Einstieg."""
    mixin, shutdown_flags = _halted_mixin()

    def _sleep(seconds):
        if seconds >= 60:
            shutdown_flags[0] = True

    with (
        patch("core.engine.trading_loop.asyncio.sleep", AsyncMock(side_effect=_sleep)),
        patch("core.engine.trading_loop.kill_switch", create=True) as ks,
    ):
        ks.is_halted.return_value = False
        await mixin.live_trading_loop()

    assert mixin._process_signal_event.await_count == 0


# ---------------------------------------------------------------------------
# Tor 2 — das Kill-Switch-Tor im Order-Pfad
# ---------------------------------------------------------------------------


def _sell_setup(triggered_by_stop: bool):
    """SELL-Aufbau wie in test_order_executor.py, mit steuerbarem Schutz-Exit-Kennzeichen."""
    from tests.unit.test_order_executor import _make_executor, _make_sell_tenant_setup

    executor = _make_executor()
    tenant, event, rm, pm, tenant_api, _ = _make_sell_tenant_setup(executor)
    event.decision_context.triggered_by_stop = triggered_by_stop
    event.decision_context.stop_type = "position_stop" if triggered_by_stop else ""
    return executor, tenant, event, tenant_api


def _redis_mock():
    redis_mock = MagicMock()
    redis_mock.publish = AsyncMock()
    redis_mock.lock = MagicMock(
        return_value=MagicMock(
            acquire=AsyncMock(return_value=True), release=AsyncMock(return_value=True)
        )
    )
    return redis_mock


@pytest.mark.anyio
async def test_protective_exit_passes_the_kill_switch_gate():
    """Ein Schutz-Exit wird ausgefuehrt, obwohl der Kill-Switch ausgeloest ist."""
    executor, tenant, event, tenant_api = _sell_setup(triggered_by_stop=True)

    with (
        patch("core.engine.order_executor.RedisClient") as mock_redis,
        patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
        patch("core.engine.order_executor.kill_switch") as ks,
    ):
        mock_redis.get_redis = AsyncMock(return_value=_redis_mock())
        ks.check_halt = MagicMock(
            side_effect=Exception("System is HALTED by Kill Switch")
        )
        await executor._execute_tenant_order(tenant, event)

    tenant_api.submit_order.assert_called_once()


@pytest.mark.anyio
async def test_a_normal_order_stays_blocked_while_halted():
    """Gegenprobe: ohne Schutz-Exit-Kennzeichen bleibt das Tor geschlossen."""
    executor, tenant, event, tenant_api = _sell_setup(triggered_by_stop=False)

    with (
        patch("core.engine.order_executor.RedisClient") as mock_redis,
        patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
        patch("core.engine.order_executor.kill_switch") as ks,
    ):
        mock_redis.get_redis = AsyncMock(return_value=_redis_mock())
        ks.check_halt = MagicMock(
            side_effect=Exception("System is HALTED by Kill Switch")
        )
        await executor._execute_tenant_order(tenant, event)

    tenant_api.submit_order.assert_not_called()


@pytest.mark.anyio
async def test_the_exemption_is_not_silent():
    """Eine Freistellung, die niemand sieht, ist ein blinder Fleck.

    CODING_POLICY §5.6: Ersatz- und Ausnahmepfade werden auf WARNING angekuendigt.
    """
    executor, tenant, event, tenant_api = _sell_setup(triggered_by_stop=True)

    with (
        patch("core.engine.order_executor.RedisClient") as mock_redis,
        patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
        patch("core.engine.order_executor.kill_switch") as ks,
        patch("core.engine.order_executor.logging") as mock_logging,
    ):
        mock_redis.get_redis = AsyncMock(return_value=_redis_mock())
        ks.check_halt = MagicMock(
            side_effect=Exception("System is HALTED by Kill Switch")
        )
        await executor._execute_tenant_order(tenant, event)

    # Ohne diese Zusicherung bestuende der Test auch dann, wenn die Order GEBLOCKT
    # wurde — die Blockade loggt ebenfalls „halt". Geprueft wird die Ausnahme, nicht
    # die Regel.
    tenant_api.submit_order.assert_called_once()

    gemeldet = " ".join(
        str(c)
        for c in mock_logging.warning.call_args_list
        + mock_logging.critical.call_args_list
    )
    assert (
        "halt" in gemeldet.lower()
    ), "Der Schutz-Exit lief trotz Halt, ohne dass die Ausnahme gemeldet wurde."


# ---------------------------------------------------------------------------
# Tor 2b — dasselbe Tor im GLOBALEN Pfad (Desktop)
# ---------------------------------------------------------------------------
#
# Die Freistellung oben galt nur fuer ``_execute_tenant_order``. Der Desktop hat keine
# OAuth-Mandanten; seine Orders nehmen den globalen Zweig von ``_process_signal_event``.
# Dort standen zwei ``kill_switch.check_halt`` **unbedingt** — ein gehaltener Desktop
# konnte seinen Stop-Loss nicht ausfuehren. Genau die Lage, die #3380 beheben sollte:
# „sonst liegt eine Position genau dann ohne Schwelle da, wenn die Lage ohnehin schon
# schlecht ist."


def _global_sell_setup(triggered_by_stop: bool):
    from tests.unit.test_order_executor import _make_executor, _make_signal

    api = MagicMock()
    api.submit_order.return_value = MagicMock(id="order-global-sell")
    api.get_account.return_value = MagicMock(
        cash="100000", equity="100000", buying_power="100000", multiplier="1"
    )
    api.get_open_position.return_value = MagicMock(qty="200")
    executor = _make_executor(api=api)
    strategy = MagicMock()
    strategy.portfolio_manager = MagicMock()
    executor.active_strategy = strategy

    event = _make_signal(action="SELL", symbol="AAPL", qty=2.0)
    event.decision_context.triggered_by_stop = triggered_by_stop
    event.decision_context.stop_type = "position_stop" if triggered_by_stop else ""
    return executor, event, api


async def _lauf_global(executor, event, *, logging_mock=None):
    from contextlib import ExitStack

    with ExitStack() as stapel:
        stapel.enter_context(
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            )
        )
        stapel.enter_context(
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True)
        )
        ks = stapel.enter_context(patch("core.engine.order_executor.kill_switch"))
        ks.check_halt = MagicMock(
            side_effect=Exception("System is HALTED by Kill Switch")
        )
        # Das Tor fragt den Halt selbst (#3447) — und er steht.
        ks.is_halted = MagicMock(return_value=True)
        if logging_mock is not None:
            stapel.enter_context(
                patch("core.engine.order_executor.logging", logging_mock)
            )
        await executor._process_signal_event(event)


@pytest.mark.anyio
async def test_global_protective_exit_passes_the_kill_switch_gate():
    """Desktop: Ein Schutz-Exit wird ausgefuehrt, obwohl der Kill-Switch ausgeloest ist."""
    executor, event, api = _global_sell_setup(triggered_by_stop=True)

    await _lauf_global(executor, event)

    api.submit_order.assert_called_once()


@pytest.mark.anyio
async def test_global_normal_sell_stays_blocked_while_halted():
    """Gegenprobe: Rotation und Trim tragen das Kennzeichen nicht — sie bleiben geblockt."""
    executor, event, api = _global_sell_setup(triggered_by_stop=False)

    await _lauf_global(executor, event)

    api.submit_order.assert_not_called()


@pytest.mark.anyio
async def test_global_buy_stays_blocked_while_halted():
    """Ein Halt bedeutet: kein neues Kapital in den Markt. Auch nicht mit Kennzeichen."""
    from tests.unit.test_order_executor import _make_executor, _make_signal

    api = MagicMock()
    executor = _make_executor(api=api)
    executor.active_strategy = MagicMock()
    event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
    event.decision_context.triggered_by_stop = (
        True  # falsch gesetzt — darf nichts oeffnen
    )

    await _lauf_global(executor, event)

    api.submit_order.assert_not_called()


@pytest.mark.anyio
async def test_global_exemption_is_not_silent():
    executor, event, api = _global_sell_setup(triggered_by_stop=True)
    mock_logging = MagicMock()

    await _lauf_global(executor, event, logging_mock=mock_logging)

    api.submit_order.assert_called_once()
    gemeldet = " ".join(str(c) for c in mock_logging.warning.call_args_list)
    assert (
        "Schutz-Exit" in gemeldet and "3380" in gemeldet
    ), "Die Freistellung im globalen Pfad wurde nicht auf WARNING gemeldet."
