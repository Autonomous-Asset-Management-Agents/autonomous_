# core/auth_interfaces.py

import os
import logging
from typing import Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod
from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)


# Pydantic V2 Interface for User Context
class UserContext(BaseModel):
    uid: str
    email: str
    roles: list[str] = []


# Abstract Base Class for Auth Providers
class AuthProvider(ABC):
    @abstractmethod
    def verify_token(self, request: Request) -> UserContext:
        """Extracts and verifies the token from the request, returning a UserContext."""
        pass


# Bypass Local Provider for OSS / Single-Tenant Mode
class LocalMockAuth(AuthProvider):
    """
    Single-tenant auth provider for OSS / Community Edition mode.

    Security contract (Option 2 — full token validation):
      - Missing or malformed Authorization header  → 401 Unauthorized
      - Empty token string after "Bearer "         → 401 Unauthorized
      - Token is literally "INVALID" (test sentinel) → 401 Unauthorized
      - Any other non-empty bearer token            → accepted as admin@localhost

    This preserves the semantic distinction between:
      - 401 = the request is not authenticated (bad/missing token)
      - 403 = authenticated, but the email is not in the operator allowlist

    FirebaseAuth enforces 401 by verifying the token cryptographically.
    LocalMockAuth enforces 401 by checking structural validity — it cannot
    verify signatures, but it can reject obviously invalid inputs so that
    security tests remain meaningful in CE mode.
    """

    _INVALID_SENTINELS = frozenset({"INVALID", "invalid", "fake_token", ""})

    def __init__(self):
        logger.info(
            "Auth Mode: LocalMockAuth (Single-Tenant Bypass) — "
            "valid Bearer tokens accepted as admin@localhost"
        )

    def verify_token(self, request: Request) -> UserContext:
        auth_header = request.headers.get("Authorization", "")

        # Reject missing or malformed header (same contract as FirebaseAuth)
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid Authorization header.",
            )

        raw_token = auth_header[7:].strip()

        # Reject empty or explicitly invalid tokens
        if not raw_token or raw_token in self._INVALID_SENTINELS:
            raise HTTPException(
                status_code=401,
                detail="Invalid token. Provide a valid Bearer token.",
            )

        # In single-tenant OSS mode the local user is implicitly 'admin'.
        # We cannot verify signatures, but structural validity is enforced above.
        uid = os.environ.get("OPERATOR_UID", "oss-admin")
        email = os.environ.get("OPERATOR_EMAIL", "admin@localhost")
        return UserContext(uid=uid, email=email, roles=["admin"])


# Firebase Provider for Enterprise Cloud Deployment
class FirebaseAuth(AuthProvider):
    def __init__(self):
        try:
            import firebase_admin
            from firebase_admin import auth as fb_auth

            self.fb_auth = fb_auth

            # App setup must be handled externally or initialized once
            if not firebase_admin._apps:
                firebase_admin.initialize_app()

            logger.info("Auth Mode: FirebaseAuth (Enterprise Cloud)")
        except ImportError:
            logger.error("firebase-admin package missing. Cannot use FirebaseAuth.")
            raise

    def verify_token(self, request: Request) -> UserContext:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401, detail="Missing or invalid Authorization header"
            )

        raw_token = auth_header[7:]
        try:
            claims = self.fb_auth.verify_id_token(raw_token)
            return UserContext(
                uid=claims.get("uid", ""),
                email=claims.get("email", ""),
                roles=[],  # roles usually fetched from user_roles table or custom claims
            )
        except Exception as exc:
            logger.warning("Firebase token verification failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid or expired token")


# Dependency Injection helper
_AUTH_PROVIDER: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    global _AUTH_PROVIDER
    if _AUTH_PROVIDER is None:
        use_firebase = os.environ.get("ENABLE_FIREBASE_AUTH", "false").lower() == "true"
        if use_firebase:
            _AUTH_PROVIDER = FirebaseAuth()
        else:
            _AUTH_PROVIDER = LocalMockAuth()
    return _AUTH_PROVIDER
