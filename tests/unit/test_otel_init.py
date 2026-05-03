# tests/unit/test_otel_init.py
# Task #361 — Backend Boundary Instrumentation (OTel SDK Initialization)
# TDD: Tests written FIRST (RED phase) — implementation in core/telemetry.py and core/otel_middleware.py
#
# Run: pytest tests/unit/test_otel_init.py -v

import os
import sys
import importlib
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 1. core/telemetry.py — init_telemetry() basics
# ---------------------------------------------------------------------------


class TestInitTelemetry:
    """Tests for the central OTel initialisation function."""

    def test_module_is_importable(self):
        """core.telemetry must be importable without raising."""
        import core.telemetry  # noqa: F401 — just check importability

    def test_init_telemetry_callable(self):
        """init_telemetry() must be a callable exported from core.telemetry."""
        from core.telemetry import init_telemetry

        assert callable(init_telemetry)

    def test_get_tracer_callable(self):
        """get_tracer() must be a callable exported from core.telemetry."""
        from core.telemetry import get_tracer

        assert callable(get_tracer)

    def test_init_telemetry_does_not_raise(self):
        """init_telemetry() must never raise, even if Cloud Trace is unavailable."""
        from core.telemetry import init_telemetry

        # Should silently swallow any Cloud Trace connection error in test env
        try:
            init_telemetry()
        except Exception as exc:
            raise AssertionError(
                f"init_telemetry() raised an exception in test env: {exc}"
            )

    def test_init_telemetry_is_idempotent(self):
        """Calling init_telemetry() multiple times must be safe (no duplicate providers)."""
        from core.telemetry import init_telemetry

        init_telemetry()
        init_telemetry()  # second call — must NOT raise or double-register

    def test_get_tracer_returns_object(self):
        """get_tracer() must return a usable tracer object."""
        from core.telemetry import init_telemetry, get_tracer

        init_telemetry()
        tracer = get_tracer("test.module")
        assert tracer is not None


# ---------------------------------------------------------------------------
# 2. service.version attribute — must contain GIT_COMMIT env var
# ---------------------------------------------------------------------------


class TestServiceVersion:
    """service.version must be set from GIT_COMMIT env var."""

    def test_service_version_from_env(self):
        """When GIT_COMMIT is set, service.version must equal its value."""
        # Re-import with mocked env to get a fresh state
        with patch.dict(os.environ, {"GIT_COMMIT": "abc1234"}, clear=False):
            # Reload to pick up env change
            if "core.telemetry" in sys.modules:
                del sys.modules["core.telemetry"]
            from core.telemetry import get_service_version

            assert get_service_version() == "abc1234"

    def test_service_version_fallback_when_env_missing(self):
        """When GIT_COMMIT is not set, service.version must fall back to 'unknown'."""
        env_without_commit = {k: v for k, v in os.environ.items() if k != "GIT_COMMIT"}
        with patch.dict(os.environ, env_without_commit, clear=True):
            if "core.telemetry" in sys.modules:
                del sys.modules["core.telemetry"]
            from core.telemetry import get_service_version

            assert get_service_version() == "unknown"


# ---------------------------------------------------------------------------
# 3. core/otel_middleware.py — OtelSpanMiddleware
# ---------------------------------------------------------------------------


class TestOtelSpanMiddleware:
    """Tests for the FastAPI span middleware."""

    def test_middleware_class_is_importable(self):
        """OtelSpanMiddleware must be importable from core.otel_middleware."""
        from core.otel_middleware import OtelSpanMiddleware  # noqa: F401

        assert OtelSpanMiddleware is not None

    def test_middleware_is_base_http_middleware_subclass(self):
        """OtelSpanMiddleware must extend Starlette BaseHTTPMiddleware."""
        from starlette.middleware.base import BaseHTTPMiddleware
        from core.otel_middleware import OtelSpanMiddleware

        assert issubclass(OtelSpanMiddleware, BaseHTTPMiddleware)

    def test_middleware_is_noop_when_otel_unavailable(self):
        """Middleware must not crash when OTel is disabled."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.otel_middleware import OtelSpanMiddleware

        app = FastAPI()
        app.add_middleware(OtelSpanMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch("core.otel_middleware.OTEL_AVAILABLE", False):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/ping")
            assert response.status_code == 200

    def test_middleware_attaches_to_fastapi_app(self):
        """Adding OtelSpanMiddleware to a FastAPI app must not crash at startup."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.otel_middleware import OtelSpanMiddleware

        app = FastAPI()
        app.add_middleware(OtelSpanMiddleware)

        @app.get("/healthz")
        async def health():
            return {"status": "ok"}

        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 4. /api/v2/universe endpoint — Gherkin requirement from ticket #361
# ---------------------------------------------------------------------------


class TestUniverseEndpoint:
    """
    Given a request to /api/v2/universe
    When the OTel SDK is initialised as the very first import
    Then spans with the mandatory attributes must be created.

    This test verifies the endpoint exists and returns valid JSON.
    Span attribute checks require a collector (tested in integration).

    NOTE: /api/v2/universe is protected by require_engine_key (PR #744).
    All requests must include ENGINE_API_KEY env var + X-Engine-Key header.
    """

    _TEST_KEY = "test-engine-key-otel"
    _AUTH_HEADERS = {"X-Engine-Key": _TEST_KEY}
    _AUTH_ENV = {"ENGINE_API_KEY": _TEST_KEY, "REQUIRE_SIG": "false"}

    def test_universe_endpoint_exists(self):
        """/api/v2/universe must return HTTP 200 or 503 — not 404."""
        from fastapi.testclient import TestClient
        from core.engine import app

        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, self._AUTH_ENV):
            response = client.get("/api/v2/universe", headers=self._AUTH_HEADERS)
        assert (
            response.status_code != 404
        ), "/api/v2/universe returned 404. Endpoint is missing."

    def test_universe_endpoint_returns_json(self):
        """/api/v2/universe must return a JSON body."""
        from fastapi.testclient import TestClient
        from core.engine import app

        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, self._AUTH_ENV):
            response = client.get("/api/v2/universe", headers=self._AUTH_HEADERS)
        assert response.status_code != 404
        data = response.json()
        assert isinstance(data, dict), (
            f"/api/v2/universe did not return valid JSON dict. "
            f"Status: {response.status_code}, Body: {response.text[:200]}"
        )

    def test_universe_response_has_symbols_key(self):
        """/api/v2/universe JSON must contain a 'symbols' key."""
        from fastapi.testclient import TestClient
        from core.engine import app

        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, self._AUTH_ENV):
            response = client.get("/api/v2/universe", headers=self._AUTH_HEADERS)
        assert response.status_code != 404
        data = response.json()
        assert (
            "symbols" in data
        ), f"Response JSON missing 'symbols' key. Got: {list(data.keys())}"
