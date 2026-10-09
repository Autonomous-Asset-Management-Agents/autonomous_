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
from core.engine.routes import ROUTER
from core.exceptions import SwapInProgressError

router = APIRouter()
ROUTER.append(router)


@router.get("/strategy")
async def get_strategy():
    return {"strategy": getattr(ar.config, "ACTIVE_STRATEGY", "RLAgent")}


@router.post("/set-strategy")
async def set_strategy(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    name = (p.get("strategy") or "").strip()
    if name not in strategies.STRATEGY_CLASSES:
        return {
            "status": "error",
            "message": f"Unknown strategy. Use one of: {list(strategies.STRATEGY_CLASSES.keys())}",  # noqa: E501
        }
    ar.config.ACTIVE_STRATEGY = name
    logging.info("Strategy mode set to: %s", name)
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
        vol_target=(
            body.vol_target if body.vol_target is not None else current["vol_target"]
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
        new = ts.apply_updates(body.settings)
    except ts.UnknownSettingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    # Same replay-distinct invariant as live enablement (LSR R7, #2255): reject a
    # reused nonce with 409 BEFORE the append.
    await ar._enforce_replay_distinct_nonce("trading_settings", body.nonce)

    await hitl_gate.log_settings_change_event(
        changes=changes,
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
    # keys plus the four legacy #2863/#3132 setup keys (deliberately OUTSIDE the S2
    # registry, Abgrenzung B8). A guardrail key (e.g. HARD_STOP_LOSS_PCT) is neither
    # and must never be resettable through here. The old→new changes come verbatim
    # from the reviewed deviations (str-only fields — the consent already computed
    # current vs. shipped default), so ONE SettingsChangeEvent covers the whole reset.
    _LEGACY_SETUP_KEYS = frozenset(
        (
            "VOL_TARGETING_SIZING_ENABLED",
            "VOL_TARGET_DAILY_VOL",
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
