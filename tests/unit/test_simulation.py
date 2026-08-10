# tests/unit/test_simulation.py
# Epic 2.3 / I-4 — TDD Coverage Backfill: core/simulation.py
# Issue #240 — Ziel: ≥60% Coverage für core/simulation.py
#
# § 12 Test-Freshness: Bei Änderungen an simulation.py immer dieses File prüfen.
# Run: pytest tests/unit/test_simulation.py --cov=core.simulation --cov-report=term-missing

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# 1. inject_market_noise()
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestInjectMarketNoise:

    def _make_ohlcv(self, n: int = 5) -> pd.DataFrame:
        """Helper: deterministic OHLCV frame."""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=n)
        return pd.DataFrame(
            {
                "open": np.full(n, 100.0),
                "high": np.full(n, 105.0),
                "low": np.full(n, 95.0),
                "close": np.full(n, 100.0),
                "volume": np.full(n, 1_000_000.0),
            },
            index=dates,
        )

    def test_noise_modifies_close(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv()
        noisy = inject_market_noise(df, noise_level=0.05)
        assert not (noisy["close"] == df["close"]).all()

    def test_zero_noise_returns_unchanged(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv()
        noisy = inject_market_noise(df, noise_level=0.0)
        pd.testing.assert_frame_equal(noisy, df)

    def test_empty_dataframe_returns_unchanged(self):
        from core.simulation import inject_market_noise

        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        noisy = inject_market_noise(df)
        assert noisy.empty

    def test_candle_high_ge_low(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv(50)
        noisy = inject_market_noise(df, noise_level=0.05)
        assert (noisy["high"] >= noisy["low"]).all()

    def test_volume_non_negative(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv(50)
        noisy = inject_market_noise(df, noise_level=0.20)
        assert (noisy["volume"] >= 0).all()

    def test_df_without_open_column(self):
        from core.simulation import inject_market_noise

        df = pd.DataFrame({"close": [100.0, 101.0, 99.0]})
        noisy = inject_market_noise(df, noise_level=0.02)
        assert "close" in noisy.columns

    def test_original_df_not_mutated(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv()
        original_close = df["close"].copy()
        inject_market_noise(df, noise_level=0.05)
        pd.testing.assert_series_equal(df["close"], original_close)

    def test_structure_preserved(self):
        from core.simulation import inject_market_noise

        df = self._make_ohlcv(20)
        noisy = inject_market_noise(df)
        assert list(noisy.columns) == list(df.columns)
        assert len(noisy) == len(df)


# ---------------------------------------------------------------------------
# 2. Trade + PendingOrder Dataclasses
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestTradeDataclass:

    def test_trade_fields(self):
        from core.simulation import Trade

        now = datetime.now()
        t = Trade(
            symbol="AAPL",
            side="buy",
            qty=10.0,
            price=150.0,
            timestamp=now,
            order_id="ord_1",
        )
        assert t.symbol == "AAPL"
        assert t.side == "buy"
        assert t.qty == 10.0
        assert t.price == 150.0
        assert t.timestamp == now
        assert t.order_id == "ord_1"


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestPendingOrderDataclass:

    def test_pending_order_defaults(self):
        from core.simulation import PendingOrder

        now = datetime.now()
        po = PendingOrder(
            symbol="TSLA", qty=5.0, side="sell", timestamp_created=now, order_id="ord_2"
        )
        assert po.trade_context == {}

    def test_pending_order_with_context(self):
        from core.simulation import PendingOrder

        now = datetime.now()
        ctx = {"signal": 0.9, "confidence": 0.7}
        po = PendingOrder(
            symbol="MSFT",
            qty=2.0,
            side="buy",
            timestamp_created=now,
            order_id="ord_3",
            trade_context=ctx,
        )
        assert po.trade_context["signal"] == 0.9

    def test_pending_order_side_stored(self):
        from core.simulation import PendingOrder

        now = datetime.now()
        po = PendingOrder(
            symbol="AMZN", qty=1.0, side="sell", timestamp_created=now, order_id="ord_4"
        )
        assert po.side == "sell"


# ---------------------------------------------------------------------------
# 3. SimulationAccount
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestSimulationAccount:

    def test_init_defaults(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount()
        assert acc.cash == 100_000.0
        assert acc.initial_cash == 100_000.0
        assert acc.equity == 100_000.0
        assert acc.portfolio_value == 0.0
        assert acc.positions == {}
        assert acc.trade_history == []

    def test_init_custom_cash(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount(initial_cash=50_000.0)
        assert acc.cash == 50_000.0
        assert acc.equity == 50_000.0

    def test_update_portfolio_value_empty(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount(initial_cash=100_000.0)
        acc.update_portfolio_value({})
        assert acc.portfolio_value == 0.0
        assert acc.equity == 100_000.0

    def test_update_portfolio_value_with_position(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount(initial_cash=90_000.0)
        acc.positions["AAPL"] = {"qty": 10.0, "avg_price": 100.0, "market_value": 0.0}
        acc.update_portfolio_value({"AAPL": 150.0})
        assert acc.portfolio_value == 1_500.0
        assert acc.equity == 91_500.0

    def test_update_portfolio_missing_price_no_crash(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount(initial_cash=100_000.0)
        acc.positions["TSLA"] = {"qty": 5.0, "avg_price": 200.0, "market_value": 0.0}
        acc.update_portfolio_value({})
        assert acc.portfolio_value == 0.0

    def test_update_portfolio_multiple_positions(self):
        from core.simulation import SimulationAccount

        acc = SimulationAccount(initial_cash=0.0)
        acc.positions["AAPL"] = {"qty": 10.0, "avg_price": 100.0, "market_value": 0.0}
        acc.positions["TSLA"] = {"qty": 5.0, "avg_price": 200.0, "market_value": 0.0}
        acc.update_portfolio_value({"AAPL": 110.0, "TSLA": 220.0})
        assert acc.portfolio_value == pytest.approx(10 * 110 + 5 * 220)


# ---------------------------------------------------------------------------
# 4. NewsSimulator
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestNewsSimulator:

    def test_init_empty_cache(self):
        from core.simulation import NewsSimulator

        ns = NewsSimulator()
        assert ns.news_cache == {}

    def test_get_historical_news_returns_list(self):
        from core.simulation import NewsSimulator

        ns = NewsSimulator()
        date = datetime(2024, 1, 15)
        news = ns.get_historical_news("AAPL", date)
        assert isinstance(news, list)

    def test_get_historical_news_caches_result(self):
        from core.simulation import NewsSimulator

        ns = NewsSimulator()
        date = datetime(2024, 1, 15)
        first = ns.get_historical_news("AAPL", date)
        second = ns.get_historical_news("AAPL", date)
        assert first is second

    def test_different_dates_different_cache_keys(self):
        from core.simulation import NewsSimulator

        ns = NewsSimulator()
        ns.get_historical_news("AAPL", datetime(2024, 1, 15))
        ns.get_historical_news("AAPL", datetime(2024, 1, 16))
        assert len(ns.news_cache) == 2

    def test_news_items_structure(self):
        from core.simulation import NewsSimulator

        np.random.seed(1)
        ns = NewsSimulator()
        # Force at least one news item by seeding
        ns.news_cache["TSLA_20240301"] = [
            {
                "timestamp": datetime(2024, 3, 1),
                "headline": "Test",
                "symbols": ["TSLA"],
                "sentiment": "positive",
                "sentiment_score": 0.5,
                "reason": "Test news",
            }
        ]
        news = ns.get_historical_news("TSLA", datetime(2024, 3, 1))
        for item in news:
            assert "headline" in item
            assert "sentiment" in item
            assert "symbols" in item


# ---------------------------------------------------------------------------
# 5. RealisticSimulationClient — unit-testable methods (api=None)
# ---------------------------------------------------------------------------


@pytest.fixture
def sim_client(tmp_path):
    from core.simulation import RealisticSimulationClient

    with patch("core.simulation.HistoricalDataProvider") as MockDP:
        MockDP.return_value = MagicMock()
        client = RealisticSimulationClient(
            api=None,
            initial_cash=100_000.0,
            symbols=["AAPL", "TSLA"],
        )
        client.trade_log_file = str(tmp_path / "trades.csv")
        client.equity_log_file = str(tmp_path / "equity.csv")
        client.prognosis_log_file = str(tmp_path / "prognosis.csv")
        client._initialize_log_files()
        return client


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientInit:

    def test_init_sets_symbols(self, sim_client):
        assert "AAPL" in sim_client.symbols

    def test_init_empty_positions(self, sim_client):
        assert sim_client.account.positions == {}

    def test_init_pending_orders_empty(self, sim_client):
        assert sim_client.pending_orders == []

    def test_init_cash(self, sim_client):
        assert sim_client.account.cash == 100_000.0

    def test_get_account(self, sim_client):
        from core.simulation import SimulationAccount

        assert isinstance(sim_client.get_account(), SimulationAccount)


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientPositions:

    def test_list_positions_empty(self, sim_client):
        assert sim_client.list_positions() == []

    def test_list_positions_with_one_long(self, sim_client):
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 150.0,
            "market_value": 1_500.0,
        }
        positions = sim_client.list_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["side"] == "long"

    def test_list_positions_short_side(self, sim_client):
        sim_client.account.positions["TSLA"] = {
            "qty": -5.0,
            "avg_price": 200.0,
            "market_value": -1_000.0,
        }
        positions = sim_client.list_positions()
        assert positions[0]["side"] == "short"

    def test_get_position_existing(self, sim_client):
        sim_client.account.positions["TSLA"] = {
            "qty": 5.0,
            "avg_price": 200.0,
            "market_value": 1_000.0,
        }
        assert sim_client.get_position("TSLA") is not None

    def test_get_position_missing_returns_none(self, sim_client):
        assert sim_client.get_position("NONEXISTENT") is None

    def test_get_open_position_missing_raises_404(self, sim_client):
        from alpaca.common.exceptions import APIError

        with pytest.raises(APIError) as exc_info:
            sim_client.get_open_position("NONEXISTENT")

        assert exc_info.value.status_code == 404
        assert exc_info.value.status_code == 404
        # Note: the message is a dict containing code and message
        assert "40410000" in str(exc_info.value)


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientOrders:

    def test_submit_order_no_date_returns_none(self, sim_client):
        sim_client.current_date = None
        assert sim_client.submit_order("AAPL", 10.0, "buy") is None

    def test_submit_order_accepted(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        result = sim_client.submit_order("AAPL", 10.0, "buy")
        assert result["status"] == "accepted"

    def test_submit_order_queued(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        sim_client.submit_order("AAPL", 10.0, "buy")
        assert len(sim_client.pending_orders) == 1

    def test_submit_order_unique_ids(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        r1 = sim_client.submit_order("AAPL", 5.0, "buy")
        r2 = sim_client.submit_order("TSLA", 3.0, "sell")
        assert r1["id"] != r2["id"]

    def test_execute_pending_orders_empty_no_crash(self, sim_client):
        sim_client.pending_orders = []
        sim_client._execute_pending_orders()

    def test_execute_pending_orders_clears_queue(self, sim_client):
        from core.simulation import PendingOrder

        sim_client.current_date = datetime(2024, 6, 1)
        sim_client.pending_orders.append(
            PendingOrder("NOSYMBOL", 10.0, "buy", datetime(2024, 5, 31), "x1")
        )
        sim_client._execute_pending_orders()
        assert sim_client.pending_orders == []


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientBuyExecution:

    def test_buy_creates_position(self, sim_client):
        sim_client._process_buy_execution("AAPL", qty=10.0, price=150.0)
        assert "AAPL" in sim_client.account.positions
        assert sim_client.account.positions["AAPL"]["qty"] == 10.0

    def test_buy_deducts_cash(self, sim_client):
        initial_cash = sim_client.account.cash
        sim_client._process_buy_execution("AAPL", qty=10.0, price=100.0)
        expected = initial_cash - (10.0 * 100.0 + sim_client.COMMISSION_PER_TRADE)
        assert abs(sim_client.account.cash - expected) < 0.01

    def test_buy_averages_existing_position(self, sim_client):
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        sim_client._process_buy_execution("AAPL", qty=10.0, price=200.0)
        assert sim_client.account.positions["AAPL"]["qty"] == 20.0
        assert sim_client.account.positions["AAPL"]["avg_price"] == 150.0


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientSellExecution:

    def test_sell_closes_position_fully(self, sim_client):
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        sim_client._process_sell_execution("AAPL", qty=10.0, price=150.0)
        assert "AAPL" not in sim_client.account.positions

    def test_sell_partial_reduces_qty(self, sim_client):
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        sim_client._process_sell_execution("AAPL", qty=5.0, price=150.0)
        assert sim_client.account.positions["AAPL"]["qty"] == 5.0

    def test_sell_adds_cash(self, sim_client):
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        initial_cash = sim_client.account.cash
        sim_client._process_sell_execution("AAPL", qty=10.0, price=150.0)
        proceeds = 10.0 * 150.0 - sim_client.COMMISSION_PER_TRADE
        assert abs(sim_client.account.cash - (initial_cash + proceeds)) < 0.01

    def test_sell_without_position_creates_short(self, sim_client):
        sim_client._process_sell_execution("NEWSTOCK", qty=5.0, price=100.0)
        assert "NEWSTOCK" in sim_client.account.positions
        assert sim_client.account.positions["NEWSTOCK"]["qty"] == -5.0


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientGetBars:

    def test_get_bars_no_date_empty(self, sim_client):
        sim_client.current_date = None
        assert sim_client.get_bars("AAPL", "1D").empty

    def test_get_bars_unknown_symbol_empty(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        assert sim_client.get_bars("NONEXISTENT", "1D").empty

    def test_get_bars_filtered_to_current_date(self, sim_client):
        dates = pd.date_range("2024-01-01", periods=10)
        df = pd.DataFrame(
            {
                "close": range(10),
                "open": range(10),
                "high": range(10),
                "low": range(10),
                "volume": range(10),
            },
            index=dates,
        )
        sim_client.simulation_data["AAPL"] = df
        sim_client.current_date = dates[4]
        result = sim_client.get_bars("AAPL", "1D", limit=100)
        assert len(result) == 5


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientGetSnapshots:

    def test_get_snapshots_no_date(self, sim_client):
        sim_client.current_date = None
        assert sim_client.get_snapshots(["AAPL"]) == {}

    def test_get_snapshots_with_data(self, sim_client):
        date = datetime(2024, 6, 3)
        df = pd.DataFrame(
            {
                "open": [100.0],
                "high": [105.0],
                "low": [98.0],
                "close": [102.0],
                "volume": [500_000],
            },
            index=[date],
        )
        sim_client.simulation_data["AAPL"] = df
        sim_client.current_date = date
        result = sim_client.get_snapshots(["AAPL"])
        assert "AAPL" in result
        assert result["AAPL"]["latest_trade"]["c"] == 102.0


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientFetchFundamentals:

    def test_no_api_key_returns_defaults(self, sim_client):
        with patch("core.simulation.POLYGON_API_KEY", ""):
            result = sim_client._fetch_fundamentals(["AAPL", "TSLA"])
        assert result["AAPL"] == {"marketCap": 0.0, "trailingPE": 0.0}

    def test_polygon_error_returns_defaults(self, sim_client):
        with patch("core.simulation.POLYGON_API_KEY", "key"), patch(
            "core.simulation.polygon_fetch_fundamentals",
            side_effect=Exception("API error"),
        ):
            result = sim_client._fetch_fundamentals(["AAPL"])
        assert result["AAPL"] == {"marketCap": 0.0, "trailingPE": 0.0}


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientGetNews:

    def test_get_news_no_date_returns_empty(self, sim_client):
        sim_client.current_date = None
        assert sim_client.get_news(["AAPL"]) == []

    def test_get_news_returns_list(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        news = sim_client.get_news(["AAPL"])
        assert isinstance(news, list)


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRealisticSimulationClientCloseAll:

    def test_close_all_cancels_pending(self, sim_client):
        """cancel_orders=True clears any pre-existing pending orders."""
        from core.simulation import PendingOrder

        sim_client.current_date = datetime(2024, 6, 1)
        # Add a pre-existing pending BUY order (the one we want cancelled)
        old_buy = PendingOrder("AAPL", 5.0, "buy", datetime(2024, 5, 31), "old_buy_id")
        sim_client.pending_orders.append(old_buy)
        # Open position that close_all will submit a NEW sell for
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        sim_client.close_all_positions(cancel_orders=True)
        # Old buy order must be gone — only the new sell order should be present
        order_ids = [o.order_id for o in sim_client.pending_orders]
        assert "old_buy_id" not in order_ids

    def test_close_all_submits_sell(self, sim_client):
        sim_client.current_date = datetime(2024, 6, 1)
        sim_client.account.positions["AAPL"] = {
            "qty": 10.0,
            "avg_price": 100.0,
            "market_value": 1_000.0,
        }
        sim_client.close_all_positions(cancel_orders=False)
        assert any(o.side == "sell" for o in sim_client.pending_orders)


# ---------------------------------------------------------------------------
# 10. _process_single_order with real simulation data
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestProcessSingleOrderWithData:

    def _make_client_with_data(self, tmp_path):
        """Sim client with real OHLCV dataframe for AAPL."""
        from core.simulation import RealisticSimulationClient

        with patch("core.simulation.HistoricalDataProvider") as MockDP:
            MockDP.return_value = MagicMock()
            client = RealisticSimulationClient(api=None, initial_cash=100_000.0)
            client.trade_log_file = str(tmp_path / "trades.csv")
            client.equity_log_file = str(tmp_path / "equity.csv")
            client.prognosis_log_file = str(tmp_path / "prognosis.csv")
            client._initialize_log_files()

        date = datetime(2024, 6, 3)
        df = pd.DataFrame(
            {
                "open": [100.0],
                "high": [105.0],
                "low": [98.0],
                "close": [102.0],
                "volume": [500_000],
            },
            index=[date],
        )
        client.simulation_data["AAPL"] = df
        client.current_date = date
        return client

    def test_process_buy_order_creates_position(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_data(tmp_path)
        order = PendingOrder("AAPL", 10.0, "buy", datetime(2024, 6, 2), "test_buy")
        client._process_single_order(order)
        assert "AAPL" in client.account.positions

    def test_process_sell_order_adds_cash(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_data(tmp_path)
        # First buy
        client._process_buy_execution("AAPL", qty=10.0, price=100.0)
        initial_cash = client.account.cash
        # Now sell via process_single_order
        order = PendingOrder("AAPL", 10.0, "sell", datetime(2024, 6, 2), "test_sell")
        client._process_single_order(order)
        assert client.account.cash > initial_cash

    def test_process_order_no_data_skipped(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_data(tmp_path)
        order = PendingOrder("TSLA", 5.0, "buy", datetime(2024, 6, 2), "test_no_data")
        client._process_single_order(order)  # TSLA not in simulation_data → skip
        assert "TSLA" not in client.account.positions

    def test_process_order_wrong_date_skipped(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_data(tmp_path)
        client.current_date = datetime(2024, 7, 1)  # Date not in dataframe
        order = PendingOrder(
            "AAPL", 5.0, "buy", datetime(2024, 6, 2), "test_wrong_date"
        )
        client._process_single_order(order)
        assert "AAPL" not in client.account.positions

    def test_process_buy_order_insufficient_cash(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_data(tmp_path)
        client.account.cash = 0.01  # Too little cash
        initial_positions = dict(client.account.positions)
        order = PendingOrder("AAPL", 1000.0, "buy", datetime(2024, 6, 2), "test_nocash")
        client._process_single_order(order)
        # Position should not have been added (insufficient cash)
        assert "AAPL" not in client.account.positions


# ---------------------------------------------------------------------------
# 11. advance_day
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestAdvanceDay:

    def _make_client_with_range(self, tmp_path):
        from core.simulation import RealisticSimulationClient

        with patch("core.simulation.HistoricalDataProvider") as MockDP:
            MockDP.return_value = MagicMock()
            client = RealisticSimulationClient(api=None, initial_cash=100_000.0)
            client.trade_log_file = str(tmp_path / "trades.csv")
            client.equity_log_file = str(tmp_path / "equity.csv")
            client.prognosis_log_file = str(tmp_path / "prognosis.csv")
            client._initialize_log_files()

        dates = pd.date_range("2024-06-01", periods=5)
        df = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "high": [105.0] * 5,
                "low": [98.0] * 5,
                "close": [102.0] * 5,
                "volume": [500_000] * 5,
            },
            index=dates,
        )
        client.simulation_data["AAPL"] = df
        client.date_range = list(dates)
        client.current_index = 0
        client.current_date = dates[0]
        return client

    def test_advance_day_moves_forward(self, tmp_path):
        client = self._make_client_with_range(tmp_path)
        first_date = client.current_date
        result = client.advance_day()
        assert result is True
        assert client.current_date != first_date

    def test_advance_day_at_end_returns_false(self, tmp_path):
        client = self._make_client_with_range(tmp_path)
        client.current_index = 4  # Last index
        result = client.advance_day()
        assert result is False

    def test_advance_day_executes_pending_orders(self, tmp_path):
        from core.simulation import PendingOrder

        client = self._make_client_with_range(tmp_path)
        # Add pending buy order to be executed on next day
        po = PendingOrder("AAPL", 10.0, "buy", client.current_date, "advance_test")
        client.pending_orders.append(po)
        client.advance_day()
        # Queue should be cleared after execute
        assert client.pending_orders == []


# ---------------------------------------------------------------------------
# 12. _log_trade and _log_equity
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestLogMethods:

    def test_log_trade_writes_csv(self, tmp_path):
        from core.simulation import RealisticSimulationClient, Trade

        with patch("core.simulation.HistoricalDataProvider") as MockDP:
            MockDP.return_value = MagicMock()
            client = RealisticSimulationClient(api=None)
            client.trade_log_file = str(tmp_path / "trades.csv")
            client.equity_log_file = str(tmp_path / "equity.csv")
            client.prognosis_log_file = str(tmp_path / "prognosis.csv")
            client._initialize_log_files()

        trade = Trade("AAPL", "buy", 10.0, 150.0, datetime(2024, 6, 1), "t1")
        client._log_trade(trade, {"signal": 0.9})
        content = open(client.trade_log_file).read()
        assert "AAPL" in content

    def test_log_equity_writes_csv(self, tmp_path):
        from core.simulation import RealisticSimulationClient

        with patch("core.simulation.HistoricalDataProvider") as MockDP:
            MockDP.return_value = MagicMock()
            client = RealisticSimulationClient(api=None)
            client.trade_log_file = str(tmp_path / "t.csv")
            client.equity_log_file = str(tmp_path / "eq.csv")
            client.prognosis_log_file = str(tmp_path / "p.csv")
            client._initialize_log_files()

        client._log_equity(datetime(2024, 6, 1), 105_000.0)
        content = open(client.equity_log_file).read()
        assert "105000" in content
