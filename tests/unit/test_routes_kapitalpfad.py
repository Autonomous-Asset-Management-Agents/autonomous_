"""#4068 (ARC-E6 G-4b) — der Kapitalpfad liegt in ``core/engine/routes/kapitalpfad.py``.

Scharfschalten, Abgleich, Stornos, Panik-Verkauf, Kill-Switch und Stop stehen in einer
eigenen Datei, die CODEOWNERS eigens nennen kann. Die HTTP-Flaeche bleibt gleich (das
beweist ``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich
umgezogen sind und Patches auf ``core.engine.api_routes.engine`` weiter greifen.

Plan: ``docs/4068-g4b-kapitalpfad-router/implementation_plan.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

REPO = Path(__file__).resolve().parents[3]

#: Route → Name des Handlers (Plan §1).
KAPITALPFAD = {
    ("POST", "/start-live"): "start_live",
    ("POST", "/stop"): "stop",
    ("POST", "/panic-sell"): "panic_sell",
    ("POST", "/reset-kill-switch"): "reset_kill_switch",
    ("GET", "/api/reconciliation/block"): "reconciliation_block",
    ("POST", "/api/reconciliation/release"): "reconciliation_release",
    ("POST", "/cancel-order"): "cancel_order",
    ("POST", "/api/live/enable"): "live_enable",
    ("POST", "/api/live/disable"): "live_disable",
}
HELFER = ("_abgleich_dienst", "_sperr_bericht")

_KEY = "test-engine-key-kapitalpfad"


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_neun_routen_liegen_im_kapitalpfad_router():
    from core.engine.routes import ROUTER, kapitalpfad

    assert kapitalpfad.router in ROUTER
    assert _routen(kapitalpfad.router) == KAPITALPFAD


def test_die_app_bedient_den_kapitalpfad_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in KAPITALPFAD
    }
    assert bedient == dict.fromkeys(KAPITALPFAD, "core.engine.routes.kapitalpfad")


def test_der_kapitalpfad_ist_auch_beim_import_des_routers_zuerst_eingebunden():
    """Wer ``kapitalpfad`` vor ``api_routes`` importiert, bekommt trotzdem eine App mit
    Kapitalpfad — der Import-Kreis laeuft ueber ``core/engine/__init__.py``."""
    probe = (
        "from core.engine.routes import kapitalpfad\n"
        "from core.engine import api_routes\n"
        "pfade = {r.path for r in api_routes.app.routes}\n"
        "assert '/panic-sell' in pfade and '/api/live/enable' in pfade, pfade\n"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO / "ai_trading_bot",
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert lauf.returncode == 0, lauf.stderr[-2000:]


def test_api_routes_enthaelt_den_kapitalpfad_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*KAPITALPFAD.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_codeowners_nennt_den_kapitalpfad():
    zeilen = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    eintraege = {
        z.split()[0]: z.split()[1:]
        for z in (zeile.split("#", 1)[0].strip() for zeile in zeilen)
        if z
    }
    assert eintraege.get("/ai_trading_bot/core/engine/routes/kapitalpfad.py") == [
        "@apeldorn"
    ]


def test_ein_patch_auf_api_routes_engine_erreicht_panic_sell():
    from fastapi.testclient import TestClient

    from core.engine import api_routes
    from core.kill_switch import kill_switch

    gepatcht = MagicMock()
    gepatcht.api.get_all_positions.return_value = []
    # Der echte Halt (fail_closed) schaltet die Konfiguration auf Papier und schreibt einen
    # WORM-Eintrag — das bliebe ueber diesen Test hinaus stehen. Geprueft wird hier nur,
    # dass der Handler den gepatchten Zustand sieht.
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
        kill_switch, "trip"
    ) as halt, patch.object(api_routes, "engine", gepatcht):
        antwort = TestClient(api_routes.app).post(
            "/panic-sell", headers={"X-Engine-Key": _KEY}
        )

    assert antwort.status_code == 200
    halt.assert_called_once()
    gepatcht.api.cancel_orders.assert_called_once()
    gepatcht.api.get_all_positions.assert_called_once()
