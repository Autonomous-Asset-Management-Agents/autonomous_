"""
Read-only public API proxy for localhost:8081.

Authentication:
  - All endpoints require a valid Firebase ID token (Authorization: Bearer <token>).
  - Token is verified with firebase-admin SDK.
  - Phase 2 upgrade: uncomment role check in _require_auth() to restrict by claims.

Upstream Auth:
  - Calls to aaa-backend (Cloud Run, OIDC-protected) use a GCP OIDC token
    fetched from the metadata server (automatic when running on Cloud Run).

Endpoints:
  GET  /system-health          CPU %, RAM %, uptime, backend latency
  GET  /strategy               Proxied from aaa-backend
  GET  /portfolio-summary      Proxied from aaa-backend
  GET  /stock-history          Proxied from aaa-backend
  GET  /top-picks              Proxied from aaa-backend
  GET  /recent-news            Proxied from aaa-backend
  GET  /benchmark-equity       Proxied from aaa-backend
  POST /chat                   Proxied from aaa-backend (info only)

Usage (local dev):
  set ENGINE_URL=http://localhost:8001
  python serve_public_api.py
"""

# Task #361: OTel SDK MUST be initialised before any other import
from core.telemetry import init_telemetry  # noqa: E402

init_telemetry(service_name="aaa-api-public")

import os

from dotenv import load_dotenv

load_dotenv(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.oss"
    )
)

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode  # noqa: F401 — kept for potential future use

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
import psutil
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter

from core.auth_interfaces import get_auth_provider
from core.otel_middleware import OtelSpanMiddleware
from core.redis_client import RedisClient
from core.structured_logging import setup_logging
from core.user_wallet_store import wallet_store

try:
    from sqlalchemy import select

    from core.database.models import RoundTableSession
    from core.database.session import AsyncSessionLocal

    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    AsyncSessionLocal = None
    RoundTableSession = None

# Initialize structured logging
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:8001").rstrip("/")
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
PROXY_ENGINE_SHARED_SECRET = os.environ.get("PROXY_ENGINE_SHARED_SECRET", "")
# Firebase project that issued the frontend tokens (project 'aaagents', NOT the GCP
# project this service runs in). Must be set explicitly so verify_id_token() checks
# the correct 'aud' claim in the JWT.
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "aaagents")
_START_TIME = time.time()

# ---------------------------------------------------------------------------
# Operator Allowlist — who may access this API
# Set ALLOWED_DOMAIN and/or ALLOWED_EMAILS (comma-separated) env vars on
# Cloud Run to configure without code changes.
# ---------------------------------------------------------------------------
ALLOWED_DOMAIN: str = os.environ.get(
    "ALLOWED_DOMAIN", "aaagents.dev"
)  # disabled — use explicit list
_extra = os.environ.get("ALLOWED_EMAILS", "")
ALLOWED_EMAILS: set[str] = {e.strip() for e in _extra.split(",") if e.strip()}
# Explicit operator list — add/remove here + redeploy:
_operator_email = os.environ.get("OPERATOR_EMAIL", "admin@localhost")
if _operator_email:
    ALLOWED_EMAILS.add(_operator_email)


def _is_email_allowed(email: str | None) -> bool:
    if not email:
        return False
    if email.endswith(f"@{ALLOWED_DOMAIN}"):
        return True
    return email in ALLOWED_EMAILS


ALLOWED_GET_PATHS = {
    "/strategy",
    "/portfolio-summary",
    "/stock-history",
    "/top-picks",
    "/recent-news",
    "/recent-trades",
    "/benchmark-equity",
    "/health",
    "/health/deep",
    "/diagnostics",
    "/compliance-status",
}

ALLOWED_POST_PATHS = {
    "/start-live",
    "/stop",
    "/panic-sell",
    "/reset-kill-switch",
    "/set-strategy",
    "/run-benchmark",
    "/run-simulation",
}


# ---------------------------------------------------------------------------
# OIDC token for Cloud Run → Cloud Run calls
# ---------------------------------------------------------------------------
def _get_upstream_oidc_token() -> str | None:
    """Fetch an OIDC ID token for calling the upstream aaa-backend.
    Works automatically on Cloud Run via metadata server.
    Returns None in local dev, CI, or when ENGINE_URL is not HTTPS
    (e.g. http://backend:8001 in Docker Compose CI — no GCP metadata server).
    """
    # Skip OIDC fetch for local/CI environments:
    # - http://localhost (local dev)
    # - IS_CI=true env var (GitHub Actions, docker-compose.ci.yml)
    # - any non-HTTPS URL (e.g. http://backend:8001 in docker-compose)
    if (
        ENGINE_URL.startswith("http://localhost")
        or ENGINE_URL.startswith("http://backend")
        or os.environ.get("IS_CI", "").lower() in ("1", "true", "yes")
        or not ENGINE_URL.startswith("https://")
    ):
        return None
    try:
        auth_req = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(auth_req, ENGINE_URL)
    except Exception as exc:
        logger.warning("Could not fetch upstream OIDC token: %s", exc)
        return None


def _upstream_headers() -> dict[str, str]:
    token = _get_upstream_oidc_token()
    h = {"Authorization": f"Bearer {token}"} if token else {}
    if ENGINE_API_KEY:
        h["X-Engine-Key"] = ENGINE_API_KEY
    return h


def _upstream_engine_headers() -> dict[str, str]:
    """Headers for operator-level requests: OIDC + engine API key."""
    # _upstream_headers already includes X-Engine-Key, so we just return it.
    # Keep X-Bot-Api-Key for backwards compatibility if needed, but the engine expects X-Engine-Key now.
    h = _upstream_headers()
    if ENGINE_API_KEY:
        h["X-Bot-Api-Key"] = ENGINE_API_KEY
    return h


def _add_hmac_signature(headers: dict[str, str], user_email: str) -> None:
    """Sign X-User-Id with HMAC-SHA256 for engine verification."""
    if not PROXY_ENGINE_SHARED_SECRET:
        logger.warning(
            "PROXY_ENGINE_SHARED_SECRET not set — "
            "HMAC signing disabled, engine will reject "
            "requests if REQUIRE_SIG=true"
        )
        return
    if not user_email:
        return
    ts = str(int(time.time()))
    msg = f"{user_email}:{ts}".encode("utf-8")
    sig = hmac.new(
        PROXY_ENGINE_SHARED_SECRET.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()
    headers["X-User-Id-Sig"] = sig
    headers["X-User-Id-Ts"] = ts


# ---------------------------------------------------------------------------
# Firebase Auth middleware helper
# ---------------------------------------------------------------------------
def _require_auth(request: Request, require_operator: bool = False) -> dict:
    """Verify ID token from Authorization header.

    Accepts two token types:
    1. Firebase ID token (iss: securetoken.google.com) — standard web session.
    2. Google OIDC token (iss: accounts.google.com) — direct Cloud Run IAM auth
       for user:andreas@aaagents.de / user:georg@aaagents.de principals.

    Both paths enforce the operator email allowlist if require_operator is True.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    raw_token = auth_header[7:]
    email = ""

    # --- Path 1: AuthProvider / Firebase ID token ---
    try:
        user_context = get_auth_provider().verify_token(request)
        email = user_context.email
    except Exception as exc:
        pass  # Fall through to Google OIDC path

    # --- Path 2: Google OIDC token (direct Cloud Run user IAM) ---
    if not email:
        try:
            auth_req = google.auth.transport.requests.Request()
            claims = google.oauth2.id_token.verify_token(raw_token, auth_req)
            email = claims.get("email", "")
            logger.info("Authenticated via Google OIDC token: %s", email)
        except Exception as exc:
            logger.warning("All token verifications failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid or expired token")

    if require_operator and not _is_email_allowed(email):
        logger.warning("Blocked unauthorized email from operator endpoint: %s", email)
        raise HTTPException(
            status_code=403, detail="Not authorized — contact the system operator"
        )

    return {"email": email}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
# Global rate limiter (set during lifespan, None if Redis unavailable)
_rate_limiter: RateLimiter | None = None


async def _rate_limit_dependency(request: Request, response: Response):
    """Defensive rate-limit dependency — no-op if Redis is unavailable."""
    if _rate_limiter is not None:
        await _rate_limiter(request, response)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rate_limiter
    logger.info("aaa-api-public starting. ENGINE_URL=%s", ENGINE_URL)

    # --- OSS Mode Startup Warning ---
    oss_mode = not os.environ.get("ENABLE_FIREBASE_AUTH", "false").lower() == "true"
    if oss_mode:
        logger.warning(
            "========================================================\n"
            "  WARNING: OSS Mode Active (LocalMockAuth).\n"
            "  - NOT suitable for High-Frequency Trading (HFT).\n"
            "  - Paper Trading is STRONGLY recommended.\n"
            "  - API keys must be set via .env.oss (not the dashboard).\n"
            "  - Auth is restricted to private network addresses only.\n"
            "========================================================"
        )
    require_sig = os.environ.get("REQUIRE_SIG", "true").lower() == "true"
    if require_sig and not PROXY_ENGINE_SHARED_SECRET:
        logger.error(
            "Fail-fast: REQUIRE_SIG is true but PROXY_ENGINE_SHARED_SECRET is missing!"
        )
        import sys

        sys.exit(1)

    # Initialize Redis for rate limiting — 5s timeout to avoid startup probe failure
    try:
        redis = await asyncio.wait_for(RedisClient.get_redis(), timeout=5.0)
        await asyncio.wait_for(redis.ping(), timeout=3.0)
        await FastAPILimiter.init(redis)
        _rate_limiter = RateLimiter(times=60, seconds=60)
        logger.info("FastAPI Limiter initialized with Redis")
    except asyncio.TimeoutError:
        logger.error("Redis connection timed out at startup — rate limiting disabled")
    except Exception as e:
        logger.error(
            "Failed to initialize Redis Rate Limiting — rate limiting disabled: %s", e
        )

    # Initialize PostgreSQL for Multi-Tenant metadata — 5s timeout
    try:
        await asyncio.wait_for(wallet_store.connect(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.error(
            "Cloud SQL connection timed out at startup — wallet store disabled"
        )
    except Exception as e:
        logger.error("Failed to connect to Cloud SQL Wallet Store: %s", e)

    # Start global Redis Pub/Sub listener for Explainability events
    listener_task = asyncio.create_task(_redis_pubsub_listener())

    yield

    listener_task.cancel()
    await RedisClient.close()
    await wallet_store.close()


app = FastAPI(title="AAA Public Console API", lifespan=lifespan)
# Removed slowapi exception handler and state

# CORS configuration loading from environment variables.
# By default, we use local development origins as a secure fallback.
cors_raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
if cors_raw:
    # Split by comma and strip whitespaces/empty entries
    cors_origins = [origin.strip() for origin in cors_raw.split(",") if origin.strip()]
else:
    # OSS defaults: localhost only. Enterprise domains (console.aaagents.de,
    # firebaseapp.com) must be injected via CORS_ALLOWED_ORIGINS env var.
    # See AGENTS.md § "OSS vs. Enterprise Feature Summary".
    cors_origins = [
        "http://localhost:8081",
        "http://localhost:8082",
        "http://localhost:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(OtelSpanMiddleware)  # Task #361: spans for every request


# ---------------------------------------------------------------------------
# System Health (new endpoint — no upstream proxy needed)
# ---------------------------------------------------------------------------


# Local readiness probe — NOT proxied to the engine.
# Each service (proxy + engine) has its own /ready for
# independent Cloud Run liveness/readiness checks.
@app.get("/ready")
async def ready_check():
    """Unauthenticated fast readiness check for Cloud Run."""
    return {"status": "ready"}


@app.get("/system-health", dependencies=[Depends(_rate_limit_dependency)])
async def system_health(request: Request):
    _require_auth(request)

    cpu_pct = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    uptime_seconds = int(time.time() - _START_TIME)

    # Detailed health from upstream engine
    backend_stats: dict[str, Any] = {}
    latency_ms: float | None = None
    try:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{ENGINE_URL}/system-health", headers=_upstream_headers()
            )
            r.raise_for_status()
            backend_stats = r.json()
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:
        logger.warning("Could not fetch backend system-health: %s", exc)
        backend_stats = {"status": "unreachable", "error": str(exc)}

    return {
        "proxy": {
            "cpu_pct": cpu_pct,
            "ram_pct": ram.percent,
            "uptime_seconds": uptime_seconds,
        },
        "backend": backend_stats,
        "backend_latency_ms": latency_ms,
        "engine_url": ENGINE_URL,
    }


# ---------------------------------------------------------------------------
# Proxied GET endpoints
# ---------------------------------------------------------------------------


async def proxy_get(
    request: Request, _rl: None = Depends(_rate_limit_dependency)  # noqa: B008
):
    path = request.url.path
    if path not in ALLOWED_GET_PATHS:
        raise HTTPException(status_code=403, detail="Forbidden")

    user_email = ""
    observability_paths = ["/health", "/health/deep", "/metrics"]
    if path not in observability_paths:
        auth_data = _require_auth(request)
        user_email = auth_data["email"]

    query = str(request.url.query)
    url = f"{ENGINE_URL}{path}"
    if query:
        url += "?" + query

    headers = _upstream_headers()
    if user_email:
        headers["X-User-Id"] = user_email
        _add_hmac_signature(headers, user_email)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers)
            # Do NOT raise_for_status — pass through backend error JSON as-is.
            # A 5xx from /health/deep is intentional (degraded state signalling).
            # A 4xx should surface to the caller, not become a generic Server error.
            try:
                return r.json()
            except Exception:
                # Non-JSON body (e.g. Cloud Run 404 HTML page) — return error dict
                logger.warning(
                    "Proxy upstream returned non-JSON %s: %s",
                    r.status_code,
                    r.text[:200],
                )
                return {
                    "status": "error",
                    "message": f"Upstream returned HTTP {r.status_code}",
                    "upstream_status": r.status_code,
                }
    except httpx.RequestError as exc:
        logger.warning("Proxy request failed: %s", exc)
        return {"status": "error", "message": "Upstream unavailable"}
    except Exception as e:
        logger.exception("Proxy error")
        return {"status": "error", "message": f"Proxy error: {str(e)}"}


# ADR: Dynamic route registration from ALLOWED_GET_PATHS.
# This eliminates the dual-maintenance problem (decorator + constant) that
# caused the /recent-trades bug. ALLOWED_GET_PATHS is the single source of truth.
for _path in ALLOWED_GET_PATHS:
    app.add_api_route(_path, proxy_get, methods=["GET"])


# ---------------------------------------------------------------------------
# Chat (proxied, info-only)
# ---------------------------------------------------------------------------


@app.post("/chat", dependencies=[Depends(_rate_limit_dependency)])
async def proxy_chat(request: Request):
    auth_data = _require_auth(request)
    user_email = auth_data["email"]

    try:
        body = await request.json()
    except Exception:
        return {"reply": "Invalid request.", "message": "Invalid request."}

    url = f"{ENGINE_URL}/chat"
    headers = _upstream_headers()
    if user_email:
        headers["X-User-Id"] = user_email
        _add_hmac_signature(headers, user_email)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return r.json()
    except httpx.RequestError as exc:
        logger.warning("Chat proxy failed: %s", exc)
        return {
            "reply": "Insights are temporarily unavailable.",
            "message": "Upstream unavailable",
        }
    except Exception:
        logger.exception("Chat proxy error")
        return {
            "reply": "Something went wrong. Please try again.",
            "message": "Server error",
        }


# ---------------------------------------------------------------------------
# Operator POST endpoints (proxied to engine with API key auth)
# ---------------------------------------------------------------------------


@app.post("/start-live")
@app.post("/stop")
@app.post("/panic-sell")
@app.post("/reset-kill-switch")
@app.post("/set-strategy")
@app.post("/run-benchmark")
@app.post("/run-simulation")
async def proxy_operator_post(request: Request):
    auth_data = _require_auth(request, require_operator=True)
    user_email = auth_data["email"]

    path = request.url.path
    if path not in ALLOWED_POST_PATHS:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Read body (may be empty for start-live / stop / panic-sell)
    try:
        body = await request.json()
    except Exception:
        body = {}

    url = f"{ENGINE_URL}{path}"
    headers = _upstream_engine_headers()
    if user_email:
        headers["X-User-Id"] = user_email
        _add_hmac_signature(headers, user_email)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return r.json()
    except httpx.RequestError as exc:
        logger.warning("Operator proxy request failed (%s): %s", path, exc)
        return {"status": "error", "message": "Upstream unavailable"}
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Operator proxy upstream error (%s): %s %s",
            path,
            exc.response.status_code,
            exc.response.text[:200],
        )
        return {
            "status": "error",
            "message": f"Engine returned {exc.response.status_code}",
        }
    except Exception:
        logger.exception("Operator proxy error (%s)", path)
        return {"status": "error", "message": "Server error"}


# ---------------------------------------------------------------------------
# Alpaca OAuth Integration — DISABLED in OSS Edition
# ---------------------------------------------------------------------------
# The OSS edition does not support OAuth-based key management.
# API credentials must be provided exclusively via the .env.oss file.
# The Enterprise edition uses GCP Secret Manager for credential storage.
# These stubs return a clear 400 error instead of a 404 to avoid silent
# frontend failures if a "Connect Alpaca" button is still rendered.
# ---------------------------------------------------------------------------


@app.get("/auth/alpaca/login")
async def alpaca_oauth_login_disabled():
    raise HTTPException(
        status_code=400,
        detail=(
            "OAuth is disabled in the OSS Edition. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env.oss file instead. "
            "See README.oss.md for setup instructions."
        ),
    )


@app.get("/auth/alpaca/callback")
async def alpaca_oauth_callback_disabled():
    raise HTTPException(
        status_code=400,
        detail=(
            "OAuth is disabled in the OSS Edition. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env.oss file instead. "
            "See README.oss.md for setup instructions."
        ),
    )


# ---------------------------------------------------------------------------
# Tenant Settings & Bot Status (Epic 3.1)
# ---------------------------------------------------------------------------


@app.get("/settings/risk-limits")
async def get_risk_limits(request: Request):
    """Retrieve the logged-in user's risk limits and bot status."""
    try:
        auth_data = _require_auth(request)
        user_id = auth_data["email"]
    except Exception as e:
        logger.warning(f"OIDC token validation failed for /settings/risk-limits: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")

    try:
        wallet = await wallet_store.get_wallet(user_id)
    except Exception as e:
        logger.exception(f"Failed to retrieve wallet for user {user_id}")
        return {"status": "error", "message": f"Failed to retrieve wallet: {str(e)}"}

    if not wallet:
        logger.warning(
            f"Wallet not found for user {user_id} during risk limits retrieval."
        )
        return {"status": "error", "message": "Broker not connected"}

    return {
        "status": "success",
        "risk_limits": wallet.get("risk_limits", {}),
        "bot_status": wallet.get("status", "inactive"),
    }


@app.post("/settings/risk-limits")
async def set_risk_limits(request: Request):
    """Update the logged-in user's risk limits."""
    auth_data = _require_auth(request)
    user_id = auth_data["email"]

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    limits = payload.get("risk_limits", {})
    success = await wallet_store.update_risk_limits(user_id, limits)

    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Wallet not found")


@app.post("/settings/alpaca-keys")
async def set_alpaca_keys_disabled():
    """Disabled in OSS Edition — keys must be set via .env.oss."""
    raise HTTPException(
        status_code=400,
        detail=(
            "Setting Alpaca API keys via the dashboard is disabled in the OSS Edition. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env.oss file instead. "
            "See README.oss.md for setup instructions."
        ),
    )


@app.post("/bot/status")
async def set_bot_status(request: Request):
    """Toggle the Bot 'active' or 'inactive' for the logged in user."""
    auth_data = _require_auth(request)
    user_id = auth_data["email"]

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    bot_status = payload.get("status", "inactive")
    if bot_status not in ["active", "inactive"]:
        raise HTTPException(status_code=400, detail="Status must be active or inactive")

    success = await wallet_store.update_status(user_id, bot_status)
    if success:
        return {"status": "success", "bot_status": bot_status}
    else:
        raise HTTPException(status_code=404, detail="Wallet not found")


# ---------------------------------------------------------------------------
# Audit Trail Endpoint (Epic INF-9)
# ---------------------------------------------------------------------------


async def fetch_round_table_session_by_id(session_id: str) -> dict | None:
    if not DB_AVAILABLE or not AsyncSessionLocal:
        logger.error("DB Not Available for Audit")
        return None
    async with AsyncSessionLocal() as session:
        stmt = select(RoundTableSession).where(
            RoundTableSession.session_id == session_id
        )
        result = await session.execute(stmt)
        record = result.scalars().first()
        if not record:
            return None

        # Convert SQLAlchemy object to dict
        return {
            "session_id": record.session_id,
            "symbol": record.symbol,
            "session_time": (
                record.session_time.isoformat() if record.session_time else None
            ),
            "consensus_score": record.consensus_score,
            "signal_action": record.signal_action,
            "gatekeeper_approved": record.gatekeeper_approved,
            "gatekeeper_reason": record.gatekeeper_reason,
            "vote_count": record.vote_count,
            "votes_json": record.votes_json,
            "is_simulation": record.is_simulation,
        }


@app.get("/api/v1/audit/run/{session_id}")
async def audit_run_endpoint(request: Request, session_id: str):
    """
    Fetch a persistent Audit record of a RoundTable evaluation.
    Requires an Operator/Admin level authentication.
    """
    _require_auth(request, require_operator=True)

    record = await fetch_round_table_session_by_id(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audit record not found")

    return record


# ---------------------------------------------------------------------------
# WebSocket Explainability Feed (Epic 3.1)
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        subprotocol: str | None = None,
    ):
        # When the client sent a subprotocol we must echo it on accept, otherwise
        # the browser closes the connection with 1006 "subprotocol mismatch".
        if subprotocol:
            await websocket.accept(subprotocol=subprotocol)
        else:
            await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_personal_message(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass


manager = ConnectionManager()


# Sec-WebSocket-Protocol subprotocol used to carry the Firebase ID token.
# The client sends ["access_token.jwt.v1", "<token>"]; we verify the token,
# then echo "access_token.jwt.v1" on accept. Tokens never appear in URLs
# (which leak into proxy access logs, browser history, and Referer headers).
_WS_AUTH_SUBPROTOCOL = "access_token.jwt.v1"


def _verify_ws_token(raw_token: str) -> str:
    """Return the email claim from a raw ID token, or '' if invalid.

    Mirrors the fallback in _require_auth():
      0. OSS Mode bypass (LocalMockAuth)
      1. Firebase ID token via AuthProvider
      2. Google OIDC token (direct Cloud Run IAM principal)
    """
    use_firebase = os.environ.get("ENABLE_FIREBASE_AUTH", "false").lower() == "true"
    if not use_firebase:
        _INVALID_SENTINELS = frozenset({"INVALID", "invalid", "fake_token", ""})
        if not raw_token or raw_token in _INVALID_SENTINELS:
            return ""
        return os.environ.get("OPERATOR_EMAIL", "admin@localhost")

    # Path 1: Firebase ID token via firebase_admin
    try:
        from firebase_admin import auth as fb_auth

        claims = fb_auth.verify_id_token(raw_token)
        email = claims.get("email", "")
        if email:
            return email
    except Exception:
        pass

    # Path 2: Google OIDC token
    try:
        auth_req = google.auth.transport.requests.Request()
        claims = google.oauth2.id_token.verify_token(raw_token, auth_req)
        return claims.get("email", "") or ""
    except Exception:
        return ""


@app.websocket("/ws/explainability")
async def websocket_explainability(websocket: WebSocket):
    """
    Real-time feed for Explainable AI events (why trades were rejected, etc.).

    Authentication:
        Client sends the Firebase ID token via the Sec-WebSocket-Protocol
        header as the 2nd value, prefixed by the marker subprotocol:
            Sec-WebSocket-Protocol: access_token.jwt.v1, <token>
        The server verifies the token and echoes the marker subprotocol
        on accept so the browser doesn't close with 1006.
    """
    # RFC 6455: Sec-WebSocket-Protocol is a comma-separated list.
    raw_header = websocket.headers.get("sec-websocket-protocol", "") or ""
    protocols = [p.strip() for p in raw_header.split(",") if p.strip()]

    token = ""
    if len(protocols) >= 2 and protocols[0] == _WS_AUTH_SUBPROTOCOL:
        token = protocols[1]

    if not token:
        logger.info("WS /ws/explainability rejected: missing subprotocol token")
        await websocket.close(code=1008)  # 1008 = policy violation
        return

    user_id = _verify_ws_token(token)
    if not user_id:
        logger.info("WS /ws/explainability rejected: token verification failed")
        await websocket.close(code=1008)
        return

    # Echo the marker subprotocol on accept so the browser completes the handshake.
    await manager.connect(websocket, user_id, subprotocol=_WS_AUTH_SUBPROTOCOL)
    try:
        while True:
            await websocket.receive_text()  # One-way channel backend -> frontend
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)


async def _redis_pubsub_listener():
    """Background task to listen to Redis and forward to WebSockets."""
    try:
        redis = await RedisClient.get_redis()
        pubsub = redis.pubsub()
        await pubsub.psubscribe("explainability:*")

        async for message in pubsub.listen():
            if message["type"] == "pmessage":
                channel = message["channel"]
                user_id = channel.split("explainability:", 1)[1]
                data = message["data"]
                try:
                    payload = json.loads(data)
                    await manager.send_personal_message(payload, user_id)
                except Exception as e:
                    logger.error(f"Failed processing pubsub message: {e}")
    except Exception as e:
        logger.error(f"Redis PubSub listener stopped: {e}")


# ---------------------------------------------------------------------------
# Public health (no auth — used for Cloud Run uptime check)
# ---------------------------------------------------------------------------


@app.get("/")
async def root_health():
    return {"status": "ok", "service": "aaa-api-public", "engine_url": ENGINE_URL}


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8002"))
    uvicorn.run(app, host=host, port=port)
