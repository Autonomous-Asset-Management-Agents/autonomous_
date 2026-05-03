import os
import sys
import types
from unittest.mock import patch
import time
import hmac
import hashlib

# ---------------------------------------------------------------------------
# Stub fastapi only when it is NOT installed (local dev venv without fastapi).
# ---------------------------------------------------------------------------
try:
    import fastapi as _fastapi_real

    _HTTPException = _fastapi_real.HTTPException
except ImportError:
    _fastapi_stub = types.ModuleType("fastapi")

    class _HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str = "") -> None:
            self.status_code = status_code
            self.detail = detail

    def _Header(default=None, alias=None):  # noqa: N802
        return default

    _fastapi_stub.HTTPException = _HTTPException
    _fastapi_stub.Header = _Header
    sys.modules["fastapi"] = _fastapi_stub

# Now it's safe to import core.auth
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.auth import require_engine_key, verify_user_id_sig  # noqa: E402


def _call(bot_key=None, engine_key=None, env_key="test-secret-key-abc123"):
    """Call require_engine_key; return any HTTPException raised, or None."""
    with patch.dict(os.environ, {"ENGINE_API_KEY": env_key}, clear=False):
        try:
            # We pass both to support dual header testing
            require_engine_key(x_bot_api_key=bot_key, x_engine_key=engine_key)
            return None
        except _HTTPException as e:
            return e


def test_valid_bot_key_returns_none():
    """Correct X-Bot-Api-Key should pass silently."""
    result = _call(bot_key="test-secret-key-abc123", env_key="test-secret-key-abc123")
    assert result is None


def test_valid_engine_key_returns_none():
    """Correct X-Engine-Key should pass silently (Dual Header Support)."""
    result = _call(
        engine_key="test-secret-key-abc123", env_key="test-secret-key-abc123"
    )
    assert result is None


def test_wrong_key_returns_403():
    """Wrong key should raise HTTP 403."""
    exc = _call(bot_key="wrong-key", env_key="test-secret-key-abc123")
    assert exc is not None
    assert exc.status_code == 403


def test_missing_key_returns_403():
    """No header supplied should raise HTTP 403."""
    exc = _call(bot_key=None, engine_key=None, env_key="test-secret-key-abc123")
    assert exc is not None
    assert exc.status_code == 403


def test_empty_server_key_returns_503():
    """If ENGINE_API_KEY is empty string on the server, return 503."""
    exc = _call(bot_key="any-key", env_key="")
    assert exc is not None
    assert exc.status_code == 503


def test_no_server_env_var_returns_503():
    """If ENGINE_API_KEY env var is absent entirely, return 503."""
    env = {k: v for k, v in os.environ.items() if k != "ENGINE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        try:
            require_engine_key(x_bot_api_key="some-key", x_engine_key=None)
            result = None
        except _HTTPException as e:
            result = e
    assert result is not None
    assert result.status_code == 503


def test_timing_safe_comparison():
    """Different-length keys should still return 403, not raise ValueError."""
    exc = _call(bot_key="short", env_key="much-longer-expected-key-here")
    assert exc is not None
    assert exc.status_code == 403


def test_missing_tenant_id_returns_403():
    """
    TODO (Epic 2.4/2.5 - Multi-Tenancy Rules):
    Once OIDC tenant_id extraction is fully implemented in the
    auth/dependency layer, this test must verify that a valid
    API/OIDC token that lacks a `tenant_id` claim is rejected
    with HTTP 403.
    """
    pass


# =====================================================================
# Tests for HMAC Signature verification (verify_user_id_sig)
# =====================================================================


def _call_verify(x_user_id, x_user_id_sig, x_user_id_ts, env_vars):
    """Call verify_user_id_sig and return exception or None."""
    with patch.dict(os.environ, env_vars, clear=True):
        try:
            verify_user_id_sig(
                x_user_id=x_user_id,
                x_user_id_sig=x_user_id_sig,
                x_user_id_ts=x_user_id_ts,
            )
            return None
        except _HTTPException as e:
            return e


def _generate_sig(secret: str, user_id: str, ts: str) -> str:
    msg = f"{user_id}:{ts}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def test_verify_sig_success():
    """Valid signature and timestamp should pass silently."""
    secret = "my-shared-secret"
    ts = str(int(time.time()))
    user_id = "test_user@example.com"
    sig = _generate_sig(secret, user_id, ts)

    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig=sig,
        x_user_id_ts=ts,
        env_vars={"REQUIRE_SIG": "true", "PROXY_ENGINE_SHARED_SECRET": secret},
    )
    assert result is None


def test_verify_sig_expired_timestamp():
    """Timestamp older than 60s should fail with 403."""
    secret = "my-shared-secret"
    ts = str(int(time.time()) - 61)  # 61 seconds old
    user_id = "test_user@example.com"
    sig = _generate_sig(secret, user_id, ts)

    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig=sig,
        x_user_id_ts=ts,
        env_vars={"REQUIRE_SIG": "true", "PROXY_ENGINE_SHARED_SECRET": secret},
    )
    assert result is not None
    assert result.status_code == 403
    assert "expired" in result.detail.lower()


def test_verify_sig_invalid_signature():
    """Wrong signature should fail with 403."""
    secret = "my-shared-secret"
    ts = str(int(time.time()))
    user_id = "test_user@example.com"

    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig="wrong-signature",
        x_user_id_ts=ts,
        env_vars={"REQUIRE_SIG": "true", "PROXY_ENGINE_SHARED_SECRET": secret},
    )
    assert result is not None
    assert result.status_code == 403
    assert "invalid" in result.detail.lower()


def test_verify_sig_missing_headers():
    """Missing signature or timestamp headers should fail with 403."""
    secret = "my-shared-secret"
    user_id = "test_user@example.com"

    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig=None,
        x_user_id_ts=None,
        env_vars={"REQUIRE_SIG": "true", "PROXY_ENGINE_SHARED_SECRET": secret},
    )
    assert result is not None
    assert result.status_code == 403
    assert "missing hmac" in result.detail.lower()


def test_verify_sig_require_sig_false_bypass():
    """If REQUIRE_SIG is false, signature check should be bypassed completely."""
    user_id = "test_user@example.com"

    # Missing secret, missing headers, wrong signature - doesn't matter
    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig="invalid",
        x_user_id_ts="invalid",
        env_vars={"REQUIRE_SIG": "false"},  # explicitly false
    )
    assert result is None


def test_verify_sig_no_user_id_returns_403():
    """If REQUIRE_SIG=true and no X-User-Id, must reject with 403."""
    secret = "my-shared-secret"

    result = _call_verify(
        x_user_id=None,
        x_user_id_sig=None,
        x_user_id_ts=None,
        env_vars={
            "REQUIRE_SIG": "true",
            "PROXY_ENGINE_SHARED_SECRET": secret,
        },
    )
    assert result is not None
    assert result.status_code == 403


def test_verify_sig_default_is_fail_closed():
    """Default REQUIRE_SIG must be true (fail-closed)."""
    secret = "my-shared-secret"
    user_id = "test_user@example.com"
    result = _call_verify(
        x_user_id=user_id,
        x_user_id_sig=None,
        x_user_id_ts=None,
        env_vars={
            "PROXY_ENGINE_SHARED_SECRET": secret,
        },  # no REQUIRE_SIG set!
    )
    assert result is not None
    assert result.status_code == 403
