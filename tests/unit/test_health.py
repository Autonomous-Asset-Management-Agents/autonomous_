from fastapi.testclient import TestClient
from core.engine import app
import pytest


@pytest.fixture
def client():
    """Returns a TestClient instance for the FastAPI app."""
    return TestClient(app)


def test_health_endpoint(client):
    """Test the /health endpoint returns expected status and structure.

    Returns 'healthy' when BotEngine is fully initialized (lifespan ran),
    or 'starting' when called before lifespan completes (e.g. CI without secrets).
    Both are valid HTTP 200 responses — Cloud Run probe only checks port reachability.
    """
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in (
        "healthy",
        "starting",
    ), f"Unexpected status: {data['status']!r}"
    assert "timestamp" in data
    assert data["version"] == "2.5.0"
    assert "strategy_running" in data
