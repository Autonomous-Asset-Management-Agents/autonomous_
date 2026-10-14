"""Kapitalpfad-Router (#4068, ARC-E6 G-4b).

Scharfschalten (``/api/live/enable``, ``/api/live/disable``, ``/start-live``, ``/stop``),
Abgleich-Sperre (``/api/reconciliation/block``, ``/api/reconciliation/release``),
Stornos (``/cancel-order``), Panik-Verkauf (``/panic-sell``) und Kill-Switch
(``/reset-kill-switch``). Eine eigene Datei, damit CODEOWNERS den Kapitalpfad eigens
nennen kann. Die Routen sind unveraendert aus ``api_routes.py`` umgezogen; die
HTTP-Flaeche bleibt gleich (``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und geteilte Helfer liest das Modul zur Laufzeit ueber ``ar``
(Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4068-g4b-kapitalpfad-router/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Optional

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from core.auth import require_engine_key, verify_user_id_sig
from core.contracts.abgleich import ReconciliationReleaseRequest
from core.contracts.live import LiveEnableRequest, LiveEnableResponse
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.governance.iron_dome_policy import apply_policy

router = APIRouter()
ROUTER.append(router)


@router.post("/start-live")
async def start_live(_: None = Depends(require_engine_key)):  # noqa: B008
    # Startup-race guard (P1): `engine` is None until the fire-and-forget lifespan init
    # task (create_task(_init_engine_async)) finishes — the documented "None means still
    # starting up" contract (see engine=None declaration above). Pressing "Start" during
    # that window used to dereference None → AttributeError → HTTP 500 → the desktop UI
    # showed "failed to fetch" / "system doesn't start". Degrade gracefully with the SAME
    # {"status": "error", "message": ...} shape the failure path below already returns.
    if ar.engine is None:
        return {
            "status": "error",
            "message": "Engine is still starting up — please wait a few seconds and try again.",  # noqa: E501
        }
    # PR-4 INC-2a: start_live_strategy() is SYNCHRONOUS and does blocking network I/O
    # (send_slack_alert, api.get_account, optional S&P-500 scrape, thread teardown). Called
    # inline on the uvicorn event loop it starves every other handler — including /health —
    # for its full duration (~36s observed), which can co-trigger the desktop health-probe
    # restart storm. Offload it so the loop stays responsive. It already spawns its own daemon
    # threads and posts UI updates via run_coroutine_threadsafe(main_loop), so running it off
    # the loop changes no ordering it relied on.
    result = await asyncio.to_thread(ar.engine.start_live_strategy)
    if result is False:
        return {
            "status": "error",
            "message": "Engine failed to start. Check Alpaca API connection and secrets.",  # noqa: E501
        }
    # ADR-SEC-06 §1 (#1619): apply the persisted policy to the freshly-created guardians so an  # noqa: E501
    # admin's runtime change SURVIVES a restart (otherwise they reset to config defaults).  # noqa: E501
    try:
        stored = await ar._load_iron_dome_policy_value()
        apply_policy(
            stored,
            [
                getattr(ar.engine, "compliance_guardian", None),
                getattr(ar.engine, "live_risk_manager", None),
                getattr(ar.engine, "sim_risk_manager", None),
            ],
        )
    except (
        Exception
    ) as exc:  # boot resilience — never block live start on a policy reload
        logging.warning("Iron Dome boot-load failed (non-fatal): %s", exc)
    return {"status": "success", "message": "Live strategy started."}


@router.post("/stop")
async def stop(_: None = Depends(require_engine_key)):  # noqa: B008
    # NOTE: do NOT write a live_enablement WORM record here. Stopping the strategy is not a
    # change of live-trading authorization; the desktop boot gate (verifyAuditChain) treats
    # the latest live_enablement with action!="enable" as a REVOCATION, so emitting one on a
    # routine stop silently demotes a live operator to paper on the next boot. Operator halts
    # are already audited via core/kill_switch.py (kill_switch_audit.log). (#1983 regression fix)
    # Startup-race guard (P1): same "None means still starting up" contract as /start-live —
    # a /stop during the init window must not dereference None → AttributeError → HTTP 500.
    if ar.engine is None:
        return {
            "status": "error",
            "message": "Engine is still starting up — nothing to stop yet.",
        }
    ar.engine.stop_strategy()
    return {"status": "success"}


@router.post("/panic-sell")
async def panic_sell(_: None = Depends(require_engine_key)):  # noqa: B008
    # RTS 6 Art. 5 (LIVE-1 T3, #1426): HALT all algorithms FIRST — before and independent of the  # noqa: E501
    # broker liquidation below — so the trading loop / order_executor (check_halt, order_executor.py  # noqa: E501
    # :654) cannot place or re-enter an order during/after the emergency. The halt persists until an  # noqa: E501
    # explicit /reset-kill-switch, even if the broker is unreachable.
    from core.kill_switch import kill_switch

    kill_switch.trip("panic-sell: operator emergency halt (RTS 6 Art. 5)")
    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the halt).  # noqa: E501
    ar.bump_usage("panic_sells")
    try:
        if ar.engine.api:
            ar.engine.api.cancel_orders()
            logging.warning("🚨 PANIC SELL: Cancelled all open orders")
            positions = ar.engine.api.get_all_positions()

            # #3383: ging bisher direkt an den Broker — der Pfad, der den GESAMTEN
            # Bestand liquidiert, hinterliess keinen einzigen Compliance-Datensatz.
            # Jetzt je Position ein OrderIntent mit Grund-Code `emergency` durch das
            # Tor. Die Freistellung ist notwendig: `kill_switch.trip` oben hat den
            # Halt gerade selbst gesetzt — ohne sie haette der Notverkauf sich
            # ausgesperrt.
            from core.engine.order_executor import gateway_for
            from core.gateway import liquidate_positions

            decision_id = f"panic-{uuid.uuid4()}"

            def _panic_request(symbol: str, qty: float):
                # Ganze Stuecke GTC wie bisher; ein bruchteiliger Rest geht als DAY
                # heraus, statt wie frueher mit einer WARNING liegenzubleiben.
                whole = int(qty)
                if whole >= 1:
                    return MarketOrderRequest(
                        symbol=symbol,
                        qty=whole,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC,
                    )
                return MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )

            bericht = liquidate_positions(
                gateway_for(ar.engine.api),
                positions,
                decision_id=decision_id,
                intent_kind="panic",
                request_factory=_panic_request,
                halted=True,
            )

            pdt_blocked = sum(
                1
                for _s, grund in bericht.failed
                if "pattern day trading" in grund.lower()
            )
            msg = f"{bericht.submitted} positions sold"
            if pdt_blocked > 0:
                msg += f", {pdt_blocked} blocked by PDT"
            if not bericht.complete:
                msg += f", {len(bericht.failed)} NOT closed"
            return {
                "status": "success",
                "message": msg,
                "decision_id": decision_id,
                "complete": bericht.complete,
            }
        else:
            return {"status": "error", "message": "No API connection"}
    except Exception as e:
        logging.error("panic_sell failed: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


@router.post("/reset-kill-switch")
async def reset_kill_switch(_: None = Depends(require_engine_key)):  # noqa: B008
    """Reset the global Kill Switch (clears Redis key + local state).

    Use this after a transient network issue has resolved and you want to
    resume live trading.  The engine will NOT auto-start — call /start-live
    afterwards to resume the trading loop.
    """
    from core.kill_switch import kill_switch

    try:
        # Capture the trip reason BEFORE reset clears it, so the response can name what  # noqa: E501
        # tripped even after the state is gone.
        last = kill_switch.last_trip()
        was_halted = kill_switch.is_halted()
        kill_switch.reset()
        # PR F: anonymous operator-action counter (additive, fail-safe — never alters the reset).  # noqa: E501
        ar.bump_usage("kill_switch_resets")
        # NOTE: do NOT write a live_enablement=disable WORM record on reset. A reset CLEARS the
        # halt to RESUME trading — the opposite of revoking live authorization. verifyAuditChain
        # would read the trailing disable as a revocation and demote the operator to paper on the
        # next boot. The reset is already durably audited in kill_switch_audit.log. (#1983 fix)
        # RE-CHECK after reset: if the underlying condition re-trips instantly (the  # noqa: E501
        # operator's exact pain point — a reset that "doesn't stick"), surface it so the  # noqa: E501
        # response self-explains instead of silently reporting success.
        still_halted = kill_switch.is_halted()
        retrip = kill_switch.last_trip()
        retrip_reason = (retrip or {}).get("reason") if still_halted else None
        logging.info(
            "🔓 Kill Switch has been RESET via API. Was halted: %s", was_halted
        )
        message = "Kill switch reset. Call /start-live to resume trading."
        if still_halted:
            message = (
                "Reset ran but the kill switch RE-TRIPPED immediately: "
                f"{retrip_reason}. Fix the underlying condition first."
            )
        return {
            "status": "success",
            "message": message,
            "was_halted": was_halted,
            "last_trip_reason": (last or {}).get("reason"),
            "still_halted": still_halted,
            "retrip_reason": retrip_reason,
        }
    except Exception as e:
        logging.error("Failed to reset kill switch: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


def _abgleich_dienst():
    return getattr(ar.engine, "reconciler", None) if ar.engine is not None else None


def _sperr_bericht(dienst) -> dict:
    if dienst is None:
        return {
            "available": False,
            "entries_blocked": False,
            "block_on_break": False,
            "run_id": None,
            "breaks": [],
            "last_run_id": None,
            "last_run_clean": None,
        }
    befund = dienst.block_record if dienst.entries_blocked else None
    letzter = dienst.last_record
    return {
        "available": True,
        "entries_blocked": bool(dienst.entries_blocked),
        "block_on_break": bool(dienst.block_on_break),
        "run_id": befund.run_id if befund else None,
        "finished_at": befund.finished_at.isoformat() if befund else None,
        "breaks": [b.model_dump() for b in befund.breaks] if befund else [],
        "last_run_id": letzter.run_id if letzter else None,
        "last_run_clean": letzter.clean if letzter else None,
    }


@router.get(
    "/api/reconciliation/block",
    dependencies=[Depends(require_engine_key)],
)
async def reconciliation_block():
    """Die Sperre mit dem Befund, der sie haelt — beide Seiten jeder Abweichung."""
    return _sperr_bericht(_abgleich_dienst())


@router.post(
    "/api/reconciliation/release",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def reconciliation_release(
    p: Optional[ReconciliationReleaseRequest] = None,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),  # noqa: B008
):
    """Hebt die Abgleich-Sperre auf. Nur ein Mensch tut das (#3389).

    Der Urheber kommt aus dem Auth-Kontext: die signierte ``X-User-Id``, wenn die Signatur
    geprueft wird — sonst ``LOKALER_BEDIENER``. Ein Grund darf mitkommen, ersetzt aber nie
    den Urheber. Laesst sich der Protokolleintrag nicht schreiben, bleibt die Sperre.
    """
    from core.auth import resolve_require_sig
    from core.reconciliation_audit import festhalten

    dienst = _abgleich_dienst()
    if dienst is None:
        raise HTTPException(status_code=503, detail="Kein Abgleich aktiv.")

    urheber = (
        x_user_id
        if (x_user_id and resolve_require_sig(os.environ))
        else ar.LOKALER_BEDIENER
    )
    grund = (p.reason if p else "").strip()
    befund = dienst.block_record
    vorher = _sperr_bericht(dienst)
    try:
        # Dateizugriff im Thread: die Ereignisschleife bleibt frei (Review #3481).
        await asyncio.to_thread(
            festhalten,
            "release",
            by=urheber,
            reason=grund,
            was_blocked=vorher["entries_blocked"],
            run_id=vorher["run_id"],
            breaks=vorher["breaks"],
        )
    except Exception as exc:  # noqa: BLE001 — ohne Eintrag keine Aufhebung
        logging.exception(
            "Abgleich-Sperre NICHT aufgehoben: Protokoll nicht schreibbar."
        )
        raise HTTPException(
            status_code=500,
            detail="Aufhebung nicht protokollierbar — die Sperre bleibt bestehen.",
        ) from exc
    # Waehrend des Schreibens kann ein Abgleichlauf einen neuen Befund gesetzt haben. Frei
    # gegeben wird nur, was protokolliert wurde — sonst hoebe die Aufhebung eine Abweichung
    # auf, die niemand gesehen hat.
    if dienst.block_record is not befund:
        raise HTTPException(
            status_code=409,
            detail="Der Befund hat sich geaendert — bitte neu ansehen. Die Sperre bleibt.",
        )
    dienst.release_block(by=urheber)
    return {"status": "released", "by": urheber, **_sperr_bericht(dienst)}


@router.post(
    "/cancel-order",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def cancel_order(request: Request):
    """HITL cancel of a single OPEN order (#2137). This is an EU AI Act Art. 14 human-oversight
    control: it is ALWAYS available to the operator, independent of any autonomous mode — the human
    must be able to pull a working order at any time. Tenant-resolved like /open-orders; fail-soft
    (never 500). The intervention is sealed onto the tamper-evident Art-14 WORM audit chain (via
    ``hitl_gate.log_manual_cancel_event``) after the broker confirms the cancel.
    """
    try:
        body = await request.json()
        order_id = str((body or {}).get("order_id") or "").strip()
        if not order_id:
            return {"status": "error", "message": "missing_order_id"}
        api_client = await ar._resolve_orders_client(request)

        import asyncio

        await asyncio.to_thread(api_client.cancel_order_by_id, order_id)
        # Art-14 evidence: seal the manual intervention onto the tamper-evident WORM hash chain
        # after the broker confirms the cancel. Best-effort (never blocks this safety action).
        await ar.hitl_gate.log_manual_cancel_event(order_id=order_id, actor="operator")
        logging.warning(
            "HITL: operator cancelled open order %s (EU AI Act Art. 14 oversight).",
            order_id,
        )
        return {"status": "success", "order_id": order_id}
    except Exception as e:
        logging.error("cancel-order failed: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


@router.post(
    "/api/live/enable", response_model=LiveEnableResponse, status_code=201
)  # noqa: E501
async def live_enable(
    body: LiveEnableRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Record a deliberate live-trading enablement on the tamper-evident WORM chain BEFORE the  # noqa: E501
    engine is permitted to boot live (T1 reads it via ``verifyAuditChain``). audit-before-enable:  # noqa: E501
    a strict WORM-write failure raises → HTTP 500 with no false success."""
    from core import hitl_gate

    # GTM-1 (#1800) Brick-4 defense-in-depth: refuse to arm live for a tier that disallows
    # it (fail-closed), independent of the startup LiveGuard. No-op on Cloud/Dev/CI where
    # resolve_entitlement() returns the full bundle (allow_live=True).
    from core.entitlement import resolve_entitlement

    if not resolve_entitlement().allow_live:
        raise HTTPException(
            status_code=403,
            detail="[Entitlement] tier does not allow live trading (BASIC = paper only).",
        )

    # GTM-1 (#1801) BaFin risk-disclaimer gate: the operator must have accepted the CURRENT
    # risk-disclaimer before arming live (fail-closed, LOCAL-only — cloud/enterprise BYOC handles
    # its own compliance and is byte-identical). Placed consistently with the #1800 tier check
    # above and BEFORE the WORM write, so an unaccepted/outdated disclaimer never gets recorded as
    # a live-enablement. Placeholder text/version now; the real BaFin wording is #1804.
    from core.disclaimer import assert_disclaimer_accepted

    try:
        assert_disclaimer_accepted()
    except (
        Exception
    ) as exc:  # noqa: BLE001 — fail-closed: any gate failure blocks arming
        raise HTTPException(
            status_code=403,
            detail=(
                "[Disclaimer] BaFin risk-disclaimer not accepted / re-acceptance required after "
                "update — accept the current risk-disclaimer before arming live trading."
            ),
        ) from exc

    # LSR R7 (#2255): enforce the documented "replay-distinct" nonce — reject a nonce already on
    # the WORM chain with 409 BEFORE the append, so a replay cannot write a duplicate record.
    await ar._enforce_replay_distinct_nonce("enable", body.nonce)

    # #2277 INC-1: the nonce IS the switch_id — the correlation id that the UI also threads onto the
    # engine restart (AAA_SWITCH_ID) and every boot/degrade/revoke span. Emit a domain `live.enable`
    # span carrying it so the diagnose-export can join the authoritative enable action to the boot it
    # armed. Best-effort: the span must never fail the audited enable.
    with ar._tracer.start_as_current_span("live.enable") as _span:
        _span.set_attribute("switch_id", body.nonce)
        _span.set_attribute("action", "enable")

    await hitl_gate.log_live_enablement_event(
        action="enable",
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        switch_id=body.nonce,
        strict=True,
    )
    return LiveEnableResponse(
        success=True,
        action="enable",
        detail="live-enablement recorded on the WORM chain",
    )


@router.post(
    "/api/live/disable", response_model=LiveEnableResponse, status_code=201
)  # noqa: E501
async def live_disable(
    body: LiveEnableRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Revoke live trading — a ``disable`` event on the same WORM chain. ``verifyAuditChain`` treats  # noqa: E501
    a later disable as revoking an earlier enable, so the engine returns to fail-closed paper.  # noqa: E501

    LSR R1 (#2252, audit #02): ``disable`` is an IMMEDIATE, in-process halt — not just a WORM note
    that only bites on the next restart. ``force_paper_trading()`` alone does NOT redirect the
    boot-built live broker (``TradingClient(..., paper=is_paper)`` @ :145-153; the submit never
    re-reads ``config.PAPER_TRADING`` @ ``order_executor.py:967``), so we reuse the EXISTING
    kill-switch — no new interrupt. Order matters: halt first (blocks new submits synchronously +
    mass-cancels in-flight), then force paper, then the WORM write. **Semantics:** ``disable`` is a
    FULL halt (stops paper too, exits the loop via ``trading_loop.py:365``); resume is ONLY via an
    engine restart (the boot-live client stays live-bound otherwise). RTS-6/Art.5-conform.
    """
    from core import hitl_gate
    from core.kill_switch import kill_switch

    # LSR R7 (#2255): /disable shares the replay-distinct nonce contract — reject a nonce already
    # on the WORM chain (from any enable/disable) with 409 BEFORE the append.
    await ar._enforce_replay_distinct_nonce("disable", body.nonce)

    _reason = "live trading disabled by operator"
    _actor = "operator"

    # 1) Trip the existing kill-switch: _local_halted=True synchronously (every subsequent
    #    check_halt raises → no new submit) AND async_mass_cancel (aborts resting/in-flight
    #    orders). This is the load-bearing stop; force_paper alone cannot redirect self.api.
    with ar._tracer.start_as_current_span("live_disable.kill_switch_tripped") as _span:
        _span.set_attribute("reason", _reason)
        _span.set_attribute("actor", _actor)
        # #2277 INC-1: nonce == switch_id — join this disable action to the restart it triggered.
        _span.set_attribute("switch_id", body.nonce)
        # #2467 FIX 4b: fail_closed=False — this endpoint force-papers (step 2) + writes the WORM
        # disable (step 3) itself, so trip() must NOT duplicate them.
        kill_switch.trip(reason=_reason, fail_closed=False)

    # 2) Belt-and-suspenders: future / next-cycle clients select the paper account.
    with ar._tracer.start_as_current_span("live_disable.forced_paper") as _span:
        _span.set_attribute("reason", _reason)
        _span.set_attribute("switch_id", body.nonce)
        ar.config.force_paper_trading(reason=_reason)

    # 3) The existing WORM disable record (Art-14 audit + restart fail-closed). strict=True →
    #    a failed WORM write re-raises (no false success); the halt above has already landed.
    await hitl_gate.log_live_enablement_event(
        action="disable",
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        switch_id=body.nonce,
        strict=True,
    )
    return LiveEnableResponse(
        success=True,
        action="disable",
        detail="live-disable: kill-switch tripped + forced paper + WORM disable recorded",  # noqa: E501
    )
