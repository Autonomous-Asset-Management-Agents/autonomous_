"""#4071 (ARC-E6 G-4e) — Admin- und Entitlement-Routen liegen in ``core/engine/routes/admin.py``.

Staging-Gate, Entitlement, Iron-Dome-Policy und Portfolio-Grenzen (beide mit Vier-Augen-Pfad)
sowie die Telemetrie des Abgleichs hängen an Admin- oder Lizenzrechten. Die HTTP-Fläche bleibt
gleich (das beweist ``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich
umgezogen sind und Patches auf ``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4071-g4e-admin-router/implementation_plan.md``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
ADMIN = {
    ("GET", "/staging-gate"): "staging_gate",
    ("POST", "/api/entitlement/checkout"): "entitlement_checkout",
    ("POST", "/api/entitlement/webhook"): "entitlement_webhook",
    (
        "POST",
        "/api/entitlement/lemonsqueezy-webhook",
    ): "entitlement_lemonsqueezy_webhook",
    ("GET", "/api/entitlement/license"): "entitlement_license_lookup",
    ("POST", "/api/entitlement/activate"): "entitlement_activate",
    ("GET", "/api/entitlement/status"): "get_entitlement_status",
    ("POST", "/api/admin/iron-dome-policy"): "set_iron_dome_policy",
    ("POST", "/api/admin/iron-dome-policy/propose"): "propose_iron_dome_policy",
    ("POST", "/api/admin/iron-dome-policy/approve"): "approve_iron_dome_policy",
    (
        "GET",
        "/api/admin/portfolio-constraints/pending",
    ): "get_portfolio_constraints_pending",
    (
        "POST",
        "/api/admin/portfolio-constraints/propose",
    ): "propose_portfolio_constraints",
    (
        "POST",
        "/api/admin/portfolio-constraints/approve",
    ): "approve_portfolio_constraints",
    ("POST", "/api/telemetry/switch-reconcile"): "telemetry_switch_reconcile",
}
HELFER = (
    "_commit_iron_dome_policy",
    "_create_pending",
    "_get_pending",
    "_update_pending_approvals",
    "_mark_pending_applied",
    "_apply_iron_dome_policy_live",
    "_save_iron_dome_policy",
    "_relative_caps_or_none",
)


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_vierzehn_routen_liegen_im_admin_router():
    from core.engine.routes import ROUTER, admin

    assert admin.router in ROUTER
    assert _routen(admin.router) == ADMIN


def test_die_app_bedient_die_admin_routen_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in ADMIN
    }
    assert bedient == dict.fromkeys(ADMIN, "core.engine.routes.admin")


def test_api_routes_enthaelt_die_admin_routen_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*ADMIN.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_ein_patch_auf_api_routes_record_iron_dome_policy_change_erreicht_den_handler():
    """``record_iron_dome_policy_change`` bleibt in ``api_routes`` — der Patch dort muss im
    umgezogenen ``set_iron_dome_policy`` ankommen (Plan §5, vierter Fall)."""
    from fastapi.testclient import TestClient

    from core.auth import require_engine_key
    from core.engine import api_routes
    from core.engine.routes import admin
    from core.governance.iron_dome_admin_auth import require_iron_dome_admin

    app = api_routes.app
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[require_iron_dome_admin] = lambda: None
    try:
        with patch(
            "core.engine.api_routes.record_iron_dome_policy_change",
            new_callable=AsyncMock,
        ) as gepatcht, patch(
            "core.engine.api_routes._load_iron_dome_policy_value",
            new=AsyncMock(return_value={"max_daily_trades": 10}),
        ), patch.object(
            admin, "_save_iron_dome_policy", new_callable=AsyncMock
        ):
            antwort = TestClient(app).post(
                "/api/admin/iron-dome-policy", json={"max_daily_trades": 5}
            )
    finally:
        app.dependency_overrides.pop(require_engine_key, None)
        app.dependency_overrides.pop(require_iron_dome_admin, None)

    assert antwort.status_code == 200
    gepatcht.assert_awaited_once()
    assert gepatcht.call_args.args[0] == {"max_daily_trades": 10}
