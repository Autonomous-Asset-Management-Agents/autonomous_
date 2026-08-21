"""TDD: GET /api/entitlement/status exposes the resolved tier (GTM-1 #1915).

Powers the console's sidebar Upgrade CTA. The route runs on the LOCAL engine and
reflects resolve_entitlement() verbatim, so `can_upgrade` is true only for Junior
(BASIC) desktops — Senior (PRO)+ users never see the CTA.

Auth mirrors the sibling console GETs: X-Engine-Key via require_engine_key, with
REQUIRE_SIG=false so the read route needs no proxy HMAC (test-isolated env).
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.engine.api_routes import app
from core.entitlement.tier import TIER_REGISTRY, Tier


@pytest.fixture
def client():
    return TestClient(app)


def _get_status(client):
    with patch.dict(
        "os.environ",
        {"ENGINE_API_KEY": "test-engine-key", "REQUIRE_SIG": "false"},
    ):
        return client.get(
            "/api/entitlement/status",
            headers={"x-engine-key": "test-engine-key"},
        )


@patch(
    "core.entitlement.resolve_entitlement",
    return_value=TIER_REGISTRY[Tier.BASIC],
)
def test_basic_can_upgrade(_mock_resolve, client):
    resp = _get_status(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "BASIC"
    assert body["allow_live"] is False
    assert body["can_upgrade"] is True


@patch(
    "core.entitlement.resolve_entitlement",
    return_value=TIER_REGISTRY[Tier.PRO],
)
def test_pro_cannot_upgrade(_mock_resolve, client):
    resp = _get_status(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "PRO"
    assert body["allow_live"] is True
    assert body["can_upgrade"] is False


def test_status_requires_engine_key(client):
    with patch.dict("os.environ", {"ENGINE_API_KEY": "test-engine-key"}):
        resp = client.get("/api/entitlement/status")
    assert resp.status_code == 403
