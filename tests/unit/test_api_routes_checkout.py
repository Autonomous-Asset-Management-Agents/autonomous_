# tests/unit/test_api_routes_checkout.py
# GTM-1 (#1840) — Brick 1: the POST /api/entitlement/checkout endpoint. Body {tier} ->
# create_checkout_session -> {"checkout_url": ...}. Purchasable tiers (PRO/PROFESSIONAL)
# succeed; free/B2B/invalid tiers -> HTTP 400. The endpoint delegates all Stripe/Secret
# Manager work to core.entitlement.payment (mocked here), so this is pure routing + error
# mapping — NO network.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.entitlement import Tier

_AUTH_HEADERS = {"x-engine-key": "test-engine-key"}


@pytest.fixture
def client(monkeypatch):
    # Satisfy the engine-key + user-sig auth guards the way the neighbouring api_routes
    # unit tests do (ENGINE_API_KEY set + REQUIRE_SIG=false + the X-Engine-Key header).
    monkeypatch.setenv("ENGINE_API_KEY", "test-engine-key")
    monkeypatch.setenv("REQUIRE_SIG", "false")
    monkeypatch.delenv("K_SERVICE", raising=False)
    return TestClient(api_routes_mod.app)


def test_checkout_returns_url_for_purchasable_tier(client, monkeypatch):
    captured = {}

    def _fake_create(tier):
        captured["tier"] = tier
        return "https://checkout.stripe.test/session-xyz"

    monkeypatch.setattr(api_routes_mod, "create_checkout_session", _fake_create)

    resp = client.post(
        "/api/entitlement/checkout", json={"tier": "PRO"}, headers=_AUTH_HEADERS
    )

    assert resp.status_code == 200
    assert resp.json() == {"checkout_url": "https://checkout.stripe.test/session-xyz"}
    assert captured["tier"] is Tier.PRO


def test_checkout_rejects_free_tier_with_400(client, monkeypatch):
    def _fake_create(tier):
        raise ValueError(f"tier {tier.value} is not purchasable")

    monkeypatch.setattr(api_routes_mod, "create_checkout_session", _fake_create)

    resp = client.post(
        "/api/entitlement/checkout", json={"tier": "BASIC"}, headers=_AUTH_HEADERS
    )
    assert resp.status_code == 400


def test_checkout_rejects_unknown_tier_string_with_400(client):
    resp = client.post(
        "/api/entitlement/checkout", json={"tier": "GOLD"}, headers=_AUTH_HEADERS
    )
    assert resp.status_code == 400
