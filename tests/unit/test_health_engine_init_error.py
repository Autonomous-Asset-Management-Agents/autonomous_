"""TDD: GET /health surfaces a fatal engine-init failure instead of eternal "starting".

The engine is built in a fire-and-forget background task (`asyncio.create_task(_init_engine_async())`
in the FastAPI lifespan). That task has no caller — so if ANY step raises (DB bootstrap, client build,
BotEngine construction) the task dies silently, the module-global `engine` stays None, and /health
reported `status:"starting"` forever. Operators saw a never-ready engine with no reason.

This pins the fix: `_init_engine_async` records the failure in `engine_init_error`, and /health returns
`status:"error"` + `detail` when the engine is None AND an init error was captured — so the reason is
visible (diagnostics / API) instead of an invisible permanent "starting".
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@patch("core.engine.api_routes.engine", new=None)
@patch("core.engine.api_routes.engine_init_error", new=None)
def test_health_starting_when_no_init_error(client):
    # engine None + no captured error → still the normal "starting" boot window.
    data = client.get("/health").json()
    assert data["status"] == "starting"


@patch("core.engine.api_routes.engine", new=None)
@patch(
    "core.engine.api_routes.engine_init_error",
    new="RuntimeError: G0a DB bootstrap failed",
)
def test_health_error_surfaces_init_failure(client):
    # engine None BUT an init failure was captured → honest error, not eternal "starting".
    data = client.get("/health").json()
    assert data["status"] == "error"
    assert "G0a DB bootstrap failed" in data["detail"]
