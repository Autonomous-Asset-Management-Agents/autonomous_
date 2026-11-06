"""Strategie-Simulation der Werkstatt (#4917, STW-9b; ADR-027 Punkte 8 und 12).

``POST /api/strategy-sim/runs`` startet einen Vergleich von 1-3 Strategien als Subprozess
(optional zusaetzlich der Lieferzustand ``vergleich.LIEFERZUSTAND``, #5126),
``GET /api/strategy-sim/runs/{run_id}`` liefert Status und Ergebnis. Die Logik liegt in
``core/sim/vergleich.py`` (#4635); hier stehen nur Pruefreihenfolge und HTTP-Codes:

Flag (404) → Eingabe (422) → Ablage (503) → Editions-Grenze (403) → Korpus (409) → Start oder
Wiederverwendung (202) → Span. Gerechnet wird nie im Engine-Prozess. ``simulation_enabled``
gilt dem alten Thread-Backtest (``/run-simulation``), nicht der Werkstatt.

Fehler im Format von #4890 (``core/engine/fehler.py``), der 404 bei Flag aus bleibt der des
Frameworks. Auf der HTTP-Flaeche heisst die Lauf-ID ``run_id``; ``vergleich.py`` nennt sie
``lauf_id``, ``_antwort`` uebersetzt.

Kein ``bump_usage`` und keine Ergebniszahl in Logs oder Spans (ADR-027 Regel 5).

Plan: ``docs/4917-stw-9b-feat-engine-engine-routes-and-api-client/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_engine_key
from core.engine import api_routes as ar
from core.engine.fehler import CodedHTTPException
from core.engine.routes import ROUTER

router = APIRouter()
ROUTER.append(router)

#: Serialisiert Pruefen und Starten: zwei gleiche POSTs starten hoechstens einen Prozess.
_SPERRE = asyncio.Lock()

#: ``KeinKorpus.grund`` → Code (#4890); ``params.reason`` traegt den Grund unveraendert.
_KORPUS_CODE = {
    "kein_korpus": "simulation_corpus_missing",
    "keine_keys": "simulation_keys_missing",
}


def _aktiv() -> None:
    # Wie ``steuerung._strategien_aktiv``: die Route soll fehlen, nicht abgelehnt aussehen.
    if not ar.config.STRATEGY_PROFILES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _heute() -> date:
    """Der Kalendertag in New York, ueber die Engine-Uhr (Uhr-Ratsche #3668)."""
    from zoneinfo import ZoneInfo

    from core.sim.clock import engine_now

    return engine_now(ZoneInfo("America/New_York")).date()


def _antwort(ergebnis: Dict[str, Any]) -> Dict[str, Any]:
    """``lauf_id`` → ``run_id``; der Rest so, wie ``lies_ergebnis`` ihn liefert."""
    out = {"run_id": ergebnis["lauf_id"]}
    out.update((k, w) for k, w in ergebnis.items() if k != "lauf_id")
    return out


@router.post("/api/strategy-sim/runs", status_code=202)
async def strategy_sim_start(
    body: Dict, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Vergleich starten — oder den vorhandenen Lauf derselben Eingabe zurueckgeben."""
    from core import strategie_profile as sp
    from core.engine.routes.steuerung import _anfrage_ungueltig
    from core.entitlement import resolve_entitlement
    from core.sim import vergleich as v

    _aktiv()
    try:
        eingabe = v.VergleichsEingabe.model_validate(body)
    except ValueError as exc:
        raise _anfrage_ungueltig(exc) from exc
    try:
        bestand = sp.lade_strategien()
    except sp.StrategieAblageError as exc:
        raise CodedHTTPException(503, str(exc), code=exc.code) from exc
    monate = resolve_entitlement().backtest_months
    try:
        v.pruefe_eingabe(eingabe, _heute(), monate, list(bestand))
    except v.ZeitraumUeberEditionsGrenze as exc:
        raise CodedHTTPException(
            403,
            f"The edition allows at most {monate} months",
            code="simulation_edition_limit",
            params={"months": monate},
        ) from exc
    except v.EingabeUngueltig as exc:
        # Der Grund aus ``vergleich.py`` ist deutsch; er bleibt im Log, nicht in der Antwort.
        ar.logger.info("[STRATEGIE-SIM] Eingabe abgelehnt: %s", exc)
        raise CodedHTTPException(
            422, "Invalid comparison input", code="simulation_input_invalid"
        ) from exc
    try:
        v.pruefe_korpus(eingabe.beginn, eingabe.ende)
    except v.KeinKorpus as exc:
        raise CodedHTTPException(
            409,
            "No simulation data for this period",
            code=_KORPUS_CODE.get(exc.grund, "simulation_corpus_missing"),
            params={"reason": exc.grund},
        ) from exc

    kanonisch = v.kanonisch(eingabe, v.werte_fuer(eingabe.strategien, bestand))
    run_id = v.lauf_id(kanonisch)
    async with _SPERRE:
        if v.laeuft(run_id):
            status, wieder = "laeuft", True
        elif v.ist_fertig(run_id):
            status, wieder = "fertig", True
        else:
            await v.starte(run_id, kanonisch)
            status, wieder = "laeuft", False

    try:
        with ar._tracer.start_as_current_span("strategy_sim.start") as _span:
            _span.set_attribute("run_id", run_id)
            lieferzustand = v.LIEFERZUSTAND in eingabe.strategien
            _span.set_attribute(
                "strategy_count", len(eingabe.strategien) - lieferzustand
            )
            _span.set_attribute("delivery_state", lieferzustand)
            _span.set_attribute("days", (eingabe.ende - eingabe.beginn).days)
            _span.set_attribute("reused", wieder)
    except Exception as exc:  # noqa: BLE001 — Telemetrie bricht nie den Start
        ar.logger.warning("[STRATEGIE-SIM] strategy_sim.start span failed: %s", exc)
    return {"run_id": run_id, "status": status}


@router.get("/api/strategy-sim/runs/{run_id}")
async def strategy_sim_result(
    run_id: str, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Status ``laeuft`` / ``fertig`` / ``fehler``; bei ``fertig`` das Ergebnis. Ohne Span."""
    from core.sim import vergleich as v

    _aktiv()
    ergebnis = v.lies_ergebnis(run_id)
    if ergebnis is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _antwort(ergebnis)
