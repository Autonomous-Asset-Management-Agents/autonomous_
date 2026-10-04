"""GTM-2 (#1809) T6/T7 — route wiring for the Lemon Squeezy webhook + success-page lookup.

Thin wrappers over core.entitlement.lemonsqueezy (unit-tested separately). Here we only assert
the HTTP plumbing: the webhook forwards the RAW body + X-Signature header verbatim (a
re-serialised body would break the HMAC), and the lookup maps None → 404, a hit → 200.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.engine.api_routes import app


@pytest.fixture
def client():
    return TestClient(app)


def test_webhook_forwards_raw_body_and_signature():
    raw = b'{"meta":{"event_name":"order_created"},"spaced":  "keep"}'
    with patch(
        "core.entitlement.lemonsqueezy.handle_lemonsqueezy_webhook",
        new=AsyncMock(return_value={"status": "minted", "order_identifier": "ord-1"}),
    ) as handler:
        resp = TestClient(app).post(
            "/api/entitlement/lemonsqueezy-webhook",
            content=raw,
            headers={"X-Signature": "abc123", "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "minted"
    # Exact raw bytes + header forwarded (not re-serialised JSON).
    args = handler.await_args.args
    assert args[0] == raw
    assert args[1] == "abc123"


def test_webhook_needs_no_engine_key():
    """LS cannot send an engine key — the route must not require one (auth is the signature)."""
    with patch(
        "core.entitlement.lemonsqueezy.handle_lemonsqueezy_webhook",
        new=AsyncMock(return_value={"status": "ignored", "event_name": "x"}),
    ):
        resp = TestClient(app).post(
            "/api/entitlement/lemonsqueezy-webhook", content=b"{}"
        )
    assert resp.status_code == 200  # not 403


def test_lookup_hit_returns_token(client):
    with patch(
        "core.entitlement.lemonsqueezy.lookup_license",
        new=AsyncMock(return_value={"tier": "PRO", "token": "T.OK"}),
    ):
        resp = client.get(
            "/api/entitlement/license", params={"order_identifier": "ord-1"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"tier": "PRO", "token": "T.OK"}


def test_lookup_miss_returns_404(client):
    with patch(
        "core.entitlement.lemonsqueezy.lookup_license",
        new=AsyncMock(return_value=None),
    ):
        resp = client.get(
            "/api/entitlement/license", params={"order_identifier": "nope"}
        )
    assert resp.status_code == 404
