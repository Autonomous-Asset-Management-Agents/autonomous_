import unittest
from unittest.mock import MagicMock

import allure
import pandas as pd
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from core.data_provider import HistoricalDataProvider
from core.risk_manager import RiskManager
from core.simulation_adapter import SimulationAdapter


@allure.feature("VC-3 Trading & Execution")
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

    def test_risk_manager_close_all_positions(self):
        """Test that RiskManager uses ClosePositionRequest"""
        rm = RiskManager(self.mock_trading_client, total_capital=10000)
        rm.portfolio_stop_loss_pct = 0  # Disable portfolio stop for this test

        # Trigger circuit breaker to force close_all_positions
        rm.update_account_equity(5000)  # 50% drawdown

        # Verify that close_all_positions was called with cancel_orders=True
        self.mock_trading_client.close_all_positions.assert_called_once_with(
            cancel_orders=True
        )

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
