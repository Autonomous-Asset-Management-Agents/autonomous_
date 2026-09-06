# ADR-SEC-06 (#1583) · sub-issue #1595 — admin endpoint route. TDD RED first.
# The route POST /api/admin/iron-dome-policy clamps the submitted policy to the immutable
# hard-floor (via load_policy, #1594) and persists it. Auth is unit-tested separately in
# test_iron_dome_admin_auth.py; here we test the route logic (auth bypassed) and that the
# auth dependency is actually wired (a non-private host is rejected).

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app
from core.governance.iron_dome_admin_auth import require_iron_dome_admin


@pytest.fixture
def client_authed():
    # Bypass the auth deps to exercise the route logic in isolation.
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[require_iron_dome_admin] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@patch("core.engine.api_routes._save_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes.record_iron_dome_policy_change", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_endpoint_clamps_to_floor_and_persists(
    mock_load, mock_record, mock_save, client_authed
):
    # Stored policy already at the ceiling, so clamping 999 -> 50 is not a loosening
    # (four-eyes, #1598, gates only a widening) and the route reaches the persist path.
    mock_load.return_value = {"max_daily_trades": 50}
    r = client_authed.post(
        "/api/admin/iron-dome-policy", json={"max_daily_trades": 999}
    )
    assert r.status_code == 200
    # ADR-C04 ceiling: an over-limit submission is clamped to 50, never widened.
    assert r.json()["policy"]["max_daily_trades"] == 50
    mock_save.assert_awaited_once()


def test_endpoint_rejects_non_private_host():
    # No dependency override: TestClient's host is "testclient" (not a private/loopback
    # IP), so the proxy-safe IP gate must reject even with a valid engine key + token.
    app.dependency_overrides.clear()
    client = TestClient(app)
    with patch.dict(os.environ, {"ENGINE_API_KEY": "k", "IRON_DOME_ADMIN_TOKEN": "t"}):
        r = client.post(
            "/api/admin/iron-dome-policy",
            json={"max_daily_trades": 5},
            headers={"X-Engine-Key": "k", "X-Iron-Dome-Admin-Token": "t"},
        )
    assert r.status_code == 403
