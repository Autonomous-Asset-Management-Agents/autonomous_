"""#4069 (ARC-E6 G-4c) — die Steuerung liegt in ``core/engine/routes/steuerung.py``.

HITL, Einstellungen, Abweichungen, Portfolio-Form und Strategie sind die Stellschrauben,
die ein Mensch an der laufenden Engine dreht. Die HTTP-Flaeche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich umgezogen sind
und Patches auf ``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4069-g4c-steuerungs-router/implementation_plan.md``.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
STEUERUNG = {
    ("GET", "/strategy"): "get_strategy",
    ("POST", "/set-strategy"): "set_strategy",
    ("POST", "/api/strategy/swap"): "strategy_swap",
    ("GET", "/api/hitl/pending"): "hitl_pending",
    ("POST", "/api/hitl/approve"): "hitl_approve",
    ("POST", "/api/hitl/reject"): "hitl_reject",
    ("POST", "/api/portfolio-shape"): "portfolio_shape",
    ("GET", "/api/trading-settings"): "trading_settings_get",
    ("POST", "/api/trading-settings"): "trading_settings_post",
    ("POST", "/api/settings-deviation-ack"): "settings_deviation_ack",
    ("GET", "/api/hitl/policy"): "hitl_get_policy",
    ("POST", "/api/hitl/policy"): "hitl_set_policy",
}
HELFER = ("_hitl_policy_dto",)

_KEY = "test-engine-key-steuerung"


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_zwoelf_routen_liegen_im_steuerungs_router():
    from core.engine.routes import ROUTER, steuerung

    assert steuerung.router in ROUTER
    assert _routen(steuerung.router) == STEUERUNG


def test_die_app_bedient_die_steuerung_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in STEUERUNG
    }
    assert bedient == dict.fromkeys(STEUERUNG, "core.engine.routes.steuerung")


def test_api_routes_enthaelt_die_steuerung_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*STEUERUNG.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_ein_patch_auf_api_routes_hitl_gate_erreicht_die_steuerung():
    """Der Handler prueft die Nonce ueber ``ar._enforce_replay_distinct_nonce``, und der liest
    ``api_routes.hitl_gate`` — ein Patch dort muss im umgezogenen Handler ankommen."""
    from fastapi.testclient import TestClient

    from core import hitl_gate as echter_hitl_gate
    from core.engine import api_routes

    gepatcht = MagicMock()
    gepatcht.collect_live_enablement_nonces = AsyncMock(return_value={"n-1"})
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
        echter_hitl_gate, "log_settings_deviation_ack", AsyncMock()
    ) as siegel, patch.object(api_routes, "hitl_gate", gepatcht):
        antwort = TestClient(api_routes.app).post(
            "/api/settings-deviation-ack",
            headers={"X-Engine-Key": _KEY},
            json={
                "decision": "keep",
                "deviations": [{"key": "K", "current": "1", "default": "2"}],
                "nonce": "n-1",
            },
        )

    assert antwort.status_code == 409
    gepatcht.collect_live_enablement_nonces.assert_awaited_once()
    siegel.assert_not_awaited()


def test_ein_patch_auf_api_routes_engine_erreicht_den_strategie_swap():
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    gepatcht = MagicMock()
    gepatcht.api.list_positions.return_value = [MagicMock(symbol="AAPL")]
    # Die HMAC-Pruefung des Proxys gehoert nicht zu dieser Frage — nur der Positions-Lock.
    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes, "engine", gepatcht
        ):
            antwort = TestClient(api_routes.app).post(
                "/api/strategy/swap",
                headers={"X-Engine-Key": _KEY},
                json={"strategy_name": "RLAgent"},
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)

    assert antwort.status_code == 423
    gepatcht.api.list_positions.assert_called_once()
