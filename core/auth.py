# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/auth.py
"""
FastAPI dependency for engine API key authentication.

Usage:
    from core.auth import require_engine_key
    from fastapi import Depends

    @app.post("/start-live")
    async def start_live(_: None = Depends(require_engine_key)):
        ...

The key is read from the ENGINE_API_KEY environment variable at request time
(so it picks up the value loaded by secrets_loader.py from GCP Secret Manager).
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


def engine_key_ok(candidate) -> bool:
    """Constant-time validation of a RAW engine-key string, for the WebSocket handshake where a
    browser cannot send the X-Engine-Key header (so the key travels as a ?key= query param).
    Returns False when ENGINE_API_KEY is unset or the candidate is missing/wrong (fail-closed).
    """
    expected: str = os.environ.get("ENGINE_API_KEY", "")
    if not expected or not candidate:
        return False
    return secrets.compare_digest(str(candidate), expected)


def require_engine_key(
    x_bot_api_key: Optional[str] = Header(None, alias="X-Bot-Api-Key"),  # noqa: B008
    x_engine_key: Optional[str] = Header(None, alias="X-Engine-Key"),  # noqa: B008
) -> None:
    """
    Validate the X-Bot-Api-Key request header against ENGINE_API_KEY env var.

    Raises:
        HTTP 503 if ENGINE_API_KEY is not configured on the server.
        HTTP 403 if the key is missing or incorrect.
    """
    expected: str = os.environ.get("ENGINE_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Engine API key not configured. Set ENGINE_API_KEY.",
        )
    # Prefer X-Engine-Key; fall back to legacy X-Bot-Api-Key
    if x_engine_key:
        actual_key = x_engine_key
    elif x_bot_api_key:
        logger.warning("X-Bot-Api-Key is deprecated, use X-Engine-Key")
        actual_key = x_bot_api_key
    else:
        actual_key = None
    if not actual_key:
        raise HTTPException(
            status_code=403,
            detail="Missing X-Engine-Key or X-Bot-Api-Key header.",
        )
    # secrets.compare_digest prevents timing-based key discovery
    if not secrets.compare_digest(actual_key, expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )


def resolve_require_sig(env) -> bool:
    """Resolve whether the proxy→engine HMAC (X-User-Id signature) is enforced.

    REQUIRE_SIG guards the signature a reverse proxy adds in front of the engine. That proxy exists ONLY
    in Cloud Run, so a loopback/desktop/OSS engine has none → default OFF (otherwise the from-source
    browser path 403s and serve_public_api.py fail-fasts on the missing PROXY_ENGINE_SHARED_SECRET). An
    explicit ``REQUIRE_SIG`` env value wins; Cloud Run (``K_SERVICE``) forbids OFF and is re-forced ON
    (ADR-SEC-02). ``env`` is any mapping (``os.environ`` in prod; a dict in tests).
    """
    cloud = bool(env.get("K_SERVICE"))
    on = env.get("REQUIRE_SIG", "true" if cloud else "false").lower() == "true"
    if not on and cloud:
        logger.warning(
            "REQUIRE_SIG=false is forbidden in Cloud Run. Enforcing REQUIRE_SIG=true."
        )
        return True
    return on


def verify_user_id_sig(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),  # noqa: B008
    x_user_id_sig: Optional[str] = Header(None, alias="X-User-Id-Sig"),  # noqa: B008
    x_user_id_ts: Optional[str] = Header(None, alias="X-User-Id-Ts"),  # noqa: B008
) -> None:
    """
    Verify the HMAC signature of X-User-Id from the proxy.
    """
    # ADR-SEC-02: proxy HMAC — off by default off-cloud, forced on in Cloud Run (see resolve_require_sig).
    if not resolve_require_sig(os.environ):
        return

    if not x_user_id:
        raise HTTPException(
            status_code=403,
            detail="X-User-Id required when REQUIRE_SIG is enabled",
        )

    shared_secret = os.environ.get("PROXY_ENGINE_SHARED_SECRET", "")
    if not shared_secret:
        raise HTTPException(
            status_code=500, detail="Missing PROXY_ENGINE_SHARED_SECRET"
        )

    if not x_user_id_sig or not x_user_id_ts:
        raise HTTPException(
            status_code=403, detail="Missing HMAC signature or timestamp"
        )

    try:
        ts = int(x_user_id_ts)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid timestamp format")

    if abs(time.time() - ts) > 60:
        raise HTTPException(status_code=403, detail="Signature timestamp expired")

    msg = f"{x_user_id}:{x_user_id_ts}".encode("utf-8")
    expected_sig = hmac.new(
        shared_secret.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()

    if not secrets.compare_digest(expected_sig, x_user_id_sig):
        raise HTTPException(status_code=403, detail="Invalid X-User-Id signature")
