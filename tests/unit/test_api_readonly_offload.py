"""Fix B: the read-only display endpoints offload their synchronous broker/Redis calls to a thread so
a slow call cannot stall the single uvicorn event loop (which was starving EVERY request, incl. /health
— observed: even a no-op OPTIONS preflight took 2-97s). This proves the offloaded calls still flow
their results through unchanged (behaviour-preserving) via /health/deep, whose is_market_open comes
from the now-`asyncio.to_thread`-wrapped engine.api.get_clock() and whose Alpaca probe uses the
wrapped engine.api.get_account()."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@patch("core.cloud_logger.get_cloud_logger")
def test_health_deep_offloaded_alpaca_probe_flows_through(mock_cloud, client):
    mock_cloud.return_value = SimpleNamespace(is_connected=False)

    eng = MagicMock()
    eng.api.get_account.return_value = SimpleNamespace(status="ACTIVE", equity=1000.0)
    eng.api.get_clock.return_value = SimpleNamespace(is_open=True)
    eng.active_strategy = None  # → the model-file-check branch (no live strategy)
    eng.strategy_running.is_set.return_value = True
    eng._last_scan_time = __import__("time").time()

    with patch.object(api_routes_mod, "engine", eng):
        resp = client.get("/health/deep")
        data = resp.json()

    # is_market_open is the value returned by the to_thread-wrapped get_clock() → proves the offload
    # returns the callable's result unchanged.
    assert data["is_market_open"] is True
    # the to_thread-wrapped get_account() was actually invoked (the probe ran, did not error out).
    assert eng.api.get_account.called
    assert eng.api.get_clock.called
