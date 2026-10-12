"""Admin- und Entitlement-Router (#4071, ARC-E6 G-4e).

Staging-Gate (``/staging-gate``), Entitlement (``/api/entitlement/*``: Checkout, Webhooks,
Lizenz, Aktivierung, Status), Iron-Dome-Policy und Portfolio-Grenzen (``/api/admin/*``, beide
mit Vier-Augen-Pfad) und die Telemetrie des Abgleichs (``/api/telemetry/switch-reconcile``):
alles, was an Admin- oder Lizenzrechten haengt. Die Routen und ihre Helfer sind unveraendert
aus ``api_routes.py`` umgezogen; die HTTP-Flaeche bleibt gleich
(``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und ueber ``api_routes`` gepatchte Namen (``engine``, ``config``,
``RedisClient``, ``_load_iron_dome_policy_value``, ``record_iron_dome_policy_change``,
``log_policy_event``, ``create_checkout_session``, ``_tracer``) liest das Modul zur Laufzeit
ueber ``ar`` (Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4071-g4e-admin-router/implementation_plan.md``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from core.auth import require_engine_key, verify_user_id_sig
from core.contracts.abgleich import SwitchReconcileRequest
from core.contracts.abrechnung import ActivateRequest, CheckoutRequest
from core.contracts.portfolio import (
    PortfolioConstraintsApproveRequest,
    PortfolioConstraintsProposeRequest,
)
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.entitlement.tier import Tier
from core.governance.four_eyes import (
    add_approval,
    four_eyes_required,
    is_loosening,
    is_ready_to_apply,
)
from core.governance.iron_dome_admin_auth import require_iron_dome_admin
from core.governance.iron_dome_policy import CONFIG_KEY, load_policy

router = APIRouter()
ROUTER.append(router)


# --- INF-8: Staging Quality Gate ---


@router.get(
    "/staging-gate",
)
async def staging_gate():
    """INF-8: Deterministic staging health gate.

    Used by deploy-backend.yml smoke test to verify correct staging deployment.
    No auth required — internal staging use only (not proxied by aaa-api-public).  # noqa: E501

    Returns 503 if:
    - STAGING_ENV=true but SHADOW_MODE is not True (misconfiguration)
    - Redis is unreachable

    Returns 200 with gate status if all checks pass.
    """
    staging_env = getattr(ar.config, "STAGING_ENV", False)
    shadow_mode = getattr(ar.config, "SHADOW_MODE", False)

    # Check Redis connectivity
    try:
        redis_ok = await ar.RedisClient.check_health()
    except Exception:
        redis_ok = False

    result = {
        "shadow_mode": shadow_mode,
        "staging_env": staging_env,
        "redis": "ok" if redis_ok else "error",
        "strategy_active": getattr(ar.config, "AUTO_START_STRATEGY", False),
        "engine_ready": ar.engine is not None,
    }

    # Gate: on STAGING_ENV, shadow_mode MUST be True
    if staging_env and not shadow_mode:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(
            status_code=503,
            detail={
                "error": "staging_misconfiguration",
                "message": "SHADOW_MODE must be True on staging to prevent real order execution.",  # noqa: E501
                "gate": result,
            },
        )

    # Gate: Redis must be reachable
    if not redis_ok:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(
            status_code=503,
            detail={
                "error": "redis_unreachable",
                "message": "Redis ping failed — staging environment is not healthy.",  # noqa: E501
                "gate": result,
            },
        )

    return result


# --- GTM-1 (#1840) tier-upgrade checkout (Brick 1) ---------------------------------
@router.post(
    "/api/entitlement/checkout",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def entitlement_checkout(req: CheckoutRequest) -> dict:
    """Start a Stripe subscription checkout for a purchasable tier (#1840, Brick 1).

    Body ``{tier}`` -> a hosted Stripe Checkout URL as ``{"checkout_url": ...}``. Only
    PRO and PROFESSIONAL are purchasable; BASIC (free), INSTITUTIONAL (B2B invoicing), and
    any unknown tier string are rejected with HTTP 400. The Stripe secret key is loaded
    from Secret Manager inside create_checkout_session (cloud-only) — never here.
    """
    try:
        tier = Tier(req.tier)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unknown tier: {req.tier!r}."
        ) from exc
    try:
        checkout_url = ar.create_checkout_session(tier)
    except ValueError as exc:
        # Non-purchasable tier or unconfigured price id — a client error, not a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"checkout_url": checkout_url}


@router.post("/api/entitlement/webhook")
async def entitlement_webhook(request: Request) -> dict:
    """Stripe webhook: on a paid checkout, mint + persist an entitlement token (#1840, Brick 2).

    W1 (critical): there is NO engine-key / user-sig dependency here — Stripe cannot send one.
    Authentication is EXCLUSIVELY the Stripe signature, verified inside handle_stripe_webhook
    via stripe.Webhook.construct_event over the RAW request body. A missing/invalid signature
    fails closed with HTTP 400 (handle_stripe_webhook raises HTTPException) and never mints.

    The raw body MUST be read as bytes (``await request.body()``) — the FastAPI-parsed JSON
    would be re-serialised and break the HMAC signature. Cloud-only (K_SERVICE) is enforced in
    the handler. Idempotent: Stripe retries are de-duplicated on ``stripe_session_id``.
    """
    from core.entitlement.payment import handle_stripe_webhook

    payload = await request.body()  # exact raw bytes — do NOT use request.json()
    sig_header = request.headers.get("Stripe-Signature")
    return await handle_stripe_webhook(payload, sig_header)


@router.post("/api/entitlement/lemonsqueezy-webhook")
async def entitlement_lemonsqueezy_webhook(request: Request) -> dict:
    """Lemon Squeezy webhook (GTM-2 #1809 T7): on a paid order, mint + persist a Senior token.

    Like the Stripe webhook, there is NO engine-key/user-sig dependency — LS cannot send one.
    Authentication is EXCLUSIVELY the X-Signature HMAC-SHA256 over the RAW body, verified inside
    handle_lemonsqueezy_webhook. A missing/invalid signature fails closed with HTTP 400 and never
    mints. The raw body MUST be read as bytes (``await request.body()``) — a re-serialised JSON
    would break the HMAC. Cloud-only (K_SERVICE) is enforced in the handler. Idempotent on the LS
    order UUID.
    """
    from core.entitlement.lemonsqueezy import handle_lemonsqueezy_webhook

    payload = await request.body()  # exact raw bytes — do NOT use request.json()
    sig_header = request.headers.get("X-Signature")
    return await handle_lemonsqueezy_webhook(payload, sig_header)


@router.get("/api/entitlement/license")
async def entitlement_license_lookup(order_identifier: str) -> dict:
    """Success-page token lookup (GTM-2 #1809 T6): return the minted token for an LS order.

    The Lemon Squeezy order UUID (``order_identifier``) is an unguessable capability presented
    by the post-checkout success page, so no engine key is required (the public page cannot send
    one). Returns ``{tier, token}`` or HTTP 404 if unknown. Cloud-only (the DB is cloud-side).
    """
    from core.entitlement.lemonsqueezy import lookup_license

    result = await lookup_license(order_identifier)
    if result is None:
        raise HTTPException(status_code=404, detail="No license found for that order.")
    return result


@router.post(
    "/api/entitlement/activate",
    dependencies=[Depends(require_engine_key)],
)
async def entitlement_activate(req: ActivateRequest) -> dict:
    """GTM-2 (#1809) T4 — activate a pasted license key OFFLINE on the local engine.

    Body ``{token}`` → verified with the shared Ed25519 verifier and, only on a pass,
    persisted as the local ``license.json`` so the next status poll resolves the tier.
    Fully offline: no cloud call, no login (BaFin-safe — no server in the trading loop,
    no remote kill-switch). Fail-closed: a blank token, or one that is invalid / expired
    / untrusted / non-LOCAL, returns HTTP 400 and NEVER writes a license file.
    """
    from core.entitlement import activate_license

    token = (req.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing license key.")
    tier = activate_license(token)
    if tier is None:
        raise HTTPException(
            status_code=400, detail="Invalid or unverifiable license key."
        )
    return {"status": "activated", "tier": tier.value}


@router.post("/api/admin/iron-dome-policy")
async def set_iron_dome_policy(
    p: Dict,
    _engine: None = Depends(require_engine_key),  # noqa: B008
    _admin: None = Depends(require_iron_dome_admin),  # noqa: B008
):
    """ADR-SEC-06 (#1595): admin write-path for the Iron Dome policy.

    Clamps the submitted policy to the immutable hard-floor caps — a value can only ever  # noqa: E501
    be tightened, never widened — and persists the effective policy to SystemConfig.  # noqa: E501
    Auth: engine key + (OSS) loopback/private IP + IRON_DOME_ADMIN_TOKEN (the Round-Table  # noqa: E501
    agents have no path here; ADR-SEC-05 invariant preserved).
    """
    from dataclasses import asdict

    old_policy = await ar._load_iron_dome_policy_value()
    effective = asdict(load_policy(p))
    # ADR-SEC-06 §5: a LOOSENING requires four-eyes (propose -> approve by a distinct admin);  # noqa: E501
    # tightening is safety-positive and applies directly. Off in the LOCAL single-operator edition.  # noqa: E501
    if four_eyes_required() and is_loosening(old_policy, effective):
        raise HTTPException(
            status_code=409,
            detail=(
                "Loosening an Iron Dome limit requires four-eyes; POST "
                "/api/admin/iron-dome-policy/propose then /approve."
            ),
        )
    await _commit_iron_dome_policy(old_policy, effective)
    return {"status": "ok", "policy": effective}


async def _commit_iron_dome_policy(
    old_policy: dict, effective: dict, actor: str = "iron_dome_admin"
) -> None:
    """Audit -> persist -> live-reload the effective policy (tightening / approved path).  # noqa: E501

    WORM (ADR-SEC-06 §4): the Art-14 chain is recorded BEFORE mutating (a failed audit re-raises  # noqa: E501
    and refuses the change); ``actor`` carries the accountable identity (``initiator->approver``  # noqa: E501
    for a four-eyes apply). §5a then reloads the RUNNING guardians (no restart).  # noqa: E501
    """
    await ar.record_iron_dome_policy_change(old_policy, effective, actor=actor)
    await _save_iron_dome_policy(effective)
    _apply_iron_dome_policy_live(effective)
    logging.info("Iron Dome policy updated via admin endpoint: %s", effective)


@router.post("/api/admin/iron-dome-policy/propose")
async def propose_iron_dome_policy(
    p: Dict,
    _engine: None = Depends(require_engine_key),  # noqa: B008
    _admin: None = Depends(require_iron_dome_admin),  # noqa: B008
    _sig: None = Depends(verify_user_id_sig),  # noqa: B008
    x_user_id: str = Header(None, alias="X-User-Id"),  # noqa: B008
):
    """ADR-SEC-06 §5: open a four-eyes request to LOOSEN the Iron Dome policy.

    The shared admin token has no per-admin identity, so segregation of duties keys on the  # noqa: E501
    HMAC-bound ``X-User-Id`` (``verify_user_id_sig`` validates its signature). Persisted with a  # noqa: E501
    10-min cool-off; a DISTINCT admin must then /approve it.
    """
    from dataclasses import asdict

    if not x_user_id:
        raise HTTPException(
            status_code=400, detail="X-User-Id header required."
        )  # noqa: E501
    effective = asdict(load_policy(p))
    now = datetime.now(timezone.utc)
    cooloff_until = now + timedelta(minutes=10)
    pending_id = uuid.uuid4().hex
    await _create_pending(
        pending_id=pending_id,
        initiator=x_user_id,
        requested_policy=effective,
        created_at=now,
        cooloff_until=cooloff_until,
    )
    logging.info(
        "Iron Dome loosening proposed by %s: %s", x_user_id, pending_id
    )  # noqa: E501
    return {
        "status": "pending",
        "pending_id": pending_id,
        "cooloff_until": cooloff_until.isoformat(),
    }


@router.post("/api/admin/iron-dome-policy/approve")
async def approve_iron_dome_policy(
    body: Dict,
    _engine: None = Depends(require_engine_key),  # noqa: B008
    _admin: None = Depends(require_iron_dome_admin),  # noqa: B008
    _sig: None = Depends(verify_user_id_sig),  # noqa: B008
    x_user_id: str = Header(None, alias="X-User-Id"),  # noqa: B008
):
    """ADR-SEC-06 §5: approve a pending loosening. The approver (HMAC-bound ``X-User-Id``) MUST  # noqa: E501
    be distinct from the initiator (segregation of duties); once a distinct approval lands and  # noqa: E501
    the cool-off has elapsed, the change is committed with an ``initiator->approver`` audit.  # noqa: E501
    """
    if not x_user_id:
        raise HTTPException(
            status_code=400, detail="X-User-Id header required."
        )  # noqa: E501
    pending_id = body.get("pending_id")
    pending = await _get_pending(pending_id)
    if pending is None:
        raise HTTPException(
            status_code=404, detail="Pending change not found."
        )  # noqa: E501
    if pending.applied:
        raise HTTPException(
            status_code=409, detail="Pending change already applied."
        )  # noqa: E501
    approvals = add_approval(
        pending.approvals or [],
        approver=x_user_id,
        initiator=pending.initiator,  # noqa: E501
    )
    now = datetime.now(timezone.utc)
    cooloff = pending.cooloff_until
    # tz-safety: some DB backends read the timestamp back tz-naive; treat it as UTC.  # noqa: E501
    if cooloff is not None and cooloff.tzinfo is None:
        cooloff = cooloff.replace(tzinfo=timezone.utc)
    if is_ready_to_apply(approvals, cooloff, now):
        old = await ar._load_iron_dome_policy_value()
        await _commit_iron_dome_policy(
            old,
            pending.requested_policy,
            actor=f"{pending.initiator}->{x_user_id}",  # noqa: E501
        )
        await _mark_pending_applied(pending_id, approvals)
        logging.info(
            "Iron Dome loosening %s applied (approver %s)",
            pending_id,
            x_user_id,  # noqa: E501
        )
        return {"status": "applied", "policy": pending.requested_policy}
    await _update_pending_approvals(pending_id, approvals)
    return {"status": "pending", "approvals": approvals}


async def _create_pending(
    *, pending_id, initiator, requested_policy, created_at, cooloff_until
) -> None:
    """Insert a new PendingPolicyChange row (a loosening awaiting a distinct second admin)."""  # noqa: E501
    from core.database.models import PendingPolicyChange
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        session.add(
            PendingPolicyChange(
                id=pending_id,
                initiator=initiator,
                requested_policy=requested_policy,
                approvals=[],
                created_at=created_at,
                cooloff_until=cooloff_until,
                applied=False,
            )
        )
        await session.commit()


async def _get_pending(pending_id):
    """Return the PendingPolicyChange row for ``pending_id``, or None."""
    import sqlalchemy as sa

    from core.database.models import PendingPolicyChange
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(PendingPolicyChange).filter_by(id=pending_id)
        )
        return result.scalars().first()


async def _update_pending_approvals(pending_id, approvals) -> None:
    """Persist the updated approver list on a still-pending change."""
    import sqlalchemy as sa

    from core.database.models import PendingPolicyChange
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(PendingPolicyChange).filter_by(id=pending_id)
        )
        row = result.scalars().first()
        if row is not None:
            row.approvals = approvals
            await session.commit()


async def _mark_pending_applied(pending_id, approvals) -> None:
    """Mark a pending change applied (idempotency guard against a double-approve)."""  # noqa: E501
    import sqlalchemy as sa

    from core.database.models import PendingPolicyChange
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(PendingPolicyChange).filter_by(id=pending_id)
        )
        row = result.scalars().first()
        if row is not None:
            row.approvals = approvals
            row.applied = True
            await session.commit()


def _apply_iron_dome_policy_live(policy_dict: dict) -> None:
    """ADR-SEC-06 §5a: apply the policy to the running guardians without a restart.  # noqa: E501

    Skips a not-yet-started engine (``engine is None``) or a disabled/uninitialised guardian.  # noqa: E501
    ``reload_policy`` is total (load_policy clamps + fails closed), so a stored value can only  # noqa: E501
    tighten the live limits. Runs after the persist + WORM audit have committed.  # noqa: E501
    """
    for attr in (
        "compliance_guardian",
        "live_risk_manager",
        "sim_risk_manager",
    ):  # noqa: E501
        target = getattr(ar.engine, attr, None)
        if target is not None and hasattr(target, "reload_policy"):
            target.reload_policy(policy_dict)


async def _save_iron_dome_policy(policy_dict: dict) -> None:
    """Upsert the effective policy into SystemConfig (config_key=iron_dome_policy)."""  # noqa: E501
    from datetime import datetime, timezone

    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(SystemConfig).filter_by(config_key=CONFIG_KEY)
        )
        row = result.scalars().first()
        now = datetime.now(timezone.utc)
        if row:
            row.config_value = policy_dict
            row.updated_at = now
        else:
            session.add(
                SystemConfig(
                    config_key=CONFIG_KEY,
                    config_value=policy_dict,
                    updated_at=now,  # noqa: E501
                )
            )
        await session.commit()


@router.get("/api/entitlement/status")
async def get_entitlement_status(_: None = Depends(require_engine_key)):  # noqa: B008
    """GTM-1 #1915 — the resolved tier for the console (read-only, offline).

    Runs on the LOCAL engine, which reads license.json via resolve_entitlement()
    (fail-closed to BASIC). Powers the sidebar Upgrade CTA: `can_upgrade` is true
    only for Junior (BASIC) desktops, so Senior (PRO)+ users never see the CTA.
    """
    from core.entitlement import resolve_entitlement

    ent = resolve_entitlement()
    return {
        "tier": ent.tier.value,
        "allow_live": ent.allow_live,
        "can_upgrade": ent.tier == Tier.BASIC,
        # Whether the desktop Simulation/backtest page is exposed for this edition.
        # Central switch: core/entitlement/tier.py (currently False for all tiers).
        "simulation_enabled": ent.simulation_enabled,
        # GTM-2 (#1809): whether the paid Private/Senior paywall is active. OFF = the
        # console offers the free offline beta claim; ON = it offers the paid checkout +
        # license-key activation. Read from the live config so a single toggle flips it.
        "paywall_enabled": bool(
            getattr(ar.config.get_config(), "PAYWALL_ENABLED", False)
        ),
        # GTM-2 (#1809) T5: the Lemon Squeezy hosted checkout URL for "Get Senior"; None
        # until ops provisions it → the console falls back to the key-activation panel.
        "checkout_url": (
            getattr(ar.config.get_config(), "LEMONSQUEEZY_CHECKOUT_URL", "") or ""
        ).strip()
        or None,
        # GTM-2 (#1809): Finance-controlled display price for the paid Senior card (one
        # config source, not hardcoded in the UI). None if unset → card shows a placeholder.
        "price_display": (
            getattr(ar.config.get_config(), "LEMONSQUEEZY_PRICE_DISPLAY", "") or ""
        ).strip()
        or None,
    }


# ── #2653 (Epic #2655, Weg 1): portfolio sector-constraint governance ────────────
# DEDICATED endpoint — deliberately NOT the iron-dome policy path: sector caps are
# portfolio-sizing governance; the Iron Dome's compliance hard-floors stay a separate,
# untouched object (epic guardrail). Reuses the four-eyes MACHINERY (distinct approver in
# the enterprise edition) and the Art-14 WORM chain (strict write BEFORE the mutation).


def _relative_caps_or_none(raw: dict) -> Optional[dict]:
    """Archon stale-proposal guard: RELATIVE caps only (0 < cap <= 1) — absolute $ amounts
    approved hours later would mis-trim against a moved portfolio."""
    if not isinstance(raw, dict) or not raw:
        return None
    caps = {}
    for sector, cap in raw.items():
        if (
            isinstance(cap, bool)
            or not isinstance(cap, (int, float))
            or not 0 < cap <= 1
        ):
            return None
        caps[str(sector)] = float(cap)
    return caps


@router.get("/api/admin/portfolio-constraints/pending")
async def get_portfolio_constraints_pending(
    _: None = Depends(require_engine_key),  # noqa: B008
):  # noqa: B008
    """Read-only review data for the local approval UI (#2666) — GET never mutates."""
    from core.governance.portfolio_constraints import (
        load_approved_constraints,
        load_pending_proposal,
    )

    return {
        "pending": load_pending_proposal(),
        "approved": load_approved_constraints(),
    }


@router.post("/api/admin/portfolio-constraints/propose")
async def propose_portfolio_constraints(
    body: PortfolioConstraintsProposeRequest,
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """Stage a proposal for a DISTINCT admin's /approve. Mints the same Ed25519 approval
    token the EOD assessment uses (12h TTL) so the approve path is uniform."""
    from core.engine.eod_assessment import mint_approval_token
    from core.governance.portfolio_constraints import save_pending_proposal

    caps = _relative_caps_or_none(body.caps)
    if caps is None:
        raise HTTPException(
            status_code=400, detail="caps must be relative fractions (0 < cap <= 1)"
        )
    token = mint_approval_token(caps)
    save_pending_proposal(
        {
            "caps": caps,
            "actor": body.actor,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        }
    )
    return {"status": "pending", "token": token}


@router.post("/api/admin/portfolio-constraints/approve")
async def approve_portfolio_constraints(
    body: PortfolioConstraintsApproveRequest,
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """Four-eyes approve: verify the Ed25519 token (expired/non-relative -> 400), bind it to
    the staged pending proposal, refuse self-approve in the enterprise edition, then WORM-audit
    BEFORE persisting (a failed chain write re-raises and the mutation is refused)."""
    from core.engine.eod_assessment import verify_approval_token
    from core.governance.four_eyes import four_eyes_required
    from core.governance.portfolio_constraints import (
        commit_approved_constraints,
        load_approved_constraints,
        load_pending_proposal,
    )

    payload, err = verify_approval_token(body.token)
    if err == "expired":
        raise HTTPException(status_code=400, detail="Token Expired")
    if err is not None:
        raise HTTPException(status_code=400, detail=f"invalid approval token ({err})")

    pending = load_pending_proposal()
    if not pending:
        raise HTTPException(status_code=404, detail="no pending constraint proposal")
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    if pending.get("token_sha256") != token_hash:
        raise HTTPException(
            status_code=409, detail="token does not match the pending proposal"
        )

    initiator = str(pending.get("actor", "unknown"))
    if four_eyes_required() and body.approver == initiator:
        raise HTTPException(
            status_code=403,
            detail="four-eyes: the approver must be a distinct admin (segregation of duties)",
        )

    caps = _relative_caps_or_none(payload.get("caps", {}))
    if caps is None:  # defense-in-depth; verify_approval_token already screens this
        raise HTTPException(status_code=400, detail="non-relative caps in token")

    actor = f"{initiator}->{body.approver}"
    old = load_approved_constraints()
    # WORM (ADR-SEC-06 pattern): the Art-14 chain is recorded BEFORE mutating — a failed
    # audit re-raises (500) and the change is refused, never applied unaudited.
    await ar.log_policy_event(
        {"portfolio_constraints": old},
        {"portfolio_constraints": caps},
        actor,
        strict=True,
    )
    commit_approved_constraints(
        caps,
        {"actor": actor, "approved_at": datetime.now(timezone.utc).isoformat()},
    )
    return {"status": "approved", "caps": caps, "actor": actor}


@router.post("/api/telemetry/switch-reconcile", status_code=202)
async def telemetry_switch_reconcile(
    body: SwitchReconcileRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """#2277 INC-3 (§4b R4): emit a ``health_reconcile`` span for the console's post-switch reconcile.

    ``reconcilePaper()`` posts ONCE, fail-open (a telemetry post NEVER blocks the switch), carrying
    ``{switch_id, attempts, settled_status, paper_trading}``. The audit's GAP-4 was that
    ``health_reconcile`` was promised in §4b but existed nowhere — so a reconcile that gave up left
    no structured trace in the diagnose-export. This route closes it. Best-effort: the span emit is
    guarded so a telemetry hiccup can never turn a fail-open post into a 500.
    """
    try:
        with ar._tracer.start_as_current_span("health_reconcile") as _span:
            if body.switch_id:
                _span.set_attribute("switch_id", body.switch_id)
            _span.set_attribute("attempts", body.attempts)
            if body.settled_status is not None:
                _span.set_attribute("settled_status", body.settled_status)
            if body.paper_trading is not None:
                _span.set_attribute("paper_trading", body.paper_trading)
    except (
        Exception
    ):  # noqa: BLE001 — telemetry must never fail a fail-open reconcile post
        logging.debug("health_reconcile span emit skipped", exc_info=True)
    return {"ok": True}
