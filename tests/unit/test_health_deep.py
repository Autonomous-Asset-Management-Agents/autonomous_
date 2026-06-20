from unittest.mock import MagicMock, patch

import allure


# Move imports inside the test or use late binding to avoid anyio issues during collection
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_deep_health_endpoint_structure():
    """Test the /health/deep endpoint returns expected JSON structure."""
    from fastapi.testclient import TestClient

    from core.engine import app

    client = TestClient(app)

    with patch("core.engine.api_routes.engine") as mock_engine:
        # Mock engine state
        import time

        mock_engine._last_scan_time = time.time()
        mock_engine.strategy_running.is_set.return_value = True

        # Mock alpaca API
        mock_api = MagicMock()
        mock_engine.api = mock_api
        mock_acc = MagicMock()
        mock_acc.status = "ACTIVE"
        mock_acc.equity = 100000.0
        mock_api.get_account.return_value = mock_acc

        # Mock strategy
        mock_strategy = MagicMock()
        mock_strategy.torch_model = MagicMock()
        mock_strategy.rl_model = MagicMock()
        mock_engine.active_strategy = mock_strategy

        with patch("core.cloud_logger.get_cloud_logger") as mock_logger:
            mock_logger.return_value.is_connected = True

            response = client.get("/health/deep")
            assert response.status_code == 200
            data = response.json()

            assert "status" in data
            assert data["status"] == "healthy"
            assert "components" in data
            assert "alpaca" in data["components"]
            assert "cloud_sql" in data["components"]
            assert "models" in data["components"]
            # data["components"]["models"]["gemini"] is checked against ok/disabled in implementation


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_deep_health_degraded_if_alpaca_down():
    """Test overall status is 'degraded' if a component is down."""
    from fastapi.testclient import TestClient

    from core.engine import app

    client = TestClient(app)

    with patch("core.engine.api_routes.engine") as mock_engine:
        import time

        mock_engine._last_scan_time = time.time()
        mock_engine.strategy_running.is_set.return_value = True
        mock_engine.api = None  # Alpaca down
        mock_engine.active_strategy = None

        with patch("core.cloud_logger.get_cloud_logger") as mock_logger:
            mock_logger.return_value.is_connected = True

            # Patch os.path.exists to simulate missing model files
            with patch("os.path.exists", return_value=False):
                response = client.get("/health/deep")
                assert response.status_code == 500
                data = response.json()
                assert data["status"] == "degraded"
                assert data["components"]["alpaca"]["status"] == "unavailable"
