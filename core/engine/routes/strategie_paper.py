"""Zuweisung einer Strategie an einen Paper-Platz (#5123, STW-2e, Epic #4625).

``POST /api/strategies/{name}/assign-paper`` ist das Gegenstueck zu
``steuerung.strategies_assign_live`` fuer die Plaetze ``paper1`` … ``paper3``. Die Console
(``PaperSlotStrategy.tsx``, #4953) ruft sie, die Shell schreibt ``applied`` und startet den
Platz neu (#5117) — die Route selbst startet nichts.

Pruefreihenfolge: Flag (404) → Eingabe (422, auch ``konto='live'``) → Bestand (404/503) →
``basis`` (422 bei unbekanntem Key) → Nonce (409) → WORM-Eintrag (503) → Spiegel → Span.

Owner-Bestaetigung 03.11.2026 an #5123: Der Eintrag geht auf die Kette der antwortenden
Console-Engine, mit ``konto`` am Eintrag und am Span; **kein HITL-Gate** und kein
``verfahren_tor`` (beides Live-Regel, #4989). Die Bestaetigung gilt nur, weil ``konto='live'``
hier mit 422 abgewiesen wird, bevor etwas geschrieben oder eine Nonce verbraucht ist
(``sp.StrategyAssignPaperRequest``). Der Live-Weg bleibt allein ``strategies_assign_live``.

Eine eigene Datei, weil ``steuerung.py`` an der Datei-Schwelle von 1000 Zeilen steht
(``tests/architecture/vertrag.toml`` ``[groessen]``). Geteilter Zustand nur ueber ``ar``
(``tests/unit/test_routes_zugriffsregel.py``).

Plan: ``docs/5123-*/implementation_plan.md`` §2.1.
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends

from core.auth import require_engine_key
from core.engine import api_routes as ar
from core.engine.fehler import CodedHTTPException
from core.engine.routes import ROUTER

router = APIRouter()
ROUTER.append(router)


def _basis_kanonisch(basis: Dict[str, str]) -> Dict[str, str]:
    """``basis`` in kanonischer Textform, fehlende Keys mit ihrem Default.

    Unbekannte Keys → ``UnknownSettingError`` (422). Ein nicht parsebarer Wert wird wie in
    ``ts._parse`` zum Default — derselbe Klemm-Vertrag wie ueberall in der Registry.
    """
    from core import trading_settings as ts

    unbekannt = [k for k in basis if k not in ts.REGISTRY]
    if unbekannt:
        raise ts.UnknownSettingError(unbekannt)
    return {
        s.key: ts.canonical_text(s.key, basis.get(s.key, s.default))
        for s in ts._SETTINGS
    }


@router.post("/api/strategies/{name}/assign-paper", status_code=201)
async def strategies_assign_paper(
    name: str, body: Dict, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Strategie einem Paper-Platz zuweisen — versiegelt, mit ``konto`` am Eintrag.

    Der Diff laeuft gegen ``basis`` (den Stand des Platzes), nicht gegen die antwortende
    Engine. ``applied`` ist der volle Zielstand; die Shell schreibt ihn vollstaendig.
    """
    from core import hitl_gate
    from core import strategie_profile as sp
    from core import trading_settings as ts
    from core.engine.routes import strategie_zuweisung as sz
    from core.engine.routes.steuerung import (
        _als_422,
        _anfrage_ungueltig,
        _strategie_fehlt,
        _strategien_aktiv,
        _strategien_laden,
        _zuweisungs_ziel,
    )

    _strategien_aktiv()
    try:
        # 422 bei konto='live' steht hier: vor Sperre, Nonce und jedem Schreiben.
        req = sp.StrategyAssignPaperRequest.model_validate(body)
    except ValueError as exc:
        raise _anfrage_ungueltig(exc) from exc
    async with sp.SPERRE:
        bestand = _strategien_laden()
        name = name.strip()
        if name not in bestand:
            raise _strategie_fehlt(name)
        werte = bestand[name]

        ziel = _zuweisungs_ziel(werte)
        try:
            basis = _basis_kanonisch(req.basis)
            ts.pruefe_llm_ranking(ziel, basis)
            new = ts.apply_updates(ziel)
        except (ts.UnknownSettingError, ts.UngueltigeEinstellungError) as exc:
            raise _als_422(exc) from exc
        changes = ts.describe_changes(basis, new)

        # Kein HITL-Gate und kein verfahren_tor: Owner-Bestaetigung an #5123, gueltig nur
        # mit der 422 fuer konto='live' oben. Gleicher Nonce-Scope wie Live: eine Nonce
        # zaehlt einmal, ueber alle Konten.
        await ar._enforce_replay_distinct_nonce("strategy_assign", req.nonce)

        strategie_hash = sp.strategie_hash(werte)
        try:
            zeitpunkt = await hitl_gate.log_strategy_assignment_event(
                strategie=name,
                strategie_hash=strategie_hash,
                changes=ts.worm_changes(changes),
                acknowledgment=req.acknowledgment,
                nonce=req.nonce,
                konto=req.konto,
                switch_id=req.nonce,
                strict=True,
            )
        except Exception as exc:  # noqa: BLE001 — ohne Eintrag keine Zuweisung
            ar.logger.warning(
                "[STRATEGIE] Zuweisung %r an %s nicht auditiert: %s",
                name,
                req.konto,
                exc,
            )
            raise CodedHTTPException(
                503, "audit write failed — nothing assigned", code="audit_write_failed"
            ) from exc
        spiegel_ok = sz.zuweisung_spiegeln(
            name, strategie_hash, zeitpunkt, konto=req.konto
        )

    try:
        with ar._tracer.start_as_current_span("strategy.assign") as _span:
            _span.set_attribute("strategie", name)
            _span.set_attribute("konto", req.konto)
            _span.set_attribute("nonce", req.nonce)
            _span.set_attribute("keys", ",".join(sorted(c["key"] for c in changes)))
            _span.set_attribute("restart_required", "true")
            _span.set_attribute("actor", "operator")
            _span.set_attribute("mirror_failed", "false" if spiegel_ok else "true")
    except (
        Exception
    ) as exc:  # noqa: BLE001 — Telemetrie bricht nie den auditierten Pfad
        ar.logger.warning("[STRATEGIE] strategy.assign span failed: %s", exc)

    return sp.StrategyAssignResponse(
        success=True,
        strategie=name,
        applied=new,
        changes=changes,
        detail=f"strategy assignment for {req.konto} recorded on the WORM chain",
    )
