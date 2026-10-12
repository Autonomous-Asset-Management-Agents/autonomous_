"""TDD: GET /health exposes system_halted (#1642).

So the console Overview can show a live kill-switch status (AKTIV/GESTOPPT) instead of inferring it
post-hoc. Read-only; sourced from the KillSwitch singleton (Redis-backed).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@patch("core.engine.api_routes.engine", new=None)
def test_health_starting_reports_not_halted(client):
    data = client.get("/health").json()
    assert data["system_halted"] is False


@patch(
    "core.engine.api_routes.RedisClient.check_health",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("core.kill_switch.KillSwitch.is_halted", return_value=True)
@patch("core.engine.api_routes.engine")
def test_health_reports_system_halted(mock_engine, mock_is_halted, mock_redis, client):
    mock_engine.strategy_running.is_set.return_value = True
    data = client.get("/health").json()
    assert data["status"] == "healthy"
    assert data["system_halted"] is True
