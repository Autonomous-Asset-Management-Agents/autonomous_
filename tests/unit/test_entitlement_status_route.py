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
    # Paywall is OFF by default (free offline beta) — the console stays on claimBeta.
    assert body["paywall_enabled"] is False


@patch(
    "core.entitlement.resolve_entitlement",
    return_value=TIER_REGISTRY[Tier.BASIC],
)
def test_paywall_enabled_reflected(_mock_resolve, client):
    """When PAYWALL_ENABLED is flipped on, the status route reports it so the
    console switches Junior desktops from claimBeta to the paid checkout path."""
    from config import get_config

    with patch.object(get_config(), "PAYWALL_ENABLED", True):
        resp = _get_status(client)
    assert resp.status_code == 200
    assert resp.json()["paywall_enabled"] is True


@patch(
    "core.entitlement.resolve_entitlement",
    return_value=TIER_REGISTRY[Tier.BASIC],
)
def test_checkout_url_reflected(_mock_resolve, client):
    """The status route surfaces the Lemon Squeezy hosted checkout URL so 'Get Senior'
    can open it. null/absent by default (unprovisioned) → the button falls back to the
    key-activation panel."""
    from config import get_config

    body = _get_status(client).json()
    assert body["checkout_url"] is None  # unconfigured by default

    with patch.object(
        get_config(), "LEMONSQUEEZY_CHECKOUT_URL", "https://x.lemonsqueezy.com/buy/abc"
    ):
        body = _get_status(client).json()
    assert body["checkout_url"] == "https://x.lemonsqueezy.com/buy/abc"


@patch(
    "core.entitlement.resolve_entitlement",
    return_value=TIER_REGISTRY[Tier.BASIC],
)
def test_price_display_reflected(_mock_resolve, client):
    """The status route surfaces the Finance-controlled display price so the paid Senior
    card shows a real figure (kept in one config source, not hardcoded in the UI)."""
    from config import get_config

    with patch.object(get_config(), "LEMONSQUEEZY_PRICE_DISPLAY", "9,99 €"):
        body = _get_status(client).json()
    assert body["price_display"] == "9,99 €"


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


# --------------------------------------------------------------------------- #
# GTM-2 (#1809) T4 — POST /api/entitlement/activate (offline key-paste activation)
# --------------------------------------------------------------------------- #
def _post_activate(client, body):
    with patch.dict(
        "os.environ",
        {"ENGINE_API_KEY": "test-engine-key", "REQUIRE_SIG": "false"},
    ):
        return client.post(
            "/api/entitlement/activate",
            headers={"x-engine-key": "test-engine-key"},
            json=body,
        )


@patch("core.entitlement.activate_license", return_value=Tier.PRO)
def test_activate_valid_key_returns_tier(_mock_activate, client):
    resp = _post_activate(client, {"token": "a-signed-token"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "activated", "tier": "PRO"}
    _mock_activate.assert_called_once_with("a-signed-token")


@patch("core.entitlement.activate_license", return_value=None)
def test_activate_invalid_key_is_400(_mock_activate, client):
    resp = _post_activate(client, {"token": "garbage"})
    assert resp.status_code == 400


def test_activate_blank_token_is_400(client):
    resp = _post_activate(client, {"token": "   "})
    assert resp.status_code == 400


def test_activate_requires_engine_key(client):
    with patch.dict("os.environ", {"ENGINE_API_KEY": "test-engine-key"}):
        resp = client.post("/api/entitlement/activate", json={"token": "x"})
    assert resp.status_code == 403
