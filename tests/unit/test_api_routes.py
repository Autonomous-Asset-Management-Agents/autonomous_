from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app

client = TestClient(app)


@patch("config.get_secret_str")
@patch("core.llm.health.resolved_provider_name")
def test_readiness_all_secrets_present(mock_provider, mock_get_secret):
    mock_provider.return_value = "gemini"
    mock_get_secret.side_effect = ["secret", "secret", "secret"]

    response = client.get("/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["can_start"] is True
    assert data["missing_requirements"] == []
    assert data["status_label"] == "Ready"


@patch("config.get_secret_str")
@patch("core.llm.health.resolved_provider_name")
def test_readiness_missing_alpaca_keys(mock_provider, mock_get_secret):
    mock_provider.return_value = "gemini"

    # ALPACA_API_KEY missing, ALPACA_SECRET_KEY missing, GEMINI_API_KEY present
    mock_get_secret.side_effect = ["", "", "secret"]

    response = client.get("/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["can_start"] is False
    assert "ALPACA_API_KEY" in data["missing_requirements"]
    assert "ALPACA_SECRET_KEY" in data["missing_requirements"]
    assert "GEMINI_API_KEY" not in data["missing_requirements"]
    assert data["status_label"] == "Setup Required"


@patch("config.get_secret_str")
@patch("core.llm.health.resolved_provider_name")
def test_readiness_missing_gemini_key_when_gemini_provider(
    mock_provider, mock_get_secret
):
    mock_provider.return_value = "gemini"

    # ALPACA_API_KEY present, ALPACA_SECRET_KEY present, GEMINI_API_KEY missing
    mock_get_secret.side_effect = ["secret", "secret", ""]

    response = client.get("/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["can_start"] is False
    assert "GEMINI_API_KEY" in data["missing_requirements"]
    assert "ALPACA_API_KEY" not in data["missing_requirements"]
    assert data["status_label"] == "Setup Required"


@patch("config.get_secret_str")
@patch("core.llm.health.resolved_provider_name")
def test_readiness_ignore_gemini_when_other_provider(mock_provider, mock_get_secret):
    mock_provider.return_value = "ollama"

    # ALPACA_API_KEY present, ALPACA_SECRET_KEY present, GEMINI_API_KEY missing
    # Since provider is ollama, it shouldn't check GEMINI_API_KEY and only consume 2 items from side_effect
    mock_get_secret.side_effect = ["secret", "secret"]

    response = client.get("/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["can_start"] is True
    assert data["missing_requirements"] == []
    assert data["status_label"] == "Ready"


# --- CORS preflight regression (desktop dynamic renderer port → UI freeze) ---
#
# The desktop shell serves the renderer on a DYNAMIC loopback port (e.g. 8082),
# not the fixed :3000/:8081 in the explicit ALLOWED_ORIGINS list. Every browser
# CORS preflight (OPTIONS) from that origin was rejected with 400 → the frontend
# retried forever → UI freeze. Fix: allow_origin_regex for any loopback origin
# (localhost / 127.0.0.1 / [::1], any port) PLUS "OPTIONS" in allow_methods,
# while keeping the check tight (no wildcard, credentials-compatible).


def test_cors_preflight_dynamic_loopback_port_allowed():
    # A preflight from a dynamic desktop renderer port must succeed (200, not
    # 400) and reflect the origin so the real GET/POST is not blocked.
    origin = "http://localhost:8082"
    response = client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200, (
        "dynamic-loopback-port preflight must not 400 "
        f"(got {response.status_code}: {response.text!r})"
    )
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_non_loopback_origin_not_reflected():
    # Control: a non-loopback origin must NOT be reflected (regex is loopback-only,
    # no wildcard) — proves the fix did not open CORS to arbitrary sites.
    evil = "http://evil.example.com"
    response = client.options(
        "/health",
        headers={
            "Origin": evil,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != evil


# --- Startup-race guard: /start-live and /stop before engine init (P1) ---------
#
# The module global ``engine`` is initialized by a fire-and-forget background task
# in the FastAPI lifespan (api_routes.py:253 create_task(_init_engine_async)), so
# ``engine is None`` during the init window. ``/health`` already guards this and
# returns status="starting" (api_routes.py:345). But ``/start-live`` and ``/stop``
# dereferenced ``engine`` with NO None-check → pressing "Start" during init raised
# AttributeError: 'NoneType' object has no attribute 'start_live_strategy' → HTTP 500
# → the desktop UI showed "failed to fetch" / "system doesn't start" (confirmed from
# a live production stacktrace at api_routes.py:1239). Regression became user-visible
# after 0.2.12 because the live/paper-switch rework (#2220) made pressing "Start"
# REQUIRED for paper too, so users now hit /start-live early, during init.
#
# These tests patch the module global ``engine`` to None (the "still starting up"
# state) and assert both endpoints degrade GRACEFULLY: HTTP 200 with the existing
# {"status": "error", "message": ...} shape — never a 500 / AttributeError.


@pytest.fixture
def engine_key_override():
    """Bypass the X-Engine-Key auth dependency for these unit tests."""
    app.dependency_overrides[require_engine_key] = lambda: None
    yield
    app.dependency_overrides.pop(require_engine_key, None)


# raise_server_exceptions=False so an unguarded AttributeError surfaces as a 500
# *response* (RED) rather than propagating out of the test; the guard makes it 200.
_no_raise_client = TestClient(app, raise_server_exceptions=False)


def test_start_live_while_engine_still_starting_returns_graceful_error(
    engine_key_override,
):
    with patch("core.engine.api_routes.engine", None):
        response = _no_raise_client.post("/start-live")

    # Must degrade gracefully to 200, never a 500 / AttributeError.
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "error"
    assert "starting" in data["message"].lower()


def test_stop_while_engine_still_starting_returns_graceful_error(
    engine_key_override,
):
    with patch("core.engine.api_routes.engine", None):
        response = _no_raise_client.post("/stop")

    # Must degrade gracefully to 200, never a 500 / AttributeError.
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "error"
    assert "starting" in data["message"].lower()
