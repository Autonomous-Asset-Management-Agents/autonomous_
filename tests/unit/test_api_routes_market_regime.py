"""TDD for GET /market-regime (Epic #1582 / Sub-Issue #1621).

Read-only endpoint that surfaces the engine's latest market regime + VIX (populated by the monitor
loop in engine.current_market_data) so the demo snapshot runner (#1618) can report real values
instead of defaults. No state change; defaults cleanly when the engine is still starting.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@patch("core.engine.api_routes.engine")
def test_market_regime_returns_live_values(mock_engine, client):
    mock_engine.current_market_data = {"regime": "Trending", "vix": 22.45}
    resp = client.get("/market-regime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["regime"] == "Trending"
    assert data["vix"] == 22.45


@patch("core.engine.api_routes.engine", new=None)
def test_market_regime_defaults_when_engine_starting(client):
    resp = client.get("/market-regime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["regime"] == "Ranging"
    assert data["vix"] is None


@patch("core.engine.api_routes.engine")
def test_market_regime_defaults_when_no_market_data(mock_engine, client):
    mock_engine.current_market_data = {}
    resp = client.get("/market-regime")
    assert resp.status_code == 200
    assert resp.json()["regime"] == "Ranging"
