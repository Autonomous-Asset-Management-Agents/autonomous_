import uuid
import sys
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Module-level mocks — must be set BEFORE importing serve_public_api
# ---------------------------------------------------------------------------


# Mock firebase_admin to prevent ModuleNotFoundError in local test envs.
# Using a plain dummy (not MagicMock) to avoid PicklingError in CI.
class DummyFirebaseAdmin:
    class auth:
        pass

    @staticmethod
    def initialize_app(*args, **kwargs):
        pass


sys.modules["firebase_admin"] = DummyFirebaseAdmin()
sys.modules["firebase_admin.auth"] = DummyFirebaseAdmin.auth()


# Mock fastapi_limiter to prevent ImportError when fastapi-limiter >= 0.2.0
# is installed locally (API changed; requirements.txt pins 0.1.6 for production).
# RateLimiter is used only at lifespan startup — safe to stub for unit tests.
class _DummyFastAPILimiter:
    @staticmethod
    async def init(*args, **kwargs):
        pass


class _DummyRateLimiter:
    def __init__(self, *args, **kwargs):
        pass

    async def __call__(self, *args, **kwargs):
        pass


_dummy_limiter_module = MagicMock()
_dummy_limiter_module.FastAPILimiter = _DummyFastAPILimiter
_dummy_depends_module = MagicMock()
_dummy_depends_module.RateLimiter = _DummyRateLimiter

sys.modules["fastapi_limiter"] = _dummy_limiter_module
sys.modules["fastapi_limiter.depends"] = _dummy_depends_module

from fastapi.testclient import TestClient
from serve_public_api import app

client = TestClient(app)


def test_audit_run_endpoint_missing_token():
    """Test that a missing/malformed Authorization header returns 401 Unauthorized.

    With LocalMockAuth (Option 2 security contract), tokens that are missing,
    empty, or explicitly invalid (e.g. "INVALID", "fake_token") are rejected
    at the auth layer before reaching the operator allowlist check.

    This mirrors the behaviour of FirebaseAuth (which rejects bad tokens
    cryptographically) so that security tests remain meaningful in OSS mode:
      - 401 = not authenticated (bad/missing token — rejected by auth provider)
      - 403 = authenticated, but not authorised (email not in operator list)
    """
    session_id = str(uuid.uuid4())

    # No Authorization header at all
    response = client.get(f"/api/v1/audit/run/{session_id}")
    assert response.status_code == 401

    # Explicit INVALID sentinel
    headers = {"Authorization": "Bearer INVALID"}
    response = client.get(f"/api/v1/audit/run/{session_id}", headers=headers)
    assert response.status_code == 401

    # Empty token after "Bearer "
    headers = {"Authorization": "Bearer "}
    response = client.get(f"/api/v1/audit/run/{session_id}", headers=headers)
    assert response.status_code == 401


def test_audit_run_endpoint_unauthorized_operator():
    """Test that a valid Bearer token not in the operator allowlist returns 403 Forbidden.

    With LocalMockAuth, a structurally valid (non-empty, non-INVALID) Bearer
    token is accepted as admin@localhost. The operator email allowlist in
    serve_public_api then blocks the request with 403 because admin@localhost
    is not a known operator email.

    This is the second semantic step after authentication:
      - 401 = not authenticated (handled by test_audit_run_endpoint_missing_token)
      - 403 = authenticated as admin@localhost, but not an authorised operator
    """
    session_id = str(uuid.uuid4())
    # Structurally valid token — passes LocalMockAuth, blocked by allowlist
    headers = {"Authorization": "Bearer some-valid-looking-oss-token"}
    response = client.get(f"/api/v1/audit/run/{session_id}", headers=headers)
    assert response.status_code == 403


@patch("serve_public_api._require_auth")
@patch("serve_public_api.fetch_round_table_session_by_id", new_callable=AsyncMock)
def test_audit_run_endpoint_success(mock_fetch, mock_auth):
    """Test that a valid session ID returns the correct audit artifact."""
    session_id = str(uuid.uuid4())

    mock_auth.return_value = {"email": "operator@aaagents.de"}

    # Mock the DB request via async patch
    mock_session_record = {
        "session_id": session_id,
        "symbol": "AAPL",
        "consensus_score": 0.75,
        "signal_action": "BUY",
        "gatekeeper_approved": True,
        "gatekeeper_reason": "Risk OK",
        "votes_json": [
            {"agent": "Mock", "score": 1, "weight": 0.5, "reasoning": "Test"}
        ],
        "vote_count": 1,
    }

    # Configure AsyncMock to return the dict
    mock_fetch.return_value = mock_session_record

    headers = {"Authorization": "Bearer fake_admin_token"}  # bypassed by mock
    response = client.get(f"/api/v1/audit/run/{session_id}", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    assert data["symbol"] == "AAPL"
    assert data["consensus_score"] == 0.75
    assert data["gatekeeper_approved"] is True
    assert len(data["votes_json"]) == 1


@patch("serve_public_api._require_auth")
@patch("serve_public_api.fetch_round_table_session_by_id", new_callable=AsyncMock)
def test_audit_run_endpoint_not_found(mock_fetch, mock_auth):
    """Test response when session is not found in database."""
    session_id = str(uuid.uuid4())

    mock_auth.return_value = {"email": "operator@aaagents.de"}
    mock_fetch.return_value = None

    headers = {"Authorization": "Bearer fake_admin_token"}
    response = client.get(f"/api/v1/audit/run/{session_id}", headers=headers)

    assert response.status_code == 404
