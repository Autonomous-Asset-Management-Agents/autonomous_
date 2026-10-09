"""#3827 (ARC-E6 G-4a) — Schnappschuss der HTTP-Flaeche von ``api_routes.app``.

Der Router-Schnitt (G-4b … G-4i) verschiebt Routen aus ``api_routes.py`` in
``core/engine/routes/``. Jeder Teil muss beweisen, dass sich die HTTP-Flaeche dabei nicht
aendert. Verglichen wird je Pfad und Methode, was ein Client sieht: ``operationId``,
Parameter (Name, Ort, Pflicht), Request-Body und Antwortcodes; dazu die WebSocket-Routen.

Ein bewusster Wechsel der Flaeche wird neu erzeugt und als Diff eingecheckt:

    SCHNAPPSCHUSS_NEU=1 python -m pytest tests/unit/test_api_flaeche_schnappschuss.py

Plan: ``docs/3827-router-je-domaene-kapitalpfad-zuerst-einschliess/implementation_plan.md``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from starlette.routing import WebSocketRoute

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

SCHNAPPSCHUSS = Path(__file__).with_name("api_flaeche.json")
_METHODEN = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _schema_kurz(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " | ".join(_schema_kurz(s) for s in schema["anyOf"])
    return schema.get("type", "?")


def _body(operation: dict) -> dict | None:
    body = operation.get("requestBody")
    if body is None:
        return None
    return {
        "pflicht": body.get("required", False),
        "inhalt": {
            art: _schema_kurz(teil.get("schema", {}))
            for art, teil in sorted(body.get("content", {}).items())
        },
    }


def flaeche(app: FastAPI) -> dict:
    """Die vergleichbare HTTP-Flaeche einer App, sortiert und JSON-faehig."""
    operationen = {}
    for pfad, eintrag in app.openapi()["paths"].items():
        for methode, op in eintrag.items():
            if methode not in _METHODEN:
                continue
            operationen[f"{methode.upper()} {pfad}"] = {
                "operationId": op.get("operationId"),
                "parameter": sorted(
                    (
                        {
                            "name": p["name"],
                            "ort": p["in"],
                            "pflicht": p.get("required", False),
                        }
                        for p in op.get("parameters", [])
                    ),
                    key=lambda p: (p["ort"], p["name"]),
                ),
                "body": _body(op),
                "antworten": sorted(op.get("responses", {})),
            }
    websockets = sorted(r.path for r in app.routes if isinstance(r, WebSocketRoute))
    return {"operationen": dict(sorted(operationen.items())), "websockets": websockets}


def schreibe_schnappschuss(app: FastAPI, ziel: Path = SCHNAPPSCHUSS) -> None:
    ziel.write_text(
        json.dumps(flaeche(app), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def unterschiede(soll: dict, ist: dict) -> list[str]:
    """Lesbare Abweichungen: Pfad, Methode und Feld."""
    meldungen = []
    for schluessel in sorted(set(soll["operationen"]) | set(ist["operationen"])):
        alt = soll["operationen"].get(schluessel)
        neu = ist["operationen"].get(schluessel)
        if alt is None:
            meldungen.append(f"{schluessel}: neu hinzugekommen")
        elif neu is None:
            meldungen.append(f"{schluessel}: entfallen")
        else:
            for feld in sorted(set(alt) | set(neu)):
                if alt.get(feld) != neu.get(feld):
                    meldungen.append(
                        f"{schluessel} [{feld}]: "
                        + _feld_diff(alt.get(feld), neu.get(feld))
                    )
    if soll["websockets"] != ist["websockets"]:
        meldungen.append(
            "WebSocket-Routen: " + _feld_diff(soll["websockets"], ist["websockets"])
        )
    return meldungen


def _feld_diff(alt, neu) -> str:
    if isinstance(alt, list) and isinstance(neu, list):
        nur_alt = [x for x in alt if x not in neu]
        nur_neu = [x for x in neu if x not in alt]
        return f"nur im Schnappschuss {nur_alt!r}, nur in der App {nur_neu!r}"
    return f"Schnappschuss {alt!r}, App {neu!r}"


@pytest.fixture(scope="module")
def app_flaeche():
    from core.engine import api_routes

    return api_routes.app, flaeche(api_routes.app)


def test_die_http_flaeche_stimmt_mit_dem_schnappschuss(app_flaeche):
    app, ist = app_flaeche
    if os.environ.get("SCHNAPPSCHUSS_NEU") == "1":
        schreibe_schnappschuss(app)
    assert (
        SCHNAPPSCHUSS.is_file()
    ), f"{SCHNAPPSCHUSS.name} fehlt — mit SCHNAPPSCHUSS_NEU=1 erzeugen und einchecken."
    soll = json.loads(SCHNAPPSCHUSS.read_text(encoding="utf-8"))
    meldungen = unterschiede(soll, ist)
    assert not meldungen, "HTTP-Flaeche weicht ab:\n" + "\n".join(meldungen)


def test_ein_entfallener_parameter_nennt_pfad_methode_und_feld(app_flaeche):
    _, ist = app_flaeche
    soll = json.loads(json.dumps(ist))
    schluessel, op = next(
        (k, v) for k, v in soll["operationen"].items() if v["parameter"]
    )
    entfernt = op["parameter"].pop()
    (meldung,) = unterschiede(soll, ist)
    assert meldung.startswith(f"{schluessel} [parameter]:"), meldung
    assert entfernt["name"] in meldung


def test_eine_entfallene_websocket_route_faellt_auf(app_flaeche):
    _, ist = app_flaeche
    soll = json.loads(json.dumps(ist))
    soll["websockets"].append("/ws/probe")
    (meldung,) = unterschiede(soll, ist)
    assert "/ws/probe" in meldung


def _probe_app_mit_dekorator() -> FastAPI:
    app = FastAPI()

    @app.get("/api/probe/{nr}")
    async def probe(nr: int, tiefe: int = 1):
        return {}

    return app


def _probe_app_mit_router() -> FastAPI:
    router = APIRouter()

    @router.get("/api/probe/{nr}")
    async def probe(nr: int, tiefe: int = 1):
        return {}

    from core.engine.routes import binde_ein

    app = FastAPI()
    binde_ein(app, [router])
    return app


def test_ein_eingebundener_router_hat_dieselbe_flaeche_wie_eine_app_route():
    assert flaeche(_probe_app_mit_router()) == flaeche(_probe_app_mit_dekorator())


def test_api_routes_bindet_jeden_router_aus_der_liste_ein():
    from core.engine import api_routes
    from core.engine.routes import ROUTER

    eingebunden = {id(getattr(r, "endpoint", None)) for r in api_routes.app.routes}
    for router in ROUTER:
        for route in router.routes:
            assert id(route.endpoint) in eingebunden, route.path
