# core/entitlement/lemonsqueezy.py
# GTM-2 (#1809) T6/T7 — SERVER-SIDE Lemon Squeezy (Merchant-of-Record) license issuance.
#
# On a paid order, LS calls our webhook. We authenticate it by the X-Signature HMAC-SHA256
# over the RAW body (LS signing secret), then MINT our own offline Ed25519 token via the
# shared issuer (mint_tier_token) and persist it keyed by the LS order UUID. The success page
# later fetches it by that UUID (lookup_license). The desktop verifies the pasted token fully
# OFFLINE (crypto.verify_token) — this server is only a billing/issuance step, never in the
# trading loop, with no persistent connection to the customer and no remote kill-switch
# (BaFin-safe). CLOUD-ONLY: the signing key + webhook secret live in Secret Manager and this
# path must never run on a desktop.
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import config
from core.entitlement.issuer import mint_tier_token
from core.entitlement.tier import Tier

logger = logging.getLogger(__name__)

# Secret Manager name of the Lemon Squeezy webhook SIGNING secret (authenticates inbound
# webhooks). Distinct from any LS API key. Never logged.
_LS_WEBHOOK_SECRET_SECRET = "lemonsqueezy-webhook-secret"

# LS events that grant/renew a Senior license. subscription lifecycle beyond creation
# (renewals/cancellations) is deferred — the annual token simply lapses if not renewed.
_HANDLED_EVENTS = {"order_created", "subscription_created"}

# Annual license (owner decision 2026: "erstmal einmal pro Jahr"). The signed token carries
# the authoritative expiry; this is the mint lifetime. Config-overridable for parity/testing.
_DEFAULT_LICENSE_VALID_DAYS = 365


def _require_cloud() -> None:
    """Fail-closed cloud-only guard: issuance must never run on a desktop.

    ``K_SERVICE`` is set by Cloud Run. Refusing anywhere else keeps the signing key and the
    webhook secret from ever loading outside the cloud service. Mirrors issuer._require_cloud.
    """
    if not os.environ.get("K_SERVICE"):
        raise RuntimeError(
            "[LemonSqueezy] license issuance is cloud-only (K_SERVICE required); "
            "the signing key never runs on the desktop."
        )


def _load_webhook_secret() -> str:
    """Load the LS webhook signing secret from Secret Manager (lazy; cloud-only).

    Mirrors payment._load_webhook_secret (lazy GCP import, cloud guard, strip). Never logged.
    """
    _require_cloud()

    project_id = config.GCP_PROJECT_ID
    if not project_id:
        raise RuntimeError(
            "[LemonSqueezy] GCP_PROJECT_ID is not set — cannot load the webhook secret."
        )

    # Imported lazily: google-cloud-secret-manager is a cloud-only dependency.
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    version_path = (
        f"projects/{project_id}/secrets/{_LS_WEBHOOK_SECRET_SECRET}/versions/latest"
    )
    response = client.access_secret_version(request={"name": version_path})
    return response.payload.data.decode("utf-8").strip()


def verify_ls_signature(payload: bytes, sig_header: str | None, secret: str) -> bool:
    """Return True iff ``sig_header`` is the hex HMAC-SHA256 of ``payload`` under ``secret``.

    Lemon Squeezy signs each delivery with X-Signature = hex(HMAC-SHA256(raw_body, secret)).
    Timing-safe compare; a missing header or any mismatch is False (fail-closed). ``payload``
    MUST be the exact raw request bytes — re-serialising the JSON breaks the HMAC.
    """
    if not sig_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _license_valid_days() -> int:
    """Annual lifetime, overridable via config for parity/testing (defaults to 365)."""
    return int(
        getattr(config, "LEMONSQUEEZY_LICENSE_VALID_DAYS", _DEFAULT_LICENSE_VALID_DAYS)
    )


async def handle_lemonsqueezy_webhook(payload: bytes, sig_header: str | None) -> dict:
    """Verify an LS webhook and, on a paid order event, mint + persist a Senior token.

    Authentication is EXCLUSIVELY the X-Signature HMAC (LS cannot send an engine key). A
    missing/invalid signature fails closed with HTTP 400 and NO mint. Idempotent on the LS
    order UUID (``data.attributes.identifier``): a retried delivery is a no-op. Returns a small
    status dict (``minted`` / ``duplicate`` / ``ignored``). Never logs the token or the secret.

    Raises:
        RuntimeError: off Cloud Run (K_SERVICE unset) — the cloud-only guard.
        fastapi.HTTPException(400): missing/invalid signature or malformed/missing fields.
    """
    _require_cloud()

    from fastapi import HTTPException

    secret = _load_webhook_secret()
    if not verify_ls_signature(payload, sig_header, secret):
        logger.warning(
            "[LemonSqueezy] webhook signature verification failed — rejecting."
        )
        raise HTTPException(
            status_code=400, detail="Invalid Lemon Squeezy webhook signature."
        )

    try:
        event = json.loads(payload)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — malformed body is a client error, fail-closed
        raise HTTPException(status_code=400, detail="Malformed webhook body.") from exc

    event_name = (
        ((event.get("meta") or {}).get("event_name"))
        if isinstance(event, dict)
        else None
    )
    if event_name not in _HANDLED_EVENTS:
        logger.info(
            "[LemonSqueezy] webhook event %s ignored (not handled).", event_name
        )
        return {"status": "ignored", "event_name": event_name}

    data = event.get("data") or {}
    attrs = data.get("attributes") or {}
    identifier = attrs.get("identifier")
    if not identifier or not isinstance(identifier, str):
        raise HTTPException(
            status_code=400, detail="Missing Lemon Squeezy order identifier."
        )

    # Licensee id for the hash only (raw id never stored). Prefer the buyer email, else the
    # numeric order id, else the identifier — all are hashed before persistence.
    licensee = str(attrs.get("user_email") or data.get("id") or identifier)

    from sqlalchemy import select

    from core.database.models import LemonSqueezyLicense
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(LemonSqueezyLicense).where(
                    LemonSqueezyLicense.order_identifier == identifier
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "[LemonSqueezy] order already processed — no re-mint (idempotent)."
            )
            return {"status": "duplicate", "order_identifier": identifier}

        tier = Tier.PRO  # the only Lemon Squeezy-purchasable tier (Private/Senior).
        valid_days = _license_valid_days()
        # Mint AFTER the idempotency check so a retry never re-mints.
        token = mint_tier_token(tier=tier, issued_to=licensee, valid_days=valid_days)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=valid_days)
        db.add(
            LemonSqueezyLicense(
                order_identifier=identifier,
                issued_to_hash=hashlib.sha256(licensee.encode("utf-8")).hexdigest(),
                tier=tier.value,
                token=token,
                expires_at=expires_at,
                created_at=now,
            )
        )
        import sqlalchemy.exc

        try:
            await db.commit()
        except sqlalchemy.exc.IntegrityError:
            # A concurrent delivery won the unique-identifier race → idempotent duplicate.
            await db.rollback()
            logger.info(
                "[LemonSqueezy] persist raced a duplicate order — treated idempotent."
            )
            return {"status": "duplicate", "order_identifier": identifier}

    logger.info("[LemonSqueezy] minted + persisted Senior token for an order (ok).")
    return {"status": "minted", "order_identifier": identifier}


async def lookup_license(order_identifier: str) -> dict | None:
    """Return ``{tier, token}`` for a minted LS order, or None if unknown (T6 success page).

    The LS order UUID is an unguessable capability, so presenting it is sufficient to fetch
    the token. Cloud-only (the DB lives in the cloud). Never logs the token.
    """
    _require_cloud()

    ident = (order_identifier or "").strip()
    if not ident:
        return None

    from sqlalchemy import select

    from core.database.models import LemonSqueezyLicense
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(LemonSqueezyLicense).where(
                    LemonSqueezyLicense.order_identifier == ident
                )
            )
        ).scalar_one_or_none()

    if row is None:
        return None
    return {"tier": row.tier, "token": row.token}
