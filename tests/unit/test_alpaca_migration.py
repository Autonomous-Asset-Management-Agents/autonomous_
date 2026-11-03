import unittest
from unittest.mock import MagicMock

import allure
import pandas as pd
import pytest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from core.data_provider import HistoricalDataProvider
from core.risk_manager import RiskManager
from core.simulation_adapter import SimulationAdapter


@pytest.mark.vc3
@allure.story("Execution Engine")
class TestAlpacaMigration(unittest.TestCase):
    def setUp(self):
        self.mock_trading_client = MagicMock()
        self.mock_data_client = MagicMock()
        self.data_provider = HistoricalDataProvider(
            api=self.mock_data_client, trading_api=self.mock_trading_client
        )

    def test_data_provider_get_bars(self):
        """Test that data_provider.get_bars uses the new StockHistoricalDataClient format"""
        from alpaca.data.requests import StockBarsRequest

        # Mock the get_stock_bars method
        mock_bars = MagicMock()
        # Simulation of alpaca-py response structure: df attribute
        mock_bars.df = pd.DataFrame(
            {
                "open": [100],
                "high": [110],
                "low": [90],
                "close": [105],
                "volume": [1000],
            },
            index=pd.MultiIndex.from_tuples(
                [("AAPL", pd.Timestamp("2023-01-01"))], names=["symbol", "timestamp"]
            ),
        )

        self.mock_data_client.get_stock_bars.return_value = mock_bars

        df = self.data_provider.get_bars("AAPL", "1Day", limit=1)

        self.mock_data_client.get_stock_bars.assert_called_once()
        args = self.mock_data_client.get_stock_bars.call_args[0][0]
        self.assertIsInstance(args, StockBarsRequest)

        # Normalize to list for comparison if it's a string
        actual_syms = (
            args.symbol_or_symbols
            if isinstance(args.symbol_or_symbols, list)
            else [args.symbol_or_symbols]
        )
        self.assertEqual(actual_syms, ["AAPL"])
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]["close"], 105)

    def test_risk_manager_liquidates_position_by_position(self):
        """#3383: Der Breaker schliesst je Position einzeln ueber das Tor.

        Frueher hielt dieser Test `close_all_positions(cancel_orders=True)` fest. Dieser
        Sammelaufruf kennt weder Symbol noch Menge — ein Compliance-Datensatz dazu
        koennte nur "alles" sagen, und im Audit waere hinterher nicht belegbar, WAS der
        Breaker liquidiert hat. Seit #3383 laeuft jede Schliessung als eigener
        OrderIntent mit Grund-Code `breaker` durch das Gateway.
        """
        position = MagicMock()
        position.symbol = "AAPL"
        position.qty = "2"
        position.qty_available = "2"
        self.mock_trading_client.get_all_positions.return_value = [position]

        rm = RiskManager(
            self.mock_trading_client,
            total_capital=10000,
            clock=type(
                "MockClock",
                (),
                {
                    "now": lambda self: __import__("datetime").datetime(
                        2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
                    ),
                    "time": lambda self: 1735732800.0,
                },
            )(),
        )
        rm.portfolio_stop_loss_pct = 0  # Disable portfolio stop for this test

        rm.update_account_equity(5000)  # 50% drawdown

        self.mock_trading_client.close_all_positions.assert_not_called()
        self.mock_trading_client.submit_order.assert_called_once()
        gesendet = self.mock_trading_client.submit_order.call_args[0][0]
        self.assertEqual(gesendet.symbol, "AAPL")

    def test_simulation_adapter_compatibility(self):
        """Test that SimulationAdapter handles MarketOrderRequest and get_open_position"""
        mock_sim_client = MagicMock()
        adapter = SimulationAdapter(mock_sim_client)

        # Test get_open_position
        mock_sim_client.get_position.return_value = {
            "qty": 10,
            "avg_entry_price": 100,
            "market_value": 1100,
        }
        pos = adapter.get_open_position("AAPL")
        self.assertEqual(pos.symbol, "AAPL")
        self.assertEqual(pos.qty, 10)

        # Test submit_order with MarketOrderRequest
        req = MarketOrderRequest(
            symbol="AAPL", qty=5, side=OrderSide.BUY, time_in_force=TimeInForce.GTC
        )
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(adapter.submit_order(req))

        mock_sim_client.submit_order.assert_called_once_with("AAPL", 5, "buy")


if __name__ == "__main__":
    unittest.main()
