import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

# Ensure the project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.engine.api_routes
from core.engine import app


class TestMonitoringEndpoints(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            "os.environ", {"ENGINE_API_KEY": "test-key", "REQUIRE_SIG": "false"}
        )
        self.env_patcher.start()
        # Set both headers just in case
        self.client = TestClient(
            app, headers={"X-Engine-Key": "test-key", "X-Bot-Api-Key": "test-key"}
        )
        # Mock engine.api (TradingClient)
        core.engine.api_routes.engine = MagicMock()
        core.engine.api_routes.engine.api = MagicMock()
        core.engine.api_routes.engine.data_api = MagicMock()
        core.engine.api_routes.engine._cycle_latencies = []

    def tearDown(self):
        self.env_patcher.stop()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertIn("strategy_running", data)

    def test_system_health_endpoint(self):
        response = self.client.get("/system-health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("cpu_pct", data)
        self.assertIn("latency_metrics", data)

    @patch("core.engine.api_routes.BotEngine.get_chat_context")
    def test_portfolio_summary_live_mock(self, mock_context):
        # Mock account data
        mock_acc = MagicMock()
        mock_acc.equity = "10500.50"
        core.engine.api_routes.engine.api.get_account.return_value = mock_acc

        # Mock positions
        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"
        mock_pos.qty = "10"
        mock_pos.market_value = "1500.00"
        mock_pos.unrealized_pl = "50.00"
        mock_pos.unrealized_plpc = "0.034"
        core.engine.api_routes.engine.api.get_all_positions.return_value = [mock_pos]

        response = self.client.get("/portfolio-summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["status"], "success")
        self.assertEqual(data["message"], "Live account")
        self.assertEqual(len(data["positions"]), 1)
        self.assertEqual(data["positions"][0]["symbol"], "AAPL")
        self.assertEqual(data["equity"], 10500.5)

    def test_top_picks_endpoint(self):
        # Manually set top picks in engine
        core.engine.api_routes.engine._last_top_picks = [
            {"symbol": "TSLA", "score": 0.95, "reason": "High momentum"}
        ]

        response = self.client.get("/top-picks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["picks"]), 1)
        self.assertEqual(data["picks"][0]["symbol"], "TSLA")

    @patch("core.engine.api_routes.RedisClient")
    def test_benchmark_equity_endpoint_missing_file(self, mock_redis_client):
        # Force Redis returning None (no data)
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_client.get_sync_redis.return_value = mock_r

        response = self.client.get("/benchmark-equity")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("No benchmark run yet", data["message"])


if __name__ == "__main__":
    unittest.main()
