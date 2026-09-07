"""LSR R2 (#2253): /health surfaces the live-degrade breadcrumbs (I-4, fixes #09 dead flag).

``LIVE_ENABLE_FAILED_REASON`` was set at boot but READ nowhere (#09). R2 exposes it — plus the new
``DEGRADED_LIVE`` — on ``/health`` so the desktop console can tell the operator WHY a live arming
booted PAPER instead of silently sitting on paper.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import config
import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@patch(
    "core.engine.api_routes.RedisClient.check_health",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("core.engine.api_routes.engine")
def test_health_exposes_live_degrade_fields(mock_engine, mock_redis, client):
    mock_engine.strategy_running.is_set.return_value = True
    with patch.object(
        config, "LIVE_ENABLE_FAILED_REASON", "degraded-live: Ollama warming"
    ), patch.object(config, "DEGRADED_LIVE", True):
        data = client.get("/health").json()
    assert data["status"] == "healthy"
    assert data["live_enable_failed_reason"] == "degraded-live: Ollama warming"
    assert data["degraded_live"] is True


@patch(
    "core.engine.api_routes.RedisClient.check_health",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("core.engine.api_routes.engine")
def test_health_defaults_when_no_degrade(mock_engine, mock_redis, client):
    mock_engine.strategy_running.is_set.return_value = True
    with patch.object(config, "LIVE_ENABLE_FAILED_REASON", None), patch.object(
        config, "DEGRADED_LIVE", False
    ):
        data = client.get("/health").json()
    assert data["live_enable_failed_reason"] is None
    assert data["degraded_live"] is False
