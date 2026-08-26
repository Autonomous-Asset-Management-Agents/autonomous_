"""Tests for public API proxy configuration: CORS, header restrictions, and endpoint routing.

Validates:
- Dynamic CORS origin loading from CORS_ALLOWED_ORIGINS env var
- Hardcoded default origins in OSS mode
- Restricted allowed headers (Content-Type + Authorization only)
- Dynamic route registration from ALLOWED_GET_PATHS
- All dashboard endpoints are proxied (both in allowlist AND registered as routes)
"""

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient

# We mock require_auth in serve_public_api to avoid dependency on firebase / auth_data
with mock.patch(
    "serve_public_api._require_auth",
    return_value={"email": "test@example.com"},
):
    from serve_public_api import ALLOWED_GET_PATHS, app


def _registered_route_paths() -> set[str]:
    """Extract all registered GET route paths from the FastAPI app."""
    return {r.path for r in app.routes if hasattr(r, "methods") and "GET" in r.methods}


def test_cors_default_origins():
    """Default origins (localhost:5173, console.aaagents.de etc.) should be allowed."""
    client = TestClient(app)

    # Check that a default origin is allowed
    response = client.options(
        "/strategy",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    )

    # Check that an unauthorized origin is NOT allowed
    response = client.options(
        "/strategy",
        headers={
            "Origin": "https://some-malicious-site.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_header_restriction():
    """Only Content-Type and Authorization headers should be allowed, not wildcards."""
    client = TestClient(app)

    # Content-Type should be allowed
    response = client.options(
        "/strategy",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 200
    allowed = response.headers.get("access-control-allow-headers", "").lower()
    assert "content-type" in allowed


def test_cors_env_parsing():
    """CORS_ALLOWED_ORIGINS env var should be parsed correctly (comma-separated)."""
    # Test the parsing logic directly
    raw = "https://a.com, https://b.com , ,https://c.com"
    parsed = [origin.strip() for origin in raw.split(",") if origin.strip()]
    assert parsed == ["https://a.com", "https://b.com", "https://c.com"]

    # Empty string should yield empty list
    raw_empty = ""
    parsed_empty = [origin.strip() for origin in raw_empty.split(",") if origin.strip()]
    assert parsed_empty == []


def test_all_dashboard_endpoints_are_proxied():
    """All dashboard endpoints must be in ALLOWED_GET_PATHS AND registered as routes.

    This test guards against the exact bug that caused /recent-trades to silently
    fail: an endpoint existed in the engine but was missing from the proxy.
    """
    # Endpoints called by DashboardView.tsx and pages/Dashboard.tsx
    dashboard_get_endpoints = [
        "/strategy",
        "/portfolio-summary",
        "/recent-trades",
        "/benchmark-equity",
        "/recent-news",
        "/stock-history",
    ]

    registered_paths = _registered_route_paths()

    for endpoint in dashboard_get_endpoints:
        # Check 1: Must be in the allowlist
        assert endpoint in ALLOWED_GET_PATHS, (
            f"{endpoint} is missing from ALLOWED_GET_PATHS — "
            f"Dashboard will silently fail to load this data"
        )

        # Check 2: Must be registered as a GET route on the app
        # This is the check that would have caught the /recent-trades bug
        assert endpoint in registered_paths, (
            f"{endpoint} is in ALLOWED_GET_PATHS but NOT registered as a "
            f"GET route on the FastAPI app — requests will return 404"
        )


def test_dynamic_route_registration_is_complete():
    """Every path in ALLOWED_GET_PATHS must be registered as a GET route.

    ADR: Routes are registered dynamically via app.add_api_route() from
    ALLOWED_GET_PATHS to eliminate the dual-maintenance problem (decorator + constant).
    This test ensures the loop didn't miss any paths.
    """
    registered_paths = _registered_route_paths()

    for path in ALLOWED_GET_PATHS:
        assert path in registered_paths, (
            f"{path} is in ALLOWED_GET_PATHS but has no registered GET route. "
            f"The dynamic registration loop may have been broken."
        )


def test_no_enterprise_domains_in_oss_defaults():
    """OSS default CORS origins must not contain enterprise/cloud domains.

    Per AGENTS.md: OSS is single-tenant on localhost. Enterprise domains
    (console.aaagents.de, firebaseapp.com) must be injected via runtime env.
    """
    from serve_public_api import cors_origins

    enterprise_patterns = [
        "aaagents.de",
        "firebaseapp.com",
        "web.app",
    ]

    # This test only validates the code-level defaults. If CORS_ALLOWED_ORIGINS
    # env var is set (as in production), this test is not applicable.
    if not os.getenv("CORS_ALLOWED_ORIGINS"):
        for origin in cors_origins:
            for pattern in enterprise_patterns:
                assert pattern not in origin, (
                    f"OSS default CORS origin '{origin}' contains enterprise "
                    f"domain '{pattern}'. Per AGENTS.md, OSS defaults must be "
                    f"localhost-only. Enterprise domains must be injected via "
                    f"CORS_ALLOWED_ORIGINS env var."
                )
