from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def api_client():
    app = api_routes_mod.app
    # Wir überschreiben den auth dependency wie in anderen tests
    app.dependency_overrides[api_routes_mod.require_engine_key] = lambda: None
    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


@patch("core.orchestration.graph.build_symbol_eval_graph")
@patch("core.engine.api_routes.engine")
@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_force_cycle_success(mock_engine, mock_build_graph, api_client):
    """Testet den Force Cycle Endpoint mit validen Mock-Daten."""
    # Setup mock data provider
    mock_df = MagicMock()
    mock_df.empty = False
    mock_df.iloc = [
        {"open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 100000.0}
    ]
    mock_engine.data_provider.get_data.return_value = mock_df
    mock_engine.cached_regime = {"regime": "bull"}

    # Setup mock graph
    mock_graph = MagicMock()
    mock_final_state = {
        "session_id": "test-session-123",
        "signal": MagicMock(action="BUY"),
        "round_table_scores": [{"agent": "Mock", "score": 1.0}],
    }
    mock_graph.ainvoke = AsyncMock(return_value=mock_final_state)
    mock_build_graph.return_value = mock_graph

    target_date = "2026-04-08T00:00:00+00:00"
    response = api_client.post(
        "/api/v1/engine/force-cycle",
        json={"symbol": "AAPL", "target_date": target_date},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["session_id"] == "test-session-123"
    assert data["signal"] == "BUY"
    assert data["close_price"] == 102.0
    assert len(data["round_table_scores"]) == 1


@patch("core.engine.api_routes.engine")
@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_force_cycle_no_data(mock_engine, api_client):
    """Testet das Verhalten wenn der Data Provider keine Daten (leeres df) findet."""
    mock_df = MagicMock()
    mock_df.empty = True
    mock_engine.data_provider.get_data.return_value = mock_df

    response = api_client.post(
        "/api/v1/engine/force-cycle",
        json={"symbol": "UNKNOWN", "target_date": "2026-04-08T00:00:00+00:00"},
    )

    assert response.status_code == 404
    assert "No historical data found" in response.json()["detail"]


@patch("core.engine.api_routes.engine", new=None)
@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_force_cycle_no_engine(api_client):
    """Testet das Verhalten wenn die Engine nicht initialisiert ist."""
    response = api_client.post(
        "/api/v1/engine/force-cycle",
        json={"symbol": "AAPL", "target_date": "2026-04-08T00:00:00+00:00"},
    )

    assert response.status_code == 503
    assert "Engine/DataProvider not available" in response.json()["detail"]


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_force_cycle_rejects_invalid_symbol(api_client):
    """SEC M7: a crafted/oversized symbol is rejected (422) before it reaches the data
    provider — even though the endpoint is already require_engine_key + localhost."""
    for bad in ["../etc/passwd", "A" * 20, "a b", "1ABC", "AAPL;DROP", ""]:
        r = api_client.post(
            "/api/v1/engine/force-cycle",
            json={"symbol": bad, "target_date": "2026-04-08T00:00:00+00:00"},
        )
        assert r.status_code == 422, f"expected 422 for {bad!r}, got {r.status_code}"
