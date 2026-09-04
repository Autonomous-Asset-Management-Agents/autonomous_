# tests/test_admin_alpaca_routes.py
# Epic 3.4-pre Issue #412 — TDD: Tests FIRST (RED phase)
# Admin-API for Alpaca User-Account Mapping
# All DB and Secret Manager calls are mocked.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_firebase_uid():
    return "abc123_firebase_uid"


@pytest.fixture
def mock_admin_claims(mock_firebase_uid):
    return {"uid": mock_firebase_uid, "email": "admin@aaagents.de"}


@pytest.fixture()  # NOT autouse — 401 test needs real auth path
def patch_firebase(mock_admin_claims):
    """Bypass real Firebase token verification — use explicitly in tests that need auth."""
    with patch(
        "core.admin_routes.verify_firebase_token", return_value=mock_admin_claims
    ):
        yield


@pytest.fixture(autouse=True)
def patch_db():
    """Mock all database calls + set DB_AVAILABLE=True."""
    mock_session = AsyncMock()
    # Make it work as `async with AsyncSessionLocal() as session:`
    mock_session_instance = AsyncMock()
    mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
    mock_session_instance.__aexit__ = AsyncMock(return_value=False)
    # begin() also needs to be an async context manager
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session_instance.begin = MagicMock(return_value=mock_begin)
    # execute returns a result object
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value="admin")
    mock_session_instance.execute = AsyncMock(return_value=mock_result)
    mock_session_instance.add = MagicMock()
    mock_session_instance.commit = AsyncMock()

    mock_session_factory = MagicMock(return_value=mock_session_instance)

    with patch("core.admin_routes.AsyncSessionLocal", mock_session_factory), patch(
        "core.admin_routes.DB_AVAILABLE", True
    ):
        yield mock_session_instance


@pytest.fixture(autouse=True)
def patch_secrets():
    """Mock GCP Secret Manager calls."""
    mock_sm = MagicMock()
    mock_sm.store_user_alpaca_secret.return_value = "alpaca-abc123_firebase_uid"
    mock_sm.revoke_user_alpaca_secret.return_value = None
    with patch("core.admin_routes.user_alpaca_secrets", mock_sm):
        yield mock_sm


@pytest.fixture
def client():
    """FastAPI TestClient using the admin_routes app."""
    from fastapi import FastAPI

    from core.admin_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _auth_header():
    return {"Authorization": "Bearer fake_token"}


# ── POST /admin/users/{uid}/alpaca-account ────────────────────────────────────


class TestCreateAlpacaAccount:

    def test_creates_account_returns_201(
        self, client, patch_firebase, patch_db, patch_secrets
    ):
        """Admin can register a new Alpaca account for a user."""
        # patch_db already has role='admin' as default return
        payload = {
            "api_key": "AKTEST123",
            "secret_key": "SKTESTXYZ",
            "base_url": "https://paper-api.alpaca.markets",
            "account_type": "paper",
            "label": "My Paper Account",
        }
        resp = client.post(
            "/admin/users/target_uid/alpaca-account",
            json=payload,
            headers=_auth_header(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "account_id" in body
        assert "api_key" not in body  # credentials MUST NOT be in response
        assert "secret_key" not in body  # credentials MUST NOT be in response

    def test_returns_403_if_not_admin(self, client, patch_firebase, patch_db):
        """Non-admin users cannot create account mappings."""
        patch_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value="trader")  # role = trader
            )
        )
        payload = {
            "api_key": "K",
            "secret_key": "S",
            "base_url": "https://paper",
            "account_type": "paper",
        }
        resp = client.post(
            "/admin/users/target_uid/alpaca-account",
            json=payload,
            headers=_auth_header(),
        )
        assert resp.status_code == 403

    def test_returns_403_if_no_role_row(self, client, patch_firebase, patch_db):
        """Users with no row in user_roles are denied (default deny)."""
        patch_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=None)  # no row
            )
        )
        payload = {
            "api_key": "K",
            "secret_key": "S",
            "base_url": "https://paper",
            "account_type": "paper",
        }
        resp = client.post(
            "/admin/users/target_uid/alpaca-account",
            json=payload,
            headers=_auth_header(),
        )
        assert resp.status_code == 403

    def test_returns_401_without_auth_header(self, client):
        """Requests without Authorization header are rejected."""
        resp = client.post(
            "/admin/users/uid/alpaca-account",
            json={
                "api_key": "k",
                "secret_key": "s",
                "base_url": "u",
                "account_type": "paper",
            },
        )
        assert resp.status_code == 401

    def test_rejects_invalid_account_type(self, client, patch_firebase, patch_db):
        """account_type must be 'paper' or 'live'."""
        payload = {
            "api_key": "K",
            "secret_key": "S",
            "base_url": "https://x",
            "account_type": "invalid_type",
        }
        resp = client.post(
            "/admin/users/target_uid/alpaca-account",
            json=payload,
            headers=_auth_header(),
        )
        assert resp.status_code == 422  # Pydantic validation error


# ── GET /admin/users/{uid}/alpaca-account ─────────────────────────────────────


class TestGetAlpacaAccount:

    def test_returns_metadata_without_credentials(
        self, client, patch_firebase, patch_db
    ):
        """GET returns account metadata — NEVER api_key or secret_key."""
        from datetime import datetime, timezone

        mock_row = MagicMock()
        mock_row.id = "some-uuid"
        mock_row.account_type = "paper"
        mock_row.label = "My Paper Account"
        mock_row.is_active = True
        mock_row.created_at = datetime(2026, 3, 24, tzinfo=timezone.utc)
        mock_row.secret_ref = "alpaca-target_uid"

        # First call: role check (returns 'admin')
        # Second call: account lookup (returns mock_row)
        results = iter(
            [
                MagicMock(scalar_one_or_none=MagicMock(return_value="admin")),
                MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row)),
                MagicMock(
                    scalar_one_or_none=MagicMock(return_value=None)
                ),  # audit log insert
            ]
        )
        patch_db.execute = AsyncMock(side_effect=lambda *a, **kw: next(results))

        resp = client.get(
            "/admin/users/target_uid/alpaca-account",
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["account_type"] == "paper"
        assert "api_key" not in body
        assert "secret_key" not in body
        assert "secret_ref" not in body  # internal ref also hidden

    def test_returns_404_if_no_mapping(self, client, patch_firebase, patch_db):
        """GET returns 404 when no account mapping exists."""
        results = iter(
            [
                MagicMock(scalar_one_or_none=MagicMock(return_value="admin")),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            ]
        )
        patch_db.execute = AsyncMock(side_effect=lambda *a, **kw: next(results))
        resp = client.get(
            "/admin/users/target_uid/alpaca-account",
            headers=_auth_header(),
        )
        assert resp.status_code == 404

    def test_returns_403_for_non_admin(self, client, patch_firebase, patch_db):
        patch_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value="readonly")
            )
        )
        resp = client.get(
            "/admin/users/target_uid/alpaca-account",
            headers=_auth_header(),
        )
        assert resp.status_code == 403


# ── DELETE /admin/users/{uid}/alpaca-account/{account_id} ─────────────────────


class TestRevokeAlpacaAccount:

    def test_revokes_account_returns_200(
        self, client, patch_firebase, patch_db, patch_secrets
    ):
        """Admin can revoke an existing account mapping."""
        from datetime import datetime, timezone

        mock_row = MagicMock()
        mock_row.id = "some-uuid"
        mock_row.firebase_uid = "target_uid"
        mock_row.secret_ref = "alpaca-target_uid"
        mock_row.is_active = True

        results = iter(
            [
                MagicMock(scalar_one_or_none=MagicMock(return_value="admin")),
                MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row)),
                MagicMock(
                    scalar_one_or_none=MagicMock(return_value=None)
                ),  # UPDATE execute
                MagicMock(
                    scalar_one_or_none=MagicMock(return_value=None)
                ),  # audit INSERT
            ]
        )
        patch_db.execute = AsyncMock(side_effect=lambda *a, **kw: next(results))

        resp = client.delete(
            "/admin/users/target_uid/alpaca-account/some-uuid",
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "revoked"
        # Ensure revoke_user_alpaca_secret was called
        patch_secrets.revoke_user_alpaca_secret.assert_called_once()

    def test_returns_404_if_account_not_found(self, client, patch_firebase, patch_db):
        results = iter(
            [
                MagicMock(scalar_one_or_none=MagicMock(return_value="admin")),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            ]
        )
        patch_db.execute = AsyncMock(side_effect=lambda *a, **kw: next(results))
        resp = client.delete(
            "/admin/users/target_uid/alpaca-account/nonexistent-id",
            headers=_auth_header(),
        )
        assert resp.status_code == 404

    def test_returns_403_for_non_admin(self, client, patch_firebase, patch_db):
        patch_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="trader"))
        )
        resp = client.delete(
            "/admin/users/target_uid/alpaca-account/some-uuid",
            headers=_auth_header(),
        )
        assert resp.status_code == 403


# ── Audit log ─────────────────────────────────────────────────────────────────


class TestAuditLog:

    def test_audit_log_written_on_create(
        self, client, patch_firebase, patch_db, patch_secrets
    ):
        """Every account creation must write an audit log entry (2 execute calls: INSERT mapping + INSERT audit)."""
        call_count = 0

        async def counting_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value="admin")
            return result

        patch_db.execute = AsyncMock(side_effect=counting_execute)

        payload = {
            "api_key": "K",
            "secret_key": "S",
            "base_url": "https://paper",
            "account_type": "paper",
        }
        client.post(
            "/admin/users/target_uid/alpaca-account",
            json=payload,
            headers=_auth_header(),
        )
        # At least: role check + INSERT mapping + INSERT audit_log = 3 executes
        assert call_count >= 3
