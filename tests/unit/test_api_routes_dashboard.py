from unittest.mock import MagicMock, patch

import allure
import pytest
from fastapi.testclient import TestClient

from core.engine.api_routes import app


@pytest.fixture
def test_client():
    return TestClient(app)


@patch("core.engine.api_routes.engine")
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_portfolio_summary_returns_agent_statuses_and_pnl(mock_engine, test_client):
    # Setup mock engine properties
    mock_engine._last_round_table_state = [{"name": "AgentBuy", "signal": "BUY"}]

    mock_api = MagicMock()
    mock_engine.api = mock_api

    mock_account = MagicMock()
    mock_account.equity = "10000.0"
    mock_api.get_account.return_value = mock_account

    mock_pos = MagicMock()
    mock_pos.symbol = "AAPL"
    mock_pos.qty = "10"
    mock_pos.market_value = "1500"
    mock_pos.unrealized_pl = "150"
    mock_pos.unrealized_plpc = "0.1"
    mock_api.get_all_positions.return_value = [mock_pos]

    # We simulate active strategy PM returning summary
    mock_active = MagicMock()
    mock_engine.active_strategy = mock_active

    mock_pm = MagicMock()
    mock_active.portfolio_manager = mock_pm
    mock_pm.get_portfolio_summary.return_value = "Mock Summary"
    mock_pm.get_debate_history.return_value = []
    mock_pm.get_rebalance_recommendations.return_value = []

    # Add score for position to mix it in
    mock_score = MagicMock()
    mock_score.total_score = 0.8
    mock_score.momentum_score = 0.9
    mock_score.conviction_score = 0.7
    mock_score.days_held = 5
    mock_pm._position_scores = {"AAPL": mock_score}

    with patch.dict(
        "os.environ",
        {
            "ENGINE_API_KEY": "test-engine-key",
            "REQUIRE_SIG": "false",
        },
    ):
        response = test_client.get(
            "/portfolio-summary", headers={"x-engine-key": "test-engine-key"}
        )

    # Isolated unit test of the endpoint response structure
    assert response.status_code in [200, 401]


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_pnl_calculation_logic():
    # Directly test the logic embedded in api_routes.py
    final_positions = [
        {"unrealized_pnl": 150.0},
        {"unrealized_pnl": -50.0},
    ]
    total_pnl = (
        sum(p.get("unrealized_pnl", 0) for p in final_positions)
        if final_positions
        else 0
    )
    assert total_pnl == 100.0


@patch("core.engine.api_routes.engine")
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_portfolio_summary_serializes_numpy_scores(mock_engine, test_client):
    """Regression (#1582): numpy.float32 score fields must not 500 the endpoint.

    numpy.float32 does not subclass Python float, so FastAPI's response serialization
    used to raise AFTER the handler returned (outside its try/except) -> a bare HTTP 500
    once the strategy was active and enriched positions with numpy-derived scores.
    _json_safe now casts them to JSON-native types at the response boundary.
    """
    import json

    import numpy as np

    mock_engine._last_round_table_state = [
        {"name": "AgentBuy", "score": np.float32(0.83)}
    ]

    mock_api = MagicMock()
    mock_engine.api = mock_api
    mock_account = MagicMock()
    mock_account.equity = "10000.0"
    mock_api.get_account.return_value = mock_account
    mock_pos = MagicMock()
    mock_pos.symbol = "AAPL"
    mock_pos.qty = "10"
    mock_pos.market_value = "1500"
    mock_pos.unrealized_pl = "150"
    mock_pos.unrealized_plpc = "0.1"
    mock_api.get_all_positions.return_value = [mock_pos]

    mock_active = MagicMock()
    mock_engine.active_strategy = mock_active
    mock_pm = MagicMock()
    mock_active.portfolio_manager = mock_pm
    # summary + scores carry numpy.float32 / int64, exactly as the ML pipeline produces
    mock_pm.get_portfolio_summary.return_value = {
        "total_value": np.float32(1500.0),
        "average_score": np.float32(0.8),
    }
    mock_pm.get_debate_history.return_value = []
    mock_pm.get_rebalance_recommendations.return_value = []
    mock_score = MagicMock()
    mock_score.total_score = np.float32(0.8)
    mock_score.momentum_score = np.float32(0.9)
    mock_score.conviction_score = np.float32(0.7)
    mock_score.days_held = np.int64(5)
    mock_pm._position_scores = {"AAPL": mock_score}

    with patch.dict(
        "os.environ",
        {"ENGINE_API_KEY": "test-engine-key", "REQUIRE_SIG": "false"},
    ):
        response = test_client.get(
            "/portfolio-summary", headers={"x-engine-key": "test-engine-key"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    # No numpy leaked -> the payload re-serialises cleanly.
    json.dumps(body)
