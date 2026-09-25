# ADR-SEC-06 (#1598) — four-eyes WIRING into the Iron Dome admin endpoint. TDD RED first.
# Direct write gates a LOOSENING (409) unless LOCAL; a loosening goes propose -> approve, where
# the HMAC-bound approver (X-User-Id) MUST be distinct from the initiator (segregation of duties).

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key, verify_user_id_sig
from core.engine.api_routes import app
from core.governance.iron_dome_admin_auth import require_iron_dome_admin


@pytest.fixture
def client_authed():
    # Bypass the auth deps to exercise the route logic in isolation. verify_user_id_sig is
    # overridden too (its HMAC check is unit-tested in core.auth); the route then trusts the
    # X-User-Id header as the SoD identity.
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[require_iron_dome_admin] = lambda: None
    app.dependency_overrides[verify_user_id_sig] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _pending(cooloff_until, initiator="alice"):
    return SimpleNamespace(
        initiator=initiator,
        approvals=[],
        cooloff_until=cooloff_until,
        requested_policy={"daily_drawdown_pct": 0.20},
        applied=False,
    )


# (mode, submitted drawdown, expected status, commit awaited): loosening is gated (409) in the
# enterprise edition, tightening applies, and LOCAL bypasses four-eyes entirely.
@pytest.mark.parametrize(
    "mode, drawdown, status, applies",
    [
        ("ENTERPRISE", 0.20, 409, False),  # loosening rejected
        ("ENTERPRISE", 0.10, 200, True),  # tightening applies
        ("LOCAL", 0.20, 200, True),  # LOCAL bypass allows loosening
    ],
)
@patch("core.engine.api_routes._commit_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_direct_write_four_eyes_gate(
    mock_load, mock_commit, client_authed, mode, drawdown, status, applies
):
    mock_load.return_value = {"daily_drawdown_pct": 0.175}
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": mode}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy", json={"daily_drawdown_pct": drawdown}
        )
    assert r.status_code == status
    assert mock_commit.await_count == (1 if applies else 0)


@patch("core.engine.api_routes._create_pending", new_callable=AsyncMock)
def test_propose_creates_pending(mock_create, client_authed):
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy/propose",
            json={"daily_drawdown_pct": 0.20},
            headers={"X-User-Id": "alice"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert r.json()["pending_id"]
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["initiator"] == "alice"


def test_propose_requires_admin_id(client_authed):
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy/propose",
            json={"daily_drawdown_pct": 0.20},
        )
    assert r.status_code == 400


@patch("core.engine.api_routes._mark_pending_applied", new_callable=AsyncMock)
@patch("core.engine.api_routes._commit_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
@patch("core.engine.api_routes._get_pending", new_callable=AsyncMock)
def test_approve_by_distinct_admin_applies(
    mock_get, mock_load, mock_commit, mock_mark, client_authed
):
    past = datetime.now(timezone.utc) - timedelta(minutes=30)
    mock_get.return_value = _pending(past)
    mock_load.return_value = {"daily_drawdown_pct": 0.175}
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy/approve",
            json={"pending_id": "x"},
            headers={"X-User-Id": "bob"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    mock_commit.assert_awaited_once()
    mock_mark.assert_awaited_once()


@patch("core.engine.api_routes._update_pending_approvals", new_callable=AsyncMock)
@patch("core.engine.api_routes._commit_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes._get_pending", new_callable=AsyncMock)
def test_approve_distinct_admin_before_cooloff_stays_pending(
    mock_get, mock_commit, mock_update, client_authed
):
    # A DISTINCT approver, but the cool-off has NOT yet elapsed -> not ready to apply.
    future = datetime.now(timezone.utc) + timedelta(minutes=9)
    mock_get.return_value = _pending(future)
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy/approve",
            json={"pending_id": "x"},
            headers={"X-User-Id": "bob"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    mock_commit.assert_not_awaited()


@patch("core.engine.api_routes._update_pending_approvals", new_callable=AsyncMock)
@patch("core.engine.api_routes._commit_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes._get_pending", new_callable=AsyncMock)
def test_approve_by_initiator_not_ready(
    mock_get, mock_commit, mock_update, client_authed
):
    past = datetime.now(timezone.utc) - timedelta(minutes=30)
    mock_get.return_value = _pending(past)
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
        r = client_authed.post(
            "/api/admin/iron-dome-policy/approve",
            json={"pending_id": "x"},
            headers={"X-User-Id": "alice"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    mock_commit.assert_not_awaited()
