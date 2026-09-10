# tests/unit/test_lstm_bar_store_warmup.py
# LSTM-panel-starvation fix (docs/lstm-panel-starvation/implementation_plan.md)
# P1 / trading-path. TDD Red->Green.
#
# The LSTM ranking runs on DAILY bars (static intraday). Re-fetching ~500 symbols'
# history every ~75s cycle rate-limits the free Alpaca IEX feed -> empty panel ->
# all-HOLD. The fix warms a local bar cache ONCE per trading day so the per-cycle
# reads are cache hits (provider-independent on the hot path), and pins that an
# empty ranking never clears a populated panel (sticky panel).
#
# § 12 Test-Freshness: on changes to core/data_provider.py::warm_universe_cache,
# core/engine/trading_loop.py::_warm_lstm_bar_cache, or the sticky-panel guard in
# core/strategies/lstm_strategy.py::update_lstm_rankings, update this file.

from __future__ import annotations

import importlib.util
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import pytz

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"

_END = datetime(2024, 5, 10)
_DAYS = 260  # SEQUENCE_LENGTH(60) + 200 — the SAME days _get_torch_prediction passes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _two_symbol_bars(close_a: float = 102.0, close_b: float = 205.0):
    """A 2-symbol (AAPL, MSFT) Alpaca-style MultiIndex (symbol, timestamp) frame,
    with DISTINCT closes so a downstream ranking does not trip the collapse guard."""
    dates = pd.date_range("2024-01-01", periods=120)
    frames = []
    for sym, close in (("AAPL", close_a), ("MSFT", close_b)):
        idx = pd.MultiIndex.from_arrays(
            [[sym] * len(dates), dates], names=["symbol", "timestamp"]
        )
        frames.append(
            pd.DataFrame(
                {
                    "open": [close - 2] * len(dates),
                    "high": [close + 3] * len(dates),
                    "low": [close - 5] * len(dates),
                    "close": [close] * len(dates),
                    "volume": [500_000] * len(dates),
                },
                index=idx,
            )
        )
    return pd.concat(frames)


def _mock_api_returning(df: pd.DataFrame) -> MagicMock:
    bars = MagicMock()
    bars.df = df
    api = MagicMock()
    api.get_stock_bars.return_value = bars
    return api


def _load_oss_config():
    spec = importlib.util.spec_from_file_location("config_oss_warmup_test", _OSS_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Test 1 — warm-up fills the cache; a later get_data is a HIT (no 2nd fetch)
# ---------------------------------------------------------------------------
class TestWarmFillsCache:
    def test_warm_fills_both_symbols_and_get_data_is_cache_hit(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        api = _mock_api_returning(_two_symbol_bars())
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            warmed = dp.warm_universe_cache(["AAPL", "MSFT"], _END, _DAYS)

            assert warmed == 2
            assert f"AAPL_2024-05-10_{_DAYS}" in dp.data_cache
            assert f"MSFT_2024-05-10_{_DAYS}" in dp.data_cache

            calls_after_warm = api.get_stock_bars.call_count
            res = dp.get_data("AAPL", _END, _DAYS)

        assert not res.empty
        # provider-independent read: get_data served from cache, NO second fetch
        assert api.get_stock_bars.call_count == calls_after_warm

    def test_warm_writes_parquet_to_disk(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        api = _mock_api_returning(_two_symbol_bars())
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            dp.warm_universe_cache(["AAPL", "MSFT"], _END, _DAYS)

        # restart-robustness: bars persisted per symbol (same file get_data reads)
        assert (tmp_path / "AAPL.parquet").exists()
        assert (tmp_path / "MSFT.parquet").exists()

    def test_no_api_returns_zero(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=None)
            assert dp.warm_universe_cache(["AAPL", "MSFT"], _END, _DAYS) == 0

    def test_empty_symbols_returns_zero(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        api = _mock_api_returning(_two_symbol_bars())
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            assert dp.warm_universe_cache([], _END, _DAYS) == 0
        api.get_stock_bars.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — cache-key parity: warm writes the EXACT key get_data checks first
# ---------------------------------------------------------------------------
class TestCacheKeyParity:
    def test_warm_key_equals_get_data_key(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        api = _mock_api_returning(_two_symbol_bars())
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            dp.warm_universe_cache(["AAPL"], _END, _DAYS)

            expected_key = f"AAPL_{_END.strftime('%Y-%m-%d')}_{_DAYS}"
            assert expected_key in dp.data_cache
            cached = dp.data_cache[expected_key]

            # get_data computes the SAME key first (:336-343) and returns the cache
            got = dp.get_data("AAPL", _END, _DAYS)

        pd.testing.assert_frame_equal(got, cached)
        # only the warm-up's single batched fetch ever ran
        assert api.get_stock_bars.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — chunking: N symbols -> ceil(N/chunk) batched requests
# ---------------------------------------------------------------------------
class TestChunking:
    def test_450_symbols_makes_ceil_requests(self, tmp_path):
        from core.data_provider import WARM_CACHE_CHUNK_SIZE, HistoricalDataProvider

        # Empty frame per chunk -> nothing warmed, but each chunk still issues ONE request.
        api = _mock_api_returning(pd.DataFrame())
        symbols = [f"SYM{i}" for i in range(450)]
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            warmed = dp.warm_universe_cache(symbols, _END, _DAYS)

        expected = math.ceil(450 / WARM_CACHE_CHUNK_SIZE)
        assert api.get_stock_bars.call_count == expected
        assert warmed == 0


# ---------------------------------------------------------------------------
# Test 3b — date-aware disk depth (restart-robustness): a parquet whose newest
#           bar is TODAY 00:00 must satisfy get_data's depth check for an
#           INTRADAY end (today 14:30) -> disk hit, no live per-symbol refetch.
# ---------------------------------------------------------------------------
class TestDateAwareDiskDepth:
    def test_disk_hit_when_end_is_intraday_today(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        days = 100
        today = pd.Timestamp.now().normalize()  # today 00:00 (daily-bar stamp)
        end_intraday = (today + pd.Timedelta(hours=14, minutes=30)).to_pydatetime()

        # Deep parquet covering [today-150 .. today 00:00] — newest bar is today 00:00.
        dates = pd.date_range(today - pd.Timedelta(days=days + 50), today)
        df = pd.DataFrame(
            {
                "open": [100.0] * len(dates),
                "high": [105.0] * len(dates),
                "low": [97.0] * len(dates),
                "close": [102.0] * len(dates),
                "volume": [500_000] * len(dates),
            },
            index=dates,
        )
        df.to_parquet(str(tmp_path / "AAPL.parquet"))

        api = MagicMock()  # would be used if the depth check wrongly failed
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ), patch("core.data_provider.DATABENTO_ENABLED", False):
            dp = HistoricalDataProvider(api=api)
            res = dp.get_data("AAPL", end_intraday, days)

        assert not res.empty
        # intraday end must NOT force a live refetch when disk has today's daily bar
        api.get_stock_bars.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 — fail-safe (§5.6): fetch raises -> return 0, WARNING, per-symbol
#          get_data still works (byte-identical old path preserved)
# ---------------------------------------------------------------------------
class TestFailSafe:
    def test_fetch_raises_returns_zero_and_warns(self, tmp_path, caplog):
        from core.data_provider import HistoricalDataProvider

        api = MagicMock()
        api.get_stock_bars.side_effect = RuntimeError("Alpaca down")
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            with caplog.at_level(logging.WARNING):
                warmed = dp.warm_universe_cache(["AAPL", "MSFT"], _END, _DAYS)

        assert warmed == 0
        assert any(
            r.levelno == logging.WARNING and "warm" in r.getMessage().lower()
            for r in caplog.records
        ), "warm-up failure must log at WARNING (§5.6)"

    def test_per_symbol_get_data_still_works_after_warm_failure(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        api = MagicMock()
        api.get_stock_bars.side_effect = RuntimeError("Alpaca down")
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ), patch("core.data_provider.DATABENTO_ENABLED", False):
            dp = HistoricalDataProvider(api=api)
            assert dp.warm_universe_cache(["AAPL"], _END, _DAYS) == 0

            # warm never raised and left NOTHING poisoned in the cache; the per-symbol
            # fallback path is fully intact — here it fetches (side_effect now cleared).
            dates = pd.date_range("2024-01-01", periods=120)
            single = pd.DataFrame(
                {
                    "open": [100.0] * 120,
                    "high": [105.0] * 120,
                    "low": [97.0] * 120,
                    "close": [102.0] * 120,
                    "volume": [500_000] * 120,
                },
                index=dates,
            )
            bars = MagicMock()
            bars.df = single
            api.get_stock_bars.side_effect = None
            api.get_stock_bars.return_value = bars

            res = dp.get_data("AAPL", _END, _DAYS)

        assert not res.empty
        assert api.get_stock_bars.called


# ---------------------------------------------------------------------------
# Test 5 — sticky panel (Part 3): an EMPTY ranking must NOT overwrite a
#          populated panel; a non-empty ranking DOES record (guard semantics)
# ---------------------------------------------------------------------------
class TestStickyPanel:
    def _fake_strategy(self, rank_cache=None):
        return SimpleNamespace(
            np=np,
            torch_model=object(),  # truthy -> update_lstm_rankings proceeds
            _lstm_rank_cache=list(rank_cache or []),
            _allocation_weights={},
        )

    @pytest.mark.anyio
    async def test_empty_ranking_does_not_call_record_cycle(self):
        import config as _cfg
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        fake = self._fake_strategy()
        mock_store = MagicMock()
        # snapshots empty -> every symbol skipped -> _lstm_rank_cache ends up []
        with patch(
            "core.report.lstm_panel_store.get_store", return_value=mock_store
        ), patch.object(_cfg.get_config(), "REPORT_GENERATOR_V2_ENABLED", True):
            await LSTMDynamicStrategy.update_lstm_rankings(
                fake, ["AAPL", "MSFT"], {}, {}, _END
            )

        assert fake._lstm_rank_cache == []
        mock_store.record_cycle.assert_not_called()

    @pytest.mark.anyio
    async def test_prior_snapshot_retained_when_cycle_empty(self):
        import config as _cfg
        from core.report.lstm_panel_store import get_store
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        store = get_store()
        store.reset()
        store.record_cycle(datetime(2024, 5, 9), [("AAPL", 1.5), ("MSFT", 0.3)])
        prior = store.cross_section_at(_END)
        assert prior == {"AAPL": 1.5, "MSFT": 0.3}

        fake = self._fake_strategy(rank_cache=[("AAPL", 9.9)])
        with patch.object(_cfg.get_config(), "REPORT_GENERATOR_V2_ENABLED", True):
            await LSTMDynamicStrategy.update_lstm_rankings(
                fake, ["AAPL", "MSFT"], {}, {}, _END
            )

        assert fake._lstm_rank_cache == []
        # the populated panel survives the empty cycle
        assert store.cross_section_at(_END) == prior
        store.reset()

    @pytest.mark.anyio
    async def test_nonempty_ranking_does_record(self):
        """Positive control: the guard is what gates recording — a real ranking IS
        recorded (so the empty-skip above is the guard, not a broken writer)."""
        import config as _cfg
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        fake = self._fake_strategy()
        fake._get_torch_prediction = AsyncMock(side_effect=[(1.5, None), (0.3, None)])
        snap = SimpleNamespace(latest_trade=SimpleNamespace(price=150.0, p=150.0))
        snapshots = {"AAPL": snap, "MSFT": snap}
        mock_store = MagicMock()
        with patch(
            "core.report.lstm_panel_store.get_store", return_value=mock_store
        ), patch.object(_cfg.get_config(), "REPORT_GENERATOR_V2_ENABLED", True):
            await LSTMDynamicStrategy.update_lstm_rankings(
                fake, ["AAPL", "MSFT"], snapshots, {}, _END
            )

        assert len(fake._lstm_rank_cache) == 2
        mock_store.record_cycle.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6 — wiring: the flag gates the warm-up; days == seq_len + 200; and the
#          loop invokes it on the NY-date rollover (once/day), not every cycle
# ---------------------------------------------------------------------------
class TestWiringHelper:
    def _make_helper_self(self, provider):
        return SimpleNamespace(data_provider=provider)

    @pytest.mark.anyio
    async def test_flag_on_warms_with_days_seq_len_plus_200(self):
        import config as _cfg
        from core.engine.trading_loop import TradingLoopMixin

        provider = MagicMock()
        provider.warm_universe_cache = MagicMock(return_value=2)
        strat = SimpleNamespace(
            symbols=["AAPL", "MSFT"], data_provider=provider, sequence_length=20
        )
        with patch.object(_cfg.get_config(), "LSTM_BAR_STORE_WARMUP_ENABLED", True):
            await TradingLoopMixin._warm_lstm_bar_cache(
                self._make_helper_self(provider), strat
            )

        provider.warm_universe_cache.assert_called_once()
        args, _ = provider.warm_universe_cache.call_args
        assert list(args[0]) == ["AAPL", "MSFT"]  # active universe
        assert args[2] == 20 + 200  # days == sequence_length + 200

    @pytest.mark.anyio
    async def test_flag_on_uses_default_seq_len_when_absent(self):
        import config as _cfg
        from core.engine.trading_loop import TradingLoopMixin
        from core.strategies.lstm_strategy import SEQUENCE_LENGTH

        provider = MagicMock()
        provider.warm_universe_cache = MagicMock(return_value=1)
        strat = SimpleNamespace(symbols=["AAPL"], data_provider=provider)
        with patch.object(_cfg.get_config(), "LSTM_BAR_STORE_WARMUP_ENABLED", True):
            await TradingLoopMixin._warm_lstm_bar_cache(
                self._make_helper_self(provider), strat
            )

        args, _ = provider.warm_universe_cache.call_args
        assert args[2] == SEQUENCE_LENGTH + 200

    @pytest.mark.anyio
    async def test_flag_off_never_warms(self):
        import config as _cfg
        from core.engine.trading_loop import TradingLoopMixin

        provider = MagicMock()
        provider.warm_universe_cache = MagicMock(return_value=0)
        strat = SimpleNamespace(
            symbols=["AAPL"], data_provider=provider, sequence_length=60
        )
        with patch.object(_cfg.get_config(), "LSTM_BAR_STORE_WARMUP_ENABLED", False):
            await TradingLoopMixin._warm_lstm_bar_cache(
                self._make_helper_self(provider), strat
            )

        provider.warm_universe_cache.assert_not_called()

    @pytest.mark.anyio
    async def test_warm_failure_is_non_fatal(self):
        import config as _cfg
        from core.engine.trading_loop import TradingLoopMixin

        provider = MagicMock()
        provider.warm_universe_cache = MagicMock(side_effect=RuntimeError("boom"))
        strat = SimpleNamespace(
            symbols=["AAPL"], data_provider=provider, sequence_length=60
        )
        with patch.object(_cfg.get_config(), "LSTM_BAR_STORE_WARMUP_ENABLED", True):
            # must NOT raise — a warm-up failure can never break the loop
            await TradingLoopMixin._warm_lstm_bar_cache(
                self._make_helper_self(provider), strat
            )


# ---------------------------------------------------------------------------
# Test 6b — wiring in live_trading_loop: warm on NY-date rollover (once/day)
# ---------------------------------------------------------------------------
def _make_loop_mixin(*, market_open=False):
    """Minimal TradingLoopMixin instance that runs ONE closed-market cycle then stops."""
    import threading

    from core.engine.trading_loop import TradingLoopMixin

    strategy = MagicMock()
    strategy.strategy_name = "RLAgent"
    strategy.symbols = ["AAPL", "MSFT"]
    strategy.risk_manager = MagicMock()
    strategy.risk_manager.trading_halted = False
    strategy.update_lstm_rankings = AsyncMock()

    snap_obj = MagicMock()
    snap_obj.latest_trade = MagicMock(price=150.0, p=150.0)
    data_api = MagicMock()
    data_api.get_stock_snapshot.return_value = {"AAPL": snap_obj, "MSFT": snap_obj}

    shutdown_flags = [False]
    shutdown = MagicMock()
    shutdown.is_set.side_effect = lambda: shutdown_flags[0]
    running = MagicMock()
    running.is_set.return_value = True

    clock = MagicMock()
    clock.is_open = market_open
    clock.next_open.strftime.return_value = "2026-03-10 14:30:00 UTC"
    api = MagicMock()
    api.get_clock.return_value = clock

    mixin = TradingLoopMixin.__new__(TradingLoopMixin)
    mixin.active_strategy = strategy
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
    mixin.specialist_registry = MagicMock()
    mixin.api = api
    mixin.data_api = data_api
    mixin.current_market_data = {}
    mixin._startup_health_check = AsyncMock(return_value=None)
    mixin._process_signal_event = AsyncMock()
    mixin._warm_lstm_bar_cache = AsyncMock()
    return mixin, shutdown_flags


class TestWiringLoop:
    @pytest.mark.anyio
    async def test_warm_called_on_ny_date_rollover(self):
        import config as _cfg

        mixin, shutdown_flags = _make_loop_mixin(market_open=False)
        # Different from today's NY date -> the rollover hook fires this cycle.
        mixin._last_trading_day = datetime.now(
            pytz.timezone("America/New_York")
        ).date() - timedelta(days=1)

        def _sleep(s):
            if s >= 60:
                shutdown_flags[0] = True

        with patch(
            "core.engine.trading_loop.asyncio.sleep", AsyncMock(side_effect=_sleep)
        ), patch.object(_cfg.get_config(), "MARKET_CLOSED_REPORT_ONLY", True):
            await mixin.live_trading_loop()

        mixin._warm_lstm_bar_cache.assert_awaited()
        # warmed with the active strategy (universe resolved from active_strategy)
        assert mixin._warm_lstm_bar_cache.await_args.args[0] is mixin.active_strategy

    @pytest.mark.anyio
    async def test_warm_not_called_when_same_ny_day(self):
        import config as _cfg

        mixin, shutdown_flags = _make_loop_mixin(market_open=False)
        # Already on today's NY date -> no rollover -> no warm this cycle (once/day).
        mixin._last_trading_day = datetime.now(pytz.timezone("America/New_York")).date()

        def _sleep(s):
            if s >= 60:
                shutdown_flags[0] = True

        with patch(
            "core.engine.trading_loop.asyncio.sleep", AsyncMock(side_effect=_sleep)
        ), patch.object(_cfg.get_config(), "MARKET_CLOSED_REPORT_ONLY", True):
            await mixin.live_trading_loop()

        mixin._warm_lstm_bar_cache.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 7 — end-to-end: a warmed store yields a NON-empty ranking with ZERO
#          per-symbol live fetches (the outage regression)
# ---------------------------------------------------------------------------
class TestEndToEnd:
    @pytest.mark.anyio
    async def test_warmed_store_yields_nonempty_ranking_zero_fetches(self, tmp_path):
        import config as _cfg
        from core.data_provider import HistoricalDataProvider
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        api = _mock_api_returning(_two_symbol_bars(close_a=102.0, close_b=205.0))
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=api)
            assert dp.warm_universe_cache(["AAPL", "MSFT"], _END, _DAYS) == 2
            fetches_after_warm = api.get_stock_bars.call_count  # one batched chunk

            # _get_torch_prediction reads the SAME (symbol, end, days) get_data key the
            # warm-up wrote -> a cache hit; its value derives from the fetched close so a
            # miss would surface as an abstain (None), not a false pass.
            async def fake_pred(symbol, current_time, market_data):
                hist = dp.get_data(symbol, current_time, _DAYS)
                if hist is None or hist.empty:
                    return None, None
                return float(hist["close"].iloc[-1]), None

            fake = SimpleNamespace(
                np=np,
                torch_model=object(),
                _get_torch_prediction=fake_pred,
                _lstm_rank_cache=[],
                _allocation_weights={},
            )
            snap = SimpleNamespace(latest_trade=SimpleNamespace(price=150.0, p=150.0))
            snapshots = {"AAPL": snap, "MSFT": snap}
            with patch.object(_cfg.get_config(), "REPORT_GENERATOR_V2_ENABLED", False):
                await LSTMDynamicStrategy.update_lstm_rankings(
                    fake, ["AAPL", "MSFT"], snapshots, {}, _END
                )

        assert len(fake._lstm_rank_cache) == 2  # panel NON-empty
        # ZERO per-symbol live fetches beyond the single once/day warm-up batch
        assert api.get_stock_bars.call_count == fetches_after_warm


# ---------------------------------------------------------------------------
# Test 8 — config parity: LSTM_BAR_STORE_WARMUP_ENABLED in BOTH editions, ON
# ---------------------------------------------------------------------------
class TestConfigParity:
    def test_enterprise_flag_default_true(self):
        import config as enterprise

        assert enterprise.get_config().LSTM_BAR_STORE_WARMUP_ENABLED is True

    def test_oss_flag_default_true(self):
        oss = _load_oss_config().get_config()
        assert oss.LSTM_BAR_STORE_WARMUP_ENABLED is True

    def test_both_editions_agree(self):
        import config as enterprise

        ent = enterprise.get_config().LSTM_BAR_STORE_WARMUP_ENABLED
        oss = _load_oss_config().get_config().LSTM_BAR_STORE_WARMUP_ENABLED
        assert ent == oss is True
