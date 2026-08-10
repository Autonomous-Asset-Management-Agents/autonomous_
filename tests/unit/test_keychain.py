"""SEC-5 (#1084): Unit tests for core.keychain — OS Keychain Integration.

TDD: Written BEFORE the implementation.
Tests the keychain abstraction layer that stores API credentials
in the OS-native credential store (Windows Credential Manager /
macOS Keychain / Linux Secret Service) via the `keyring` library.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove managed keys from env before each test."""
    for key in [
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "GEMINI_API_KEY",
        "POLYGON_API_KEY",
        "DATABENTO_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def mock_keyring():
    """Mock keyring module with an in-memory backend."""
    store = {}

    mock_kr = MagicMock()
    mock_kr.get_password = MagicMock(
        side_effect=lambda svc, key: store.get(f"{svc}:{key}")
    )
    mock_kr.set_password = MagicMock(
        side_effect=lambda svc, k, v: store.__setitem__(f"{svc}:{k}", v)
    )
    mock_kr.delete_password = MagicMock(
        side_effect=lambda svc, key: store.pop(f"{svc}:{key}", None)
    )

    return mock_kr, store


# ---------------------------------------------------------------------------
# load_secrets_from_keychain
# ---------------------------------------------------------------------------


class TestLoadSecretsFromKeychain:
    """Tests for loading secrets from OS keychain into os.environ."""

    def test_injects_secrets_into_environ(self, mock_keyring, monkeypatch):
        """Secrets from keychain should be injected into os.environ."""
        mock_kr, store = mock_keyring
        store["aaagents:ALPACA_API_KEY"] = "PK_test_123"
        store["aaagents:GEMINI_API_KEY"] = "gem_test_456"

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        assert os.environ.get("ALPACA_API_KEY") == "PK_test_123"
        assert os.environ.get("GEMINI_API_KEY") == "gem_test_456"
        assert "ALPACA_API_KEY" in loaded
        assert "GEMINI_API_KEY" in loaded

    def test_does_not_overwrite_explicit_env_var(
        self, mock_keyring, monkeypatch
    ):  # noqa: E501
        """Explicit env vars (e.g. from CI) must NEVER be overwritten."""
        mock_kr, store = mock_keyring
        store["aaagents:ALPACA_API_KEY"] = "from_keychain"
        monkeypatch.setenv("ALPACA_API_KEY", "from_ci_pipeline")

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        # CI env var wins over keychain
        assert os.environ["ALPACA_API_KEY"] == "from_ci_pipeline"
        assert "ALPACA_API_KEY" not in loaded

    def test_does_not_overwrite_empty_env_var(self, mock_keyring, monkeypatch):
        """An env var explicitly set to '' must NOT be overwritten (P1 fix)."""
        mock_kr, store = mock_keyring
        store["aaagents:ALPACA_API_KEY"] = "from_keychain"
        monkeypatch.setenv("ALPACA_API_KEY", "")

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        # Empty string is an explicit choice — keychain must not override
        assert os.environ["ALPACA_API_KEY"] == ""
        assert "ALPACA_API_KEY" not in loaded

    def test_returns_empty_when_keyring_not_installed(self):
        """If keyring is not installed, return empty dict without crashing."""
        with patch("core.keychain._get_keyring", return_value=None):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        assert loaded == {}

    def test_handles_backend_exception_gracefully(self, mock_keyring):
        """If keyring backend throws, log WARNING and skip that key."""
        mock_kr, _ = mock_keyring
        mock_kr.get_password.side_effect = Exception("D-Bus not available")

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        assert loaded == {}
        assert os.environ.get("ALPACA_API_KEY") is None

    def test_skips_keys_not_in_keychain(self, mock_keyring):
        """Keys not present in keychain should be silently skipped."""
        mock_kr, store = mock_keyring
        # Only GEMINI_API_KEY is set, others are missing
        store["aaagents:GEMINI_API_KEY"] = "gem_only"

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain

            loaded = load_secrets_from_keychain()

        assert os.environ.get("GEMINI_API_KEY") == "gem_only"
        assert os.environ.get("ALPACA_API_KEY") is None
        assert len(loaded) == 1


# ---------------------------------------------------------------------------
# save_secret
# ---------------------------------------------------------------------------


class TestSaveSecret:
    """Tests for saving secrets to the OS keychain."""

    def test_saves_managed_key(self, mock_keyring):
        """A managed key should be saved to keyring."""
        mock_kr, store = mock_keyring

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import save_secret

            save_secret("ALPACA_API_KEY", "PK_new_key")

        assert store["aaagents:ALPACA_API_KEY"] == "PK_new_key"

    def test_rejects_unknown_key(self, mock_keyring):
        """An unknown key should raise ValueError."""
        mock_kr, _ = mock_keyring

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import save_secret

            with pytest.raises(ValueError, match="Unknown managed key"):
                save_secret("UNKNOWN_KEY", "value")

    def test_raises_when_keyring_unavailable(self):
        """If keyring is not available, raise RuntimeError."""
        with patch("core.keychain._get_keyring", return_value=None):
            from core.keychain import save_secret

            with pytest.raises(
                RuntimeError, match="keyring library not available"
            ):  # noqa: E501
                save_secret("ALPACA_API_KEY", "value")


# ---------------------------------------------------------------------------
# delete_secret
# ---------------------------------------------------------------------------


class TestDeleteSecret:
    """Tests for removing secrets from the OS keychain."""

    def test_deletes_existing_key(self, mock_keyring):
        """An existing key should be removed."""
        mock_kr, store = mock_keyring
        store["aaagents:ALPACA_API_KEY"] = "to_delete"

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import delete_secret

            delete_secret("ALPACA_API_KEY")

        assert "aaagents:ALPACA_API_KEY" not in store

    def test_noop_when_keyring_unavailable(self):
        """If keyring is not available, delete should be a no-op."""
        with patch("core.keychain._get_keyring", return_value=None):
            from core.keychain import delete_secret

            delete_secret("ALPACA_API_KEY")  # Should not raise


# ---------------------------------------------------------------------------
# has_secrets
# ---------------------------------------------------------------------------


class TestHasSecrets:
    """Tests for checking if keychain contains secrets."""

    def test_true_when_alpaca_key_exists(self, mock_keyring):
        """Should return True when ALPACA_API_KEY is in keychain."""
        mock_kr, store = mock_keyring
        store["aaagents:ALPACA_API_KEY"] = "exists"

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import has_secrets

            assert has_secrets() is True

    def test_false_when_empty(self, mock_keyring):
        """Should return False when keychain is empty."""
        mock_kr, _ = mock_keyring

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import has_secrets

            assert has_secrets() is False

    def test_false_when_keyring_unavailable(self):
        """Should return False when keyring is not installed."""
        with patch("core.keychain._get_keyring", return_value=None):
            from core.keychain import has_secrets

            assert has_secrets() is False

    def test_false_on_backend_exception(self, mock_keyring):
        """Should return False on backend error (not crash)."""
        mock_kr, _ = mock_keyring
        mock_kr.get_password.side_effect = Exception("Backend error")

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import has_secrets

            assert has_secrets() is False


# ---------------------------------------------------------------------------
# Round-trip integration
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Integration-style tests for save → load → delete cycle."""

    def test_save_then_load_round_trip(self, mock_keyring, monkeypatch):
        """Save a secret, then load it — should appear in os.environ."""
        mock_kr, store = mock_keyring

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import load_secrets_from_keychain, save_secret

            save_secret("ALPACA_API_KEY", "PK_round_trip")
            save_secret("ALPACA_SECRET_KEY", "SK_round_trip")

            loaded = load_secrets_from_keychain()

        assert os.environ["ALPACA_API_KEY"] == "PK_round_trip"
        assert os.environ["ALPACA_SECRET_KEY"] == "SK_round_trip"
        assert len(loaded) == 2

    def test_save_delete_verify_gone(self, mock_keyring):
        """Save, then delete — has_secrets should return False."""
        mock_kr, store = mock_keyring

        with patch("core.keychain._get_keyring", return_value=mock_kr):
            from core.keychain import delete_secret, has_secrets, save_secret

            save_secret("ALPACA_API_KEY", "temporary")
            assert has_secrets() is True

            delete_secret("ALPACA_API_KEY")
            assert has_secrets() is False


# ---------------------------------------------------------------------------
# OAuthSecretManager (OSS)
# ---------------------------------------------------------------------------


class TestOAuthSecretManager:
    """Tests for saving and retrieving OAuth tokens via keychain."""

    def test_save_and_get_tokens(self, mock_keyring, monkeypatch):
        import importlib.util
        from pathlib import Path

        # Load the .oss.py file dynamically
        base_dir = Path(__file__).parent.parent.parent / "core"
        oss_file = base_dir / "secret_manager_utils.oss.py"

        spec = importlib.util.spec_from_file_location(
            "secret_manager_utils_oss", oss_file
        )
        oss_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(oss_module)

        mock_kr, store = mock_keyring
        with patch("core.keychain._get_keyring", return_value=mock_kr):
            oauth_secrets = oss_module.oauth_secrets

            user_id = "testuser123"
            secret_id = oauth_secrets.save_tokens(
                user_id, "access1", "refresh1"
            )  # noqa: E501

            assert secret_id == f"keychain:{user_id}"
            assert store[f"aaagents:OAUTH_{user_id}_ACCESS"] == "access1"
            assert store[f"aaagents:OAUTH_{user_id}_REFRESH"] == "refresh1"

            tokens = oauth_secrets.get_tokens(secret_id)
            assert tokens is not None
            assert tokens["access_token"] == "access1"
            assert tokens["refresh_token"] == "refresh1"

    def test_get_tokens_returns_none_if_not_found(self, mock_keyring):
        import importlib.util
        from pathlib import Path

        base_dir = Path(__file__).parent.parent.parent / "core"
        oss_file = base_dir / "secret_manager_utils.oss.py"
        spec = importlib.util.spec_from_file_location(
            "secret_manager_utils_oss", oss_file
        )
        oss_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(oss_module)

        mock_kr, store = mock_keyring
        with patch("core.keychain._get_keyring", return_value=mock_kr):
            oauth_secrets = oss_module.oauth_secrets

            # Not setting anything in the store
            tokens = oauth_secrets.get_tokens("keychain:unknown_user")
            assert tokens is None
