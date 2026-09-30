# tests/unit/test_async_broker_offload.py
# #1254 — blocking Alpaca broker calls inside async functions must be dispatched
# via asyncio.to_thread, not run directly on the event loop.

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.events import SignalEvent


def _make_executor(api):
    from core.engine.order_executor import OrderExecutorMixin

    ex = OrderExecutorMixin.__new__(OrderExecutorMixin)
    ex.api = api
    ex.compliance_guardian = None
    ex._log_strategy_thought = MagicMock()
    ex.cloud_logger = MagicMock()
    ex.cloud_logger.log_decision = MagicMock()
    ex.live_universe = []
    return ex


def _make_buy_event(qty: float = 2.0):
    ctx = MagicMock()
    ctx.current_price = 150.0
    ctx.conviction_score = 0.8
    ctx.alpaca_order_id = None
    ev = MagicMock(spec=SignalEvent)
    ev.action = "BUY"
    ev.symbol = "AAPL"
    ev.suggested_quantity = qty
    ev.decision_context = ctx
    ev.is_simulation = False
    return ev


async def test_global_buy_submit_order_is_offloaded_via_to_thread():
    """The blocking broker `submit_order` on the global BUY path must not run on
    the event loop (#1254). A regression that removes the offload fails this test.

    #3447: wie im Mandanten-Test unten — an ``asyncio.to_thread`` geht jetzt das Tor,
    nicht mehr ``submit_order`` selbst. Gemessen wird deshalb, auf welchem Thread der
    Broker-Aufruf tatsaechlich lief.
    """
    import threading

    ring_thread = threading.current_thread()
    broker_threads = []

    def _submit(_request):
        broker_threads.append(threading.current_thread())
        return MagicMock(id="order-123")

    api = MagicMock()
    api.submit_order.side_effect = _submit
    executor = _make_executor(api)
    event = _make_buy_event(qty=2.0)

    real_to_thread = asyncio.to_thread
    dispatched = []

    async def tracking_to_thread(fn, *args, **kwargs):
        dispatched.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch.object(
        executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
    ), patch("asyncio.to_thread", tracking_to_thread):
        await executor._process_signal_event(event)

    api.submit_order.assert_called_once()
    assert dispatched, "nothing was offloaded via asyncio.to_thread at all"
    assert broker_threads and all(t is not ring_thread for t in broker_threads), (
        "global BUY submit_order ran on the event-loop thread — the blocking broker "
        "call was not offloaded (#1254)"
    )


def _make_loop(api):
    """Bare TradingLoopMixin instance (no __init__) wired with just enough state
    for the broker-offload assertions."""
    from core.engine.trading_loop import TradingLoopMixin

    obj = TradingLoopMixin.__new__(TradingLoopMixin)
    obj.api = api
    obj._log_strategy_thought = MagicMock()
    return obj


async def test_live_loop_get_clock_is_offloaded_via_to_thread():
    """The per-cycle market-hours `get_clock` in `live_trading_loop` must be
    offloaded via `asyncio.to_thread` — it is the worst offender (runs once per
    trading cycle and would otherwise block the entire engine loop). A regression
    that unwraps it fails this test (#1254)."""
    import threading

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
    clock.is_open = True  # market open → skip the closed/sleep-300s branch
    api.get_clock.return_value = clock

    obj = _make_loop(api)
    obj._skipped_symbols = set()
    obj.strategy_running = threading.Event()
    obj.strategy_running.set()
    obj._shutdown_event = threading.Event()
    obj._startup_health_check = AsyncMock()
    obj._hitl_day_rollover = AsyncMock()
    obj.compliance_guardian = None
    obj._last_trading_day = None

    real_to_thread = asyncio.to_thread
    dispatched = []

    async def tracking_to_thread(fn, *args, **kwargs):
        dispatched.append(fn)
        # Stop the loop the instant the clock is fetched: exercises exactly the
        # get_clock path without running a full (heavily-mocked) trading cycle.
        if fn is api.get_clock:
            obj._shutdown_event.set()
        return await real_to_thread(fn, *args, **kwargs)

    with patch("asyncio.to_thread", tracking_to_thread):
        await obj.live_trading_loop()

    assert (
        api.get_clock in dispatched
    ), "get_clock was not offloaded via asyncio.to_thread"
    api.get_clock.assert_called()


async def test_graceful_handover_get_all_positions_is_offloaded_via_to_thread():
    """`get_all_positions` in `_perform_graceful_handover` must be offloaded via
    `asyncio.to_thread`, not run directly on the event loop (#1254)."""
    api = MagicMock()
    api.get_all_positions.return_value = []

    obj = _make_loop(api)
    registry = MagicMock()
    registry.has_pending_swap.return_value = True
    obj.agent_registry = registry

    real_to_thread = asyncio.to_thread
    dispatched = []

    async def tracking_to_thread(fn, *args, **kwargs):
        dispatched.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch("asyncio.to_thread", tracking_to_thread):
        await obj._perform_graceful_handover()

    assert (
        api.get_all_positions in dispatched
    ), "get_all_positions was not offloaded via asyncio.to_thread"
    api.get_all_positions.assert_called_once()


async def test_tenant_live_submit_order_is_offloaded_via_to_thread():
    """The blocking broker `submit_order` on the per-tenant LIVE path
    (`_execute_tenant_order`, non-shadow) must not run on the event loop (#1254).
    A regression that unwraps the offload fails this test.

    #3447: Der Aufruf laeuft jetzt durch das ``OrderGateway``; an ``asyncio.to_thread``
    geht darum das Tor, nicht mehr ``client.submit_order`` selbst. Gemessen wird deshalb
    die **Eigenschaft** — auf welchem Thread der Broker-Aufruf tatsaechlich lief — und
    nicht mehr, welches Callable uebergeben wurde. Das ist die strengere Pruefung: Sie
    faengt auch ein Tor, das zwar ausgelagert aufgerufen wird, den Broker aber auf den
    Ereignisring zurueckreicht.
    """
    import threading

    from alpaca.trading.enums import OrderStatus

    ring_thread = threading.current_thread()
    broker_threads = []

    def _submit(_request):
        broker_threads.append(threading.current_thread())
        return MagicMock(id="order-789")

    client = MagicMock()
    client.get_account.return_value = MagicMock(cash="100000")
    client.submit_order.side_effect = _submit
    # Order fills on the first poll → the lifecycle loop exits immediately.
    client.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)

    executor = _make_executor(MagicMock())
    rm = MagicMock()
    rm.calculate_position_size.return_value = 2.0
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "ok", None)
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

    tenant = {"user_id": "u-1", "client": client, "equity": 100000.0}
    event = _make_buy_event(qty=2.0)

    real_to_thread = asyncio.to_thread
    dispatched = []

    async def tracking_to_thread(fn, *args, **kwargs):
        dispatched.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch(
        "core.engine.order_executor.RedisClient.get_redis",
        new=AsyncMock(return_value=None),
    ), patch(
        "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
    ), patch(
        "core.engine.order_executor.kill_switch",
        # #3447: Das Tor fragt den Halt selbst ab. Ein nackter MagicMock waere wahr —
        # und „gehalten" ist dort zu Recht die sichere Richtung.
        MagicMock(is_halted=MagicMock(return_value=False)),
    ), patch(
        "core.engine.order_executor.config.SHADOW_MODE", False, create=True
    ), patch(
        "asyncio.to_thread", tracking_to_thread
    ):
        await executor._execute_tenant_order(tenant, event, source="ai")

    client.submit_order.assert_called_once()
    assert dispatched, "nothing was offloaded via asyncio.to_thread at all"
    assert broker_threads and all(t is not ring_thread for t in broker_threads), (
        "tenant live submit_order ran on the event-loop thread — the blocking broker "
        "call was not offloaded (#1254)"
    )
