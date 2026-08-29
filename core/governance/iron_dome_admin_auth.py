"""ADR-SEC-06 (#1583): auth for the admin policy write-path (sub-issue #1595).

OSS/desktop posture (SEC-01 from the ADR review): because ``LocalMockAuth`` validates
no roles, the admin endpoint is reachable only from loopback/private addresses (so the
Docker-bridge reverse proxy works while external/public networks are rejected) **and**
requires a configured ``IRON_DOME_ADMIN_TOKEN``. A strict ``127.0.0.1``-only check is
avoided because it would block the proxied console request in containerized setups.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

ADMIN_TOKEN_ENV = "IRON_DOME_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-Iron-Dome-Admin-Token"


def ip_is_allowed(client_host: str) -> bool:
    """True for loopback/private hosts (proxy-safe); False for public/external/invalid."""
    if not client_host:
        return False
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def token_is_valid(provided: Optional[str], expected: str) -> bool:
    """Constant-time check; fail-closed when no token is provided or configured."""
    if not expected or not provided:
        return False
    return secrets.compare_digest(provided, expected)


def require_iron_dome_admin(
    request: Request,
    x_iron_dome_admin_token: Optional[str] = Header(  # noqa: B008
        None, alias=ADMIN_TOKEN_HEADER
    ),
) -> None:
    """FastAPI dependency enforcing the OSS admin write-path posture.

    Raises 503 if the admin token is not configured (fail-closed); 403 if the request
    is not from a loopback/private address or the token is missing/invalid.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV, "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"Iron Dome admin token not configured. Set {ADMIN_TOKEN_ENV}.",
        )
    client_host = request.client.host if request.client else ""
    if not ip_is_allowed(client_host):
        logger.warning(
            "Rejected Iron Dome admin request from non-private host %r.", client_host
        )
        raise HTTPException(
            status_code=403,
            detail="External access forbidden; admin endpoint is loopback/private-only.",
        )
    if not token_is_valid(x_iron_dome_admin_token, expected):
        raise HTTPException(
            status_code=403, detail=f"Invalid or missing {ADMIN_TOKEN_HEADER}."
        )
