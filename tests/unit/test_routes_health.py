"""#4073 (ARC-E6 G-4g) — Health- und Status-Routen liegen in ``core/engine/routes/health.py``.

Liveness/Readiness, System-Health und die lesenden Status-Routen (Compliance, Risiko-Grenzen,
Regime, Diagnose) beantworten, in welchem Zustand die Engine ist. Die HTTP-Fläche bleibt gleich
(das beweist ``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich
umgezogen sind, ein Patch auf ``core.engine.api_routes.engine_init_error`` weiter greift und
``compute_overall_status`` nur einen Ort hat.

Plan: ``docs/4073-g4g-health-status-router/implementation_plan.md``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
HEALTH = {
    ("GET", "/ready"): "ready_check",
    ("GET", "/health/readiness"): "readiness_check",
    ("GET", "/health"): "health_check",
    ("GET", "/system-health"): "system_health",
    ("GET", "/health/deep"): "deep_health",
    ("GET", "/market-regime"): "get_market_regime",
    ("GET", "/risk-limits"): "get_risk_limits",
    ("GET", "/diagnostics"): "diagnostics",
    ("GET", "/compliance-status"): "get_compliance_status",
    ("GET", "/api/regime-preview"): "regime_preview_get",
}


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_zehn_routen_liegen_im_health_router():
    from core.engine.routes import ROUTER, health

    assert health.router in ROUTER
    assert _routen(health.router) == HEALTH


def test_die_app_bedient_die_health_routen_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in HEALTH
    }
    assert bedient == dict.fromkeys(HEALTH, "core.engine.routes.health")


def test_api_routes_enthaelt_die_health_routen_nicht_mehr():
    from core.engine import api_routes

    geblieben = [name for name in HEALTH.values() if hasattr(api_routes, name)]
    assert geblieben == []


def test_compute_overall_status_hat_einen_ort():
    """Seit G-4j (#4115) importiert ``routes/diagnose.py`` es direkt; der Rückimport in
    ``api_routes`` ist entfallen."""
    from core.engine import api_routes
    from core.engine.routes import diagnose, health

    assert health.compute_overall_status.__module__ == "core.engine.routes.health"
    assert diagnose.compute_overall_status is health.compute_overall_status
    assert not hasattr(api_routes, "compute_overall_status")


def test_ein_patch_auf_api_routes_engine_init_error_erreicht_health_check():
    """``engine_init_error`` setzt ``_init_engine_async`` zur Laufzeit neu — nur der Zugriff
    über ``ar`` sieht den aktuellen Wert (Plan §5, dritter Fall)."""
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    with patch("core.engine.api_routes.engine", None), patch(
        "core.engine.api_routes.engine_init_error", "boom"
    ):
        antwort = TestClient(api_routes.app).get("/health")

    assert antwort.status_code == 200
    assert antwort.json()["status"] == "error"
    assert antwort.json()["detail"] == "boom"
