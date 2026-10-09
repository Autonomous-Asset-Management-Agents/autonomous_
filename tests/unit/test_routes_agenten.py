"""#4072 (ARC-E6 G-4f) — Agenten-Routen liegen in ``core/engine/routes/agenten.py``.

Round Table, Specialist-Reports, Chat, Simulation und die v1/v2-Routen dienen dem Zugriff auf
die Agenten-Schicht. Die HTTP-Fläche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich umgezogen sind und
ein Patch auf ``core.engine.api_routes.engine`` weiter greift.

Plan: ``docs/4072-g4f-agenten-router/implementation_plan.md``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
AGENTEN = {
    ("POST", "/run-simulation"): "run_sim",
    ("GET", "/simulation-result"): "simulation_result",
    ("POST", "/run-learning"): "run_learn",
    ("POST", "/api/v1/engine/force-cycle"): "force_cycle",
    ("POST", "/chat"): "chat",
    ("GET", "/api/v2/universe"): "get_symbol_universe",
    ("GET", "/specialist-report/{symbol}"): "get_specialist_report",
    ("GET", "/specialist-reports"): "get_specialist_reports",
    ("GET", "/round-table-decisions"): "get_round_table_decisions_route",
    ("GET", "/round-table/{symbol}"): "get_round_table_for_symbol",
}
HELFER = ("_serialize_specialist_report",)


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_zehn_routen_liegen_im_agenten_router():
    from core.engine.routes import ROUTER, agenten

    assert agenten.router in ROUTER
    assert _routen(agenten.router) == AGENTEN


def test_die_app_bedient_die_agenten_routen_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in AGENTEN
    }
    assert bedient == dict.fromkeys(AGENTEN, "core.engine.routes.agenten")


def test_api_routes_enthaelt_die_agenten_routen_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*AGENTEN.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_ein_patch_auf_api_routes_engine_erreicht_den_round_table_handler():
    """``engine`` bleibt in ``api_routes`` — ein Patch dort muss im umgezogenen
    Handler ankommen (Plan §5, dritter Fall)."""
    from fastapi.testclient import TestClient

    from core.auth import require_engine_key
    from core.engine import api_routes

    app = api_routes.app
    app.dependency_overrides[require_engine_key] = lambda: None
    registry = MagicMock()
    registry.get_report.return_value = None
    try:
        with patch(
            "core.engine.api_routes.engine",
            SimpleNamespace(specialist_registry=registry),
        ), patch(
            "core.round_table.recent_decisions.get_round_table_decision",
            return_value=None,
        ):
            client = TestClient(app)
            round_table = client.get("/round-table/AAPL")
            report = client.get("/specialist-report/AAPL")
    finally:
        app.dependency_overrides.pop(require_engine_key, None)

    assert round_table.status_code == 200
    assert round_table.json()["symbol"] == "AAPL"
    # Der Handler hat die gepatchte Engine gesehen: deren Registry wurde befragt.
    assert report.status_code == 404
    registry.get_report.assert_called_once_with("AAPL")
