"""
Unit tests for core/auth_interfaces.py

Covers the FirebaseAuth initialization and token verification logic,
specifically the FIREBASE_PROJECT_ID audience fix (ADR: auth_interfaces L118).
"""

import os
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(token: str = "valid-token"):
    """Build a minimal mock Request with an Authorization header."""
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


# ---------------------------------------------------------------------------
# FirebaseAuth — initialize_app projectId fix
# ---------------------------------------------------------------------------


class TestFirebaseAuthInit:
    """Verify that FirebaseAuth passes FIREBASE_PROJECT_ID to initialize_app."""

    def _make_firebase_module(self, apps_empty: bool = True):
        """Return a MagicMock that looks like the firebase_admin module."""
        fb = MagicMock()
        fb._apps = {} if apps_empty else {"[DEFAULT]": MagicMock()}
        return fb

    def test_initialize_app_called_with_project_id(self):
        """FirebaseAuth must pass projectId option when FIREBASE_PROJECT_ID is set."""
        fb_mock = self._make_firebase_module(apps_empty=True)
        fb_auth_mock = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "firebase_admin": fb_mock,
                "firebase_admin.auth": fb_auth_mock,
            },
        ):
            # Reload to pick up patched module
            import importlib

            import core.auth_interfaces as auth_mod

            importlib.reload(auth_mod)

            with patch.dict(os.environ, {"FIREBASE_PROJECT_ID": "aaagents"}):
                # Reset _apps so init runs
                fb_mock._apps = {}
                auth_mod.FirebaseAuth()

            fb_mock.initialize_app.assert_called_once_with(
                options={"projectId": "aaagents"}
            )

    def test_initialize_app_called_without_options_when_no_env(self):
        """FirebaseAuth must call initialize_app with empty options if env var absent."""
        fb_mock = self._make_firebase_module(apps_empty=True)
        fb_auth_mock = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "firebase_admin": fb_mock,
                "firebase_admin.auth": fb_auth_mock,
            },
        ):
            import importlib

            import core.auth_interfaces as auth_mod

            importlib.reload(auth_mod)

            env = {k: v for k, v in os.environ.items() if k != "FIREBASE_PROJECT_ID"}
            with patch.dict(os.environ, env, clear=True):
                fb_mock._apps = {}
                auth_mod.FirebaseAuth()

            fb_mock.initialize_app.assert_called_once_with(options={})

    def test_initialize_app_skipped_when_already_initialized(self):
        """FirebaseAuth must NOT call initialize_app again if app already exists."""
        fb_mock = self._make_firebase_module(apps_empty=False)
        fb_auth_mock = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "firebase_admin": fb_mock,
                "firebase_admin.auth": fb_auth_mock,
            },
        ):
            import importlib

            import core.auth_interfaces as auth_mod

            importlib.reload(auth_mod)

            with patch.dict(os.environ, {"FIREBASE_PROJECT_ID": "aaagents"}):
                auth_mod.FirebaseAuth()

            fb_mock.initialize_app.assert_not_called()


# ---------------------------------------------------------------------------
# FirebaseAuth — verify_token
# ---------------------------------------------------------------------------


class TestFirebaseAuthVerifyToken:
    """Verify token path logic — valid token, expired token, missing header."""

    def _make_auth(self, fb_auth_mock):
        fb_mod = MagicMock()
        fb_mod._apps = {}
        with patch.dict(
            "sys.modules",
            {
                "firebase_admin": fb_mod,
                "firebase_admin.auth": fb_auth_mock,
            },
        ):
            import importlib

            import core.auth_interfaces as auth_mod

            importlib.reload(auth_mod)
            fb_mod._apps = {}  # force re-init
            instance = auth_mod.FirebaseAuth()
            instance.fb_auth = fb_auth_mock
        return instance

    def test_valid_token_returns_user_context(self):
        fb_auth_mock = MagicMock()
        fb_auth_mock.verify_id_token.return_value = {
            "uid": "uid-abc",
            "email": "andreas@aaagents.de",
        }
        auth = self._make_auth(fb_auth_mock)
        ctx = auth.verify_token(_make_request("good-token"))
        assert ctx.email == "andreas@aaagents.de"
        assert ctx.uid == "uid-abc"

    def test_missing_bearer_raises_401(self):
        fb_auth_mock = MagicMock()
        auth = self._make_auth(fb_auth_mock)
        req = MagicMock()
        req.headers = {"Authorization": "Token bad"}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        with pytest.raises(HTTPException) as exc:
            auth.verify_token(req)
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self):
        fb_auth_mock = MagicMock()
        fb_auth_mock.verify_id_token.side_effect = Exception("Token expired")
        auth = self._make_auth(fb_auth_mock)
        with pytest.raises(HTTPException) as exc:
            auth.verify_token(_make_request("expired"))
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# LocalMockAuth — basic sanity checks
# ---------------------------------------------------------------------------


class TestLocalMockAuth:
    def _get_cls(self):
        import importlib

        import core.auth_interfaces as auth_mod

        importlib.reload(auth_mod)
        return auth_mod.LocalMockAuth

    def test_valid_token_from_loopback_accepted(self):
        cls = self._get_cls()
        auth = cls()
        ctx = auth.verify_token(_make_request("any-token"))
        assert ctx.email == os.environ.get("OPERATOR_EMAIL", "admin@localhost")

    def test_empty_token_raises_401(self):
        cls = self._get_cls()
        auth = cls()
        req = MagicMock()
        req.headers = {"Authorization": "Bearer "}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        with pytest.raises(HTTPException) as exc:
            auth.verify_token(req)
        assert exc.value.status_code == 401

    def test_external_ip_raises_403(self):
        cls = self._get_cls()
        auth = cls()
        req = MagicMock()
        req.headers = {"Authorization": "Bearer valid"}
        req.client = MagicMock()
        req.client.host = "8.8.8.8"
        with pytest.raises(HTTPException) as exc:
            auth.verify_token(req)
        assert exc.value.status_code == 403
