# tests/unit/test_engine_swap_api.py
# Epic 2.3-Pre / PR-C — TDD Green-Phase
# Issue G: POST /api/strategy/swap API-Endpunkt
#
# Nutzt app.dependency_overrides um require_engine_key zu überspringen.

import os
from unittest.mock import MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_app_and_deps():
    """Gibt app + require_engine_key + verify_firebase_token zurück.

    Since DEV-15.2, /api/strategy/swap has a second auth dependency
    (verify_user_id_sig via Airlock protocol).
    Both must be overridden in dependency_overrides for tests to reach
    the actual business logic.
    """
    import core.engine.api_routes as api_routes_mod
    from core.auth import require_engine_key, verify_user_id_sig

    return api_routes_mod.app, require_engine_key, verify_user_id_sig


# ---------------------------------------------------------------------------
# 1. POST /api/strategy/swap — Endpunkt existiert
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestSwapApiEndpoint:
    def test_swap_endpoint_exists(self):
        """POST /api/strategy/swap ist im App registriert."""
        app, _, _ = _get_app_and_deps()
        routes = [r.path for r in app.routes]
        assert (
            "/api/strategy/swap" in routes
        ), f"Endpunkt nicht gefunden. Routes: {routes}"

    def test_swap_returns_success_true(self):
        """POST /api/strategy/swap gibt success=True wenn swap() erfolgreich."""
        from fastapi.testclient import TestClient

        app, require_engine_key, verify_user_id_sig = _get_app_and_deps()

        registry = MagicMock()
        registry.swap.return_value = True
        # Registriere LSTMDynamic in der Mock-Registry
        registry._strategies = {"LSTMDynamic": MagicMock()}

        # Auth überspringen via dependency_overrides (both deps since DEV-15.2)
        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            with patch(
                "core.engine.api_routes.get_global_registry", return_value=registry
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic", "shadow_mode": False},
                )
            assert (
                response.status_code == 200
            ), f"Unexpected: {response.status_code} {response.text}"
            data = response.json()
            assert data["success"] is True
            assert data["pending"] == "LSTMDynamic"
        finally:
            app.dependency_overrides.clear()

    def test_swap_returns_409_when_swap_in_progress(self):
        """POST /api/strategy/swap gibt 409 Conflict wenn SwapInProgressError."""
        from fastapi.testclient import TestClient

        from core.exceptions import SwapInProgressError

        app, require_engine_key, verify_user_id_sig = _get_app_and_deps()

        registry = MagicMock()
        registry.swap.side_effect = SwapInProgressError("Swap bereits pending")

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            with patch(
                "core.engine.api_routes.get_global_registry", return_value=registry
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic"},
                )
            assert (
                response.status_code == 409
            ), f"Expected 409, got {response.status_code}: {response.text}"
        finally:
            app.dependency_overrides.clear()

    @patch.dict(os.environ, {"REQUIRE_SIG": "true"})
    def test_swap_rejects_missing_sig_headers(self):
        """POST /api/strategy/swap requires HMAC signatures, else 403 (or 401)."""
        from fastapi.testclient import TestClient

        app, require_engine_key, _ = _get_app_and_deps()

        # ONLY mock require_engine_key, leave verify_user_id_sig active
        app.dependency_overrides[require_engine_key] = lambda: None
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/strategy/swap",
                json={"strategy_name": "LSTMDynamic"},
            )
            # Without headers, verify_user_id_sig should raise HTTPException(403/401)
            assert response.status_code in (
                401,
                403,
            ), f"Expected auth failure, got {response.status_code}"
        finally:
            app.dependency_overrides.clear()

    @patch.dict(
        os.environ,
        {
            "PROXY_ENGINE_SHARED_SECRET": "dummy_secret_for_test",
            "REQUIRE_SIG": "true",
        },
    )
    def test_swap_rejects_expired_timestamp(self):
        """POST /api/strategy/swap rejects expired timestamp headers.

        NOTE: This test implicitly relies on REQUIRE_SIG=true (the default)
        being set in the environment. If REQUIRE_SIG=false, the assert will fail.
        """
        import time

        from fastapi.testclient import TestClient

        app, require_engine_key, _ = _get_app_and_deps()

        app.dependency_overrides[require_engine_key] = lambda: None
        try:
            client = TestClient(app, raise_server_exceptions=False)
            expired_ts = str(int(time.time()) - 100)  # 100 seconds ago (> 60s max)
            response = client.post(
                "/api/strategy/swap",
                json={"strategy_name": "LSTMDynamic"},
                headers={
                    "X-User-Id": "test_user",
                    "X-User-Id-Ts": expired_ts,
                    "X-User-Id-Sig": "dummy_sig",
                },
            )
            # Should fail due to clock skew / expiration
            assert response.status_code in (
                401,
                403,
            ), f"Expected auth failure, got {response.status_code}"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TDD Red Phase — I-3: Position Lock (HTTP 423) + Audit Log
# Issue #239: feat: POST /swap + Position-Lock + Audit-Log
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestSwapPositionLockAndAudit:

    def test_swap_rejects_with_open_positions(self):
        """HTTP 423 wenn offene Positionen vorhanden und force=False (default)."""
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod
        from core.auth import require_engine_key, verify_user_id_sig

        # Mock engine.api mit 2 offenen Positionen
        mock_position_a = MagicMock()
        mock_position_a.symbol = "AAPL"
        mock_position_b = MagicMock()
        mock_position_b.symbol = "TSLA"

        app = api_routes_mod.app
        registry = MagicMock()
        registry.swap.return_value = True
        registry._strategies = {"LSTMDynamic": MagicMock()}

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            with (
                patch(
                    "core.engine.api_routes.get_global_registry", return_value=registry
                ),
                patch("core.engine.api_routes.engine") as mock_engine,
            ):
                mock_engine.api = MagicMock()
                mock_engine.api.list_positions.return_value = [
                    mock_position_a,
                    mock_position_b,
                ]
                mock_engine.agent_registry = registry

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic", "force": False},
                )

            assert (
                response.status_code == 423
            ), f"Expected 423 Position Lock, got {response.status_code}: {response.text}"
            body = response.json()
            assert "detail" in body
            assert "error" in body["detail"]
            assert body["detail"]["error"] == "position_lock"
        finally:
            app.dependency_overrides.clear()

    def test_swap_force_bypasses_position_lock(self):
        """force=True → Swap trotz offener Positionen → HTTP 200."""
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod
        from core.auth import require_engine_key, verify_user_id_sig

        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"

        app = api_routes_mod.app
        registry = MagicMock()
        registry.swap.return_value = True
        registry._strategies = {"LSTMDynamic": MagicMock()}

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            with (
                patch(
                    "core.engine.api_routes.get_global_registry", return_value=registry
                ),
                patch("core.engine.api_routes.engine") as mock_engine,
                patch("core.engine.api_routes.get_cloud_logger") as mock_logger_fn,
            ):
                mock_engine.api = MagicMock()
                mock_engine.api.list_positions.return_value = [mock_pos]
                mock_engine.agent_registry = registry
                mock_logger_fn.return_value = MagicMock()

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic", "force": True},
                )

            assert (
                response.status_code == 200
            ), f"Expected 200 with force=True, got {response.status_code}: {response.text}"
            data = response.json()
            assert data["success"] is True
        finally:
            app.dependency_overrides.clear()

    def test_swap_allowed_with_no_positions(self):
        """Kein Lock wenn keine offenen Positionen → HTTP 200."""
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod
        from core.auth import require_engine_key, verify_user_id_sig

        app = api_routes_mod.app
        registry = MagicMock()
        registry.swap.return_value = True
        registry._strategies = {"LSTMDynamic": MagicMock()}

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            with (
                patch(
                    "core.engine.api_routes.get_global_registry", return_value=registry
                ),
                patch("core.engine.api_routes.engine") as mock_engine,
                patch("core.engine.api_routes.get_cloud_logger") as mock_logger_fn,
            ):
                mock_engine.api = MagicMock()
                mock_engine.api.list_positions.return_value = []  # keine Positionen
                mock_engine.agent_registry = registry
                mock_logger_fn.return_value = MagicMock()

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic"},
                )

            assert (
                response.status_code == 200
            ), f"Expected 200 (no positions), got {response.status_code}: {response.text}"
        finally:
            app.dependency_overrides.clear()

    def test_swap_audit_log_called_on_success(self):
        """log_swap_event() wird exakt 1x aufgerufen bei erfolgreichem Swap."""
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod
        from core.auth import require_engine_key, verify_user_id_sig

        app = api_routes_mod.app
        registry = MagicMock()
        registry.swap.return_value = True
        registry._strategies = {"LSTMDynamic": MagicMock()}

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            mock_logger = MagicMock()
            with (
                patch(
                    "core.engine.api_routes.get_global_registry", return_value=registry
                ),
                patch("core.engine.api_routes.engine") as mock_engine,
                patch(
                    "core.engine.api_routes.get_cloud_logger", return_value=mock_logger
                ),
            ):
                mock_engine.api = MagicMock()
                mock_engine.api.list_positions.return_value = []
                mock_engine.agent_registry = registry

                client = TestClient(app, raise_server_exceptions=False)
                client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic", "shadow_mode": False},
                )

            mock_logger.log_swap_event.assert_called_once()
            call_kwargs = mock_logger.log_swap_event.call_args
            assert call_kwargs is not None, "log_swap_event wurde nicht aufgerufen"
        finally:
            app.dependency_overrides.clear()

    def test_swap_audit_log_failure_is_non_blocking(self):
        """Wenn log_swap_event() eine Exception wirft, gibt der Endpoint trotzdem 200 zurück."""
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod
        from core.auth import require_engine_key, verify_user_id_sig

        app = api_routes_mod.app
        registry = MagicMock()
        registry.swap.return_value = True
        registry._strategies = {"LSTMDynamic": MagicMock()}

        app.dependency_overrides[require_engine_key] = lambda: None
        app.dependency_overrides[verify_user_id_sig] = lambda: None
        try:
            mock_logger = MagicMock()
            mock_logger.log_swap_event.side_effect = Exception(
                "Cloud SQL connection failed"
            )
            with (
                patch(
                    "core.engine.api_routes.get_global_registry", return_value=registry
                ),
                patch("core.engine.api_routes.engine") as mock_engine,
                patch(
                    "core.engine.api_routes.get_cloud_logger", return_value=mock_logger
                ),
            ):
                mock_engine.api = MagicMock()
                mock_engine.api.list_positions.return_value = []
                mock_engine.agent_registry = registry

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/strategy/swap",
                    json={"strategy_name": "LSTMDynamic"},
                )

            assert (
                response.status_code == 200
            ), f"Audit failure should be non-blocking. Got {response.status_code}: {response.text}"
            assert response.json()["success"] is True
        finally:
            app.dependency_overrides.clear()
