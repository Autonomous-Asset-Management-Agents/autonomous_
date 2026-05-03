# core/admin_routes.py
# Epic 3.4-pre: Alpaca User-Account Mapping — Issue #412
# Admin-API: CRUD endpoints for Alpaca account mapping.
# TDD GREEN — written after tests confirmed RED.
#
# Security contract:
#   - All endpoints require Firebase JWT (Bearer token)
#   - Caller must have role='admin' in user_roles table (default deny)
#   - Credentials NEVER returned in response body
#   - Every operation writes to alpaca_account_audit_log

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# DB session — guarded for CI environments
try:
    from core.database.session import AsyncSessionLocal

    DB_AVAILABLE = True
except ImportError:
    try:
        from core.db_session import AsyncSessionLocal  # alternative location

        DB_AVAILABLE = True
    except ImportError:
        AsyncSessionLocal = None  # type: ignore[assignment]
        DB_AVAILABLE = False

try:
    from sqlalchemy import text
except ImportError:
    text = None  # type: ignore[assignment]

# user_secrets module
from core.user_secrets import user_alpaca_secrets, UserAlpacaSecretStoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users", tags=["admin"])


# ── Auth helper ───────────────────────────────────────────────────────────────


def verify_firebase_token(request: Request) -> dict:
    """
    Verify Token from Authorization header.
    Returns decoded claims dict with 'uid' and 'email'.
    Raises HTTPException 401 on failure.
    """
    from core.auth_interfaces import get_auth_provider

    user_context = get_auth_provider().verify_token(request)
    return {"uid": user_context.uid, "email": user_context.email}


async def _require_admin_role(actor_uid: str, session) -> None:
    """
    Check actor_uid has role='admin' in user_roles table.
    Raises HTTPException 403 if not admin (default deny).
    """
    result = await session.execute(
        text("SELECT role FROM user_roles WHERE firebase_uid = :uid"),
        {"uid": actor_uid},
    )
    role = result.scalar_one_or_none()
    if role != "admin":
        logger.warning("Admin-API denied for uid=%s (role=%s)", actor_uid, role)
        raise HTTPException(
            status_code=403,
            detail="Admin role required — contact the system operator.",
        )


async def _write_audit_log(
    session,
    action: str,
    firebase_uid: str,
    actor_uid: str,
    account_type: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Append-only audit log entry."""
    await session.execute(
        text(
            """
            INSERT INTO alpaca_account_audit_log
              (id, action, firebase_uid, actor_uid, account_type, timestamp, details_json)
            VALUES
              (:id, :action, :firebase_uid, :actor_uid, :account_type, :timestamp, :details_json)
        """
        ),
        {
            "id": str(uuid.uuid4()),
            "action": action,
            "firebase_uid": firebase_uid,
            "actor_uid": actor_uid,
            "account_type": account_type,
            "timestamp": datetime.now(timezone.utc),
            "details_json": details or {},
        },
    )


# ── Pydantic models ───────────────────────────────────────────────────────────


class CreateAlpacaAccountRequest(BaseModel):
    api_key: str
    secret_key: str
    base_url: str
    account_type: Literal["paper", "live"]
    label: Optional[str] = None


# ── POST /admin/users/{uid}/alpaca-account ────────────────────────────────────


@router.post("/{target_uid}/alpaca-account", status_code=201)
async def create_alpaca_account(
    target_uid: str,
    payload: CreateAlpacaAccountRequest,
    request: Request,
):
    """
    Register a new Alpaca account mapping for target_uid.
    Actor must be admin. Credentials stored in GCP Secret Manager only.
    """
    claims = verify_firebase_token(request)
    actor_uid = claims["uid"]

    if not DB_AVAILABLE or AsyncSessionLocal is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with AsyncSessionLocal() as session:
        await _require_admin_role(actor_uid, session)

        # Store credentials in GCP Secret Manager
        try:
            secret_ref = user_alpaca_secrets.store_user_alpaca_secret(
                uid=target_uid,
                api_key=payload.api_key,
                secret_key=payload.secret_key,
                base_url=payload.base_url,
            )
        except UserAlpacaSecretStoreError as exc:
            logger.error("Failed to store secrets for %s: %s", target_uid, exc)
            raise HTTPException(status_code=500, detail="Failed to store credentials")

        # Insert mapping row
        account_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                """
                INSERT INTO user_alpaca_accounts
                  (id, firebase_uid, account_type, secret_ref, label, is_active, created_at)
                VALUES
                  (:id, :firebase_uid, :account_type, :secret_ref, :label, true, :created_at)
            """
            ),
            {
                "id": account_id,
                "firebase_uid": target_uid,
                "account_type": payload.account_type,
                "secret_ref": secret_ref,
                "label": payload.label,
                "created_at": now,
            },
        )

        # Audit log
        await _write_audit_log(
            session,
            action="created",
            firebase_uid=target_uid,
            actor_uid=actor_uid,
            account_type=payload.account_type,
            details={"label": payload.label, "account_id": account_id},
        )

        await session.commit()

        logger.info(
            "Admin %s created Alpaca mapping for uid=%s (type=%s, id=%s)",
            actor_uid,
            target_uid,
            payload.account_type,
            account_id,
        )

    # Response intentionally excludes credentials
    return {
        "account_id": account_id,
        "firebase_uid": target_uid,
        "account_type": payload.account_type,
        "label": payload.label,
        "is_active": True,
        "created_at": now.isoformat(),
    }


# ── GET /admin/users/{uid}/alpaca-account ─────────────────────────────────────


@router.get("/{target_uid}/alpaca-account")
async def get_alpaca_account(target_uid: str, request: Request):
    """Retrieve Alpaca account metadata. Never returns credentials."""
    claims = verify_firebase_token(request)
    actor_uid = claims["uid"]

    if not DB_AVAILABLE or AsyncSessionLocal is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with AsyncSessionLocal() as session:
        await _require_admin_role(actor_uid, session)

        result = await session.execute(
            text(
                """
                SELECT id, account_type, label, is_active, created_at
                FROM user_alpaca_accounts
                WHERE firebase_uid = :uid AND is_active = true
                ORDER BY created_at DESC
                LIMIT 1
            """
            ),
            {"uid": target_uid},
        )
        row = result.scalar_one_or_none()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No active Alpaca account mapping for uid={target_uid!r}",
            )

        await _write_audit_log(
            session,
            action="accessed",
            firebase_uid=target_uid,
            actor_uid=actor_uid,
            account_type=getattr(row, "account_type", None),
        )
        await session.commit()

    return {
        "account_id": str(row.id),
        "firebase_uid": target_uid,
        "account_type": row.account_type,
        "label": row.label,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # secret_ref intentionally omitted
    }


# ── DELETE /admin/users/{uid}/alpaca-account/{account_id} ─────────────────────


@router.delete("/{target_uid}/alpaca-account/{account_id}")
async def revoke_alpaca_account(target_uid: str, account_id: str, request: Request):
    """
    Revoke an Alpaca account mapping.
    Marks is_active=false, sets revoked_at, disables GCP Secret Manager versions.
    """
    claims = verify_firebase_token(request)
    actor_uid = claims["uid"]

    if not DB_AVAILABLE or AsyncSessionLocal is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with AsyncSessionLocal() as session:
        await _require_admin_role(actor_uid, session)

        result = await session.execute(
            text(
                """
                SELECT id, firebase_uid, secret_ref, is_active
                FROM user_alpaca_accounts
                WHERE id = :account_id AND firebase_uid = :uid
            """
            ),
            {"account_id": account_id, "uid": target_uid},
        )
        row = result.scalar_one_or_none()

        if row is None:
            raise HTTPException(status_code=404, detail="Account mapping not found")

        revoked_at = datetime.now(timezone.utc)

        await session.execute(
            text(
                """
                UPDATE user_alpaca_accounts
                SET is_active = false, revoked_at = :revoked_at
                WHERE id = :account_id
            """
            ),
            {"revoked_at": revoked_at, "account_id": account_id},
        )

        # Disable GCP Secret Manager versions
        try:
            user_alpaca_secrets.revoke_user_alpaca_secret(target_uid)
        except Exception as exc:
            logger.error("Failed to revoke secrets for %s: %s", target_uid, exc)

        await _write_audit_log(
            session,
            action="revoked",
            firebase_uid=target_uid,
            actor_uid=actor_uid,
            details={"account_id": account_id, "revoked_at": revoked_at.isoformat()},
        )

        await session.commit()

        logger.info(
            "Admin %s revoked Alpaca mapping %s for uid=%s",
            actor_uid,
            account_id,
            target_uid,
        )

    return {
        "status": "revoked",
        "account_id": account_id,
        "revoked_at": revoked_at.isoformat(),
    }
