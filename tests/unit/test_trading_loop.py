# tests/unit/test_trading_loop.py
# Epic 1.7 / PR-C — TDD Red-Phase
# Tests für TradingLoopMixin (wird nach core/engine/trading_loop.py extrahiert)
#
# Gherkin-Kriterien:
#   Given: BotEngine mit gemockten Clients
#   When:  live_trading_loop läuft einen Zyklus
#   Then:  Korrektes Verhalten je nach Markt/Strategie-Zustand


from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# autouse-Fixture: kill_switch immer als nicht-halted mocken
# Verhindert dass Singleton-State aus test_kill_switch.py die Loop abbricht
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_kill_switch_not_halted():
    """Patch kill_switch.is_halted() to always return False in all TradingLoop tests."""
    with patch("core.engine.trading_loop.kill_switch", create=True) as mock_ks:
        mock_ks.is_halted.return_value = False
        yield mock_ks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine(market_open=True, has_strategy=True, has_symbols=True):
    """Erstellt eine minimale BotEngine-ähnliche Instanz zum Testen der Loop."""
    from core.engine.base import BotEngine

    engine = MagicMock(spec=BotEngine)
    engine.strategy_running = MagicMock()
    engine.strategy_running.is_set.return_value = True
    engine._shutdown_event = MagicMock()
    engine._shutdown_event.is_set.return_value = False
    engine.strategy_lock = MagicMock()
    engine.strategy_lock.__enter__ = MagicMock(return_value=None)
    engine.strategy_lock.__exit__ = MagicMock(return_value=False)
    engine._skipped_symbols = set()
    engine._cycle_latencies = []
    engine._last_cycle_details = {}
    engine.cloud_logger = MagicMock()
    engine.cloud_logger.log_latency_metric = MagicMock()
    engine._log_strategy_thought = MagicMock()
    engine._send_update_threadsafe = MagicMock()
    # Startup health check is async — must be AsyncMock so live_trading_loop can await it
    engine._startup_health_check = AsyncMock(return_value=None)

    # Alpaca Client
    clock = MagicMock()
    clock.is_open = market_open
    clock.next_open.strftime.return_value = "2026-03-10 14:30:00 UTC"
    engine.api = MagicMock()
    engine.api.get_clock.return_value = clock

    # Active Strategy
    if has_strategy:
        strategy = MagicMock()
        strategy.symbols = ["AAPL", "MSFT"] if has_symbols else []
        strategy.risk_manager = MagicMock()
        strategy.risk_manager.trading_halted = False
        strategy.strategy_name = "RLAgent"
        strategy.update_lstm_rankings = AsyncMock()  # Fix TypeError when awaited
        engine.active_strategy = strategy
    else:
        engine.active_strategy = None

    engine.data_api = MagicMock()
    engine.current_market_data = {}
    return engine


# ---------------------------------------------------------------------------
# 1. Market Closed → 300s sleep
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestMarketClosed:
    @pytest.mark.anyio
    async def test_market_closed_sleeps_and_continues(self):
        """
        Given: Markt ist geschlossen (is_open=False)
        When:  live_trading_loop wird aufgerufen
        Then:  300s sleep wird gerufen, kein Dispatch
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine = _make_engine(market_open=False)
        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.__dict__.update(engine.__dict__)
        mixin._shutdown_event = engine._shutdown_event

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if seconds >= 5:
                # Shutdown nach erstem echten Application-Sleep
                mixin._shutdown_event.is_set.return_value = True
                engine._shutdown_event.is_set.return_value = True

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep):
            await mixin.live_trading_loop()

        assert 300 in sleep_calls, "Soll 300s schlafen wenn Markt zu"
        engine.data_api.get_stock_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# 1b. BYPASS_MARKET_HOURS=True → loop proceeds even when market closed
#     (off-hours paper-trading smoke tests; default False — prod always honours
#     market hours)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestBypassMarketHours:
    @pytest.mark.anyio
    async def test_bypass_market_hours_proceeds_when_closed(self):
        """
        Given: Markt ist geschlossen (is_open=False) UND BYPASS_MARKET_HOURS=True
        When:  live_trading_loop läuft
        Then:  Kein 300s-sleep — die Loop fällt durch zur Strategy-Ebene
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine = _make_engine(market_open=False, has_strategy=False)
        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.__dict__.update(engine.__dict__)
        mixin._shutdown_event = engine._shutdown_event

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)
            # Exit on the first non-300 sleep so the test terminates
            if seconds != 300 and seconds >= 5:
                mixin._shutdown_event.is_set.return_value = True
                engine._shutdown_event.is_set.return_value = True

        with patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", True), patch(
            "core.engine.trading_loop.asyncio.sleep", mock_sleep
        ):
            await mixin.live_trading_loop()

        assert (
            300 not in sleep_calls
        ), "Soll NICHT 300s schlafen wenn BYPASS_MARKET_HOURS=True"


# ---------------------------------------------------------------------------
# 2. Kein active_strategy → 5s sleep
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestNoActiveStrategy:
    @pytest.mark.anyio
    async def test_no_strategy_sleeps_5s(self):
        """
        Given: engine.active_strategy ist None
        When:  live_trading_loop läuft
        Then:  5s sleep, kein Snapshot-Fetch
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine = _make_engine(has_strategy=False)
        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.__dict__.update(engine.__dict__)

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if seconds >= 5:
                engine._shutdown_event.is_set.return_value = True

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep):
            await mixin.live_trading_loop()

        assert 5 in sleep_calls
        engine.data_api.get_stock_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Shutdown-Event → Loop bricht ab
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestShutdownEvent:
    @pytest.mark.anyio
    async def test_shutdown_breaks_loop(self):
        """
        Given: _shutdown_event ist sofort gesetzt
        When:  live_trading_loop startet
        Then:  Loop beendet sich sofort ohne Dispatch
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine = _make_engine()
        engine._shutdown_event.is_set.return_value = True  # Sofort shutdown
        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.__dict__.update(engine.__dict__)

        await mixin.live_trading_loop()

        engine.data_api.get_stock_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Symbol fehlt in Snapshots → überspringen
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestMissingSnapshot:
    @pytest.mark.anyio
    async def test_symbol_without_snapshot_is_skipped(self):
        """
        Given: Symbol AAPL ist nicht in snapshots-Response
        When:  live_trading_loop verarbeitet Symbole
        Then:  AAPL wird zu _skipped_symbols hinzugefügt, kein Task
        """
        from core.engine.trading_loop import TradingLoopMixin

        engine = _make_engine(market_open=True)
        # Keine Snapshots zurückgeben
        engine.data_api.get_stock_snapshot.return_value = {}

        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.__dict__.update(engine.__dict__)

        engine.active_strategy.run_for_symbol = AsyncMock(return_value=None)

        async def mock_sleep(s):
            if s >= 5:
                engine._shutdown_event.is_set.return_value = True

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep):
            await mixin.live_trading_loop()

        engine.active_strategy.run_for_symbol.assert_not_called()


# ---------------------------------------------------------------------------
# 5. LSTMDynamic → sequenziell (nicht gather)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestLSTMSequential:
    @pytest.mark.anyio
    async def test_lstm_strategy_runs_tasks_sequentially(self):
        """
        Given: active_strategy.strategy_name == "LSTMDynamic"
        When:  live_trading_loop dispatcht Tasks
        Then:  Tasks werden sequenziell ausgeführt (nicht gather)
        """
        import threading

        from core.engine.trading_loop import TradingLoopMixin

        run_mock = AsyncMock(return_value=None)

        # Strategie-Mock
        strategy = MagicMock()
        strategy.strategy_name = "LSTMDynamic"
        strategy.symbols = ["AAPL", "MSFT"]
        strategy.risk_manager = MagicMock()
        strategy.risk_manager.trading_halted = False
        strategy._lstm_rank_cache = [("AAPL", 0.9), ("MSFT", 0.7)]
        strategy.run_for_symbol = run_mock
        strategy.update_lstm_rankings = AsyncMock()  # muss awaitable sein

        # Snapshot-Mock mit daily_bar (Fail-Fast Guard benötigt high != low)
        lt = MagicMock()
        lt.price = 150.0
        lt.p = 150.0
        lt.size = 100
        bar = MagicMock()
        bar.open = 148.0
        bar.high = 152.0
        bar.low = 147.0
        bar.close = 150.0
        bar.volume = 500000.0
        snap_obj = MagicMock()
        snap_obj.latest_trade = lt
        snap_obj.daily_bar = bar

        data_api = MagicMock()
        data_api.get_stock_snapshot.return_value = {"AAPL": snap_obj, "MSFT": snap_obj}

        # Shutdown nach erstem Sleep (nach dem Zyklus)
        shutdown_flags = [False]
        shutdown = MagicMock()
        shutdown.is_set.side_effect = lambda: shutdown_flags[0]

        running = MagicMock()
        running.is_set.return_value = True

        api = MagicMock()
        clock = MagicMock()
        clock.is_open = True
        api.get_clock.return_value = clock

        cloud_logger = MagicMock()
        cloud_logger.log_latency_metric = MagicMock()

        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.active_strategy = strategy
        mixin._shutdown_event = shutdown
        mixin.strategy_running = running
        mixin.strategy_lock = threading.Lock()
        mixin._skipped_symbols = set()
        mixin._cycle_latencies = []
        mixin._last_cycle_details = {}
        mixin.cloud_logger = cloud_logger
        mixin._log_strategy_thought = MagicMock()
        mixin._send_update_threadsafe = MagicMock()
        mixin.api = api
        mixin.data_api = data_api
        mixin.current_market_data = {}
        mixin._process_signal_event = AsyncMock()

        async def mock_sleep(s):
            # Shutdown nach dem ersten Sleep (am Ende des Zyklus = sleep 60)
            if s >= 60:
                shutdown_flags[0] = True

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep), patch(
            "asyncio.gather",
            side_effect=AssertionError("LSTMDynamic darf gather nicht nutzen"),
        ):
            await mixin.live_trading_loop()

        # Genau 2 Calls: AAPL und MSFT, in rank-Reihenfolge
        assert (
            run_mock.call_count == 2
        ), f"Expected 2 run_for_symbol calls, got {run_mock.call_count}"
        call_symbols = [call[0][0] for call in run_mock.call_args_list]
        assert call_symbols == [
            "AAPL",
            "MSFT",
        ], f"Expected AAPL, MSFT. Got: {call_symbols}"


# ---------------------------------------------------------------------------
# Issue #217 — Red-Phase: LangGraph-Integration in TradingLoop
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestLangGraphDispatch:
    """
    🔴 Red-Phase Issue #217:
    Tests beschreiben das Wunschverhalten NACH dem Umbau auf LangGraph.
    Sie schlagen fehl solange trading_loop.py noch asyncio.gather direkt nutzt.
    """

    @pytest.mark.anyio
    async def test_non_lstm_uses_graph_dispatch(self):
        """
        Given: RLAgent-Strategie mit 2 Symbolen und validen Snapshots
        When:  live_trading_loop einen Zyklus ausführt
        Then:  graph.ainvoke wird für jedes Symbol aufgerufen (nicht direktes asyncio.gather)
        """
        from core.engine.trading_loop import TradingLoopMixin

        # Strategie-Mock (Non-LSTM)
        strategy = MagicMock()
        strategy.strategy_name = "RLAgent"
        strategy.symbols = ["AAPL", "MSFT"]
        strategy.risk_manager = MagicMock()
        strategy.risk_manager.trading_halted = False
        strategy.run_for_symbol = AsyncMock(return_value=None)
        strategy.update_lstm_rankings = AsyncMock()

        # Snapshot-Mock mit daily_bar (Fail-Fast Guard benötigt high != low)
        lt = MagicMock()
        lt.price = 150.0
        lt.p = 150.0
        lt.size = 100
        bar = MagicMock()
        bar.open = 148.0
        bar.high = 152.0
        bar.low = 147.0
        bar.close = 150.0
        bar.volume = 500000.0
        snap_obj = MagicMock()
        snap_obj.latest_trade = lt
        snap_obj.daily_bar = bar

        data_api = MagicMock()
        data_api.get_stock_snapshot.return_value = {"AAPL": snap_obj, "MSFT": snap_obj}

        shutdown_flags = [False]
        shutdown = MagicMock()
        shutdown.is_set.side_effect = lambda: shutdown_flags[0]
        running = MagicMock()
        running.is_set.return_value = True

        api = MagicMock()
        clock = MagicMock()
        clock.is_open = True
        api.get_clock.return_value = clock

        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.active_strategy = strategy
        mixin._shutdown_event = shutdown
        mixin.strategy_running = running
        mixin.strategy_lock = __import__("threading").Lock()
        mixin._skipped_symbols = set()
        mixin._cycle_latencies = []
        mixin._last_cycle_details = {}
        mixin.cloud_logger = MagicMock()
        mixin.cloud_logger.log_latency_metric = MagicMock()
        mixin._log_strategy_thought = MagicMock()
        mixin._send_update_threadsafe = MagicMock()
        mixin.api = api
        mixin.data_api = data_api
        mixin.current_market_data = {}
        mixin._process_signal_event = AsyncMock()

        graph_ainvoke_calls = []

        async def mock_graph_ainvoke(state, **kwargs):
            graph_ainvoke_calls.append(state["symbol"])
            return state

        mock_graph = MagicMock()
        mock_graph.ainvoke = mock_graph_ainvoke

        async def mock_sleep(s):
            if s >= 60:
                shutdown_flags[0] = True

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep), patch(
            "core.engine.trading_loop.build_symbol_eval_graph", return_value=mock_graph
        ):
            await mixin.live_trading_loop()

        assert set(graph_ainvoke_calls) == {
            "AAPL",
            "MSFT",
        }, f"Erwartet graph.ainvoke für AAPL und MSFT, got: {graph_ainvoke_calls}"

    @pytest.mark.anyio
    async def test_lstm_still_sequential_with_graph_present(self):
        """
        Given: LSTMDynamic-Strategie mit 2 Symbolen und Rank-Cache
        When:  live_trading_loop läuft (auch nachdem LangGraph eingebaut)
        Then:  Symbole werden sequenziell in Rank-Reihenfolge verarbeitet
               (LSTMDynamic bleibt IMMER sequenziell, egal ob Graph vorhanden)
        """
        from core.engine.trading_loop import TradingLoopMixin

        run_mock = AsyncMock(return_value=None)

        strategy = MagicMock()
        strategy.strategy_name = "LSTMDynamic"
        strategy.symbols = ["AAPL", "MSFT"]
        strategy.risk_manager = MagicMock()
        strategy.risk_manager.trading_halted = False
        strategy._lstm_rank_cache = [("AAPL", 0.9), ("MSFT", 0.7)]
        strategy.run_for_symbol = run_mock
        strategy.update_lstm_rankings = AsyncMock()

        # Snapshot-Mock mit daily_bar (Fail-Fast Guard benötigt high != low)
        lt = MagicMock()
        lt.price = 150.0
        lt.p = 150.0
        lt.size = 100
        bar = MagicMock()
        bar.open = 148.0
        bar.high = 152.0
        bar.low = 147.0
        bar.close = 150.0
        bar.volume = 500000.0
        snap_obj = MagicMock()
        snap_obj.latest_trade = lt
        snap_obj.daily_bar = bar

        data_api = MagicMock()
        data_api.get_stock_snapshot.return_value = {"AAPL": snap_obj, "MSFT": snap_obj}

        shutdown_flags = [False]
        shutdown = MagicMock()
        shutdown.is_set.side_effect = lambda: shutdown_flags[0]
        running = MagicMock()
        running.is_set.return_value = True

        api = MagicMock()
        clock = MagicMock()
        clock.is_open = True
        api.get_clock.return_value = clock

        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.active_strategy = strategy
        mixin._shutdown_event = shutdown
        mixin.strategy_running = running
        mixin.strategy_lock = __import__("threading").Lock()
        mixin._skipped_symbols = set()
        mixin._cycle_latencies = []
        mixin._last_cycle_details = {}
        mixin.cloud_logger = MagicMock()
        mixin.cloud_logger.log_latency_metric = MagicMock()
        mixin._log_strategy_thought = MagicMock()
        mixin._send_update_threadsafe = MagicMock()
        mixin.api = api
        mixin.data_api = data_api
        mixin.current_market_data = {}
        mixin._process_signal_event = AsyncMock()

        async def mock_sleep(s):
            if s >= 60:
                shutdown_flags[0] = True

        # LangGraph darf für LSTM NICHT aufgerufen werden — sequenziell bleibt Pflicht
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(
            side_effect=AssertionError("LSTMDynamic darf graph.ainvoke NICHT nutzen")
        )

        with patch("core.engine.trading_loop.asyncio.sleep", mock_sleep), patch(
            "core.engine.trading_loop.build_symbol_eval_graph", return_value=mock_graph
        ), patch(
            "asyncio.gather",
            side_effect=AssertionError("LSTMDynamic darf gather nicht nutzen"),
        ):
            await mixin.live_trading_loop()

        assert (
            run_mock.call_count == 2
        ), f"Expected 2 sequential calls, got {run_mock.call_count}"
        call_symbols = [call[0][0] for call in run_mock.call_args_list]
        assert call_symbols == [
            "AAPL",
            "MSFT",
        ], f"Erwartet AAPL, MSFT (Rank-Order), got: {call_symbols}"


# ---------------------------------------------------------------------------
# ML-1: SIP feed (consolidated NBBO) — MiFID II best-execution compliance
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSIPFeedConfiguration:
    """ML-1: StockSnapshotRequest must use SIP (consolidated NBBO) not IEX (single exchange)."""

    @pytest.mark.anyio
    async def test_trading_loop_uses_sip_feed(self):
        """StockSnapshotRequest must be called with feed='sip' for MiFID II best-execution.

        IEX shows only IEX prices (single exchange); SIP is the National Best Bid/Offer
        from the consolidated tape — required for best-execution evidence under MiFID II.
        ADR-D01: Alpaca SIP ($9/mo) is the live feed for ML-1 compliance.
        """
        import threading

        from core.engine.trading_loop import TradingLoopMixin

        lt = MagicMock()
        lt.price = 150.0
        lt.p = 150.0
        lt.size = 100
        bar = MagicMock()
        bar.open = 148.0
        bar.high = 152.0
        bar.low = 147.0
        bar.close = 150.0
        bar.volume = 500000.0
        snap_obj = MagicMock()
        snap_obj.latest_trade = lt
        snap_obj.daily_bar = bar

        strategy = MagicMock()
        strategy.strategy_name = "RLAgent"
        strategy.symbols = ["AAPL"]
        strategy.risk_manager = MagicMock()
        strategy.risk_manager.trading_halted = False
        strategy.run_for_symbol = AsyncMock(return_value=None)
        strategy.update_lstm_rankings = AsyncMock()

        data_api = MagicMock()
        data_api.get_stock_snapshot.return_value = {"AAPL": snap_obj}

        shutdown_flags = [False]
        shutdown = MagicMock()
        shutdown.is_set.side_effect = lambda: shutdown_flags[0]

        mixin = TradingLoopMixin.__new__(TradingLoopMixin)
        mixin.active_strategy = strategy
        mixin._shutdown_event = shutdown
        mixin.strategy_running = MagicMock()
        mixin.strategy_running.is_set.return_value = True
        mixin.strategy_lock = threading.Lock()
        mixin._skipped_symbols = set()
        mixin._cycle_latencies = []
        mixin._last_cycle_details = {}
        mixin.cloud_logger = MagicMock()
        mixin.cloud_logger.log_latency_metric = MagicMock()
        mixin._log_strategy_thought = MagicMock()
        mixin._send_update_threadsafe = MagicMock()
        mixin._startup_health_check = AsyncMock(return_value=None)
        mixin.api = MagicMock()
        clock = MagicMock()
        clock.is_open = True
        mixin.api.get_clock.return_value = clock
        mixin.data_api = data_api
        mixin.current_market_data = {}
        mixin._process_signal_event = AsyncMock()

        captured_requests = []

        original_ssr = None
        try:
            import core.engine.trading_loop as tl_mod

            original_ssr = tl_mod.StockSnapshotRequest
        except Exception:
            pass

        def capture_snapshot_request(*args, **kwargs):
            captured_requests.append(kwargs)
            return MagicMock()

        async def mock_sleep(s):
            if s >= 60:
                shutdown_flags[0] = True

        with (
            patch("core.engine.trading_loop.ALPACA_DATA_FEED", "sip"),
            patch(
                "core.engine.trading_loop.StockSnapshotRequest",
                side_effect=capture_snapshot_request,
            ),
            patch("core.engine.trading_loop.asyncio.sleep", mock_sleep),
        ):
            await mixin.live_trading_loop()

        assert captured_requests, "StockSnapshotRequest was never called"
        feeds_used = [r.get("feed") for r in captured_requests]
        assert all(f == "sip" for f in feeds_used), (
            f"Expected feed='sip' (MiFID II consolidated NBBO), got {feeds_used}. "
            "Set ALPACA_DATA_FEED=sip in config.py (ML-1)."
        )


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestDesktopEntryTimeReconcileHook:
    """#2046: durable entry-time on desktop. The #2042 reconcile hook sits only on the
    tenant path (order_executor), which the desktop/OSS fallback never enters — so on
    desktop the entry-time never reconciles and days_held stays 0. This hook reconciles
    the reachable canonical desktop PM (active_strategy.portfolio_manager) once/session.
    """

    def _mixin(self):
        from core.engine.trading_loop import TradingLoopMixin

        return TradingLoopMixin.__new__(TradingLoopMixin)

    @pytest.mark.anyio
    async def test_reconciles_active_strategy_pm_once(self):
        mixin = self._mixin()
        pm = SimpleNamespace(client=MagicMock(), _trade_history={})
        mixin.active_strategy = SimpleNamespace(portfolio_manager=pm)
        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ) as rec:
            await mixin._reconcile_active_strategy_entry_time()
            await mixin._reconcile_active_strategy_entry_time()  # 2nd call → guard
        assert rec.await_count == 1
        rec.assert_awaited_with(pm)

    @pytest.mark.anyio
    async def test_skips_when_no_pm(self):
        mixin = self._mixin()
        mixin.active_strategy = SimpleNamespace(portfolio_manager=None)
        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ) as rec:
            await mixin._reconcile_active_strategy_entry_time()
        rec.assert_not_awaited()

    @pytest.mark.anyio
    async def test_skips_when_no_client(self):
        mixin = self._mixin()
        pm = SimpleNamespace(client=None, _trade_history={})
        mixin.active_strategy = SimpleNamespace(portfolio_manager=pm)
        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ) as rec:
            await mixin._reconcile_active_strategy_entry_time()
        rec.assert_not_awaited()

    @pytest.mark.anyio
    async def test_skips_when_no_active_strategy(self):
        mixin = self._mixin()
        mixin.active_strategy = None
        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ) as rec:
            await mixin._reconcile_active_strategy_entry_time()
        rec.assert_not_awaited()

    @pytest.mark.anyio
    async def test_reconciles_new_pm_after_swap(self):
        """Strategy swap → new PM → reconcile again (guard keyed by PM identity)."""
        mixin = self._mixin()
        pm1 = SimpleNamespace(client=MagicMock(), _trade_history={})
        mixin.active_strategy = SimpleNamespace(portfolio_manager=pm1)
        with patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            new=AsyncMock(),
        ) as rec:
            await mixin._reconcile_active_strategy_entry_time()
            pm2 = SimpleNamespace(client=MagicMock(), _trade_history={})
            mixin.active_strategy = SimpleNamespace(portfolio_manager=pm2)
            await mixin._reconcile_active_strategy_entry_time()
        assert rec.await_count == 2
