"""Router je Domaene fuer ``core.engine.api_routes`` (#3827, ARC-E6 G-4a).

Die Routen wandern in Teilen (G-4b … G-4i) aus ``api_routes.py`` in Module dieses Pakets.
Jedes Modul legt einen ``APIRouter`` an und traegt ihn in ``ROUTER`` ein; ``api_routes``
bindet die Liste am Ende ein. Ohne Praefix und ohne Tags bleibt die HTTP-Flaeche dabei
unveraendert — ``tests/unit/test_api_flaeche_schnappschuss.py`` beweist es.

Zugriffsregel (bewacht von ``tests/unit/test_routes_zugriffsregel.py``):
    Geteilter Zustand (``engine``, ``config``, ``RedisClient``, ``hitl_gate``,
    ``get_global_registry``) bleibt in ``api_routes`` und wird **zur Laufzeit ueber das
    Modulobjekt** gelesen::

        from core.engine import api_routes as ar

        @router.get("/api/...")
        async def handler():
            return ar.engine.status()

    Nie ``from core.engine.api_routes import engine``: Diese Bindung sieht einen Patch auf
    ``core.engine.api_routes.engine`` nicht mehr, der Test wird still falsch. Der
    Import-Kreis ist harmlos, weil das Modulobjekt erst im Handler gelesen wird.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from fastapi import APIRouter, FastAPI

ROUTER: list[APIRouter] = []

#: Router-Module dieses Pakets. Ein Modul traegt sich erst beim Import in ``ROUTER`` ein;
#: ``binde_ein`` importiert sie deshalb vor dem Einbinden (#4068, G-4b).
_MODULE = ("kapitalpfad", "steuerung", "portfolio")


def binde_ein(app: FastAPI, router: Iterable[APIRouter] = ROUTER) -> None:
    """Bindet die Router ohne Praefix und ohne Tags ein — die Flaeche bleibt gleich."""
    for modul in _MODULE:
        importlib.import_module(f"{__name__}.{modul}")
    for r in router:
        app.include_router(r)
