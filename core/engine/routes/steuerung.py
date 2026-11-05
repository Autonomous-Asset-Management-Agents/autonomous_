"""Steuerungs-Router (#4069, ARC-E6 G-4c).

HITL (``/api/hitl/pending``, ``/api/hitl/approve``, ``/api/hitl/reject``,
``/api/hitl/policy``), Handelseinstellungen (``/api/trading-settings``), Abweichungen vom
Default (``/api/settings-deviation-ack``), Portfolio-Form (``/api/portfolio-shape``) und
Strategie (``/strategy``, ``/set-strategy``, ``/api/strategy/swap``): die Stellschrauben,
die ein Mensch an der laufenden Engine dreht. Die Routen sind unveraendert aus
``api_routes.py`` umgezogen; die HTTP-Flaeche bleibt gleich
(``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und geteilte Helfer liest das Modul zur Laufzeit ueber ``ar``
(Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4069-g4c-steuerungs-router/implementation_plan.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

import core.strategies as strategies
from core.auth import require_engine_key, verify_user_id_sig
from core.contracts.abrechnung import SwapRequest
from core.contracts.einstellungen import (
    SettingsDeviationAckRequest,
    SettingsDeviationAckResponse,
    TradingSettingsRequest,
    TradingSettingsResponse,
)
from core.contracts.hitl import (
    HitlActionResponse,
    HitlApproveRequest,
    HitlPendingResponse,
    HitlPolicyDTO,
    HitlPolicyUpdateDTO,
    HitlQueueItemDTO,
    HitlRejectRequest,
)
from core.contracts.portfolio import PortfolioShapeRequest, PortfolioShapeResponse
from core.engine import api_routes as ar
from core.engine.fehler import CodedHTTPException
from core.engine.routes import ROUTER, verfahren_tor
from core.exceptions import SwapInProgressError

router = APIRouter()
ROUTER.append(router)


@router.get("/strategy")
async def get_strategy():
    return {"strategy": getattr(ar.config, "ACTIVE_STRATEGY", "RLAgent")}


def _aktive_strategie() -> str:
    """Der Ist-Wert von ``ACTIVE_STRATEGY`` als Text — ``RLAgent``, wenn keiner lesbar ist.

    Direkter Attributzugriff statt ``getattr(config, ..., default)``: die Ratsche in
    ``tests/architecture/vertrag.toml`` zaehlt jede solche Stelle. Der ``isinstance``-Test
    haelt eine gemockte Config (``MagicMock``) aus dem WORM-Text heraus (#4629).
    """
    wert = ar.config.ACTIVE_STRATEGY
    return wert if isinstance(wert, str) else "RLAgent"


@router.post("/set-strategy")
async def set_strategy(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    """Aktive Strategie umschalten — seit #4629 auditiert (audit-before-change).

    Der ``settings_change``-Eintrag (Key ``ACTIVE_STRATEGY``) steht auf der Kette, BEVOR
    umgeschaltet wird; scheitert er, antwortet die Route 503 und nichts wechselt. Die Nonce
    erzeugt der Server: der Client-Vertrag (``src/lib/api.ts::setStrategy``) traegt keine.
    Sie macht den Eintrag eindeutig, schuetzt aber nicht gegen eine Wiederholung durch den
    Client (Plan §6). Gleicher Name: kein Eintrag (Kettenrauschen-Regel).
    """
    from core import hitl_gate

    name = (p.get("strategy") or "").strip()
    if name not in strategies.STRATEGY_CLASSES:
        return {
            "status": "error",
            "message": f"Unknown strategy. Use one of: {list(strategies.STRATEGY_CLASSES.keys())}",  # noqa: E501
        }
    alt = _aktive_strategie()
    if name == alt:
        return {"status": "success", "strategy": name}
    nonce = str(uuid4())
    try:
        await hitl_gate.log_settings_change_event(
            changes=[{"key": "ACTIVE_STRATEGY", "from": alt, "to": name}],
            actor="operator",
            acknowledgment="Operator switched active strategy via /set-strategy",
            nonce=nonce,
            switch_id=nonce,
            strict=True,
        )
    except Exception as exc:  # noqa: BLE001 — ohne Eintrag kein Umschalten
        logging.warning(
            "[STRATEGIE] /set-strategy nicht auditiert, kein Wechsel: %s", exc
        )
        raise HTTPException(
            status_code=503, detail="audit write failed — strategy unchanged"
        ) from exc
    ar.config.ACTIVE_STRATEGY = name
    logging.info("Strategy mode set to: %s", name)
    try:
        with ar._tracer.start_as_current_span("strategy.set") as _span:
            _span.set_attribute("from", alt)
            _span.set_attribute("to", name)
            _span.set_attribute("nonce", nonce)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — Telemetrie bricht nie den auditierten Pfad
        ar.logger.warning("[STRATEGIE] strategy.set span failed: %s", exc)
    return {"status": "success", "strategy": name}


@router.post(
    "/api/strategy/swap",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def strategy_swap(
    req: SwapRequest,
    request: Request,
) -> dict:
    """Graceful Strategy-Swap via AgentRegistry.

    Epic 2.3 / I-3 (Issue #239): Position Lock + MiFID Audit Log

    Position Lock (HTTP 423):
        Wenn offene Alpaca-Positionen existieren und force=False (default),
        wird der Swap mit HTTP 423 abgelehnt um Compliance-Risiken zu vermeiden.  # noqa: E501
        force=True umgeht den Lock (shadow_mode=True empfohlen).

    MiFID Audit Log:
        Jeder erfolgreiche Swap wird in risk_events (Supabase) persistiert.
        Fehler beim Audit-Log blockieren den Swap NICHT (non-blocking).

    Raises:
        423 Locked:          Offene Positionen + force=False
        409 Conflict:        SwapInProgressError (anderer Swap ausstehend)
        422 Unprocessable:   Unbekannte strategy_name
    """
    # --- 1. Position Lock (HTTP 423) ---
    if ar.engine is not None and ar.engine.api and not req.force:
        try:
            positions = ar.engine.api.list_positions()
            if positions:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "error": "position_lock",
                        "message": (
                            f"Swap abgelehnt: {len(positions)} offene Position(en). "  # noqa: E501
                            "Nur bei leerem Portfolio erlaubt."
                        ),
                        "positions": [p.symbol for p in positions],
                        "hint": "Sende force=true um trotzdem zu swappen (shadow_mode=true empfohlen).",  # noqa: E501
                    },
                )
        except HTTPException:
            raise
        except Exception as pos_err:
            logging.warning(
                "Position Lock check failed — proceeding without lock: %s",
                pos_err,  # noqa: E501
            )

    # --- 2. Registry Swap ---
    registry = ar.get_global_registry()
    if registry is None and ar.engine is not None:
        registry = ar.engine.agent_registry
    try:
        result = registry.swap(req.strategy_name, shadow_mode=req.shadow_mode)
        if not result:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannte Strategy: {req.strategy_name!r}. "
                f"Registrierte Strategies: {list(registry._strategies.keys())}",  # noqa: E501
            )
    except SwapInProgressError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # --- 3. MiFID Audit Log (non-blocking) ---
    try:
        ar.get_cloud_logger().log_swap_event(
            strategy_name=req.strategy_name,
            shadow_mode=req.shadow_mode,
            forced=req.force,
        )
    except Exception as audit_err:
        logging.warning(
            "Hot-Swap Audit Log fehlgeschlagen (non-blocking): %s", audit_err
        )

    logging.info(
        "Hot-Swap initiiert: %s (shadow=%s, force=%s)",
        req.strategy_name,
        req.shadow_mode,
        req.force,
    )
    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the swap).  # noqa: E501
    ar.bump_usage("strategy_swaps")
    return {"success": True, "pending": req.strategy_name}


def _hitl_policy_dto() -> HitlPolicyDTO:
    from core import hitl_gate

    return HitlPolicyDTO(**hitl_gate.policy_snapshot())


@router.get("/api/hitl/pending", response_model=HitlPendingResponse)
async def hitl_pending(_: None = Depends(require_engine_key)):  # noqa: B008
    """Every order currently awaiting human approval."""
    from core.hitl_queue import HitlQueue

    pending = await HitlQueue.get_pending()
    items = [
        HitlQueueItemDTO(
            approval_id=p.get("approval_id", ""),
            user_id=p.get("user_id", ""),
            symbol=p.get("symbol", ""),
            action=p.get("action", ""),
            qty=float(p.get("qty", 0.0) or 0.0),
            price=float(p.get("price", 0.0) or 0.0),
            conviction=float(p.get("conviction", 0.0) or 0.0),
            target_weight=float(p.get("target_weight", 0.0) or 0.0),
            created_at=p.get("created_at", ""),
        )
        for p in pending
    ]
    return HitlPendingResponse(items=items)


@router.post("/api/hitl/approve", response_model=HitlActionResponse)
async def hitl_approve(
    body: HitlApproveRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Approve a pending order — it moves to the approved set and the trading-loop drain  # noqa: E501
    executes it (which audits ``approved`` on execution, PR-0a-ii-5)."""
    from core.hitl_queue import HitlQueue

    payload = await HitlQueue.approve(body.approval_id)
    if payload is None:
        return HitlActionResponse(
            success=False,
            approval_id=body.approval_id,
            detail="not found or expired",  # noqa: E501
        )
    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the approval).  # noqa: E501
    ar.bump_usage("hitl_approvals")
    return HitlActionResponse(
        success=True,
        approval_id=body.approval_id,
        detail="approved; queued for execution",
    )


@router.post("/api/hitl/reject", response_model=HitlActionResponse)
async def hitl_reject(
    body: HitlRejectRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Reject a pending order — removed from the queue and audited (never silently dropped)."""  # noqa: E501
    from core import hitl_gate
    from core.hitl_queue import HitlQueue
    from core.round_table.senate_log import HITLExecutionEvent

    pending = await HitlQueue.get_pending()
    item = next(
        (p for p in pending if p.get("approval_id") == body.approval_id), None
    )  # noqa: E501
    removed = await HitlQueue.reject(body.approval_id, body.reason or "")
    if removed:
        # Audit only a real rejection — not a phantom for an order that already
        # expired/was handled (which would write a misleading reason-less row).
        await hitl_gate.log_execution_event(
            HITLExecutionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=item.get("symbol", "") if item else "",
                action=item.get("action", "") if item else "",
                branch="rejected",
                policy_hash=hitl_gate.policy_hash(hitl_gate.policy_snapshot()),
                order_value=0.0,
                approval_id=body.approval_id,
                reason=body.reason or "human_rejected",
            )
        )
    return HitlActionResponse(
        success=bool(removed),
        approval_id=body.approval_id,
        detail="rejected" if removed else "not found or already gone",
    )


@router.post(
    "/api/portfolio-shape",
    response_model=PortfolioShapeResponse,
    status_code=201,
)  # noqa: E501
async def portfolio_shape(
    body: PortfolioShapeRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Record a deliberate portfolio-structure change on the WORM chain (#3132).

    **audit-before-change**, the same contract as ``/api/live/enable``: the record is
    written FIRST and ``strict=True`` re-raises a failed write, so this endpoint cannot
    report success for an unaudited change. The caller (the desktop shell) persists to
    ``setup.json`` only after a 201 — the engine does not own that file (``main.cjs``
    writes it, the engine reads it as env at boot, #2865). The remaining ordering risk is
    the safe one: a record without a change is harmless, a change without a record is not.

    Position count and holding period decide how capital is spread and how long it stays
    committed, which is why this is audited at all rather than being a plain setting write.
    """
    from core import hitl_gate
    from core.portfolio_shape import (
        clamp_portfolio_shape,
        current_shape,
        describe_changes,
    )

    current = current_shape()
    new = clamp_portfolio_shape(
        positions=(
            body.positions if body.positions is not None else current["positions"]
        ),
        hold_days=(
            body.hold_days if body.hold_days is not None else current["hold_days"]
        ),
    )
    changes = describe_changes(current=current, new=new)

    # Nothing to do: no WORM write. A chain full of non-changes is noise that buries the
    # real decisions — and the caller has nothing to persist either.
    if not changes:
        return PortfolioShapeResponse(
            success=True,
            applied=new,
            changes=[],
            detail="no change — nothing recorded",
        )

    # Same replay-distinct invariant as live enablement (LSR R7, #2255): reject a reused
    # nonce with 409 BEFORE the append, so a replay cannot write a duplicate record.
    await ar._enforce_replay_distinct_nonce("portfolio_shape", body.nonce)

    await hitl_gate.log_portfolio_shape_event(
        changes=changes,
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        switch_id=body.nonce,
        strict=True,
    )
    return PortfolioShapeResponse(
        success=True,
        applied=new,
        changes=changes,
        detail="portfolio-shape change recorded on the WORM chain",
    )


@router.get("/api/trading-settings")
async def trading_settings_get(_: None = Depends(require_engine_key)):  # noqa: B008
    """Effective trading-settings state for the console cards (#3155, UXC-1 S2).

    Everything the UI needs comes from HERE, never from UI state (epic guardrail 4):
    the full 11-agent roster with roles/role titles/bounds, the effective value of
    every registry key (read at call time — an env-injected deviation is visible),
    the shipped defaults + bounds ("Changed" badge, reset reference, S3 defaults
    comparison), and the read-only guardrails (visible, never editable — A6).
    Read path: deliberately un-instrumented (OBS-2 noise rule).
    """
    from core import trading_settings as ts

    view = ts.agents_view()
    return {
        "agents": view["agents"],
        "implied_vol_forecast_enabled": view["implied_vol_forecast_enabled"],
        "settings": ts.current_settings(),
        "meta": ts.settings_meta(),
        "guardrails": ts.guardrails_view(),
        "effective_positions": ts.effective_positions(),
    }


@router.post(
    "/api/trading-settings",
    response_model=TradingSettingsResponse,
    status_code=201,
)
async def trading_settings_post(
    body: TradingSettingsRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Record a deliberate trading-settings change on the WORM chain (#3155, UXC-1 S2).

    **audit-before-change**, the same contract as ``/api/portfolio-shape`` (#3133): the
    record is written FIRST and ``strict=True`` re-raises a failed write, so this
    endpoint cannot report success for an unaudited change. The caller (the desktop
    shell) persists to ``setup.json`` only after a 201 — the engine does not own that
    file (#2865); values take effect at the next engine boot (S3 restart flow). The
    remaining ordering risk is the safe one: a record without a change is harmless,
    a change without a record is not.
    """
    from core import hitl_gate
    from core import trading_settings as ts

    current = ts.current_settings()
    try:
        # #4654: the LLM-ranking fields are rejected, not clamped — a silently clamped
        # source/rhythm/prompt would record a strategy the operator never chose.
        ts.pruefe_llm_ranking(body.settings, current)
        new = ts.apply_updates(body.settings)
    except (ts.UnknownSettingError, ts.UngueltigeEinstellungError) as exc:
        raise _als_422(exc) from exc
    changes = ts.describe_changes(current, new)

    # Nothing to do: no WORM write. A chain full of non-changes is noise that buries
    # the real decisions — and the caller has nothing to persist either.
    if not changes:
        return TradingSettingsResponse(
            success=True,
            applied=new,
            changes=[],
            detail="no change — nothing recorded",
        )

    verfahren_tor.pruefe(changes, body.acknowledgment, body.nonce)  # #4989
    # Same replay-distinct invariant as live enablement (LSR R7, #2255): reject a
    # reused nonce with 409 BEFORE the append.
    await ar._enforce_replay_distinct_nonce("trading_settings", body.nonce)

    # #4654: the chain gets the prompt VERSION (sha256), the response keeps the plain
    # text the shell writes to setup.json — worm_changes is called only here.
    await hitl_gate.log_settings_change_event(
        changes=ts.worm_changes(changes),
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        switch_id=body.nonce,
        strict=True,
    )

    # OTel (epic guardrail 7 / INF-13): emit the LOCAL `settings.apply` span AFTER the
    # audited write — keys + nonce only, never old/new VALUES (single source = WORM;
    # egress stays opt-in default-off). Best-effort: must never fail the audited apply.
    try:
        with ar._tracer.start_as_current_span("settings.apply") as _span:
            _span.set_attribute("keys", ",".join(sorted(c["key"] for c in changes)))
            _span.set_attribute("nonce", body.nonce)
            _span.set_attribute("restart_required", "true")
            _span.set_attribute("actor", "operator")
    except (
        Exception
    ) as exc:  # noqa: BLE001 — telemetry must never break the audited path
        ar.logger.warning(
            "[SETTINGS] settings.apply span failed (apply unaffected): %s", exc
        )

    return TradingSettingsResponse(
        success=True,
        applied=new,
        changes=changes,
        detail="trading-settings change recorded on the WORM chain",
    )


@router.post(
    "/api/settings-deviation-ack",
    response_model=SettingsDeviationAckResponse,
    status_code=201,
)
async def settings_deviation_ack(
    body: SettingsDeviationAckRequest,
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """Record the operator's keep/reset decision about default deviations (#3156, S3).

    Called by the live consent BEFORE ``/api/live/enable`` (epic guardrail 6):
    ``keep`` seals exactly one ``SettingsDeviationAckEvent``; ``reset`` atomically
    appends the ack AND the regular old→new ``SettingsChangeEvent`` in THIS one
    request (plan Rev. 3 — a failure before both appends complete is a 5xx, the
    caller persists nothing and never restarts, so a stranded reset-ack without
    execution is ruled out). The caller writes setup.json only after the 201.
    ``switch_id`` joins the decision to the live switch and the boot it arms.
    """
    from core import hitl_gate

    if body.decision not in ("keep", "reset"):
        raise HTTPException(
            status_code=422, detail="decision must be 'keep' or 'reset'"
        )
    if not body.deviations:
        raise HTTPException(
            status_code=422,
            detail="no deviations — nothing to acknowledge (the consent must not call this)",
        )

    deviations = [
        {"key": d.key, "current": d.current, "default": d.default}
        for d in body.deviations
    ]

    # For a reset, validate the keys BEFORE any append — allowed are the S2 registry
    # keys plus the two legacy #3132 setup keys (deliberately OUTSIDE the S2
    # registry, Abgrenzung B8; the two vol-targeting keys joined the registry, #4447).
    # A guardrail key (e.g. HARD_STOP_LOSS_PCT) is neither and must never be
    # resettable through here. The old→new changes come verbatim from the reviewed deviations (str-only fields — the consent already computed
    # current vs. shipped default), so ONE SettingsChangeEvent covers the whole reset.
    _LEGACY_SETUP_KEYS = frozenset(
        (
            "FULL_UNIVERSE_MAX_POSITIONS",
            "SMART_EXIT_MIN_HOLD_DAYS",
        )
    )
    reset_changes: list = []
    if body.decision == "reset":
        from core import trading_settings as ts

        unknown = [
            d["key"]
            for d in deviations
            if d["key"] not in ts.REGISTRY and d["key"] not in _LEGACY_SETUP_KEYS
        ]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="Nicht über diesen Endpoint rücksetzbar: "
                + ", ".join(sorted(unknown)),
            )
        reset_changes = [
            {"key": d["key"], "from": d["current"], "to": d["default"]}
            for d in deviations
            if d["current"] != d["default"]
        ]

    # Replay-distinct (LSR R7, #2255): reject a reused nonce with 409 BEFORE the append.
    await ar._enforce_replay_distinct_nonce("settings_deviation_ack", body.nonce)

    switch_id = body.switch_id or body.nonce
    await hitl_gate.log_settings_deviation_ack(
        decision=body.decision,
        deviations=deviations,
        nonce=body.nonce,
        switch_id=switch_id,
        strict=True,
    )
    if body.decision == "reset" and reset_changes:
        # Rev. 3 composition: the second append happens in the SAME request; strict=True
        # re-raises, so a partial failure surfaces as 5xx and the client persists nothing.
        await hitl_gate.log_settings_change_event(
            changes=reset_changes,
            acknowledgment=(
                "Reset to shipped defaults chosen in the live-switch defaults review."
            ),
            nonce=body.nonce,
            switch_id=switch_id,
            strict=True,
        )

    # OTel (epic guardrail 7): the DECISION as an attribute — never values (WORM is
    # the single source). Best-effort, logged at WARNING on failure (POLICY-01).
    try:
        with ar._tracer.start_as_current_span("settings.deviation_ack") as _span:
            _span.set_attribute("decision", body.decision)
            _span.set_attribute("nonce", body.nonce)
            _span.set_attribute("switch_id", switch_id)
            _span.set_attribute("keys", ",".join(sorted(d["key"] for d in deviations)))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — telemetry must never break the audited path
        ar.logger.warning(
            "[SETTINGS] settings.deviation_ack span failed (record unaffected): %s", exc
        )

    return SettingsDeviationAckResponse(
        success=True,
        decision=body.decision,
        detail="deviation decision recorded on the WORM chain",
    )


@router.get("/api/hitl/policy", response_model=HitlPolicyDTO)
async def hitl_get_policy(_: None = Depends(require_engine_key)):  # noqa: B008
    """The current HITL autonomy policy (all six values; ``HITL_ENABLED`` read-only)."""  # noqa: E501
    return _hitl_policy_dto()


@router.post("/api/hitl/policy", response_model=HitlPolicyDTO)
async def hitl_set_policy(
    body: HitlPolicyUpdateDTO, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Update the runtime-adjustable HITL limits. Writes an immutable HITLPolicyEvent (old→new)  # noqa: E501
    BEFORE mutating the running policy. ``HITL_ENABLED`` is not accepted (422, env-only).  # noqa: E501
    """
    import config
    from core import hitl_gate

    old = hitl_gate.policy_snapshot()
    updates = body.model_dump()
    new = {**old, **updates}
    # A policy change is operator-initiated and off the hot path → the Art-14 audit is BLOCKING:  # noqa: E501
    # if it cannot be persisted, refuse the mutation (503) rather than change the autonomy limits  # noqa: E501
    # with no immutable record of who/what/when. (Execution audits stay best-effort, by contrast.)  # noqa: E501
    try:
        await hitl_gate.log_policy_event(old, new, actor="api", strict=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="policy-change audit failed; change refused",  # noqa: E501
        ) from exc
    # #1463: apply must never raise a bare 500. The OSS-desktop edition lacked
    # config.apply_hitl_policy_update entirely (now ported); guard the call so any  # noqa: E501
    # remaining edition/runtime failure surfaces as a clean 503 the UI can show.  # noqa: E501
    try:
        config.apply_hitl_policy_update(updates)
    except Exception as exc:
        logging.exception("hitl_set_policy: failed to apply the policy update")
        raise HTTPException(
            status_code=503, detail="failed to apply the policy update"
        ) from exc
    # INC-4 (#2213): persist the tuned limits durably so they SURVIVE the engine restart (the
    # live-enable flow restarts the engine). Best-effort + WARNING (§5.6) — a persistence failure
    # must not fail the already-applied runtime change, but the operator is told it won't stick.
    try:
        from core.hitl_policy_store import save_hitl_policy

        await save_hitl_policy(new)
    except (
        Exception
    ):  # noqa: BLE001 — persistence is best-effort; runtime change already applied
        logging.warning(
            "hitl_set_policy: could not persist the HITL policy — applied at runtime but it will "
            "reset to env defaults on the next engine restart.",
            exc_info=True,
        )
    return _hitl_policy_dto()


# ── #4629 (STW-3): benannte Strategien ─────────────────────────────────────────
# Reihenfolge wie bei /api/trading-settings: Pruefung (422) → Nonce (409) → WORM (strict,
# sonst 503) → strategien.json → Span. Ohne STRATEGY_PROFILES_ENABLED antworten alle drei
# Routen 404. Plan: docs/4629-*/implementation_plan.md §2 Punkt 4.


def _strategien_aktiv() -> None:
    # #4890: bewusst ohne ``code`` — die Route soll fehlen, nicht abgelehnt aussehen.
    if not ar.config.STRATEGY_PROFILES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _als_422(exc: Exception) -> CodedHTTPException:
    """#4890: ``UngueltigeEinstellungError`` / ``UnknownSettingError`` → kodierte 422."""
    return CodedHTTPException(422, str(exc), code=exc.code, params=exc.params)


def _anfrage_ungueltig(exc: ValueError) -> CodedHTTPException:
    """#4890: ``model_validate`` gescheitert — die Felder (``loc``) ohne ihre Werte."""
    errors = getattr(exc, "errors", None)
    felder = [list(e.get("loc", ())) for e in errors()] if callable(errors) else []
    namen = ", ".join(".".join(str(t) for t in f) for f in felder)
    return CodedHTTPException(
        422,
        "Invalid request body" + (f" (fields: {namen})" if namen else ""),
        code="request_invalid",
        params={"fields": felder},
    )


def _strategie_fehlt(name: str) -> CodedHTTPException:
    return CodedHTTPException(
        404,
        f"Strategy {name!r} not found",
        code="strategy_not_found",
        params={"name": name},
    )


def _strategien_laden() -> Dict[str, Dict[str, str]]:
    from core import strategie_profile as sp

    try:
        return sp.lade_strategien()
    except sp.StrategieAblageError as exc:
        raise CodedHTTPException(503, str(exc), code=exc.code) from exc


async def _strategie_versiegeln(
    *, aktion: str, name: str, werte: Dict[str, str], acknowledgment: str, nonce: str
) -> None:
    """WORM-Eintrag, dann Ablage, dann Span. Ein gescheiterter Eintrag ist 503."""
    from core import hitl_gate
    from core import strategie_profile as sp

    try:
        await hitl_gate.log_strategy_definition_event(
            aktion=aktion,
            name=name,
            werte=sp.werte_fuer_kette(werte),
            acknowledgment=acknowledgment,
            nonce=nonce,
            switch_id=nonce,
            strict=True,
        )
    except Exception as exc:  # noqa: BLE001 — ohne Eintrag keine Ablage
        ar.logger.warning("[STRATEGIE] %s %r nicht auditiert: %s", aktion, name, exc)
        raise CodedHTTPException(
            503, "audit write failed — nothing stored", code="audit_write_failed"
        ) from exc

    try:
        with ar._tracer.start_as_current_span("strategy.define") as _span:
            _span.set_attribute("aktion", aktion)
            _span.set_attribute("name", name)
            _span.set_attribute("nonce", nonce)
            _span.set_attribute("actor", "operator")
    except (
        Exception
    ) as exc:  # noqa: BLE001 — Telemetrie bricht nie den auditierten Pfad
        ar.logger.warning("[STRATEGIE] strategy.define span failed: %s", exc)


@router.get("/api/strategies")
async def strategies_list(_: None = Depends(require_engine_key)):  # noqa: B008
    """Die 0-3 Strategien in Anlagereihenfolge, ohne Rangfolge (ADR-027 Regel 2, #4863)."""
    from core import strategie_profile as sp

    _strategien_aktiv()
    bestand = _strategien_laden()
    return sp.StrategyListResponse(
        strategien=[{"name": n, "werte": bestand[n]} for n in bestand],
        max_strategien=sp.MAX_STRATEGIEN,
    )


@router.post("/api/strategies", status_code=201)
async def strategies_post(
    body: Dict, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Strategie anlegen oder aendern — versiegelt, bevor ``strategien.json`` sie enthaelt."""
    from core import strategie_profile as sp
    from core import trading_settings as ts

    _strategien_aktiv()
    try:
        req = sp.StrategyCreateRequest.model_validate(body)
    except ValueError as exc:
        raise _anfrage_ungueltig(exc) from exc
    async with sp.SPERRE:
        bestand = _strategien_laden()
        try:
            name = sp.pruefe_name(req.name)
            werte = sp.pruefe_strategie(name, req.werte)
            sp.pruefe_obergrenze(bestand, name)
        except (ts.UnknownSettingError, ts.UngueltigeEinstellungError) as exc:
            raise _als_422(exc) from exc
        aktion = "update" if name in bestand else "create"
        unveraendert = bestand.get(name) == werte
        if not unveraendert:
            await ar._enforce_replay_distinct_nonce("strategy_define", req.nonce)
            await _strategie_versiegeln(
                aktion=aktion,
                name=name,
                werte=werte,
                acknowledgment=req.acknowledgment,
                nonce=req.nonce,
            )
            sp.speichere_strategien({**bestand, name: werte})
    return sp.StrategyResponse(
        success=True,
        aktion=aktion,
        name=name,
        werte=werte,
        detail=(
            "no change — nothing recorded"
            if unveraendert
            else "strategy recorded on the WORM chain"
        ),
        liste_verworfen=sp.liste_verworfen(werte),
    )


@router.delete("/api/strategies/{name}")
async def strategies_delete(
    name: str, body: Dict, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Strategie loeschen — der Eintrag nennt den geloeschten Satz.

    #4891: zugewiesen → 409 ``strategy_assigned``; Spiegel unlesbar → 503 (fail-closed).
    """
    from core import strategie_profile as sp
    from core import trading_settings as ts
    from core.engine.routes import strategie_zuweisung as sz

    _strategien_aktiv()
    try:
        req = sp.StrategyDeleteRequest.model_validate(body)
    except ValueError as exc:
        raise _anfrage_ungueltig(exc) from exc
    try:
        name = sp.pruefe_name(name)
    except ts.UngueltigeEinstellungError as exc:
        raise _als_422(exc) from exc
    async with sp.SPERRE:
        bestand = _strategien_laden()
        if name not in bestand:
            raise _strategie_fehlt(name)
        werte = bestand[name]
        sz.loeschsperre(name)  # #4891: vor Nonce und WORM — 409/503 verbraucht nichts
        await ar._enforce_replay_distinct_nonce("strategy_delete", req.nonce)
        await _strategie_versiegeln(
            aktion="delete",
            name=name,
            werte=werte,
            acknowledgment=req.acknowledgment,
            nonce=req.nonce,
        )
        sp.speichere_strategien({n: w for n, w in bestand.items() if n != name})
    return sp.StrategyResponse(
        success=True,
        aktion="delete",
        name=name,
        werte=werte,
        detail="strategy deletion recorded on the WORM chain",
    )


# ── #4630 (STW-4): Live-Zuweisung einer Strategie ──────────────────────────────
# Reihenfolge: Strategie (404) → Ziel-Satz → Pruefung (422) → Diff NACH dem Cross-Clamping →
# HITL (409) → Verfahrenswechsel (409, #4989) → Nonce (409) → WORM (strict, sonst 503) → Spiegel ``zuweisungen.json``
# (#4891; Fehler = WARNING, Antwort bleibt 201) → Span. Die Route schreibt weder
# setup.json noch Engine-Werte: das tut die Shell nach der 201, wirksam wird es beim
# Neustart (wie /api/trading-settings). Plan: docs/4630-*/implementation_plan.md §2 Punkt 3.


def _zuweisungs_ziel(werte: Dict[str, str]) -> Dict[str, str]:
    """Lieferzustand aller Registry-Keys, darueber die Werte der Strategie (Plan §6)."""
    from core import trading_settings as ts

    return {s.key: ts.canonical_text(s.key, s.default) for s in ts._SETTINGS} | werte


@router.post("/api/strategies/{name}/assign-live", status_code=201)
async def strategies_assign_live(
    name: str, body: Dict, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Strategie dem Live-Konto zuweisen — versiegelt, bevor die Shell sie wirksam macht.

    Der Eintrag entsteht auch bei leerem Diff: die Zuweisung selbst ist der Revisionsakt.
    Ohne ``HITL_ENABLED`` keine Live-Zuweisung (409, kein Eintrag).
    """
    from core import hitl_gate
    from core import strategie_profile as sp
    from core import trading_settings as ts
    from core.engine.routes import strategie_zuweisung as sz

    _strategien_aktiv()
    try:
        req = sp.StrategyAssignRequest.model_validate(body)
    except ValueError as exc:
        raise _anfrage_ungueltig(exc) from exc
    async with sp.SPERRE:
        bestand = _strategien_laden()
        name = name.strip()
        if name not in bestand:
            raise _strategie_fehlt(name)
        werte = bestand[name]

        current = ts.current_settings()
        ziel = _zuweisungs_ziel(werte)
        try:
            ts.pruefe_llm_ranking(ziel, current)
            new = ts.apply_updates(ziel)
        except (ts.UnknownSettingError, ts.UngueltigeEinstellungError) as exc:
            raise _als_422(exc) from exc
        # Nach apply_updates: jede Cross-Clamping-Anpassung steht so im Diff, wie sie wirkt.
        changes = ts.describe_changes(current, new)

        # Direkter Attributzugriff (Ratsche tests/architecture/vertrag.toml); ``is not True``
        # haelt auch eine gemockte Config aus der Live-Zuweisung heraus.
        if ar.config.HITL_ENABLED is not True:
            raise CodedHTTPException(
                409,
                "live assignment requires HITL_ENABLED — nothing recorded",
                code="hitl_required",
            )
        verfahren_tor.pruefe(changes, req.acknowledgment, req.nonce)  # #4989
        await ar._enforce_replay_distinct_nonce("strategy_assign", req.nonce)

        try:
            zeitpunkt = await hitl_gate.log_strategy_assignment_event(
                strategie=name,
                strategie_hash=sp.strategie_hash(werte),
                changes=ts.worm_changes(changes),
                acknowledgment=req.acknowledgment,
                nonce=req.nonce,
                switch_id=req.nonce,
                strict=True,
            )
        except Exception as exc:  # noqa: BLE001 — ohne Eintrag keine Zuweisung
            ar.logger.warning("[STRATEGIE] Zuweisung %r nicht auditiert: %s", name, exc)
            raise CodedHTTPException(
                503, "audit write failed — nothing assigned", code="audit_write_failed"
            ) from exc
        # #4891: Spiegel erst nach dem Eintrag; scheitert er, bleibt die Zuweisung versiegelt.
        spiegel_ok = sz.zuweisung_spiegeln(name, sp.strategie_hash(werte), zeitpunkt)

    try:
        with ar._tracer.start_as_current_span("strategy.assign") as _span:
            _span.set_attribute("strategie", name)
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
        detail="strategy assignment recorded on the WORM chain",
    )


# ── #4863 (STW-3b): Versionsliste je Strategie ─────────────────────────────────
# Rein lesend: Flag (404) → Name (422) → Bestand (404) → Kette im Thread (503 wenn nicht
# lesbar). Am Ende der Datei, damit die Zeilenanker der Docs nicht wandern.
# Plan: docs/4863-*/implementation_plan.md §2 Punkt 2.


@router.get("/api/strategies/{name}/versions")
async def strategies_versions(
    name: str, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Versionen einer Strategie aus den ``StrategyDefinitionEvent`` der Kette, neueste zuerst.

    Das Verzeichnis kommt aus demselben Logger, der die Kette schreibt — wie
    ``hitl_gate.collect_live_enablement_nonces``. Nicht lesbar → 503, nie eine still leere Liste.
    """
    import asyncio
    import os
    from pathlib import Path

    from core import hitl_gate
    from core import strategie_profile as sp
    from core import trading_settings as ts

    _strategien_aktiv()
    try:
        name = sp.pruefe_name(name)
    except ts.UngueltigeEinstellungError as exc:
        raise _als_422(exc) from exc
    if name not in _strategien_laden():
        raise _strategie_fehlt(name)

    log_dir = getattr(hitl_gate._resolve_audit_logger(), "_log_dir", None)
    if log_dir is None:
        log_dir = Path(os.getenv("SENATE_LOG_DIR", "oss_audit_logs"))
    try:
        versionen = await asyncio.to_thread(sp.lies_versionen, Path(log_dir), name)
    except OSError as exc:
        ar.logger.warning("[STRATEGIE] Versionen %r: Kette nicht lesbar: %s", name, exc)
        raise CodedHTTPException(
            503, "audit chain not readable", code="audit_chain_unreadable"
        ) from exc
    return sp.StrategyVersionsResponse(name=name, versionen=versionen)


# ── #4891 (STW-4d): Lesepfad der Zuweisung und Loeschsperre ────────────────────
# Liest den Spiegel ``zuweisungen.json``, nie die Kette. Helfer, Spiegel und Loeschsperre:
# ``routes/strategie_zuweisung.py``. Plan: docs/4891-*/implementation_plan.md §2 Punkt 3.


@router.get("/api/strategy-assignments")
async def strategies_assignments(_: None = Depends(require_engine_key)):  # noqa: B008
    """Die zuletzt versiegelte Zuweisung je Konto, nach Konto-Schluessel (ADR-027).

    Eigener Pfad statt ``/api/strategies/assignments``: kollidiert nie mit
    ``/api/strategies/{name}``. Kein Kettenzugriff; unlesbarer Spiegel → 503.
    """
    from core import strategie_profile as sp
    from core.engine.routes import strategie_zuweisung as sz

    _strategien_aktiv()
    zuweisungen = sz.zuweisungen_laden()
    return sp.StrategyAssignmentsResponse(
        zuweisungen=[
            sp.StrategyAssignment(konto=konto, **zuweisungen[konto])
            for konto in sorted(zuweisungen)
        ]
    )
