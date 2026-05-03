# tests/test_user_secrets.py
# Epic 3.4-pre Issue #414 (TDD) — Unit tests for core/user_secrets.py
# All GCP Secret Manager calls are mocked — no real credentials required.

import pytest
from unittest.mock import MagicMock, patch
from google.api_core.exceptions import NotFound

from core.user_secrets import (
    AlpacaUserSecretManager,
    AlpacaCredentials,
    UserAlpacaCredentialsNotFoundError,
    UserAlpacaSecretStoreError,
    _safe_uid,
    _mask,
    _secret_ref_prefix,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_sm_client():
    """Returns a MagicMock replacing Google SecretManagerServiceClient."""
    client = MagicMock()
    # Simulate existing secret by default (no NotFound on get_secret)
    client.get_secret.return_value = MagicMock()
    client.add_secret_version.return_value = MagicMock()

    def _access_secret(request):
        secret_name = request["name"]
        # Return predictable values based on key type
        if "api-key" in secret_name:
            data = b"TEST_API_KEY"
        elif "secret-key" in secret_name:
            data = b"TEST_SECRET_KEY"
        elif "base-url" in secret_name:
            data = b"https://paper-api.alpaca.markets"
        else:
            raise NotFound("secret not found")
        mock_response = MagicMock()
        mock_response.payload.data = data
        return mock_response

    client.access_secret_version.side_effect = _access_secret
    return client


@pytest.fixture
def manager(mock_sm_client):
    """AlpacaUserSecretManager with mocked GCP client."""
    mgr = AlpacaUserSecretManager(project_id="test-project")
    mgr._client = mock_sm_client
    return mgr


# ── Helper tests ──────────────────────────────────────────────────────────────


def test_safe_uid_alphanumeric():
    assert _safe_uid("abc123") == "abc123"


def test_safe_uid_special_chars():
    # Firebase UIDs can contain colons, pipe characters, etc.
    safe = _safe_uid("user:abc|def")
    assert all(c.isalnum() or c in "-_" for c in safe)


def test_mask_short_value():
    assert _mask("ab") == "***masked***"


def test_mask_normal_value():
    masked = _mask("ABCDEFGHIJ")
    assert "***" in masked
    assert "ABCDEFGHIJ" not in masked


def test_secret_ref_prefix():
    ref = _secret_ref_prefix("user123")
    assert ref == "alpaca-user123"


# ── store_user_alpaca_secret ──────────────────────────────────────────────────


def test_store_creates_3_secrets(manager, mock_sm_client):
    ref = manager.store_user_alpaca_secret(
        uid="uid_abc", api_key="KEY", secret_key="SECRET", base_url="https://paper"
    )
    # 3 add_secret_version calls (one per credential)
    assert mock_sm_client.add_secret_version.call_count == 3
    assert ref == "alpaca-uid_abc"


def test_store_creates_secret_when_not_found(manager, mock_sm_client):
    # Simulate secret not existing yet
    mock_sm_client.get_secret.side_effect = NotFound("not found")
    manager.store_user_alpaca_secret(
        uid="new_user", api_key="K", secret_key="S", base_url="U"
    )
    assert mock_sm_client.create_secret.call_count == 3


def test_store_raises_on_gcp_error(manager, mock_sm_client):
    mock_sm_client.add_secret_version.side_effect = Exception("GCP error")
    with pytest.raises(UserAlpacaSecretStoreError):
        manager.store_user_alpaca_secret(
            uid="fail_uid", api_key="K", secret_key="S", base_url="U"
        )


# ── get_user_alpaca_credentials ───────────────────────────────────────────────


def test_get_returns_credentials(manager):
    creds = manager.get_user_alpaca_credentials("uid_abc")
    assert isinstance(creds, AlpacaCredentials)
    assert creds.api_key == "TEST_API_KEY"
    assert creds.secret_key == "TEST_SECRET_KEY"
    assert creds.base_url == "https://paper-api.alpaca.markets"
    assert creds.uid == "uid_abc"


def test_get_raises_when_not_found(manager, mock_sm_client):
    mock_sm_client.access_secret_version.side_effect = NotFound("not found")
    with pytest.raises(UserAlpacaCredentialsNotFoundError) as exc_info:
        manager.get_user_alpaca_credentials("unknown_uid")
    assert "unknown_uid" in str(exc_info.value)


def test_credentials_repr_does_not_leak_key():
    creds = AlpacaCredentials(
        uid="u1",
        api_key="SUPER_SECRET_KEY",
        secret_key="SUPER_SECRET",
        base_url="https://x",
    )
    text = repr(creds)
    assert "SUPER_SECRET_KEY" not in text
    assert "***masked***" in text or "***" in text


# ── local dev fallback ────────────────────────────────────────────────────────


def test_local_fallback_store_and_retrieve():
    """When no GCP client, local in-memory store is used."""
    mgr = AlpacaUserSecretManager(project_id=None)
    mgr._client = None
    ref = mgr.store_user_alpaca_secret(
        uid="local_uid",
        api_key="LOCAL_KEY",
        secret_key="LOCAL_SECRET",
        base_url="http://local",
    )
    assert ref == "alpaca-local_uid"
    creds = mgr.get_user_alpaca_credentials("local_uid")
    assert creds.api_key == "LOCAL_KEY"


def test_local_fallback_raises_when_missing():
    mgr = AlpacaUserSecretManager(project_id=None)
    mgr._client = None
    with pytest.raises(UserAlpacaCredentialsNotFoundError):
        mgr.get_user_alpaca_credentials("nobody")


# ── revoke ────────────────────────────────────────────────────────────────────


def test_revoke_disables_secret_versions(manager, mock_sm_client):
    manager.revoke_user_alpaca_secret("uid_abc")
    assert mock_sm_client.disable_secret_version.call_count == 3


def test_revoke_handles_not_found_gracefully(manager, mock_sm_client):
    mock_sm_client.disable_secret_version.side_effect = NotFound("not found")
    # Should not raise — logs warning instead
    manager.revoke_user_alpaca_secret("uid_abc")
