"""#4115 (ARC-E6 G-4j) — Die Engine-Diagnose liegt samt Sammlern in ``core/engine/routes/diagnose.py``.

``GET /engine-diagnostics`` und ihre 17 ``_collect_*``-Sammler sind eine Zuständigkeit: der tiefe
Engine-Zustand für die Konsole. Die HTTP-Fläche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass Route und Sammler wirklich umgezogen
sind, ein Patch auf ``core.engine.api_routes.engine`` weiter greift und
``compute_overall_status`` aus ``routes.health`` kommt.

Plan: ``docs/4115-g4j-engine-diagnose-router/implementation_plan.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Plan §1 — die Helfer, die mit der Route umziehen.
HELFER = (
    "get_service_version",
    "_safe_collect",
    "_collect_process",
    "_collect_loops",
    "_collect_watchdogs",
    "_collect_kill_switch",
    "_collect_governance",
    "_collect_hitl",
    "_collect_risk",
    "_collect_compliance",
    "_collect_db",
    "_collect_execution",
    "_collect_compliance_decisions",
    "_collect_audit_write",
    "_collect_decision",
    "_collect_llm",
    "_collect_models",
    "_collect_data_providers",
    "_collect_usage",
)


@pytest.fixture
def client():
    from core.auth import require_engine_key
    from core.engine import api_routes

    api_routes.app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(api_routes.app)
    api_routes.app.dependency_overrides.clear()


def test_die_route_liegt_im_diagnose_router():
    from core.engine.routes import ROUTER, diagnose

    assert diagnose.router in ROUTER
    assert {(m, r.path) for r in diagnose.router.routes for m in r.methods} == {
        ("GET", "/engine-diagnostics")
    }


def test_die_app_bedient_die_route_aus_dem_neuen_modul():
    from core.engine import api_routes

    (modul,) = {
        r.endpoint.__module__
        for r in api_routes.app.routes
        if getattr(r, "path", None) == "/engine-diagnostics"
    }
    assert modul == "core.engine.routes.diagnose"


def test_route_und_sammler_liegen_nicht_mehr_in_api_routes():
    from core.engine import api_routes
    from core.engine.routes import diagnose

    for name in ("engine_diagnostics", *HELFER):
        assert getattr(diagnose, name).__module__ == "core.engine.routes.diagnose"
    geblieben = [n for n in ("engine_diagnostics", *HELFER) if hasattr(api_routes, n)]
    assert geblieben == []


def test_compute_overall_status_kommt_aus_routes_health():
    from core.engine.routes import diagnose, health

    assert diagnose.compute_overall_status is health.compute_overall_status


def test_ein_patch_auf_api_routes_engine_erreicht_engine_diagnostics(client):
    with patch("core.engine.api_routes.engine", None):
        assert client.get("/engine-diagnostics").json()["engine_ready"] is False
    with patch("core.engine.api_routes.engine", MagicMock()):
        assert client.get("/engine-diagnostics").json()["engine_ready"] is True


def test_ein_abstuerzender_sammler_bricht_die_route_nicht(client, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr("core.engine.routes.diagnose._collect_process", _boom)
    antwort = client.get("/engine-diagnostics")

    assert antwort.status_code == 200
    assert antwort.json()["process"] == {"_error": "RuntimeError"}
