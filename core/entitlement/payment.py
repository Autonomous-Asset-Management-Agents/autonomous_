# core/entitlement/payment.py
# GTM-1 (#1840) — Brick 1: SERVER-SIDE Stripe Checkout Session creation for the
# tier-upgrade purchase flow. This module STARTS a paid checkout; it is the counterpart to
# (but never calls) the #1839 issuer, which MINTS the token later via Brick 2's webhook.
#
# One integration, two products: the checkout is IDENTICAL for GTM-2 Private (PRO) and
# GTM-3 Professional, differing only in the Stripe price id. BASIC is free and
# INSTITUTIONAL is B2B invoicing (not Stripe), so BOTH are rejected — only PRO/PROFESSIONAL.
#
# CLOUD-ONLY: the Stripe secret key lives in GCP Secret Manager and this path must never
# run on a desktop. `stripe` and the Secret Manager client are imported LAZILY so importing
# this module never requires either dependency (mirrors the issuer's lazy import).
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import config
from core.entitlement.issuer import ENTITLEMENT_TOKEN_VALID_DAYS, mint_tier_token
from core.entitlement.tier import Tier

logger = logging.getLogger(__name__)

# Name of the Secret Manager secret holding the Stripe restricted/secret API key.
_STRIPE_SECRET_KEY_SECRET = "stripe-secret-key"

# Name of the Secret Manager secret holding the Stripe webhook signing secret (``whsec_...``).
# Distinct from the API key: it authenticates INBOUND webhooks (Brick 2), not outbound calls.
_STRIPE_WEBHOOK_SECRET_SECRET = "stripe-webhook-secret"

# The purchasable tiers and the config attribute naming their Stripe price id. BASIC (free)
# and INSTITUTIONAL (B2B invoicing) are deliberately absent → rejected by the lookup below.
_PRICE_ID_CONFIG_KEY: dict[Tier, str] = {
    Tier.PRO: "STRIPE_PRICE_ID_PRO",
    Tier.PROFESSIONAL: "STRIPE_PRICE_ID_PROFESSIONAL",
}


def _require_cloud() -> None:
    """Fail-closed cloud-only guard: checkout creation must never run on a desktop.

    ``K_SERVICE`` is set by Cloud Run. Refusing to proceed anywhere else keeps the Stripe
    secret key from ever being loaded outside the cloud service. Mirrors
    issuer._require_cloud (precedent: core/database/session.py, core/llm/provider.py).
    """
    if not os.environ.get("K_SERVICE"):
        raise RuntimeError(
            "[Payment] checkout creation is cloud-only (K_SERVICE required); "
            "the Stripe secret key never runs on the desktop."
        )


def _load_stripe_secret_key() -> str:
    """Load the Stripe secret key from Secret Manager (lazy; cloud-only).

    Kept lazy so importing this module never touches GCP. Reuses the issuer's
    SecretManagerServiceClient pattern and config.GCP_PROJECT_ID. The returned string is
    the raw Stripe secret key and MUST never be logged or persisted.
    """
    _require_cloud()

    project_id = config.GCP_PROJECT_ID
    if not project_id:
        raise RuntimeError(
            "[Payment] GCP_PROJECT_ID is not set — cannot load the Stripe secret key."
        )

    # Imported lazily: google-cloud-secret-manager is a cloud-only dependency and must not
    # be required to import this module.
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    version_path = (
        f"projects/{project_id}/secrets/{_STRIPE_SECRET_KEY_SECRET}/versions/latest"
    )
    response = client.access_secret_version(request={"name": version_path})
    return response.payload.data.decode("utf-8").strip()


def _load_webhook_secret() -> str:
    """Load the Stripe webhook signing secret from Secret Manager (lazy; cloud-only).

    Mirrors ``_load_stripe_secret_key`` exactly (lazy GCP import, cloud guard, strip). The
    returned ``whsec_...`` value authenticates inbound webhooks and MUST never be logged.
    """
    _require_cloud()

    project_id = config.GCP_PROJECT_ID
    if not project_id:
        raise RuntimeError(
            "[Payment] GCP_PROJECT_ID is not set — cannot load the Stripe webhook secret."
        )

    # Imported lazily: google-cloud-secret-manager is a cloud-only dependency and must not
    # be required to import this module.
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    version_path = (
        f"projects/{project_id}/secrets/{_STRIPE_WEBHOOK_SECRET_SECRET}/versions/latest"
    )
    response = client.access_secret_version(request={"name": version_path})
    return response.payload.data.decode("utf-8").strip()


def _resolve_price_id(tier: Tier) -> str:
    """Map ``tier`` to its configured Stripe price id, rejecting non-purchasable tiers.

    Raises:
        ValueError: if ``tier`` is BASIC/INSTITUTIONAL (not purchasable), or if the
            purchasable tier's price id is not yet configured (real ids arrive with #1805).
    """
    config_key = _PRICE_ID_CONFIG_KEY.get(tier)
    if config_key is None:
        raise ValueError(
            f"[Payment] tier {tier.value} is not purchasable via Stripe checkout "
            "(BASIC is free; INSTITUTIONAL is B2B invoicing)."
        )
    price_id = (getattr(config, config_key, "") or "").strip()
    if not price_id:
        raise ValueError(
            f"[Payment] no Stripe price id configured for {tier.value} "
            f"({config_key} is empty)."
        )
    return price_id


def create_checkout_session(tier: Tier) -> str:
    """Create a Stripe subscription Checkout Session for ``tier`` and return its URL.

    Only PRO and PROFESSIONAL are purchasable; BASIC/INSTITUTIONAL raise ValueError. The
    Stripe secret key is loaded from Secret Manager (cloud-only). The tier is stamped into
    the session ``metadata`` so Brick 2's webhook can mint the matching entitlement token.

    Raises RuntimeError off Cloud Run (K_SERVICE) / if GCP_PROJECT_ID is unset, and
    ValueError for a non-purchasable tier or an unconfigured price id.
    """
    _require_cloud()
    price_id = _resolve_price_id(tier)

    # Lazy import: `stripe` is a cloud-only dependency (see requirements.txt) and must not
    # be required to import this module.
    import stripe

    stripe.api_key = _load_stripe_secret_key()

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=config.ENTITLEMENT_CHECKOUT_SUCCESS_URL,
        cancel_url=config.ENTITLEMENT_CHECKOUT_CANCEL_URL,
        metadata={"tier": tier.value},
    )
    logger.info("[Payment] created Stripe checkout session for tier=%s", tier.value)
    return session.url


# Event types this Brick handles. Subscription lifecycle (invoice.paid /
# customer.subscription.deleted) is deferred to Brick 2b — see #1840.
_HANDLED_EVENT_TYPE = "checkout.session.completed"


async def handle_stripe_webhook(payload: bytes, sig_header: str | None) -> dict:
    """Verify a Stripe webhook, and on ``checkout.session.completed`` mint + persist a token.

    Authentication is EXCLUSIVELY the Stripe signature: there is no engine key (Stripe cannot
    send one). ``payload`` MUST be the raw request body bytes — re-serialising the JSON breaks
    the HMAC signature. A missing/invalid signature fails closed with HTTP 400 and NO mint.

    Idempotency: Stripe retries webhooks, so a duplicate ``stripe_session_id`` is a no-op — the
    unique constraint plus a pre-check guarantee exactly one row (and one mint) per session.

    Returns a small status dict (``minted`` / ``duplicate`` / ``ignored``). Never logs the
    token or any secret.

    Raises:
        RuntimeError: off Cloud Run (K_SERVICE unset) — the cloud-only guard.
        fastapi.HTTPException(400): missing/invalid signature (fail-closed).
    """
    _require_cloud()

    # Lazy import: `stripe` is a cloud-only dependency (see requirements.txt) and must not be
    # required to import this module (or run the desktop verifier).
    import stripe
    from fastapi import HTTPException

    webhook_secret = _load_webhook_secret()
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as exc:
        # Fail-closed: a missing/invalid signature (or any construct_event failure) is an
        # unauthenticated request. Return 400 and NEVER mint. Do not echo the exception body.
        logger.warning("[Payment] webhook signature verification failed — rejecting.")
        raise HTTPException(
            status_code=400, detail="Invalid Stripe webhook signature."
        ) from exc

    event_type = event["type"] if isinstance(event, dict) else event.type
    if event_type != _HANDLED_EVENT_TYPE:
        # Not our event (subscription lifecycle etc.) — acknowledge without minting.
        logger.info(
            "[Payment] webhook event %s ignored (not handled here).", event_type
        )
        return {"status": "ignored", "event_type": event_type}

    session = event["data"]["object"] if isinstance(event, dict) else event.data.object
    session_id = session["id"]
    metadata = session.get("metadata") or {}
    tier_value = metadata.get("tier")
    # The licensee id is hashed before it is ever stored (raw id never persisted).
    licensee = session.get("customer") or session_id

    try:
        tier = Tier(tier_value)
    except ValueError as exc:
        # A completed checkout with an unknown/missing tier is a data error, not an auth one.
        raise HTTPException(
            status_code=400, detail=f"Unknown tier in session metadata: {tier_value!r}."
        ) from exc

    import sqlalchemy.exc
    from sqlalchemy import select

    from core.database.models import EntitlementToken
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # Idempotency pre-check: a row for this session id means Stripe already delivered it.
        existing = (
            await db.execute(
                select(EntitlementToken).where(
                    EntitlementToken.stripe_session_id == session_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "[Payment] webhook for session already processed — no re-mint (idempotent)."
            )
            return {"status": "duplicate", "session_id": session_id}

        issued_to_hash = hashlib.sha256(licensee.encode("utf-8")).hexdigest()
        # Mint AFTER the idempotency check so a retry never re-mints.
        token = mint_tier_token(tier=tier, issued_to=licensee)

        now = datetime.now(timezone.utc)
        # Mirror the issuer's token lifetime for observability (the token itself carries the
        # authoritative, signed expiry; this column is a convenience for ops/audit queries).
        expires_at = now + timedelta(days=ENTITLEMENT_TOKEN_VALID_DAYS)
        db.add(
            EntitlementToken(
                issued_to_hash=issued_to_hash,
                tier=tier.value,
                token=token,
                expires_at=expires_at,
                stripe_session_id=session_id,
                created_at=now,
            )
        )
        try:
            await db.commit()
        except sqlalchemy.exc.IntegrityError:
            # ERR-01: ONLY the unique stripe_session_id violation is an idempotent duplicate
            # (a concurrent delivery won the race). Any OTHER DB error (timeout, connection
            # loss, crash) must NOT be masked as "duplicate" — it propagates instead of 200.
            await db.rollback()
            logger.info(
                "[Payment] webhook persist raced a duplicate session — treated as idempotent."
            )
            return {"status": "duplicate", "session_id": session_id}

    logger.info(
        "[Payment] minted + persisted entitlement token for tier=%s (session ok).",
        tier.value,
    )
    return {"status": "minted", "session_id": session_id}
