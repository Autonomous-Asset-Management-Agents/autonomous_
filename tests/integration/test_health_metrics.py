import os
import time
from fastapi.testclient import TestClient

os.environ["ENGINE_API_KEY"] = "testkey"

from core.engine import app

client = TestClient(app)


def test_system_health():
    """Test the /system-health endpoint returns correct metrics.

    Returns 'healthy' when BotEngine is ready, 'starting' during init window.
    Both are valid — CI runs without GCP secrets so engine stays in 'starting'.
    """
    headers = {"X-Bot-Api-Key": "testkey"}
    response = client.get("/system-health", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] in (
        "healthy",
        "starting",
    ), f"Unexpected status: {data['status']!r}"

    # Check CPU
    assert "cpu_pct" in data
    assert isinstance(data["cpu_pct"], (int, float))

    # Check RAM
    assert "ram_pct" in data
    assert "ram_used_gb" in data
    assert "ram_total_gb" in data
    assert isinstance(data["ram_pct"], (int, float))
    assert data["ram_total_gb"] > 0

    # Check Uptime
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0

    # Check Timestamp
    assert "timestamp" in data
    assert data["timestamp"] <= time.time()

    # Check Latency Metrics (New)
    assert "latency_metrics" in data
    lat = data["latency_metrics"]
    assert "avg_cycle_ms" in lat
    assert "max_cycle_ms" in lat
    assert "last_cycle" in lat
    assert isinstance(lat["avg_cycle_ms"], (int, float))
    assert isinstance(lat["max_cycle_ms"], (int, float))


def test_health_basic():
    """Ensure basic health check still works.

    Returns 'healthy' when BotEngine is ready, 'starting' during init.
    Both are valid HTTP 200 — Cloud Run probe only checks port reachability.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in (
        "healthy",
        "starting",
    ), f"Unexpected status: {response.json()['status']!r}"
