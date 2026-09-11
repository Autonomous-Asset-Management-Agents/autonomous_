# flake8: noqa: E501
# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG  # noqa: E501
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

# core/engine/api_routes.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: FastAPI-App, alle HTTP-Endpoints und WebSocket

# Task #361: OTel SDK MUST be initialised before any other import
from core.telemetry import (  # noqa: E402 (intentional first import)
    get_tracer,
    init_telemetry,
)

init_telemetry(service_name="aaa-backend")

# Module tracer for structured OTEL spans emitted from route handlers (e.g. the LSR R7
# replay-distinct nonce rejection, #2255). Mirrors the order_executor.py span style.
tracer = get_tracer(__name__)

import asyncio
import hashlib
import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

import psutil
import uvicorn
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware

import config
import core.strategies as strategies
from core import hitl_gate
from core.ai_components import answer_chat_with_fallback
from core.auth import require_engine_key, verify_user_id_sig

# #2548 C1: the central client factory — under SIM_MODE it returns the offline sim clients
# (VirtualLiveBroker / SimDataClient) and otherwise runs assert_not_sim() + builds the real client.
from core.client_factory import create_data_client, create_trading_client
from core.database.session import ensure_local_db_ready
from core.entitlement.payment import create_checkout_session
from core.entitlement.tier import Tier
from core.governance.four_eyes import (
    add_approval,
    four_eyes_required,
    is_loosening,
    is_ready_to_apply,
)
from core.governance.iron_dome_admin_auth import require_iron_dome_admin
from core.governance.iron_dome_audit import record_iron_dome_policy_change
from core.governance.iron_dome_policy import (  # noqa: E501
    CONFIG_KEY,
    apply_policy,
    load_policy,
)
from core.hitl_gate import log_policy_event
from core.otel_middleware import OtelSpanMiddleware
from core.redis_client import RedisClient
from core.secret_manager_utils import oauth_secrets
from core.strategies import _rl_agent_file
from core.structured_logging import setup_logging
from core.usage_counters import (  # noqa: E501
    bump_usage,
    get_usage_counters,
    register_api_routes,
)
from core.user_wallet_store import wallet_store
from core.xai.agent_core import is_agent_core_enabled
from core.xai.runtime import answer_via_xai, boot_xai_runtime
from models.torch_model import get_lstm_paths

from .base import BotEngine

setup_logging()
logger = logging.getLogger(__name__)

# LSR R1 (#2252): structured OTEL for the live-disable in-process halt (I-3). Mirrors the
# span style already used in core/engine/order_executor.py — no-op tracer when OTEL packages
# are absent (desktop/CI), so it never raises.
_tracer = get_tracer(__name__)

_START_TIME = time.time()

# Static service version. No get_service_version() accessor existed in the codebase  # noqa: E501
# (both /health and /health/deep hardcoded their own strings); centralised here so  # noqa: E501
# /engine-diagnostics and /health/deep agree on one value.
_SERVICE_VERSION = "2.5.0"


def get_service_version() -> str:
    """The engine service version (single source for the diagnostics + health surfaces)."""  # noqa: E501
    return _SERVICE_VERSION


def compute_overall_status(signals: dict) -> str:
    """Pure machine-health verdict from a flat ``signals`` dict — extracted from the  # noqa: E501
    /health/deep logic (ADR-OBS-01 §7) so BOTH surfaces derive the SAME status.

    Precedence (last match wins, mirroring the original if-ladder):
      starting → healthy → degraded → inactive → stalled.

    Signals (all optional, missing → treated as benign):
      ``engine_ready`` (bool), ``components_degraded`` (bool: a broker/model fault),  # noqa: E501
      ``strategy_running`` (bool), ``is_market_open`` (bool), ``scan_active`` (bool).  # noqa: E501

    NOTE: this only DERIVES the string — the caller decides whether to attach a 500  # noqa: E501
    (/health/deep does; /engine-diagnostics never does).
    """
    if not signals.get("engine_ready", False):
        return "starting"
    status_str = "healthy"
    if signals.get("components_degraded"):
        status_str = "degraded"
    if not signals.get("strategy_running", False):
        status_str = "inactive"
    if signals.get("is_market_open") and not signals.get("scan_active", False):
        status_str = "stalled"
    return status_str


# --- Engine (lazy-initialized in lifespan to avoid blocking port bind) ---
trading_api = None
data_api = None
engine = None  # set by lifespan — None means "still starting up"
# Captured message of a FATAL background-init failure (str) or None. The engine is built in a
# fire-and-forget create_task() with no caller — without capturing the exception here the task dies
# silently, `engine` stays None, and /health reports "starting" forever (invisible never-ready engine).
# _init_engine_async sets this on failure so /health can return an honest status:"error" + detail.
engine_init_error = None


def _init_trading_clients():
    """Synchronous helper: connect to Alpaca and construct BotEngine.

    Called from the lifespan handler via create_task so the event loop
    (and uvicorn's port-8080 bind) is never blocked.
    """
    global trading_api, data_api, engine
    # #2548 C1 — Sim-Day boot seam: under SIM_MODE build the OFFLINE sim clients via the factory
    # (VirtualLiveBroker + SimDataClient) and skip the real-client construction AND the get_account()
    # network probe entirely, so the primary engine boot path never touches real Alpaca (fail-closed).
    # This is where the sim actually activates on the desktop/uvicorn path.
    if getattr(config, "SIM_MODE", False) is True:
        trading_api = create_trading_client()
        data_api = create_data_client()
        engine = BotEngine(trading_client=trading_api, data_client=data_api)
        logging.warning(
            "SIM_MODE active — BotEngine wired to VirtualLiveBroker + SimDataClient (offline sim day)."
        )
        return

    # #2452 (CRITICAL): build the client from the ACTIVE account alias
    # (ALPACA_API_KEY/SECRET/BASE_URL), which the desktop live-swap sets to the LIVE
    # slots and force_paper_trading resets to paper. The legacy API_KEY/BASE_URL are
    # the OSS *paper source* (force_paper_trading's reset origin) — reading them sent
    # the PAPER key to the LIVE endpoint on a live boot → self.api=None → live never
    # started. Enterprise resolves API_KEY -> ALPACA_API_KEY, so ALPACA_* is edition-neutral.
    _alpaca_key_val = (
        config.get_secret_str(config.ALPACA_API_KEY) if config.ALPACA_API_KEY else ""
    )
    _offline_sentinel = _alpaca_key_val == "offline_mode"
    if config.ALPACA_API_KEY and not _offline_sentinel:
        try:
            is_paper = "paper" in config.ALPACA_BASE_URL.lower()
            api_key_str = config.get_secret_str(config.ALPACA_API_KEY)
            api_secret_str = config.get_secret_str(config.ALPACA_SECRET_KEY)

            # Defense-in-depth: route the real path through the factory too, so assert_not_sim()
            # guards this constructor (dies loudly if ever reached while SIM_MODE flips on).
            trading_api = create_trading_client(
                api_key_str, api_secret_str, paper=is_paper
            )  # noqa: E501
            data_api = create_data_client(api_key_str, api_secret_str)
            acc = trading_api.get_account()
            logging.info(
                "Alpaca API connected. Status=%s equity=%s",
                acc.status,
                acc.equity,  # noqa: E501
            )
        except Exception as _alpaca_err:
            logging.error(
                "Alpaca API init failed — engine clients will be None. Error: %s.",  # noqa: E501
                _alpaca_err,
            )
            trading_api = None
            data_api = None
    elif _offline_sentinel:
        # A3 (#2743): the 'offline_mode' sentinel is a DELIBERATE recommendation-only boot, not a
        # fault. Skip client init entirely so it never logs an ERROR "Alpaca API init failed —
        # unauthorized" (which reads like a defect in the desktop log viewer).
        logging.info(
            "Alpaca offline mode (ALPACA_API_KEY='offline_mode') — broker clients disabled; "
            "engine runs recommendation-only."
        )
    else:
        logging.warning(
            "ALPACA_API_KEY not set — live trading and portfolio data disabled."  # noqa: E501
        )
    engine = BotEngine(trading_client=trading_api, data_client=data_api)
    logging.info("BotEngine ready — Cloud Run startup complete.")


async def _fetch_and_apply_remote_config():
    """Fetches system_config from DB and applies to global config vars."""
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sa.select(SystemConfig).filter_by(config_key="global_settings")
            )
            config_row = result.scalars().first()
            if config_row and hasattr(config_row, "config_value"):
                config.apply_remote_config(config_row.config_value)
    except Exception as e:
        logging.warning(
            "Failed to fetch dynamic remote config from DB; using env vars. Error: %s",  # noqa: E501
            e,
        )


async def _init_engine_impl():
    await _fetch_and_apply_remote_config()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_trading_clients)


async def _init_engine_async():
    # Fired as a fire-and-forget create_task() from the lifespan — it has NO caller to propagate an
    # exception to. Any raise below would otherwise kill the task silently, leaving `engine` None and
    # /health stuck on "starting" forever with no reason. Wrap the WHOLE init so a fatal failure is
    # captured in `engine_init_error` (and logged with a full traceback) → /health surfaces it.
    global engine_init_error
    engine_init_error = None  # clear a prior failure on (re)start
    try:
        # G0a (#1050 / AUDIT-011, INV-24): guarantee DB tables exist BEFORE any
        # engine component issues its first write. ensure_local_db_ready() is
        # idempotent and a no-op for PostgreSQL (cloud unchanged); on a fresh
        # desktop SQLite install it runs create_all — without this call the first
        # INSERT crashes with "no such table" (the function shipped with zero call
        # sites although its docstring always said "called by engine startup code").  # noqa: E501
        # Fail-closed but LOUD (§5.6): a bootstrap failure (disk full, AV file lock)  # noqa: E501
        # must not become an invisible never-ready engine.
        try:
            await ensure_local_db_ready()
        except Exception as exc:
            logging.critical(
                "G0a DB bootstrap failed — engine will NOT start: %s", exc
            )  # noqa: E501
            raise
        # #3145 (2b): seed the fundamentals cache from the bundled snapshot on a
        # COLD cache (first boot, or after an upgrade wiped the app dir). Best-effort:
        # a missing snapshot or an already-warm cache is a no-op, and the nightly SEC
        # producer supersedes it. Agents stay weight=0 (dark) either way.
        try:
            from core.report.financials_snapshot import seed_bundled_fundamentals

            _seeded = seed_bundled_fundamentals()
            if _seeded:
                logging.info(
                    "Fundamentals: seeded %d symbols from the bundled snapshot (cold cache).",
                    _seeded,
                )
        except Exception as _exc:  # noqa: BLE001 — a seed failure must never block boot
            logging.warning("Fundamentals snapshot seed skipped: %s", _exc)
        # INC-4 (#2213): re-apply the operator's PERSISTED HITL/autonomy limits so a restart (incl. the
        # live-enable restart, which restarts the engine) does not silently reset them to env defaults.
        # After the DB is ready, before the engine/gate is built. Fail-safe — never blocks boot.
        from core.hitl_policy_store import load_and_apply_hitl_policy

        await load_and_apply_hitl_policy()
        await _init_engine_impl()
    except (
        Exception
    ) as exc:  # noqa: BLE001 — background task: capture EVERYTHING, surface via /health
        engine_init_error = f"{type(exc).__name__}: {exc}"
        logging.critical(
            "Engine initialization FAILED — engine will not start: %s",
            exc,
            exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: heavy init runs in background AFTER uvicorn binds port 8080.  # noqa: E501

    Cloud Run's TCP startup probe only checks that port 8080 is open.
    By firing initialize_engine_async as a non-blocking background task and yielding  # noqa: E501
    immediately, uvicorn opens port 8080 first, satisfying the probe.
    The config fetch and engine load continue initializing in the background.
    /health returns status='starting' until engine is ready.
    """
    asyncio.create_task(_init_engine_async())
    yield  # uvicorn binds port 8080 here — Cloud Run probe satisfied immediately  # noqa: E501
    # Graceful shutdown: signal engine threads to stop
    if engine is not None:
        try:
            engine._shutdown_event.set()
        except Exception:
            pass

    # #2499: tear down the feature ProcessPool so its worker processes don't orphan on restart
    # (no-op when FEATURE_POOL_ENABLED is off — no pool is ever created). NOTE: a force-kill
    # (taskkill /F) bypasses this path; the flag-ON validation build must watch for orphan workers.
    try:
        from models.feature_pool import shutdown_pool

        shutdown_pool()
    except Exception:
        pass

    # Cleanup DB connections (Fix memory leak on shutdown)
    try:
        from core.database.session import cleanup_engine_connector
        from core.database.session import engine as db_engine

        if db_engine is not None:
            await cleanup_engine_connector(db_engine)
    except Exception as e:
        logging.error("Failed to cleanup global DB connector: %s", e)


app = FastAPI(title="Trading Bot Engine API", lifespan=lifespan)

# CORS — nur explizit erlaubte Origins (nicht wildcard in Produktion)
_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8081,https://localhost:8081",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    # Desktop serves the renderer on a DYNAMIC loopback port (e.g. 8082), so the
    # fixed _CORS_ORIGINS list can't cover it and every OPTIONS preflight 400'd →
    # endless frontend retry → UI freeze. Match ANY loopback origin (localhost /
    # 127.0.0.1 / [::1], any port) — loopback-only, NOT a wildcard, so it stays
    # security-clean and credentials-compatible. Explicit _CORS_ORIGINS kept.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?$",
    allow_credentials=True,
    # OPTIONS for the CORS preflight itself; no DELETE/PUT/PATCH exponiert.
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Bot-Api-Key",
        "X-Engine-Key",
        # OBS-5 (#2638): allow the W3C trace header so the browser preflight does
        # not strip it on cross-origin (console) requests → UI↔engine stays one trace.
        "traceparent",
    ],  # noqa: E501
)
app.add_middleware(OtelSpanMiddleware)  # Task #361: spans for every request


# --- Health & Diagnostics ---


@app.get("/ready")
async def ready_check():
    return {"status": "ready"}


@app.get("/health/readiness")
async def readiness_check():
    import config
    from core.llm.health import resolved_provider_name

    missing = []

    if not config.get_secret_str(config.ALPACA_API_KEY):
        missing.append("ALPACA_API_KEY")
    if not config.get_secret_str(config.ALPACA_SECRET_KEY):
        missing.append("ALPACA_SECRET_KEY")

    provider = resolved_provider_name()
    if provider == "gemini":
        if not config.get_secret_str(config.GEMINI_API_KEY):
            missing.append("GEMINI_API_KEY")

    can_start = len(missing) == 0
    status_label = "Ready" if can_start else "Setup Required"

    return {
        "can_start": can_start,
        "missing_requirements": missing,
        "status_label": status_label,
    }


@app.get("/health")
async def health_check():
    # Return 200 immediately while engine is still starting up.
    # Cloud Run TCP probe only checks port reachability, not response body.
    if engine is None:
        # Local import: the healthy branch below re-imports `config`, which makes the name
        # function-local for the whole scope — reference it here without a matching local bind
        # and Python raises UnboundLocalError. The module-level `import config` is shadowed.
        import config

        # A captured fatal init failure turns the eternal "starting" into an honest, diagnosable
        # error the console/diagnostics can surface (instead of a silent never-ready engine).
        if engine_init_error is not None:
            return {
                "status": "error",
                "detail": engine_init_error,
                "redis": "unknown",
                "timestamp": time.time(),
                "version": "2.5.0",
                "strategy_running": False,
                "system_halted": False,
                "paper_trading": getattr(config, "PAPER_TRADING", True),
            }

        return {
            "status": "starting",
            "redis": "unknown",
            "timestamp": time.time(),
            "version": "2.5.0",
            "strategy_running": False,
            "system_halted": False,
            # LSR R4 (#2251, audit #08): carry the account truth even in the post-restart boot
            # window. config.PAPER_TRADING is fixed at spawn time (before engine init), so the
            # "starting" body can report it — otherwise the console's `h.paper_trading ?? true`
            # reconcile silently defaults a booting-live engine to Paper (audit #05/#16).
            "paper_trading": getattr(config, "PAPER_TRADING", True),
        }
    redis_healthy = await RedisClient.check_health()
    import config
    from core.kill_switch import KillSwitch

    # Observability (fail-safe): surface WHY the system is halted next to the boolean.  # noqa: E501
    # In-memory read only; any failure degrades to None and never breaks /health.  # noqa: E501
    try:
        _last_trip = KillSwitch().last_trip()
        halt_reason = (_last_trip or {}).get("reason")
    except Exception:
        halt_reason = None

    try:
        from core.round_table.agents import ALL_AGENTS

        inactive_agents = [
            agent.__class__.__name__ for agent in ALL_AGENTS if agent.weight <= 0.0
        ]
    except Exception:
        inactive_agents = []

    return {
        "status": "healthy",
        "redis": "connected" if redis_healthy else "disconnected",
        "timestamp": time.time(),
        "version": "2.5.0",
        "strategy_running": engine.strategy_running.is_set(),
        # #1642: kill-switch state so the Overview can show AKTIV/GESTOPPT live (read-only).  # noqa: E501
        "system_halted": KillSwitch().is_halted(),
        # killswitch-observability: the reason the system is halted (or None).
        "halt_reason": halt_reason,
        # #1425: the active Alpaca account — paper (True) vs live (False). Drives the Settings  # noqa: E501
        # account switcher so the operator always sees which account is live.
        "paper_trading": getattr(config, "PAPER_TRADING", True),
        # LSR R2 (#2253): surface WHY a live arming booted PAPER (I-4, fixes #09 dead flag). The
        # console's reconcilePaper() reads these so a degraded live-arm shows an explicit reason
        # instead of silently sitting on paper. degraded_live=True ⇒ TRANSIENT (WORM arm kept,
        # retries next boot); a TERMINAL degrade instead self-heals via a compensating WORM disable.
        "live_enable_failed_reason": getattr(config, "LIVE_ENABLE_FAILED_REASON", None),
        "degraded_live": getattr(config, "DEGRADED_LIVE", False),
        "inactive_agents": inactive_agents,
    }


@app.get("/system-health")
async def system_health(
    _: None = Depends(require_engine_key),  # noqa: B008
):  # Auth required
    cpu_pct = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    uptime_seconds = int(time.time() - _START_TIME)
    if engine is None:
        # Engine still initialising — return safe defaults
        return {
            "status": "starting",
            "cpu_pct": cpu_pct,
            "ram_pct": ram.percent,
            "ram_used_gb": round(ram.used / (1024**3), 2),
            "ram_total_gb": round(ram.total / (1024**3), 2),
            "uptime_seconds": uptime_seconds,
            "latency_metrics": {
                "avg_cycle_ms": 0,
                "max_cycle_ms": 0,
                "last_cycle": {},
            },  # noqa: E501
            "timestamp": time.time(),
        }
    avg_latency = (
        sum(engine._cycle_latencies) / len(engine._cycle_latencies)
        if engine._cycle_latencies
        else 0
    )
    max_latency = (
        max(engine._cycle_latencies) if engine._cycle_latencies else 0
    )  # noqa: E501
    return {
        "status": "healthy",
        "cpu_pct": cpu_pct,
        "ram_pct": ram.percent,
        "ram_used_gb": round(ram.used / (1024**3), 2),
        "ram_total_gb": round(ram.total / (1024**3), 2),
        "uptime_seconds": uptime_seconds,
        "latency_metrics": {
            "avg_cycle_ms": round(avg_latency, 2),
            "max_cycle_ms": round(max_latency, 2),
            "last_cycle": engine._last_cycle_details,
        },
        "timestamp": time.time(),
    }


@app.get("/health/deep")
async def deep_health(response: Response):
    from core.cloud_logger import get_cloud_logger

    alpaca_status = "unavailable"
    alpaca_details = {}
    is_market_open = False
    if engine is not None and engine.api:
        try:
            # Offload the synchronous Alpaca REST round-trips so a slow broker call cannot block the
            # single uvicorn event loop (which would stall EVERY concurrent request, incl. /health).
            acc = await asyncio.to_thread(engine.api.get_account)
            alpaca_status = "ok" if acc.status == "ACTIVE" else acc.status
            # SECURITY: Kein Equity in unauthentifiziertem Response — nur funded/unfunded  # noqa: E501
            alpaca_details = {
                "status": acc.status,
                "is_funded": float(acc.equity) > 0,
            }  # noqa: E501
            clock = await asyncio.to_thread(engine.api.get_clock)
            is_market_open = clock.is_open
        except Exception as e:
            # Log the raw exception server-side; return a generic marker to
            # unauthenticated callers (see /health/deep is publicly proxied).
            logging.error(
                "deep_health: Alpaca probe failed: %s", e, exc_info=True
            )  # noqa: E501
            alpaca_status = "error"
            alpaca_details = {"error": "alpaca_probe_failed"}

    cloud_sql_connected = False
    try:
        cloud_sql_connected = get_cloud_logger().is_connected
    except Exception:
        pass

    models_status = {}
    models_status["gemini"] = "ok" if config.GEMINI_AVAILABLE else "disabled"
    # G4a-3: additive — surfaces the resolved LLM provider for the console/UI
    # (Gemini API key vs desktop Ollama). The "gemini" key above is kept for
    # console-contract compatibility.
    models_status["llm_provider"] = (
        (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    )

    lstm_loaded = False
    rl_loaded = False
    if engine is not None and engine.active_strategy:
        lstm_loaded = (
            getattr(engine.active_strategy, "torch_model", None) is not None
        )  # noqa: E501
        rl_loaded = (
            getattr(engine.active_strategy, "rl_model", None) is not None
        )  # noqa: E501
    else:
        try:
            lstm_paths = get_lstm_paths()
            lstm_loaded = all(os.path.exists(p) for p in lstm_paths)
            rl_file = _rl_agent_file(
                getattr(config, "RL_MODEL_VERSION", "rl_agent_v3_dsr")
            )  # noqa: E501
            rl_loaded = os.path.exists(rl_file)
        except Exception as e:
            logging.error(
                "deep_health: Failed to check model files during startup: %s", e
            )
            lstm_loaded = False
            rl_loaded = False

    models_status["lstm"] = "ok" if lstm_loaded else "missing"
    models_status["rl"] = "ok" if rl_loaded else "missing"

    # DASH-1 T7 (#1472): the module-level engine may not be constructed yet during  # noqa: E501
    # the boot window. Guard every engine.* access and report an honest 'starting'  # noqa: E501
    # status (HTTP 200) instead of an uncaught AttributeError -> bare 500.
    if engine is not None:
        strategy_running = engine.strategy_running.is_set()
        last_scan_age = time.time() - engine._last_scan_time
        scan_active = last_scan_age < (
            config.STRATEGY_MONITOR_INTERVAL_SECONDS * 1.5
        )  # noqa: E501
    else:
        strategy_running = False
        last_scan_age = None
        scan_active = False

    # Reuse the shared pure verdict (ADR-OBS-01 §7) — identical precedence to before.  # noqa: E501
    components_degraded = alpaca_status != "ok" or "missing" in models_status.values()
    overall_status = compute_overall_status(
        {
            "engine_ready": engine is not None,
            "components_degraded": components_degraded,
            "strategy_running": strategy_running,
            "is_market_open": is_market_open,
            "scan_active": scan_active,
        }
    )
    # A2 (#2743): a non-healthy verdict used to attach a bare HTTP 500 with NO server log — the
    # desktop UI's fetchDeepHealth() then swallowed it to null and could not tell "degraded" (engine
    # up, broker/model fault) from "engine down". The body already carries the authoritative `status`
    # + `components_degraded`, and no probe/LB consumes this endpoint's code (only /health,
    # /health/readiness do), so it now answers **200** and the caller reads the state from the body.
    # The degraded reason is logged server-side so it is diagnosable.
    if overall_status in ("degraded", "inactive", "stalled"):
        logging.warning(
            "deep_health: status=%s (alpaca=%s, models=%s, strategy_running=%s)",
            overall_status,
            alpaca_status,
            models_status,
            strategy_running,
        )

    return {
        "status": overall_status,
        "components_degraded": components_degraded,
        "timestamp": time.time(),
        "is_market_open": is_market_open,
        "strategy_running": strategy_running,
        "last_scan_age_seconds": (
            round(last_scan_age, 1) if last_scan_age is not None else None
        ),
        "components": {
            "alpaca": {"status": alpaca_status, "details": alpaca_details},
            "cloud_sql": {
                "status": "ok" if cloud_sql_connected else "disconnected"
            },  # noqa: E501
            "models": models_status,
        },
        "version": "1.1.0",
    }


# --- ADR-OBS-01 / PR A: GET /engine-diagnostics (Tier-1 machine health) ---
#
# A privacy-safe, ALWAYS-200 aggregation of the core engine's machine health. Every  # noqa: E501
# subsystem is built by a fail-soft ``_collect_<name>()`` helper: any exception is  # noqa: E501
# caught by ``_safe_collect`` and rendered as ``{"_error": "<ExceptionClass>"}`` so a  # noqa: E501
# single broken subsystem can never bubble up and kip the whole response. Every field is  # noqa: E501
# null-safe (a missing handle → ``None``, never a raise) and NO live-broker calls are made  # noqa: E501
# (cached/existing state only). Privacy: derived booleans only — never equity/positions/  # noqa: E501
# PnL/order details/user_id/raw kill-switch scope/full REDIS_URL/LLM texts.


async def _safe_collect(collector) -> dict:
    """Run one ``_collect_*`` helper fail-soft. Any exception → ``{"_error": "<Class>"}``.  # noqa: E501

    Accepts sync or async collectors so the async subsystems (HITL/DB, which read Redis)  # noqa: E501
    and the sync ones share one uniform wrapper.
    """
    try:
        result = collector()
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as exc:  # noqa: BLE001 — deliberate: isolate a broken subsystem
        return {"_error": type(exc).__name__}


def _collect_process() -> dict:
    """Infra (Tier-3): liveness + resource pressure. No secrets."""
    ram = psutil.virtual_memory()
    return {
        "engine_ready": engine is not None,
        "service_version": get_service_version(),
        "uptime_seconds": int(time.time() - _START_TIME),
        "paper_trading": getattr(config, "PAPER_TRADING", True),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_pct": ram.percent,
    }


def _collect_loops() -> dict:
    """Trading/monitor loop liveness + scan freshness (null-safe when engine is None).  # noqa: E501

    PR B extends this with the "is the engine actually cycling" liveness that was  # noqa: E501
    missing during the halt incident: the monotone cycle/scan counters, the cycle-  # noqa: E501
    latency sample count, the high-latency count, the age of the most recent cycle,  # noqa: E501
    and the CACHED market-open flag (read WITHOUT a live-broker call)."""
    if engine is None:
        return {
            "trading_loop_running": False,
            "monitor_loop_running": False,
            "last_scan_age_seconds": None,
            "scan_active": False,
            "active_strategy": None,
            "auto_start_strategy": getattr(
                config, "AUTO_START_STRATEGY", False
            ),  # noqa: E501
            "cycles_completed": None,
            "scans_completed": None,
            "cycle_sample_count": None,
            "high_latency_cycles": None,
            "last_cycle_age_seconds": None,
            "is_market_open": None,
            "stalled": False,
            "stall_reason": "engine_not_ready",
        }
    last_scan_age = time.time() - engine._last_scan_time
    interval = getattr(config, "STRATEGY_MONITOR_INTERVAL_SECONDS", 1800)
    active = getattr(engine, "active_strategy", None)
    # last_cycle_age: wall-clock age of the most recent trading cycle, from the
    # timestamp stamped into _last_cycle_details (None until the first cycle runs).  # noqa: E501
    last_cycle_ts = (getattr(engine, "_last_cycle_details", None) or {}).get(
        "timestamp"
    )
    last_cycle_age = (
        round(time.time() - last_cycle_ts, 1)
        if last_cycle_ts is not None
        else None  # noqa: E501
    )
    # #1832 — TIME-driven stall verdict: a completed cycle older than the threshold WHILE the market
    # is open means the loop is silently dead (the cycle-driven CycleWatchdog can't see a fully dead
    # loop). Exposed here so /health/deep + /engine-diagnostics carry `stalled` as a first-class field.  # noqa: E501
    import core.cycle_watchdog as _cw

    stall = _cw.evaluate_stall(
        now=time.time(),
        last_cycle_ts=last_cycle_ts,
        market_open=bool(getattr(engine, "_last_market_open", False)),
        strategy_running=engine.strategy_running.is_set(),
        stall_after_seconds=getattr(config, "LOOP_STALL_AFTER_SECONDS", 5400),
    )
    return {
        "trading_loop_running": engine.strategy_running.is_set(),
        "monitor_loop_running": engine.monitor_running.is_set(),
        "last_scan_age_seconds": round(last_scan_age, 1),
        "scan_active": last_scan_age < (interval * 1.5),
        "active_strategy": type(active).__name__ if active else None,
        "auto_start_strategy": getattr(config, "AUTO_START_STRATEGY", False),
        "cycles_completed": getattr(engine, "_cycles_completed", None),
        "scans_completed": getattr(engine, "_scans_completed", None),
        "cycle_sample_count": len(
            getattr(engine, "_cycle_latencies", []) or []
        ),  # noqa: E501
        "high_latency_cycles": getattr(engine, "_high_latency_cycles", None),
        "last_cycle_age_seconds": last_cycle_age,
        "is_market_open": getattr(engine, "_last_market_open", None),
        "stalled": stall["stalled"],
        "stall_reason": stall["reason"],
    }


def _collect_watchdogs() -> dict:
    """PR B (Tier-2): read-only liveness of the three safety watchdogs.

    Each watchdog ``status()`` is a read-only snapshot (never mutates) and each is  # noqa: E501
    wrapped fail-soft here so ONE broken watchdog degrades to ``{"_error": ...}``  # noqa: E501
    without failing the sibling watchdogs or the response. The singletons are the  # noqa: E501
    module-level ``core.<name>.<name>`` instances."""

    def _one(getter) -> dict:
        try:
            return getter()
        except Exception as exc:  # noqa: BLE001 — isolate one broken watchdog
            return {"_error": type(exc).__name__}

    import core.cycle_watchdog as _cw
    from core.latency_watchdog import latency_watchdog
    from core.ml_watchdog import ml_watchdog

    return {
        "cycle": _one(_cw.cycle_watchdog.status),
        "latency": _one(latency_watchdog.status),
        "ml": _one(ml_watchdog.status),
    }


def _collect_kill_switch() -> dict:
    """Kill-switch halt state + last-trip reason (r6/12), scrubbed of scope/user_id.  # noqa: E501

    Exposes a derived ``is_global_halt`` boolean instead of the raw kill-switch scope,  # noqa: E501
    and NEVER the ``user_id`` on the trip record (privacy — machine view only).
    """
    from core.kill_switch import kill_switch

    st = kill_switch.status()
    trip = st.get("last_trip") or None
    last = None
    is_global = False
    if trip:
        last = {"reason": trip.get("reason"), "at": trip.get("at")}
        # Derive is_global_halt WITHOUT leaking the raw scope/user_id: a trip with no  # noqa: E501
        # user_id is a global halt (scope == "GLOBALLY").
        is_global = trip.get("user_id") is None
    return {
        "halted": bool(st.get("halted")),
        "is_global_halt": bool(st.get("halted")) and is_global,
        "last_trip": last,
    }


async def _collect_governance() -> dict:
    """Effective Iron Dome policy caps (VC-4). Reads the loader defaults fail-closed.  # noqa: E501

    ``pending_policy_change`` count is best-effort and guarded (a DB failure → None),  # noqa: E501
    never blocking the response. It is a cached, fail-safe COUNT of PENDING four-eyes  # noqa: E501
    changes (see ``core.governance.pending_policy_change``): the query runs at most once  # noqa: E501
    per TTL window, so this stays off the always-200 hot path.
    """
    from core.governance.iron_dome_policy import load_policy
    from core.governance.pending_policy_change import (  # noqa: E501
        get_pending_policy_change_count,
    )

    policy = load_policy(
        None
    )  # fail-closed STRICT_DEFAULT; live caps overlaid below  # noqa: E501
    return {
        "max_order_value": policy.max_order_value,
        "daily_drawdown_pct": policy.daily_drawdown_pct,
        "portfolio_stop_loss_pct": policy.portfolio_stop_loss_pct,
        "max_daily_trades": policy.max_daily_trades,
        "wash_trade_window_seconds": policy.wash_trade_window_seconds,
        "pending_policy_change": await get_pending_policy_change_count(),
    }


async def _collect_hitl() -> dict:
    """HITL (Art-14) queue depth + day-notional budget. Dormant → ``{"enabled": False}``."""  # noqa: E501
    if not getattr(config, "HITL_ENABLED", False):
        return {"enabled": False}

    from datetime import datetime as _dt

    from core.hitl_day_notional import HitlDayNotional
    from core.hitl_queue import HitlQueue

    # All three are read-only enumerations (get_pending / count_approved /
    # recover_orphaned_inflight); we do NOT call claim_approved (it MUTATES
    # approved→inflight). PR B: count_approved() is the new read-only accessor for the  # noqa: E501
    # approved-but-undrained depth, replacing PR A's ``approved: None``.
    pending = await HitlQueue.get_pending()
    approved = await HitlQueue.count_approved()
    orphans = await HitlQueue.recover_orphaned_inflight()
    ny_date = _dt.now(timezone.utc).strftime("%Y-%m-%d")
    day_used = await HitlDayNotional.current(ny_date)
    return {
        "enabled": True,
        "pending": len(pending),
        "approved": approved,
        "inflight": len(orphans),
        "day_notional_used": day_used,
        "day_notional_limit": getattr(config, "HITL_MAX_VALUE_PER_DAY", 0.0),
    }


def _collect_risk() -> dict:
    """Live RiskManager state (null-safe: absent manager/attributes → None)."""
    rm = (
        getattr(engine, "live_risk_manager", None) if engine is not None else None
    )  # noqa: E501
    if rm is None:
        return {
            "daily_drawdown_limit_pct": None,
            "trading_reduced": None,
            "trading_halted": None,
            "portfolio_stop_triggered": None,
        }
    ddp = getattr(rm, "daily_drawdown_limit_percent", None)
    return {
        "daily_drawdown_limit_pct": (
            round(ddp * 100.0, 2) if ddp is not None else None
        ),  # noqa: E501
        "trading_reduced": getattr(rm, "trading_reduced", None),
        "trading_halted": getattr(rm, "trading_halted", None),
        "portfolio_stop_triggered": getattr(
            rm, "_portfolio_stop_triggered", None
        ),  # noqa: E501
    }


def _collect_compliance() -> dict:
    """ComplianceGuardian limits + in-window trade counts (null-safe when absent)."""  # noqa: E501
    g = (
        getattr(engine, "compliance_guardian", None) if engine is not None else None
    )  # noqa: E501
    if g is None:
        return {
            "daily_trades_used": None,
            "daily_trades_limit": None,
            "max_order_value": None,
            "wash_trade_window_seconds": None,
            "recent_trades_in_window": None,
        }
    return {
        "daily_trades_used": getattr(g, "daily_trades", None),
        "daily_trades_limit": getattr(g, "max_daily_trades", None),
        "max_order_value": getattr(g, "max_order_value", None),
        "wash_trade_window_seconds": getattr(
            g, "_wash_trade_window_seconds", None
        ),  # noqa: E501
        "recent_trades_in_window": len(getattr(g, "_recent_trades", []) or []),
    }


async def _collect_db() -> dict:
    """Persistence reachability: Cloud SQL connected flag + Redis reachability/mode."""  # noqa: E501
    from core.cloud_logger import get_cloud_logger
    from core.redis_client import RedisClient, _is_local_mode

    cloud_sql_connected = None
    try:
        cloud_sql_connected = bool(get_cloud_logger().is_connected)
    except Exception:  # noqa: BLE001 — a cloud-logger fault must not fail the field
        cloud_sql_connected = None

    local = _is_local_mode()
    try:
        redis_reachable = await RedisClient.check_health()
    except Exception:  # noqa: BLE001
        redis_reachable = False
    return {
        "cloud_sql_connected": cloud_sql_connected,
        "redis_reachable": bool(redis_reachable),
        "redis_mode": "local" if local else "redis",
    }


def _collect_execution() -> dict:
    """PR A.2 (Tier-1): fail-safe execution counters from the order-executor hot path.  # noqa: E501

    Read-only snapshot — the counters are pure observation and never affect a submit.  # noqa: E501
    ``last_fill_age_seconds`` is derived from the last observed fill timestamp (None if  # noqa: E501
    no fill has been seen this process).
    """
    from core.engine.order_executor import get_exec_counters

    c = get_exec_counters()
    last_fill_ts = c.get("last_fill_ts")
    last_fill_age = (
        round(time.time() - last_fill_ts, 1)
        if last_fill_ts is not None
        else None  # noqa: E501
    )
    return {
        "submit_ok": c.get("submit_ok", 0),
        "submit_fail": c.get("submit_fail", 0),
        "retry_count": c.get("retry_count", 0),
        "last_fill_age_seconds": last_fill_age,
        "shadow_mode": c.get("shadow_mode"),
    }


def _collect_compliance_decisions() -> dict:
    """PR A.2 (Tier-1): fail-safe GO/NO-GO counts + top MACHINE reject codes.

    ``top_reject_reasons`` are machine reason strings only (never symbol/order content),  # noqa: E501
    ranked by frequency and capped at the five most common.
    """
    from core.compliance import get_compliance_counters

    c = get_compliance_counters()
    reasons = c.get("reject_reasons", {}) or {}
    top = dict(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:5])
    return {
        "go_count": c.get("go_count", 0),
        "nogo_count": c.get("nogo_count", 0),
        "top_reject_reasons": top,
    }


def _collect_audit_write() -> dict:
    """PR A.2 (Tier-1): fail-safe Senate audit-write ok/fail counters."""
    from core.round_table.senate_log import get_audit_counters

    c = get_audit_counters()
    return {
        "senate_write_ok": c.get("write_ok", 0),
        "senate_write_fail": c.get("write_fail", 0),
    }


def _collect_decision() -> dict:
    """PR C (Tier-1, VC-2): Round-Table decision HEALTH — the decision-activity view.  # noqa: E501

    Read-only snapshot of the fail-safe decision counters: the consensus verdict  # noqa: E501
    distribution ({buy, sell, no_trade}), how many round tables ran, the age of the last  # noqa: E501
    consensus, and the bounded per-agent vote-failure map (agent CLASS names only). The  # noqa: E501
    counters are pure observation on the VC-2 path — they never affect a verdict/vote.  # noqa: E501
    MACHINE-only: aggregate counts + agent names + a derived age; never symbols/scores/  # noqa: E501
    per-symbol verdicts.
    """
    from core.round_table.runner import get_decision_counters

    c = get_decision_counters()
    last_ts = c.get("last_consensus_ts")
    last_age = round(time.time() - last_ts, 1) if last_ts is not None else None
    outcomes = c.get("consensus_outcomes", {}) or {}
    return {
        "consensus_outcomes": {
            "buy": outcomes.get("buy", 0),
            "sell": outcomes.get("sell", 0),
            "no_trade": outcomes.get("no_trade", 0),
        },
        "round_tables_run": c.get("round_tables_run", 0),
        "last_consensus_age_seconds": last_age,
        "agent_vote_failures": dict(c.get("agent_vote_failures", {}) or {}),
    }


def _collect_llm() -> dict:
    """PR D (Tier-2, VC-1): LLM machine health — fail-safe timing counters + read-only  # noqa: E501
    provider/model identity. NO live network probe (config/getattr only). MACHINE-only:  # noqa: E501
    provider/model NAMES, latencies (ms), error CLASS names, counts, booleans — NEVER  # noqa: E501
    prompt/response text or API-key material.
    """
    from core.llm.health import resolved_model_name, resolved_provider_name
    from core.llm.telemetry import get_llm_counters

    out = dict(get_llm_counters())
    out["llm_provider"] = resolved_provider_name()
    # #2962: provider-TRUE model identity (was: unconditional GEMINI_MODEL_NAME —
    # at LLM_PROVIDER=ollama the diagnostics reported a model the engine never
    # asked for). Same env/config-only contract, no live probe.
    out["llm_model_name"] = resolved_model_name(out["llm_provider"])
    # gemini_available: config flag only (no key material, no live call).
    out["gemini_available"] = bool(getattr(config, "GEMINI_AVAILABLE", False))
    # Budget is cheaply readable (in-memory counters); guarded so a budget fault  # noqa: E501
    # degrades its two fields to None without failing the subsystem.
    try:
        from core.gemini_budget import get_budget

        budget = get_budget()
        out["gemini_budget_remaining"] = budget.remaining()
        out["gemini_budget_exhausted"] = bool(budget.is_exhausted)
    except Exception:  # noqa: BLE001 — a budget fault must not fail the llm subsystem
        out["gemini_budget_remaining"] = None
        out["gemini_budget_exhausted"] = None
    return out


def _collect_models() -> dict:
    """PR D (Tier-2, VC-1): ML model health — read-only, null-safe readouts from the  # noqa: E501
    active strategy plus the fail-safe ml-fallback counter. NO inference/live call.  # noqa: E501
    MACHINE-only: booleans, a version string, a device string, feature COUNT.
    """
    from core.strategies.rl_signal import get_ml_fallback_count

    strat = (
        getattr(engine, "active_strategy", None) if engine is not None else None
    )  # noqa: E501
    torch_model = (
        getattr(strat, "torch_model", None) if strat is not None else None
    )  # noqa: E501
    features = (
        getattr(strat, "features_list", None) if strat is not None else None
    )  # noqa: E501
    device = getattr(strat, "device", None) if strat is not None else None
    return {
        "lstm_model_loaded": torch_model is not None,
        "rl_model_loaded": getattr(strat, "rl_model", None) is not None,
        "rl_model_version": getattr(strat, "_rl_model_version", None),
        "torch_device": str(device) if device is not None else None,
        "vec_normalize_loaded": getattr(strat, "vec_normalize", None)
        is not None,  # noqa: E501
        "lstm_feature_count": (
            len(features) if features is not None else None
        ),  # noqa: E501
        "ml_fallback_count": get_ml_fallback_count(),
    }


def _collect_data_providers() -> dict:
    """PR E (Tier-2, VC-1): market-data feed HEALTH — surfaces silent feed degradation.  # noqa: E501

    Read-only snapshot of the fail-safe data-provider counters: the per-source OHLCV  # noqa: E501
    waterfall stats (alpaca / databento / polygon {ok, fail, last_error_ts}), VIX/regime  # noqa: E501
    freshness (from cached regime state — NO live fetch), the last resolved symbol  # noqa: E501
    universe (source + aggregate count), and the specialist free-API per-source ok/fail  # noqa: E501
    map. All counters are pure observation on the data path — they never affect a fetch  # noqa: E501
    or its fallback. MACHINE-only: source NAMES, counts, timestamps, booleans, ages —  # noqa: E501
    never symbols, prices, or order content.
    """
    from core.data_provider_telemetry import (
        get_data_source_stats,
        get_regime_freshness,
        get_specialist_source_stats,
        get_universe_state,
    )

    freshness = get_regime_freshness()
    universe = get_universe_state()
    return {
        "sources": get_data_source_stats(),
        "vix_present": freshness.get("vix_present", False),
        "vix_regime_age_seconds": freshness.get("vix_regime_age_seconds"),
        "universe_source": universe.get("universe_source"),
        "universe_count": universe.get("universe_count"),
        "specialist_sources": get_specialist_source_stats(),
    }


def _collect_usage() -> dict:
    """PR F (§6): ANONYMOUS usage analytics — *how* the app is used (VC-1/2/3/5/6).  # noqa: E501

    Merges the anonymous action/api-hit counters from ``core.usage_counters`` (this  # noqa: E501
    PR's fail-safe instruments) with the READ-ONLY loop/decision/exec counters that  # noqa: E501
    earlier PRs already ship — REFERENCED, never re-instrumented:
      * ``scans_run``        ← engine ``_scans_completed`` (loop counter, PR B)
      * ``round_tables_run`` / ``consensus_outcomes`` ← runner ``get_decision_counters`` (PR C)  # noqa: E501
      * ``orders_submitted`` ← order_executor ``get_exec_counters()['submit_ok']`` (PR A.2)  # noqa: E501
    Each cross-reference is guarded so a missing/faulty source degrades to a null/0  # noqa: E501
    slice, never raising out of the fail-soft diagnostics surface.

    PRIVACY (DSGVO): anonymous + machine-only — aggregate INTEGER counters keyed by  # noqa: E501
    fixed action names + ROUTE TEMPLATES; NEVER a user_id / raw path / query / symbol  # noqa: E501
    / order content / IP / PII. Everything is LOCAL: this subsystem adds NO egress —  # noqa: E501
    opt-in egress of these aggregates is separate epic work (#1457 / #1458).
    """
    usage = get_usage_counters()

    # scans_run — READ from the existing PR-B loop counter (null-safe when no engine).  # noqa: E501
    scans_run = (
        getattr(engine, "_scans_completed", None)
        if engine is not None
        else None  # noqa: E501
    )

    # decision counters — READ from the existing PR-C runner accessor (fail-soft).  # noqa: E501
    round_tables_run = None
    consensus_outcomes: dict = {}
    try:
        from core.round_table.runner import get_decision_counters

        dc = get_decision_counters()
        round_tables_run = dc.get("round_tables_run", 0)
        consensus_outcomes = dict(dc.get("consensus_outcomes", {}) or {})
    except Exception:  # noqa: BLE001 — a faulty source degrades to null, never raises
        pass

    # orders_submitted — READ from the existing PR-A.2 execution counters (fail-soft).  # noqa: E501
    orders_submitted = None
    try:
        from core.engine.order_executor import get_exec_counters

        orders_submitted = get_exec_counters().get("submit_ok", 0)
    except Exception:  # noqa: BLE001
        pass

    return {
        "api_hits": usage.get("api_hits", {}),
        "strategy_swaps": usage.get("strategy_swaps", 0),
        "panic_sells": usage.get("panic_sells", 0),
        "kill_switch_resets": usage.get("kill_switch_resets", 0),
        "force_cycles": usage.get("force_cycles", 0),
        "hitl_approvals": usage.get("hitl_approvals", 0),
        "scans_run": scans_run,
        "round_tables_run": round_tables_run,
        "orders_submitted": orders_submitted,
        "consensus_outcomes": consensus_outcomes,
    }


@app.get("/engine-diagnostics", dependencies=[Depends(require_engine_key)])
async def engine_diagnostics():
    """ADR-OBS-01 (PR A): aggregated, privacy-safe machine-health view of the core engine.  # noqa: E501

    ALWAYS returns HTTP 200 while the process lives — health is carried ONLY in
    ``overall_status`` so monitoring can always parse the body. Each subsystem is  # noqa: E501
    fail-soft (a raising collector becomes ``{"_error": ...}``) and every field is  # noqa: E501
    null-safe. Auth: engine key (like /system-health).
    """
    process = await _safe_collect(_collect_process)
    loops = await _safe_collect(_collect_loops)
    watchdogs = await _safe_collect(_collect_watchdogs)
    kill_switch_sub = await _safe_collect(_collect_kill_switch)
    governance = await _safe_collect(_collect_governance)
    hitl = await _safe_collect(_collect_hitl)
    risk = await _safe_collect(_collect_risk)
    compliance = await _safe_collect(_collect_compliance)
    db = await _safe_collect(_collect_db)
    execution = await _safe_collect(_collect_execution)
    compliance_decisions = await _safe_collect(_collect_compliance_decisions)
    audit_write = await _safe_collect(_collect_audit_write)
    decision = await _safe_collect(_collect_decision)
    llm = await _safe_collect(_collect_llm)
    models = await _safe_collect(_collect_models)
    data_providers = await _safe_collect(_collect_data_providers)
    usage = await _safe_collect(_collect_usage)

    # Derive the shared verdict from whatever the loops collector could read (fail-soft:  # noqa: E501
    # a broken loops subsystem degrades cleanly to the 'starting'/'inactive' path).  # noqa: E501
    # PR A.2: a failing audit-write is a compliance-critical signal — surface it as a  # noqa: E501
    # 'degraded' component (trivially safe: never a 500, endpoint stays always-200).  # noqa: E501
    audit_failing = (
        isinstance(audit_write, dict)
        and (audit_write.get("senate_write_fail", 0) or 0) > 0
    )
    overall_status = compute_overall_status(
        {
            "engine_ready": engine is not None,
            "strategy_running": loops.get("trading_loop_running", False),
            "scan_active": loops.get("scan_active", False),
            # No cheap cached market-open flag here (no live-broker calls) → omit, so a  # noqa: E501
            # non-scanning idle engine is 'inactive', never falsely 'stalled'.
            "is_market_open": False,
            "components_degraded": audit_failing,
        }
    )

    return {
        "overall_status": overall_status,
        "engine_ready": engine is not None,
        "generated_at": time.time(),
        "process": process,
        "loops": loops,
        "watchdogs": watchdogs,
        "kill_switch": kill_switch_sub,
        "governance": governance,
        "hitl": hitl,
        "risk": risk,
        "compliance": compliance,
        "db": db,
        "execution": execution,
        "compliance_decisions": compliance_decisions,
        "audit_write": audit_write,
        "decision": decision,
        "llm": llm,
        "models": models,
        "data_providers": data_providers,
        "usage": usage,
    }


# --- INF-8: Staging Quality Gate ---


@app.get(
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
    staging_env = getattr(config, "STAGING_ENV", False)
    shadow_mode = getattr(config, "SHADOW_MODE", False)

    # Check Redis connectivity
    try:
        redis_ok = await RedisClient.check_health()
    except Exception:
        redis_ok = False

    result = {
        "shadow_mode": shadow_mode,
        "staging_env": staging_env,
        "redis": "ok" if redis_ok else "error",
        "strategy_active": getattr(config, "AUTO_START_STRATEGY", False),
        "engine_ready": engine is not None,
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


# --- Strategy Control ---


@app.post("/start-live")
async def start_live(_: None = Depends(require_engine_key)):  # noqa: B008
    # Startup-race guard (P1): `engine` is None until the fire-and-forget lifespan init
    # task (create_task(_init_engine_async)) finishes — the documented "None means still
    # starting up" contract (see engine=None declaration above). Pressing "Start" during
    # that window used to dereference None → AttributeError → HTTP 500 → the desktop UI
    # showed "failed to fetch" / "system doesn't start". Degrade gracefully with the SAME
    # {"status": "error", "message": ...} shape the failure path below already returns.
    if engine is None:
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
    result = await asyncio.to_thread(engine.start_live_strategy)
    if result is False:
        return {
            "status": "error",
            "message": "Engine failed to start. Check Alpaca API connection and secrets.",  # noqa: E501
        }
    # ADR-SEC-06 §1 (#1619): apply the persisted policy to the freshly-created guardians so an  # noqa: E501
    # admin's runtime change SURVIVES a restart (otherwise they reset to config defaults).  # noqa: E501
    try:
        stored = await _load_iron_dome_policy_value()
        apply_policy(
            stored,
            [
                getattr(engine, "compliance_guardian", None),
                getattr(engine, "live_risk_manager", None),
                getattr(engine, "sim_risk_manager", None),
            ],
        )
    except (
        Exception
    ) as exc:  # boot resilience — never block live start on a policy reload
        logging.warning("Iron Dome boot-load failed (non-fatal): %s", exc)
    return {"status": "success", "message": "Live strategy started."}


@app.post("/stop")
async def stop(_: None = Depends(require_engine_key)):  # noqa: B008
    # NOTE: do NOT write a live_enablement WORM record here. Stopping the strategy is not a
    # change of live-trading authorization; the desktop boot gate (verifyAuditChain) treats
    # the latest live_enablement with action!="enable" as a REVOCATION, so emitting one on a
    # routine stop silently demotes a live operator to paper on the next boot. Operator halts
    # are already audited via core/kill_switch.py (kill_switch_audit.log). (#1983 regression fix)
    # Startup-race guard (P1): same "None means still starting up" contract as /start-live —
    # a /stop during the init window must not dereference None → AttributeError → HTTP 500.
    if engine is None:
        return {
            "status": "error",
            "message": "Engine is still starting up — nothing to stop yet.",
        }
    engine.stop_strategy()
    return {"status": "success"}


@app.get("/strategy")
async def get_strategy():
    return {"strategy": getattr(config, "ACTIVE_STRATEGY", "RLAgent")}


@app.get("/market-regime")
async def get_market_regime():
    """Read-only: the engine's latest market regime + VIX (cached by the monitor loop).  # noqa: E501

    Surfaces ``engine.current_market_data`` so the demo snapshot runner (#1618) can report real  # noqa: E501
    values instead of defaults. No state change; defaults cleanly while the engine is starting.  # noqa: E501
    """
    if engine is None:
        return {"regime": "Ranging", "vix": None, "indicator": "Unavailable"}
    md = getattr(engine, "current_market_data", None) or {}
    return {
        "regime": md.get("regime", "Ranging"),
        "vix": md.get("vix"),
        "indicator": "live" if md.get("regime") else "Default",
    }


@app.get("/risk-limits")
async def get_risk_limits():
    """Read-only: the authoritative daily-drawdown limit as a PERCENT (e.g. 17.5).  # noqa: E501

    R6-3c (#1698): sources the LIVE Iron Dome policy (same path as the admin write handler) so  # noqa: E501
    the public demo snapshot can render the drawdown card. FAIL-CLOSED — on ANY error return  # noqa: E501
    ``None`` (never fabricate a percentage, never 500 the snapshot); the card is then omitted.  # noqa: E501
    """
    try:
        stored = await _load_iron_dome_policy_value()
        policy = load_policy(stored)
        return {
            "daily_drawdown_limit_pct": round(policy.daily_drawdown_pct * 100.0, 2)
        }  # noqa: E501
    except (
        Exception
    ):  # noqa: BLE001 — fail-closed: never fabricate, never 500 the snapshot
        return {"daily_drawdown_limit_pct": None}


@app.post("/set-strategy")
async def set_strategy(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    name = (p.get("strategy") or "").strip()
    if name not in strategies.STRATEGY_CLASSES:
        return {
            "status": "error",
            "message": f"Unknown strategy. Use one of: {list(strategies.STRATEGY_CLASSES.keys())}",  # noqa: E501
        }
    config.ACTIVE_STRATEGY = name
    logging.info("Strategy mode set to: %s", name)
    return {"status": "success", "strategy": name}


# --- Hot-Swap API (Epic 2.3-Pre / PR-C) ---

from pydantic import BaseModel  # noqa: E402

from core.agent_registry import get_global_registry  # noqa: E402
from core.cloud_logger import get_cloud_logger  # noqa: E402
from core.exceptions import SwapInProgressError  # noqa: E402


class SwapRequest(BaseModel):
    strategy_name: str
    shadow_mode: bool = False
    force: bool = (
        False  # Bypass Position Lock (Shadow-Mode empfohlen bei force=True)  # noqa: E501
    )


def verify_firebase_token(request: Request) -> dict:
    """Auth-Guard: verifiziert Auth-Token.

    In Tests wird diese Funktion via pytest.monkeypatch oder patch() überschrieben.  # noqa: E501
    In Produktion nutzt sie den AuthProvider.
    """
    from core.auth_interfaces import get_auth_provider

    user_context = get_auth_provider().verify_token(request)
    return {"uid": user_context.uid}


@app.post(
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
    if engine is not None and engine.api and not req.force:
        try:
            positions = engine.api.list_positions()
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
    registry = get_global_registry()
    if registry is None and engine is not None:
        registry = engine.agent_registry
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
        get_cloud_logger().log_swap_event(
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
    bump_usage("strategy_swaps")
    return {"success": True, "pending": req.strategy_name}


# --- GTM-1 (#1840) tier-upgrade checkout (Brick 1) ---------------------------------
class CheckoutRequest(BaseModel):
    tier: str  # canonical Tier string, e.g. "PRO" / "PROFESSIONAL"


@app.post(
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
        checkout_url = create_checkout_session(tier)
    except ValueError as exc:
        # Non-purchasable tier or unconfigured price id — a client error, not a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"checkout_url": checkout_url}


@app.post("/api/entitlement/webhook")
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


@app.post("/api/entitlement/lemonsqueezy-webhook")
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


@app.get("/api/entitlement/license")
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


class ActivateRequest(BaseModel):
    token: str  # the signed Ed25519 license key the user pastes after checkout


@app.post(
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


# Alias für Tests (TestClient braucht einen router)
router = app.router


# --- Diagnostics ---


@app.get(
    "/diagnostics",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def diagnostics():
    rl_loaded = False
    if engine.active_strategy and hasattr(engine.active_strategy, "rl_model"):
        rl_loaded = engine.active_strategy.rl_model is not None
    lstm_loaded = False
    if engine.active_strategy and hasattr(
        engine.active_strategy, "torch_model"
    ):  # noqa: E501
        lstm_loaded = engine.active_strategy.torch_model is not None
    return {
        "alpaca_connected": engine.api is not None,
        "strategy_running": engine.strategy_running.is_set(),
        "active_strategy": (
            type(engine.active_strategy).__name__
            if engine.active_strategy
            else None  # noqa: E501
        ),
        "lstm_model_loaded": lstm_loaded,
        "rl_model_loaded": rl_loaded,
        "has_portfolio_manager": bool(
            getattr(engine.active_strategy, "portfolio_manager", None)
        ),
        "has_trade_intelligence": bool(
            getattr(engine.active_strategy, "trade_intelligence", None)
        ),
        # SECURITY: API-Key-Details nicht exponieren
        "config_api_key_set": bool(config.API_KEY),
    }


# --- Emergency ---


@app.post("/panic-sell")
async def panic_sell(_: None = Depends(require_engine_key)):  # noqa: B008
    # RTS 6 Art. 5 (LIVE-1 T3, #1426): HALT all algorithms FIRST — before and independent of the  # noqa: E501
    # broker liquidation below — so the trading loop / order_executor (check_halt, order_executor.py  # noqa: E501
    # :654) cannot place or re-enter an order during/after the emergency. The halt persists until an  # noqa: E501
    # explicit /reset-kill-switch, even if the broker is unreachable.
    from core.kill_switch import kill_switch

    kill_switch.trip("panic-sell: operator emergency halt (RTS 6 Art. 5)")
    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the halt).  # noqa: E501
    bump_usage("panic_sells")
    try:
        if engine.api:
            engine.api.cancel_orders()
            logging.warning("🚨 PANIC SELL: Cancelled all open orders")
            positions = engine.api.get_all_positions()
            sell_count = 0
            pdt_blocked = 0
            for position in positions:
                try:
                    qty = float(
                        position.qty_available
                        if hasattr(position, "qty_available")
                        else position.qty
                    )
                    whole_shares = int(qty)
                    if whole_shares >= 1:
                        try:
                            order_data = MarketOrderRequest(
                                symbol=position.symbol,
                                qty=whole_shares,
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.GTC,
                            )
                            engine.api.submit_order(order_data)
                            sell_count += 1
                        except Exception as gtc_err:
                            if "pattern day trading" in str(gtc_err).lower():
                                pdt_blocked += 1
                            else:
                                raise gtc_err
                    else:
                        logging.warning(
                            f"⚠️ {position.symbol}: Only {qty:.4f} fractional shares"  # noqa: E501
                        )
                except Exception as e:
                    logging.error(f"Failed to sell {position.symbol}: {e}")
            msg = f"{sell_count} positions sold"
            if pdt_blocked > 0:
                msg += f", {pdt_blocked} blocked by PDT"
            return {"status": "success", "message": msg}
        else:
            return {"status": "error", "message": "No API connection"}
    except Exception as e:
        logging.error("panic_sell failed: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


@app.post("/reset-kill-switch")
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
        bump_usage("kill_switch_resets")
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


@app.post("/api/admin/iron-dome-policy")
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

    old_policy = await _load_iron_dome_policy_value()
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
    await record_iron_dome_policy_change(old_policy, effective, actor=actor)
    await _save_iron_dome_policy(effective)
    _apply_iron_dome_policy_live(effective)
    logging.info("Iron Dome policy updated via admin endpoint: %s", effective)


@app.post("/api/admin/iron-dome-policy/propose")
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


@app.post("/api/admin/iron-dome-policy/approve")
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
        old = await _load_iron_dome_policy_value()
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
        target = getattr(engine, attr, None)
        if target is not None and hasattr(target, "reload_policy"):
            target.reload_policy(policy_dict)


async def _load_iron_dome_policy_value() -> dict:
    """Return the currently-stored Iron Dome policy value (for the audit old->new), or {}."""  # noqa: E501
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(SystemConfig).filter_by(config_key=CONFIG_KEY)
        )
        row = result.scalars().first()
        return row.config_value if row else {}


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


# --- Market Data & Portfolio ---


@app.get(
    "/top-picks",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_top_picks():
    return {
        "status": "success",
        "picks": getattr(engine, "_last_top_picks", []),
    }  # noqa: E501


def _enum_value_lower(x: object) -> str:
    """Normalise an Alpaca enum-or-str field to its lowercase value.

    alpaca-py fields like ``Order.status`` / ``Order.side`` are ``(str, Enum)``
    members whose ``str()`` is ``"OrderStatus.FILLED"`` (Py3.11+ ``Enum.__str__``),
    NOT the value ``"filled"`` — so ``str(o.status).lower() == "filled"`` never
    matched and ``/recent-trades`` returned 0 fills despite open positions. Reading
    ``.value`` (enum) or falling back to the object itself (plain string) fixes
    both the status filter and the returned ``side``.
    """
    return str(getattr(x, "value", x)).lower()


async def _resolve_orders_client(request: Request):
    """Alpaca TradingClient for order queries — the engine's shared client, or a
    per-user OAuth client when an X-User-Id maps to a stored wallet (multi-tenant)."""
    api_client = engine.api
    user_id = request.headers.get("X-User-Id")
    if user_id:
        try:
            wallet = await wallet_store.get_wallet(user_id)
            if wallet and wallet.get("secret_manager_id"):
                tokens = oauth_secrets.get_tokens(wallet["secret_manager_id"])
                if tokens and tokens.get("access_token"):
                    is_paper = "paper" in config.BASE_URL.lower()
                    api_client = TradingClient(
                        oauth_token=tokens["access_token"], paper=is_paper
                    )
        except Exception:
            pass
    return api_client


# Sort-key fallback for a missing/None order timestamp. MUST be a tz-aware datetime (not "") — in
# Python 3 `sorted()` comparing a datetime against a str raises TypeError, which would fail-soft the
# /open-orders, /activities and /recent-trades endpoints to empty whenever ANY order lacks a stamp.
_SORT_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _order_to_trade(o: object) -> dict:
    """Map an Alpaca Order to the trade dict of /recent-trades and /activities."""
    filled_at = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
    return {
        "id": str(getattr(o, "id", "")),
        "symbol": str(getattr(o, "symbol", "")),
        "side": _enum_value_lower(getattr(o, "side", "")),
        "qty": float(getattr(o, "filled_qty", None) or getattr(o, "qty", 0) or 0),
        "price": float(getattr(o, "filled_avg_price", None) or 0),
        "filled_at": str(filled_at) if filled_at else None,
    }


def _order_to_open_order(o: object) -> dict:
    """Map an Alpaca OPEN/working Order to the /open-orders dict (display-only, #2137).

    Distinct from _order_to_trade: an open order has no fill price yet, but carries its intent
    (order type + limit/stop) and its live status. All numeric fields are None-safe; qty keeps its
    fractional precision (the console renders it with fmtQty)."""

    def _num(v):
        return float(v) if v is not None else None

    submitted = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
    return {
        "id": str(getattr(o, "id", "")),
        "symbol": str(getattr(o, "symbol", "")),
        "side": _enum_value_lower(getattr(o, "side", "")),
        "qty": float(getattr(o, "qty", None) or 0),
        "type": _enum_value_lower(
            getattr(o, "order_type", None) or getattr(o, "type", "")
        ),
        "limit_price": _num(getattr(o, "limit_price", None)),
        "stop_price": _num(getattr(o, "stop_price", None)),
        "status": _enum_value_lower(getattr(o, "status", "")),
        "submitted_at": str(submitted) if submitted else None,
    }


@app.get(
    "/recent-trades",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_recent_trades(request: Request, limit: int = 20):
    """Return the last N filled orders from Alpaca (single 500-order window)."""
    try:
        api_client = await _resolve_orders_client(request)

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        orders = await asyncio.to_thread(api_client.get_orders, req)
        filled = [
            o for o in orders if _enum_value_lower(getattr(o, "status", "")) == "filled"
        ]
        filled = sorted(
            filled,
            key=lambda o: getattr(o, "filled_at", None)
            or getattr(o, "submitted_at", None)
            or _SORT_EPOCH,
            reverse=True,
        )[:limit]

        trades = [_order_to_trade(o) for o in filled]
        return {"status": "success", "trades": trades}
    except Exception as e:
        logging.error("recent_trades failed: %s", e, exc_info=True)
        return {"status": "error", "trades": [], "message": "internal_error"}


@app.get(
    "/activities",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_activities(request: Request, max_orders: int = 10000):
    """Return the FULL filled-order history from Alpaca (paginated).

    Unlike /recent-trades (a single 500-order window), this pages backwards
    through get_orders until the oldest order, so no fill is silently dropped once
    the account exceeds 500 orders. ``truncated`` is true if the page-count safety
    cap was reached before the history was exhausted.
    """
    try:
        api_client = await _resolve_orders_client(request)

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        page_size = 500
        max_pages = max(1, (max_orders + page_size - 1) // page_size)
        seen: set = set()
        orders: list = []
        until = None
        truncated = True
        for _ in range(max_pages):
            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL, limit=page_size, until=until
            )
            page = await asyncio.to_thread(api_client.get_orders, req)
            fresh = [o for o in page if str(getattr(o, "id", "")) not in seen]
            for o in fresh:
                seen.add(str(getattr(o, "id", "")))
                orders.append(o)
            # A short page (or no new ids) means we reached the oldest order.
            if len(page) < page_size or not fresh:
                truncated = False
                break
            # Advance the cursor to the oldest order in this page and page again.
            until = min(
                (
                    getattr(o, "submitted_at", None)
                    for o in page
                    if getattr(o, "submitted_at", None) is not None
                ),
                default=None,
            )
            if until is None:
                truncated = False
                break

        filled = [
            o for o in orders if _enum_value_lower(getattr(o, "status", "")) == "filled"
        ]
        filled = sorted(
            filled,
            key=lambda o: getattr(o, "filled_at", None)
            or getattr(o, "submitted_at", None)
            or _SORT_EPOCH,
            reverse=True,
        )
        trades = [_order_to_trade(o) for o in filled]
        return {
            "status": "success",
            "trades": trades,
            "count": len(trades),
            "truncated": truncated,
        }
    except Exception as e:
        logging.error("activities failed: %s", e, exc_info=True)
        return {
            "status": "error",
            "trades": [],
            "count": 0,
            "truncated": False,
            "message": "internal_error",
        }


@app.get(
    "/open-orders",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_open_orders(request: Request):
    """Return the operator's currently OPEN / working orders from Alpaca (#2137, display-only).

    Mirrors /activities' tenant resolution (``_resolve_orders_client``) and fail-soft posture. Open
    orders are few, so a single OPEN-status window suffices (no pagination); newest first. Never
    raises → ``{status:"error", orders:[]}``, so a broker/engine hiccup can never blank the page.
    This is the console's "live working orders" view; it does NOT cancel or place orders.
    """
    try:
        api_client = await _resolve_orders_client(request)
        if not api_client:
            return {"status": "error", "orders": [], "count": 0}

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        orders = await asyncio.to_thread(api_client.get_orders, req)
        orders = sorted(
            orders,
            key=lambda o: getattr(o, "submitted_at", None) or _SORT_EPOCH,
            reverse=True,
        )
        out = [_order_to_open_order(o) for o in orders]
        return {"status": "success", "orders": out, "count": len(out)}
    except Exception as e:
        logging.error("open-orders failed: %s", e, exc_info=True)
        return {
            "status": "error",
            "orders": [],
            "count": 0,
            "message": "internal_error",
        }


@app.post(
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
        api_client = await _resolve_orders_client(request)

        import asyncio

        await asyncio.to_thread(api_client.cancel_order_by_id, order_id)
        # Art-14 evidence: seal the manual intervention onto the tamper-evident WORM hash chain
        # after the broker confirms the cancel. Best-effort (never blocks this safety action).
        await hitl_gate.log_manual_cancel_event(order_id=order_id, actor="operator")
        logging.warning(
            "HITL: operator cancelled open order %s (EU AI Act Art. 14 oversight).",
            order_id,
        )
        return {"status": "success", "order_id": order_id}
    except Exception as e:
        logging.error("cancel-order failed: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


@app.get(
    "/recent-news",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_recent_news():
    return {
        "status": "success",
        "articles": getattr(engine, "_recent_news_cache", [])[-50:],
    }


@app.get(
    "/compliance-status",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_compliance_status():
    if not engine.compliance_guardian:
        return {
            "status": "success",
            "enabled": False,
            "message": "ComplianceGuardian is disabled.",
        }
    g = engine.compliance_guardian
    return {
        "status": "success",
        "enabled": True,
        "max_order_value": g.max_order_value,
        "max_daily_trades": g.max_daily_trades,
        "daily_trades_today": g.daily_trades,
        "restricted_symbols": g.restricted_list,
        "wash_trade_window_seconds": g._wash_trade_window_seconds,
        "recent_trades_in_window": len(g._recent_trades),
    }


def _stock_history_days(range_key: str) -> int:
    r = (range_key or "1m").strip().lower()
    return {"1d": 2, "1w": 7, "1m": 30, "1y": 365, "max": 1825}.get(r, 30)


@app.get(
    "/stock-history",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_stock_history(symbol: str = "", period: str = "1m"):
    if not symbol or not symbol.strip():
        return {"status": "error", "message": "Missing symbol"}
    symbol = symbol.strip().upper()
    # C9/SEC M7 (#2375): validate ticker shape before it reaches the data provider's cache path
    # (os.path.join(DATA_CACHE_DIR, f"{symbol}.parquet")) — blocks path traversal. Same rule as
    # /force-cycle: starts alpha, alnum/./- only, <= 10 chars (no path separators possible).
    if not (
        1 <= len(symbol) <= 10
        and symbol[0].isalpha()
        and all(c.isalnum() or c in ".-" for c in symbol)
    ):
        return {
            "status": "error",
            "symbol": symbol,
            "message": "Invalid symbol",
            "data": [],
        }
    days = _stock_history_days(period)
    end_date = datetime.now(timezone.utc)
    try:
        df = engine.data_provider.get_data(symbol, end_date, days=days)
        if df is None or df.empty:
            return {
                "status": "success",
                "symbol": symbol,
                "range": period,
                "data": [],
                "message": "No data",
            }
        df = df.sort_index()
        data = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row.get("volume", 0) or 0),
            }
            for idx, row in df.iterrows()
        ]
        return {
            "status": "success",
            "symbol": symbol,
            "range": period,
            "data": data,
        }  # noqa: E501
    except Exception as e:
        logging.error(
            "stock_history failed for %s: %s", symbol, e, exc_info=True
        )  # noqa: E501
        return {
            "status": "error",
            "symbol": symbol,
            "message": "internal_error",
            "data": [],
        }


def _json_safe(obj):
    """Coerce a value tree to JSON-native types for FastAPI response serialization.  # noqa: E501

    numpy.float32 does NOT subclass Python float, so FastAPI's jsonable_encoder cannot  # noqa: E501
    serialize it and raises AFTER the handler returns (outside its try/except) -> a bare  # noqa: E501
    HTTP 500. This walks dicts/lists, converts numpy scalars via ``.item()``, and maps  # noqa: E501
    non-finite floats (NaN/Inf) to None; JSON-native values pass through unchanged.  # noqa: E501
    Fixes the /portfolio-summary 500 when the strategy is active and enriches positions  # noqa: E501
    with numpy-derived scores (momentum/conviction/total_score).
    """
    import math

    import numpy as np

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


@app.get(
    "/portfolio-summary",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_portfolio_summary(request: Request):  # noqa: C901
    try:
        active = getattr(engine, "active_strategy", None)
        user_id = request.headers.get("X-User-Id")

        # Fallback to the engine's global client, but try to load the user's personal client  # noqa: E501
        api_client = engine.api
        api_message = "No portfolio manager active"

        if user_id:
            try:
                wallet = await wallet_store.get_wallet(user_id)
                if wallet and wallet.get("secret_manager_id"):
                    tokens = oauth_secrets.get_tokens(
                        wallet["secret_manager_id"]
                    )  # noqa: E501
                    if tokens and tokens.get("access_token"):
                        is_paper = "paper" in config.BASE_URL.lower()
                        api_client = TradingClient(
                            oauth_token=tokens["access_token"], paper=is_paper
                        )
                        api_message = "Live personal account"
            except Exception as e:
                logging.error(
                    f"Failed to fetch multi-tenant API client for {user_id}: {e}"  # noqa: E501
                )

        # If we have a personal api_client, fetch exact positions & equity first.  # noqa: E501
        # This overrides the Bot's "portfolio manager" score-based positions,
        # because the user dashboard should reflect reality.
        real_positions = None
        real_equity = None
        # The REAL broker settled cash (Alpaca account.cash) — the authoritative Cash/margin figure
        # for the Overview. The console used to DERIVE cash as `equity − Σ market_value`, which is
        # wrong on a margin/short account (ignores buying power / unsettled funds). None when the
        # account probe fails → the console keeps its legacy derivation as a fallback.
        real_cash = None
        # PR-B: the real account currency (Alpaca account.currency, e.g. "USD"),
        # so the console shows the correct symbol instead of a hardcoded €.
        real_currency = "USD"
        if api_client:
            try:
                # Offload the synchronous Alpaca REST round-trips off the uvicorn event loop so a
                # slow broker call can't stall every other request (this is a read-only display path).
                acc = await asyncio.to_thread(api_client.get_account)
                positions = await asyncio.to_thread(api_client.get_all_positions)
                real_equity = float(acc.equity or 0)
                # account.cash can be legitimately negative (margin used) → don't coerce with `or 0`.
                # Isolated parse: a missing/unparseable cash must degrade to None (console then keeps
                # its legacy derivation), never abort the whole — otherwise-live — summary fetch.
                try:
                    _cash = getattr(acc, "cash", None)
                    real_cash = float(_cash) if _cash is not None else None
                except (TypeError, ValueError):
                    real_cash = None
                real_currency = getattr(acc, "currency", None) or "USD"
                api_message = (
                    "Live account" if not user_id else "Live personal account"
                )  # noqa: E501
                real_positions = [
                    {
                        "symbol": p.symbol,
                        "qty": float(p.qty),
                        "market_value": float(p.market_value or 0),
                        "unrealized_pnl": float(p.unrealized_pl or 0),
                        "unrealized_pnl_pct": (
                            float(p.unrealized_plpc or 0) * 100
                            if p.unrealized_plpc
                            else 0
                        ),
                    }
                    for p in positions
                ]
            except Exception as api_err:
                # #2980 (§5.6): a live-broker fetch failure is a fallback event —
                # operators must SEE it (WARNING), not have it swallowed at DEBUG.
                logging.warning("Live account fallback failed: %s", api_err)
                api_message = "Failed to fetch broker data"

        # Now merge with PortfolioManager insights if the bot is active
        if active and hasattr(active, "portfolio_manager"):
            pm = active.portfolio_manager
            if pm:
                summary = pm.get_portfolio_summary()
                debates = pm.get_debate_history(limit=5)
                rebalance_recs = pm.get_rebalance_recommendations()

                # Use real positions if available, otherwise fallback to bot's internal state  # noqa: E501
                if real_positions is not None:
                    # Enrich real positions with bot scores
                    for rp in real_positions:
                        if rp["symbol"] in pm._position_scores:
                            score = pm._position_scores[rp["symbol"]]
                            rp["total_score"] = score.total_score
                            rp["momentum_score"] = score.momentum_score
                            rp["conviction_score"] = score.conviction_score
                            rp["days_held"] = score.days_held
                    final_positions = real_positions
                    final_equity = real_equity
                else:
                    final_positions = [
                        {
                            "symbol": symbol,
                            "qty": score.qty,
                            "market_value": score.market_value,
                            "unrealized_pnl": score.unrealized_pnl,
                            "unrealized_pnl_pct": score.unrealized_pnl_pct,
                            "total_score": score.total_score,
                            "momentum_score": score.momentum_score,
                            "conviction_score": score.conviction_score,
                            "days_held": score.days_held,
                        }
                        for symbol, score in pm._position_scores.items()
                    ]
                    final_equity = None  # Bot scores don't represent total equity accurately  # noqa: E501

                total_pnl = (
                    sum(p.get("unrealized_pnl", 0) for p in final_positions)
                    if final_positions
                    else 0
                )
                return _json_safe(
                    {
                        "status": "success",
                        "currency": real_currency,
                        # Bug B (0.3.5): tag which account this snapshot belongs to, using the
                        # SAME source /health reports, so the mode-switch overlay can reject a
                        # stale cross-account in-flight poll.
                        "paper_trading": getattr(config, "PAPER_TRADING", True),
                        "summary": summary,
                        "positions": final_positions,
                        "equity": final_equity,
                        "cash": real_cash,
                        "recent_debates": debates,
                        "rebalance_recommendations": rebalance_recs,
                        "agent_statuses": getattr(
                            engine, "_last_round_table_state", []
                        ),
                        "message": api_message,
                        "total_unrealized_pnl": total_pnl,
                    }
                )

        if real_positions is not None:
            total_pnl = (
                sum(p.get("unrealized_pnl", 0) for p in real_positions)
                if real_positions
                else 0
            )
            return _json_safe(
                {
                    "status": "success",
                    "currency": real_currency,
                    "paper_trading": getattr(
                        config, "PAPER_TRADING", True
                    ),  # Bug B (0.3.5)
                    "summary": None,
                    "positions": real_positions,
                    "equity": real_equity,
                    "cash": real_cash,
                    "agent_statuses": getattr(
                        engine, "_last_round_table_state", []
                    ),  # noqa: E501
                    "message": api_message,
                    "total_unrealized_pnl": total_pnl,
                }
            )

        return _json_safe(
            {
                "status": "success",
                "currency": real_currency,
                "paper_trading": getattr(
                    config, "PAPER_TRADING", True
                ),  # Bug B (0.3.5)
                "summary": None,
                "positions": [],
                "agent_statuses": getattr(
                    engine, "_last_round_table_state", []
                ),  # noqa: E501
                "message": api_message,
                "total_unrealized_pnl": 0,
            }
        )
    except Exception as e:
        logging.error("portfolio_summary failed: %s", e, exc_info=True)
        # Bug B (0.3.5): tag even the error path with the account so a poll that fails during the
        # target engine's early boot cannot leave the mode-switch overlay's account-aware gate
        # without a tag → the reveal never wedges to the 90s honest timeout.
        return {
            "status": "error",
            "message": "internal_error",
            "paper_trading": getattr(config, "PAPER_TRADING", True),
        }


def _reconstruct_spy_points(points, initial_capital):
    """Rebuild the SPY benchmark series aligned to ``points`` (portfolio daily equity),
    normalized so it starts at ``initial_capital`` on the first point's date.

    Returns ``(spy_points, spy_first_close)``. Fail-soft: returns ``([], 1.0)`` on any error
    or when SPY data is unavailable. Backend-agnostic (uses ``engine.data_provider`` only) so
    the caller's Redis / LocalState caching behaves identically in every edition (BORA). This
    is the SINGLE source of the SPY reconstruction — called on a cold cache AND to self-heal a
    frozen-empty ``spy_points`` (an early empty reconstruction used to stay empty forever, so
    the S&P line never appeared even once SPY data became available).
    """
    spy_points: list = []
    spy_first_close = 1.0
    if not points or not initial_capital:
        return spy_points, spy_first_close
    try:
        first_date = str(points[0].get("date"))
        t_start = datetime.strptime(first_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        elapsed_days = (now_utc - t_start).days + 1
        spy_df = None
        if engine and getattr(engine, "data_provider", None) is not None:
            spy_df = engine.data_provider.get_data(
                "SPY", now_utc, days=max(10, elapsed_days + 5)
            )
            if (spy_df is None or spy_df.empty) and elapsed_days > 30:
                spy_df = engine.data_provider.get_data(
                    "SPY", now_utc, days=max(10, elapsed_days + 5), use_case="live"
                )
        if (
            (spy_df is None or spy_df.empty or "close" not in spy_df.columns)
            and engine
            and getattr(engine, "api", None) is not None
        ):
            try:
                from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
                from alpaca.trading.requests import StockBarsRequest

                from core.data_provider import _bars_to_dataframe

                bars_resp = engine.api.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols="SPY",
                        timeframe=AlpacaTimeFrame.Day,
                        start=t_start,
                        end=now_utc,
                    )
                )
                if (
                    bars_resp
                    and getattr(bars_resp, "df", None) is not None
                    and not bars_resp.df.empty
                ):
                    spy_df = _bars_to_dataframe(bars_resp.df)
            except Exception as alpaca_err:
                logging.warning(
                    "benchmark: direct Alpaca SPY fetch fallback failed: %s", alpaca_err
                )

        if spy_df is None or spy_df.empty or "close" not in spy_df.columns:
            logging.warning(
                "benchmark: SPY data unavailable (get_data('SPY') returned empty) — the S&P "
                "line is omitted. Check the market-data source (Alpaca IEX on a paper account)."
            )
        if spy_df is not None and not spy_df.empty and "close" in spy_df.columns:
            spy_df = spy_df.sort_index()
            spy_map = {
                idx.strftime("%Y-%m-%d"): float(row["close"])
                for idx, row in spy_df.iterrows()
            }
            # rc3 finding (#3071): the curve may start on a NON-trading day (a chosen
            # baseline like Saturday 2026-08-01, or a weekend inception). The old
            # fallback took spy_df.iloc[0] — the OLDEST close of the fetched window —
            # which rebased the S&P card to a months-old level (+11.10% shown where the
            # real since-baseline figure was +1.08%). Correct base: the first close
            # ON/AFTER the start date; only if none exists (curve beyond the data),
            # the last known close BEFORE it (fail-soft, never the window's oldest).
            spy_first_close = spy_map.get(first_date)
            if not spy_first_close:
                _after = [d for d in sorted(spy_map) if d >= first_date]
                if _after:
                    spy_first_close = spy_map[_after[0]]
                else:
                    _before = [d for d in sorted(spy_map) if d < first_date]
                    spy_first_close = (
                        spy_map[_before[-1]]
                        if _before
                        else float(spy_df.iloc[0]["close"])
                    )
            if spy_first_close and spy_first_close > 0:
                last_known = spy_first_close
                for p in points:
                    close = spy_map.get(p.get("date"), last_known)
                    last_known = close
                    spy_points.append(
                        {
                            "date": p.get("date"),
                            "equity": round(
                                initial_capital * (close / spy_first_close), 2
                            ),
                        }
                    )
    except Exception as spy_err:
        logging.warning("Failed to reconstruct SPY points: %s", spy_err, exc_info=True)
    return spy_points, spy_first_close


def _get_inception_equity():
    """Fetch the account's FULL equity history from Alpaca to find the true trading inception —
    the first day the account held non-zero equity — so "since inception" is measured from the
    real account start, not the first recorded ``PortfolioSnapshot`` (engine boot). (#1782)

    Returns ``(inception_date, inception_equity, backfill_points)`` (daily, UTC) or ``None``
    (fail-soft) so the caller falls back to ``records[0]``. Backend-agnostic; never hard-fails.
    """
    try:
        if engine is None or not engine.api:
            return None
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        # #2717: do NOT request cashflow_types here — portfolio-history's `cashflow` field comes back
        # EMPTY from Alpaca in practice, so it never stripped deposits. Real CSD/CSW cash-flows are
        # attached to the points later, from the account-activities API (see _get_live_cashflows).
        #
        # #2878: request the FULL history via ``period="all"`` (measured against a live paper
        # account: returns every day from the first funded day onward). Do NOT use ``start=`` —
        # Alpaca treats it as a ~1-MONTH window STARTING at that date, not "from that date to now",
        # so a pre-inception ``start`` yields an all-zero month and NO inception (the bug that
        # truncated the chart to the first local snapshot). If ``period="all"`` ever comes back
        # empty/misaligned (a transient Alpaca quirk — the original "worked until yesterday"
        # incident), retry with an explicit 1-year window before failing soft to ``records[0]``.
        def _fetch(**kw):
            return engine.api.get_portfolio_history(
                GetPortfolioHistoryRequest(timeframe="1D", **kw)
            )

        def _aligned(h):
            ts = list(getattr(h, "timestamp", None) or [])
            eq = list(getattr(h, "equity", None) or [])
            return (ts, eq) if ts and len(ts) == len(eq) else (None, None)

        timestamps, equities = _aligned(_fetch(period="all"))
        if timestamps is None:
            logging.warning(
                "benchmark: Alpaca portfolio-history period=all empty/misaligned — retrying "
                "with period=1A. (#2878)"
            )
            timestamps, equities = _aligned(_fetch(period="1A"))
        if timestamps is None:
            logging.warning(
                "benchmark: Alpaca portfolio-history empty/misaligned — falling back to the "
                "first recorded snapshot as inception. (#1782)"
            )
            return None
        backfill: list = []
        inception_date = None
        inception_equity = None
        for ts, eq in zip(timestamps, equities):
            if eq is None:
                continue
            eq = float(eq)
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            if inception_equity is None:
                if eq <= 0:
                    continue  # skip pre-deposit $0 days (created_at != first funded day)
                inception_date, inception_equity = day, eq
            backfill.append({"date": day, "equity": round(eq, 2), "cashflow": 0.0})
        if inception_equity is None or not backfill:
            logging.warning(
                "benchmark: Alpaca portfolio-history had no non-zero equity — falling back to "
                "the first recorded snapshot as inception. (#1782)"
            )
            return None
        return inception_date, inception_equity, backfill
    except (
        Exception
    ) as exc:  # noqa: BLE001 — fail-soft: any error -> DB records[0] fallback
        logging.warning(
            "benchmark: Alpaca portfolio-history fetch failed (%s) — falling back to the first "
            "recorded snapshot as inception. (#1782)",
            exc,
            exc_info=True,
        )
        return None


def _get_live_cashflows(period: str = "all") -> dict:
    """Deposit/withdrawal cash-flows ``{"YYYY-MM-DD": net_flow}`` from the Alpaca **account-activities
    API** — the source that actually returns them (#2717 follow-up).

    ROOT-CAUSE FIX: the prior implementation read ``get_portfolio_history(...).cashflow``, but that
    field comes back EMPTY from Alpaca in practice (verified against a live account with a real
    deposit), so ``net_deposits`` stayed 0 and the deposit was never stripped from the return. The
    canonical source for CSD (cash deposit) / CSW (cash withdrawal) / FEE (funding fee, #3049)
    transfers is ``GET /v2/account/activities/{CSD,CSW,FEE}``. Paper and live are distinct Alpaca accounts/keys, so
    this reflects the CURRENT account only. Fail-soft: ``{}`` on any error (``period`` is unused now,
    kept for call-site compatibility).
    """
    try:
        if engine is None or not engine.api:
            return {}
        from core.engine.perf_metrics import (
            FUNDING_ACTIVITY_TYPES,
            net_activities_by_date,
        )

        activities: list = []
        for atype in FUNDING_ACTIVITY_TYPES:  # #3049: CSD, CSW, FEE
            try:
                rows = engine.api.get(f"/account/activities/{atype}")
                if isinstance(rows, list):
                    activities.extend(rows)
                    # rc4 R4 diagnostics: log the RAW ledger answer per type, so a
                    # missing deposit is attributable (broker posting lag vs fetch
                    # failure vs classification) instead of guessed at.
                    logging.info(
                        "benchmark: activities %s -> %d row(s), net %.2f",
                        atype,
                        len(rows),
                        sum(abs(float(r.get("net_amount", 0) or 0)) for r in rows),
                    )
            except (
                Exception
            ) as e:  # noqa: BLE001 — one type failing must not lose the other
                logging.warning(
                    "benchmark: account-activities %s fetch failed: %s", atype, e
                )
        return net_activities_by_date(activities)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — cash-flow markers are best-effort; never kill the curve
        logging.warning(
            "benchmark: cash-flow fetch failed (%s) — TWR falls back to raw return "
            "for this cycle. (#2717)",
            exc,
            exc_info=True,
        )
        return {}


def _return_metrics_from_points(points: list) -> dict:
    """Cash-flow-adjusted KPIs from a benchmark points list (#1957).

    Each point is ``{"date", "equity", "cashflow"?}``; a missing ``cashflow`` counts as 0.
    Returns ``{twr_pct, net_deposits, pnl_net_of_deposits}`` (all 0 for <2 points). This is the
    single source of truth for the return KPIs — the frontend stops computing (now-start)/start,
    so a deposit no longer inflates Growth Rate / Total Return / the SPY comparison.
    """
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    dates = [p.get("date") for p in points]
    equity = [float(p.get("equity") or 0.0) for p in points]
    cashflows = [float(p.get("cashflow") or 0.0) for p in points]
    res = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    # #3145: write the EQUITY-EFFECTIVE flow back onto the points, so the curve the console
    # receives carries exactly the flows this function chained. The console recomputes the same
    # return for the chart and the hero; before this it chained the GROSS amounts and landed
    # 0.0056 pp away from the KPI card on the live vector — the same number shown twice,
    # differently. ``net_deposits`` remains gross.
    eff = res.get("effective_cashflows") or []
    if len(eff) == len(points):
        for _p, _e in zip(points, eff):
            if _e:
                _p["cashflow"] = round(float(_e), 2)
            elif "cashflow" in _p:
                _p.pop("cashflow")
    return res


# #3071: user-selectable performance baseline. Stored in system_config (nullable ISO date);
# null/absent = true inception (today's behaviour). The choice re-anchors every KPI served by
# /benchmark-equity, so it lives ENGINE-side (single source of truth, auditable timestamped
# write) — see docs/3071-performance-baseline-date/implementation_plan.md (Dual Design, Option A).
PERF_BASELINE_KEY = "performance_baseline_date"


async def _get_performance_baseline():
    """Read the persisted baseline date (``YYYY-MM-DD``) or ``None``. Fail-soft: any DB
    error means "no baseline" — the curve falls back to true inception, never breaks."""
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            row = (
                (
                    await session.execute(
                        sa.select(SystemConfig).filter_by(config_key=PERF_BASELINE_KEY)
                    )
                )
                .scalars()
                .first()
            )
            value = row.config_value if row else None
            if isinstance(value, dict):
                value = value.get("baseline_date")
            if not value:
                return None
            # Strict ISO-date validation: anything else (corrupt row, mocked session in
            # tests) degrades to "no baseline" instead of poisoning the slice.
            try:
                return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime(
                    "%Y-%m-%d"
                )
            except (ValueError, TypeError):
                return None
    except Exception as exc:  # noqa: BLE001 — reporting preference, never fatal
        logging.warning("performance-baseline read failed (%s) — using inception", exc)
        return None


@app.get(
    "/api/performance-baseline",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_performance_baseline():
    """#3071: the persisted performance baseline (null = true inception)."""
    return {"baseline_date": await _get_performance_baseline()}


@app.post(
    "/api/performance-baseline",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def set_performance_baseline(body: dict):
    """#3071: persist (or clear, with ``{"baseline_date": null}``) the baseline date.

    Timestamped ``system_config`` upsert → the choice is auditable (fair-presentation
    guardrail: the console always labels the chosen base, reports carry both figures).
    """
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    raw = (body or {}).get("baseline_date")
    if raw is not None:
        try:
            raw = datetime.strptime(str(raw)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422, detail="baseline_date must be YYYY-MM-DD or null"
            )
    async with AsyncSessionLocal() as session:
        row = (
            (
                await session.execute(
                    sa.select(SystemConfig).filter_by(config_key=PERF_BASELINE_KEY)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            session.add(
                SystemConfig(
                    config_key=PERF_BASELINE_KEY,
                    config_value={"baseline_date": raw},
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.config_value = {"baseline_date": raw}
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
    # The benchmark cache is keyed with baseline_date — the next poll's stale-check
    # (baseline mismatch) forces a full rebuild, so no explicit invalidation is needed.
    return {"baseline_date": raw}


@app.get(
    "/benchmark-equity",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_benchmark_equity():
    try:
        r = RedisClient.get_sync_redis()
        # Offload the blocking Redis GET off the event loop (read-only cache read for the chart).
        data_str = await asyncio.to_thread(r.get, "benchmark_equity_data")
        _cached = None
        if data_str:
            try:
                _cached = __import__("json").loads(data_str)
            except Exception:
                _cached = None
        # PR-3: current trading mode. is_simulation is BACKTEST-only (a paper account is a REAL
        # Alpaca paper account -> is_simulation=False for BOTH paper and live), so paper-vs-live is
        # a SEPARATE discriminator. Fail-safe default = paper (True).
        cfg_paper = bool(getattr(config, "PAPER_TRADING", True))
        # PR-3: a cache tagged with the OTHER mode (e.g. the paper curve that persists in Redis/DB
        # across the paper->live restart) is stale — force a DB rebuild under the current mode so
        # paper numbers never pollute the live-labelled curve. Legacy caches lack the key ->
        # default True (paper); in live that mismatches config -> correctly treated as stale.
        _cache_mode_stale = bool(_cached) and (
            bool(_cached.get("paper_trading", True)) != cfg_paper
        )
        # #3071: the served window depends on the persisted baseline — a cache built under a
        # DIFFERENT baseline (or before one was set/cleared) is stale and must rebuild.
        _baseline = await _get_performance_baseline()
        # rc4 R4: fingerprint helper — imported once, used by every branch below.
        from core.engine.perf_metrics import flows_signature

        _cache_baseline_stale = bool(_cached) and (
            (_cached.get("baseline_date") or None) != _baseline
        )
        # Rebuild fully from the DB when the cache is absent OR "thin" OR wrong-mode (stale).
        # _append_live_equity_to_benchmark (core/engine/base.py) warms this key every engine cycle
        # with `points` only (no initial_capital / spy_points), which otherwise pre-empts this
        # reconstruction — so the handler returned a points-only cache forever: NO S&P line + a
        # history truncated to base.py's per-cycle appends. A missing initial_capital marks a thin
        # cache -> rebuild.
        # #1957 (Archon finding 5 — addressed differently): a legacy cache lacks twr_pct. Rather
        # than force a rebuild, the cached-return path below recomputes the cash-flow-adjusted KPIs
        # from `points` on EVERY read, so the response always carries a fresh twr_pct regardless of
        # what the cache holds — strictly better than a one-time cache invalidation (no stale
        # window, no cache churn, and it never fights the warm-cache spy self-heal).
        if (
            not _cached
            or not _cached.get("initial_capital")
            or _cache_mode_stale
            or _cache_baseline_stale
        ):
            # Fallback to database query if Redis cache is empty/cold
            import json
            from datetime import timezone

            import sqlalchemy as sa

            from core.database.models import PortfolioSnapshot
            from core.database.session import AsyncSessionLocal

            # #3054: LIVE (separate Alpaca account) — build the COMPLETE daily curve from Alpaca
            # portfolio_history (period=all) instead of the sparse local snapshots that starve
            # Sharpe/drawdown/TWR (14 of ~32 days). PR-3 gated the Alpaca backfill to paper for fear
            # of paper-era pollution; that does not apply to a SEPARATE live account (owner sign-off
            # 2026-08-26). Cash-flows (incl. the #3049 FEE) are attached exactly as the snapshot
            # path. Fail-soft: no Alpaca history -> fall through to the snapshot logic below.
            if not cfg_paper:
                _live_inc = await asyncio.to_thread(_get_inception_equity)
                # #3054: _get_inception_equity returns a (date, equity, backfill) 3-tuple or
                # None. Guard the shape defensively so an unexpected return can never crash the
                # endpoint on the live path (fail-soft to the snapshot logic below).
                if isinstance(_live_inc, (tuple, list)) and len(_live_inc) == 3:
                    # #3068: jump-aligned attribution (NOT by activity date) — a settlement-
                    # lagged deposit otherwise strips from the wrong sub-period (the real
                    # -22.94% inception / -50.91% drawdown on a live account).
                    from core.engine.perf_metrics import align_cashflows_to_points

                    _inc_date, _inc_eq, _backfill = _live_inc
                    live_points = [
                        {"date": p["date"], "equity": p["equity"]} for p in _backfill
                    ]
                    # rc5 R7: Alpaca answers period=all for the whole account lifetime — the
                    # days BEFORE the account was funded come back as zero equity. They are not
                    # history, and as the curve's base they make the account's own value read
                    # as market P&L. Trim the leading run (fail-soft, interior zeros kept).
                    from core.engine.perf_metrics import trim_leading_unfunded

                    live_points = trim_leading_unfunded(live_points)
                    live_cf = await asyncio.to_thread(_get_live_cashflows)
                    if live_cf:
                        live_buckets = align_cashflows_to_points(
                            [p["date"] for p in live_points],
                            [p["equity"] for p in live_points],
                            live_cf,
                        )
                        for _p, _c in zip(live_points, live_buckets):
                            if _c:
                                _p["cashflow"] = round(_c, 2)
                    # #3071: dual figures — the TRUE-inception TWR is computed on the full
                    # aligned curve BEFORE slicing (reports/audit always keep both).
                    from core.engine.perf_metrics import slice_points_to_baseline

                    _live_inception_date = live_points[0]["date"]
                    _live_full_metrics = _return_metrics_from_points(live_points)
                    live_points = slice_points_to_baseline(live_points, _baseline)
                    _live_initial = live_points[0]["equity"]
                    live_metrics = _return_metrics_from_points(live_points)
                    live_spy, live_spy_first = await asyncio.to_thread(
                        _reconstruct_spy_points, live_points, _live_initial
                    )
                    live_resp = {
                        "points": live_points,
                        "spy_points": live_spy,
                        "spy_first_close": live_spy_first,
                        "initial_capital": _live_initial,
                        "start_date": live_points[0]["date"],
                        "end_date": live_points[-1]["date"],
                        "strategy": "RLAgent",
                        "final_equity": live_points[-1]["equity"],
                        "paper_trading": False,
                        # #3071: honest labelling + audit — the chosen base and the true
                        # inception travel with every response.
                        "baseline_date": _baseline,
                        "inception_date": _live_inception_date,
                        "inception_twr_pct": _live_full_metrics["twr_pct"],
                        # rc4 R4: ledger fingerprint — cached polls compare against a
                        # fresh fetch and evict on change (late-posting deposits).
                        "flows_sig": flows_signature(live_cf),
                        **live_metrics,
                    }
                    try:
                        r.set("benchmark_equity_data", json.dumps(live_resp))
                    except (
                        Exception
                    ) as _r_err:  # noqa: BLE001 — cache write is best-effort
                        logging.warning(
                            "Failed to cache live benchmark data: %s", _r_err
                        )
                    return live_resp

            # #2588: a failed snapshot query must DEGRADE, never kill the endpoint. The
            # outer catch used to turn e.g. a local schema drift (``no such column:
            # portfolio_snapshots.is_sim_day`` after #2544) into ``internal_error`` with
            # ZERO points — every chart range beyond 1D/1W went "Collecting equity
            # history…" and the Since-inception/Started/MaxDD KPIs died. With
            # ``records = []`` the existing no-records path below serves the broker's
            # full portfolio history in paper (live stays fail-closed: no_live_history).
            try:
                async with AsyncSessionLocal() as session:
                    dialect = session.bind.dialect.name
                    # PR-3: segregate paper vs live. LIVE -> only REAL live snapshots
                    # (paper_trading IS False). PAPER -> paper (True) OR legacy NULL rows written
                    # before this column existed (isnot False = True OR NULL). Applied to BOTH the
                    # postgres-distinct and the sqlite max-per-day statements so the live curve is
                    # built ONLY from live snapshots (no paper carryover after a paper->live restart).
                    mode_filter = (
                        PortfolioSnapshot.paper_trading.isnot(False)
                        if cfg_paper
                        else PortfolioSnapshot.paper_trading.is_(False)
                    )
                    if dialect == "postgresql":
                        stmt = (
                            sa.select(PortfolioSnapshot)
                            .where(mode_filter)
                            .distinct(
                                sa.func.date_trunc(
                                    "day", PortfolioSnapshot.timestamp
                                )  # noqa: E501
                            )
                            .order_by(
                                sa.func.date_trunc(
                                    "day", PortfolioSnapshot.timestamp
                                ),  # noqa: E501
                                PortfolioSnapshot.timestamp.desc(),
                            )
                        )
                        result = await session.execute(stmt)
                        records = list(result.scalars().all())
                        records.sort(key=lambda x: x.timestamp)
                    else:
                        # SQLite: subquery max(timestamp) grouped by date(timestamp)  # noqa: E501
                        subq = (
                            sa.select(
                                sa.func.date(PortfolioSnapshot.timestamp).label(
                                    "snapshot_date"
                                ),
                                sa.func.max(PortfolioSnapshot.timestamp).label(
                                    "max_ts"
                                ),  # noqa: E501
                            )
                            .where(mode_filter)
                            .group_by(sa.func.date(PortfolioSnapshot.timestamp))
                            .subquery()
                        )
                        stmt = (
                            sa.select(PortfolioSnapshot)
                            .join(
                                subq,
                                sa.and_(
                                    sa.func.date(PortfolioSnapshot.timestamp)
                                    == subq.c.snapshot_date,
                                    PortfolioSnapshot.timestamp == subq.c.max_ts,
                                ),
                            )
                            .where(mode_filter)
                            .order_by(PortfolioSnapshot.timestamp.asc())
                        )
                        result = await session.execute(stmt)
                        records = list(result.scalars().all())
            except Exception as db_err:  # noqa: BLE001 — degrade, never a dead chart
                logging.warning(
                    "benchmark-equity: DB snapshot query failed (%s) — degrading to "
                    "the broker's portfolio history so the chart/KPIs stay alive. "
                    "Likely local schema drift; init_local_db heals it on the next "
                    "boot. (#2588)",
                    db_err,
                    exc_info=True,
                )
                records = []

            if not records:
                if not cfg_paper:
                    # PR-3 (LIVE, zero live snapshots): show NO live history until real LIVE
                    # snapshots accumulate. NO paper carryover and NO broker-seeded inception —
                    # do NOT call _get_inception_equity (no Alpaca period=all backfill), because
                    # that broker curve/inception would pollute the freshly-live account with its
                    # pre-live (paper-era) equity. initial_capital is None until a live snapshot
                    # lands. PAPER is unaffected (the bug is live-only).
                    return {
                        "points": [],
                        "spy_points": [],
                        "strategy": "RLAgent",
                        "initial_capital": None,
                        "message": "no_live_history",
                    }
                # PAPER (unchanged): fresh engine / empty DB — no recorded snapshots yet. Still
                # surface the account's REAL equity history from Alpaca (period=all) so the Console
                # + demo render the full curve from inception on day one, instead of an empty chart.
                # Only a truly empty account (no Alpaca history either) returns the "no run yet"
                # placeholder. (#1790, follow-up to #1782 — the records path below is unchanged.)
                inception = await asyncio.to_thread(_get_inception_equity)
                if inception is None:
                    return {
                        "points": [],
                        "spy_points": [],
                        "strategy": "RLAgent",
                        "initial_capital": None,
                        "message": "No benchmark run yet.",
                    }
                _, inception_equity, backfill = inception
                points = list(backfill)
                spy_points, spy_first_close = await asyncio.to_thread(
                    _reconstruct_spy_points, points, inception_equity
                )
                metrics = _return_metrics_from_points(points)
                alpaca_only = {
                    "points": points,
                    "spy_points": spy_points,
                    "spy_first_close": spy_first_close,
                    "initial_capital": inception_equity,
                    "start_date": points[0]["date"],
                    "end_date": points[-1]["date"],
                    "strategy": "RLAgent",
                    "final_equity": points[-1]["equity"],
                    # PR-3: tag the rebuilt cache with the mode so the next read's stale-check
                    # (embedded paper_trading vs config.PAPER_TRADING) recognises it as current.
                    "paper_trading": cfg_paper,
                    **metrics,  # #1957: twr_pct / net_deposits / pnl_net_of_deposits
                }
                try:
                    r.set("benchmark_equity_data", json.dumps(alpaca_only))
                except Exception as r_err:
                    logging.warning(
                        "Failed to save Alpaca-only benchmark data to Redis: %s",
                        r_err,  # noqa: E501
                    )
                return {
                    "points": points,
                    "spy_points": spy_points,
                    "start_date": points[0]["date"],
                    "end_date": points[-1]["date"],
                    "strategy": "RLAgent",
                    "initial_capital": inception_equity,
                    "final_equity": points[-1]["equity"],
                    **metrics,
                }

            earliest_snap = records[0]
            initial_capital = earliest_snap.total_equity or 100000.0

            points = []
            for r_item in records:
                points.append(
                    {
                        "date": r_item.timestamp.strftime("%Y-%m-%d"),
                        "equity": round(r_item.total_equity or 0.0, 2),
                    }
                )

            # #1782: anchor "since inception" to the account's REAL trading start (Alpaca full
            # portfolio-history), not records[0] (the first RECORDED snapshot = engine boot).
            # Prepend the pre-snapshot daily equity + use the true inception as initial_capital.
            # Fail-soft: _get_inception_equity() -> None keeps the records[0] behaviour.
            #
            # PR-3: this Alpaca broker-seed is PAPER-ONLY. In LIVE we must NOT prepend the broker
            # history: period=all would drag in the account's pre-live (paper-era) equity and
            # pollute the live curve/inception. In live, initial_capital stays the earliest LIVE
            # snapshot's total_equity (records[0]) and no backfill is prepended.
            if cfg_paper:
                inception = await asyncio.to_thread(_get_inception_equity)
                if inception is not None:
                    _, inception_equity, backfill = inception
                    initial_capital = inception_equity
                    seen = {p["date"] for p in points}
                    first_recorded = points[0]["date"] if points else None
                    points = [
                        p
                        for p in backfill
                        if p["date"] not in seen
                        and (first_recorded is None or p["date"] < first_recorded)
                    ] + points
            # #2717: attach the account's real deposit/withdrawal cash-flows (from the
            # account-activities API — portfolio-history.cashflow is empty in reality) and bucket
            # each into the first point on/after its date, so a deposit that landed in a SPARSE
            # snapshot gap (a day the engine was off) still lands in the sub-period that spans it
            # instead of being dropped — which left the deposit-inflated return on screen. Applies to
            # BOTH modes (a live account is the reported case; paper simply has no CSD/CSW).
            acct_cf = await asyncio.to_thread(_get_live_cashflows)
            if acct_cf:
                # #3068: jump-aligned (not date-based) — see the live block above.
                from core.engine.perf_metrics import align_cashflows_to_points

                buckets = align_cashflows_to_points(
                    [p.get("date") for p in points],
                    [p.get("equity") or 0.0 for p in points],
                    acct_cf,
                )
                for p, cf in zip(points, buckets):
                    if cf:
                        p["cashflow"] = round(cf, 2)

            # #3071: slice AFTER the jump-alignment above (a boundary deposit attaches to its
            # in-window jump exactly once); dual figures — full-curve TWR survives for reports.
            from core.engine.perf_metrics import slice_points_to_baseline

            _inception_date_str = points[0]["date"] if points else None
            _full_metrics = _return_metrics_from_points(points)
            points = slice_points_to_baseline(points, _baseline)
            if _baseline and points:
                initial_capital = points[0].get("equity") or initial_capital

            start_date_str = (
                points[0]["date"]
                if points
                else earliest_snap.timestamp.strftime("%Y-%m-%d")
            )

            spy_points, spy_first_close = await asyncio.to_thread(
                _reconstruct_spy_points, points, initial_capital
            )

            metrics = _return_metrics_from_points(points)
            reconstructed_data = {
                "points": points,
                "spy_points": spy_points,
                "spy_first_close": spy_first_close,
                "initial_capital": initial_capital,
                "start_date": start_date_str,
                "end_date": records[-1].timestamp.strftime("%Y-%m-%d"),
                "strategy": earliest_snap.strategy_name or "RLAgent",
                "final_equity": records[-1].total_equity,
                # PR-3: tag the rebuilt cache with the mode so the next read's stale-check
                # recognises it as current (else live would rebuild on every poll).
                "paper_trading": cfg_paper,
                # #3071: baseline echo (cache stale-check key) + true-inception figures.
                "baseline_date": _baseline,
                "inception_date": _inception_date_str,
                "inception_twr_pct": _full_metrics["twr_pct"],
                # rc4 R4: ledger fingerprint (see live path).
                "flows_sig": flows_signature(acct_cf),
                **metrics,  # #1957: twr_pct / net_deposits / pnl_net_of_deposits
            }

            try:
                r.set("benchmark_equity_data", json.dumps(reconstructed_data))
            except Exception as r_err:
                logging.warning(
                    "Failed to save reconstructed benchmark data to Redis: %s",
                    r_err,  # noqa: E501
                )

            return {
                "points": points,
                "spy_points": spy_points,
                "start_date": start_date_str,
                "end_date": records[-1].timestamp.strftime("%Y-%m-%d"),
                "strategy": earliest_snap.strategy_name or "RLAgent",
                "initial_capital": initial_capital,
                "final_equity": records[-1].total_equity,
                **metrics,
            }

        data = _cached
        points = list(data.get("points", []))
        spy_points = list(data.get("spy_points", []))
        initial_capital = data.get("initial_capital")
        today_str = date.today().strftime("%Y-%m-%d")

        if engine.api and initial_capital:
            try:
                acc = await asyncio.to_thread(engine.api.get_account)
                live_equity = float(acc.equity or 0)
                # rc3 R2: a settlement-scale step between the cached tail and the live
                # equity means a deposit/withdrawal just landed. The append below carries
                # NO cashflow, so Net deposits / today's tile would stay wrong for hours.
                # Evict the cache: the NEXT poll (<=60s) does a full rebuild with freshly
                # aligned flows. This response still serves the cached (stale) numbers.
                from core.engine.perf_metrics import is_settlement_scale_jump

                # rc4 R4: LEDGER FRESHNESS — refetch the funding flows on every cached
                # poll and compare fingerprints. A deposit that posts to the activity
                # ledger AFTER the rebuild (the real 25.08. CSD that stayed invisible:
                # Net deposits +5.79k vs an 11.9k book, the +95.99% "return") evicts
                # the cache -> full rebuild with fresh flows on the next poll (<=60s).
                _fresh_cf = await asyncio.to_thread(_get_live_cashflows)
                if flows_signature(_fresh_cf) != (data.get("flows_sig") or "none"):
                    try:
                        r.delete("benchmark_equity_data")
                        logging.info(
                            "benchmark: funding-ledger changed (sig %s -> %s) — cache "
                            "evicted for full rebuild on next poll. (rc4 R4)",
                            data.get("flows_sig"),
                            flows_signature(_fresh_cf),
                        )
                    except Exception:  # noqa: BLE001 — eviction is best-effort
                        pass
                if points and is_settlement_scale_jump(
                    points[-1].get("equity"), live_equity
                ):
                    try:
                        r.delete("benchmark_equity_data")
                        logging.info(
                            "benchmark: settlement-scale live step (%.2f -> %.2f) — "
                            "cache evicted for full rebuild on next poll. (rc3 R2)",
                            float(points[-1].get("equity") or 0.0),
                            live_equity,
                        )
                    except Exception:  # noqa: BLE001 — eviction is best-effort
                        pass
                if live_equity > 0 and (
                    not points or points[-1].get("date") != today_str
                ):
                    points.append(
                        {"date": today_str, "equity": round(live_equity, 2)}
                    )  # noqa: E501
            except Exception:
                pass

        spy_first_close = data.get("spy_first_close")
        if not spy_points and points and initial_capital:
            # Self-heal a frozen-empty spy_points: it is reconstructed only on a COLD cache,
            # so an early empty result (SPY data not yet available at the first reconstruction)
            # stayed empty forever and the S&P line never appeared. Recompute it from the
            # current points now, and heal the cache so we don't refetch on every poll.
            spy_points, spy_first_close = await asyncio.to_thread(
                _reconstruct_spy_points, points, initial_capital
            )
            if spy_points:
                data["spy_points"] = spy_points
                data["spy_first_close"] = spy_first_close
                try:
                    r.set("benchmark_equity_data", __import__("json").dumps(data))
                except Exception as heal_err:
                    logging.warning("Failed to heal benchmark cache: %s", heal_err)
            else:
                # If reconstruction failed, evict thin warm cache so subsequent polls retry
                try:
                    r.delete("benchmark_equity_data")
                except Exception:
                    pass
        elif (
            spy_points and spy_first_close and initial_capital and spy_first_close > 0
        ):  # noqa: E501
            try:
                end_dt = datetime.now(timezone.utc)
                spy_df = engine.data_provider.get_data("SPY", end_dt, days=10)
                if (
                    spy_df is not None
                    and not spy_df.empty
                    and "close" in spy_df.columns
                ):
                    last_close = float(spy_df.iloc[-1]["close"])
                    if (
                        last_close > 0 and spy_points[-1].get("date") != today_str
                    ):  # noqa: E501
                        spy_points.append(
                            {
                                "date": today_str,
                                "equity": round(
                                    initial_capital * (last_close / spy_first_close),
                                    2,  # noqa: E501
                                ),
                            }
                        )
            except Exception:
                pass

        # #1957: recompute cash-flow-adjusted KPIs from the final points (today's live-equity
        # append above may have extended the series). Points keep their cached per-day cashflow;
        # the appended intraday point has none (0) — a same-day deposit surfaces on the next full
        # rebuild (cache is thin/stale then), which is acceptable at daily granularity.
        metrics = _return_metrics_from_points(points)
        return {
            "points": points,
            "spy_points": spy_points,
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "strategy": data.get("strategy", "RLAgent"),
            "initial_capital": initial_capital,
            "final_equity": data.get("final_equity"),
            # #3071: cached points are already sliced; pass the labelling fields through.
            "baseline_date": data.get("baseline_date"),
            "inception_date": data.get("inception_date"),
            "inception_twr_pct": data.get("inception_twr_pct"),
            **metrics,
        }
    except Exception as e:
        logging.error("benchmark_equity failed: %s", e, exc_info=True)
        return {"points": [], "spy_points": [], "message": "internal_error"}


# #2124: range -> (Alpaca period, timeframe). The daily /benchmark-equity path
# collapses each day to ONE point (base.py:_append_live_equity_to_benchmark), so
# a 1-day window is a 2-point straight line. This endpoint serves the INTRADAY
# equity curve at broker resolution for the Overview's short ranges. Longer
# ranges keep using /benchmark-equity (daily + S&P benchmark).
_INTRADAY_RANGES = {"1D": ("1D", "5Min"), "1W": ("1W", "1H")}
# #3189: bar width per timeframe, in seconds — the anchor point below is placed exactly one
# bar before the first bar, so it precedes the session strictly and the overnight gap draws
# as a step at the left edge instead of being averaged into the first bar.
_INTRADAY_BAR_SECONDS = {"5Min": 300, "1H": 3600}


@app.get(
    "/portfolio-intraday",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_portfolio_intraday(
    request: Request,
    range_: str = Query("1D", alias="range"),  # noqa: B008 — FastAPI query default
):
    """#2124: intraday account-equity curve so the Overview 1D/1W chart shows
    real movement instead of a single straight segment. Proxies Alpaca
    portfolio/history at 5Min (1D) / 1H (1W). Report-only, fail-soft to an empty
    curve — never a fabricated point; the daily path is untouched.

    Tenant-safe: the client is resolved per X-User-Id via ``_resolve_orders_client``
    (multi-tenant leak guard), and the blocking Alpaca call is offloaded with
    ``asyncio.to_thread`` so it never stalls the FastAPI event loop."""
    period, timeframe = _INTRADAY_RANGES.get(range_, _INTRADAY_RANGES["1D"])
    try:
        api_client = await _resolve_orders_client(request)
        if api_client is None:
            return {"points": []}
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        hist = await asyncio.to_thread(
            api_client.get_portfolio_history,
            GetPortfolioHistoryRequest(period=period, timeframe=timeframe),
        )
        if hist is None:
            # FINDING-01 (Archon #2124): explicit None guard for clearer diagnostics
            # than the getattr()-swallow path below. Fail-soft, never a fabricated point.
            logging.warning(
                "portfolio-intraday: hist response from Alpaca is None (range=%s)",
                range_,
            )
            return {"points": []}
        timestamps = list(getattr(hist, "timestamp", None) or [])
        equities = list(getattr(hist, "equity", None) or [])
        if not timestamps or len(timestamps) != len(equities):
            logging.warning(
                "portfolio-intraday: empty/misaligned history for range=%s", range_
            )
            return {"points": []}
        points = []
        for ts, eq in zip(timestamps, equities):
            if eq is None:
                continue
            iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            points.append({"date": iso, "equity": round(float(eq), 2)})
        # #3189: the curve used to START at the session's first bar, so the overnight gap between
        # yesterday's CLOSE and today's OPEN fell out of every 1D/1W figure — on an account that
        # holds positions overnight, often the bulk of the move. The P&L tile meanwhile measures
        # `equity - lastEquity` against the previous daily close, which is why the chart and the
        # tile showed two different numbers for the same day/week (owner report 03.09.).
        # Alpaca ships the correct anchor in this very response: `base_value`, the basis its own
        # profit_loss is computed from. Prepend it as the first point — BEFORE the cash-flow
        # alignment and the pre-funding trim below, so a deposit still attaches to the equity jump
        # and the anchor is not mistaken for a leading unfunded point.
        # Only the window's LEFT edge was ever affected: the overnight jumps INSIDE a week already
        # sat between two consecutive hourly bars and were counted.
        # Fail-soft: an absent / non-positive base_value invents nothing.
        try:
            _base_value = float(getattr(hist, "base_value", None) or 0.0)
        except (TypeError, ValueError):
            _base_value = 0.0
        if points and _base_value > 0.0:
            _bar_s = _INTRADAY_BAR_SECONDS.get(timeframe, 300)
            _anchor_ts = int(timestamps[0]) - _bar_s
            points.insert(
                0,
                {
                    "date": datetime.fromtimestamp(
                        _anchor_ts, tz=timezone.utc
                    ).isoformat(),
                    "equity": round(_base_value, 2),
                },
            )
        # rc5 R7: an account funded two days ago still gets a FULL week back from
        # period=1W — the earlier hours report zero equity. Plotted, that is a flat line
        # at 0 plus a cliff, and it is the base the Overview hero measures its money
        # delta against ("+$11,776.22" beside a "-0.53 %" TWR, 01.09.). Drop the leading
        # pre-funding run; interior zeros (a liquidated account) survive.
        from core.engine.perf_metrics import trim_leading_unfunded

        points = trim_leading_unfunded(points)
        # Attach external cash-flows to the intraday points, exactly like /benchmark-equity does
        # for the daily curve — so the 1D/1W chart (a) strips a deposit from its return instead of
        # painting it as a +54 % "week", and (b) shows the SAME deposit/withdrawal marker the
        # monthly view has. Same source (_get_live_cashflows) + bucketing (a flow dated D lands on
        # the first point whose ISO timestamp >= D), keyed to the intraday timestamps.
        acct_cf = await asyncio.to_thread(_get_live_cashflows)
        if acct_cf:
            # #3049: anchor a settlement-lagged deposit to the equity JUMP (not its earlier
            # activity date), so the 1D/1W chart shows the marker + strips it instead of
            # painting "+1663% today". Equity-aware intraday variant of the daily bucketer.
            from core.engine.perf_metrics import bucket_cashflows_to_points_intraday

            buckets = bucket_cashflows_to_points_intraday(
                [p["date"] for p in points],
                [p["equity"] for p in points],
                acct_cf,
            )
            for p, cf in zip(points, buckets):
                if cf:
                    p["cashflow"] = round(cf, 2)
        # #3145: the performance baseline is time zero for EVERY served curve, not just the
        # daily one. Without this the 1D/1W chart and the Overview hero kept measuring across
        # history the user had explicitly cut away (baseline 01.09., older values still shown).
        # Sliced AFTER the cash-flow alignment, exactly like the daily path — a deposit whose
        # activity date precedes the baseline but settles inside the window attaches once.
        try:
            _intraday_baseline = await _get_performance_baseline()
        except Exception as _bl_err:  # noqa: BLE001 — display path never hard-fails
            logging.warning(
                "portfolio-intraday: baseline read failed (%s) — serving true inception",
                _bl_err,
                exc_info=True,
            )
            _intraday_baseline = None
        if _intraday_baseline:
            from core.engine.perf_metrics import slice_points_to_baseline

            points = slice_points_to_baseline(points, _intraday_baseline)
        return {"points": points, "baseline_date": _intraday_baseline}
    except Exception as exc:  # noqa: BLE001 — display path never hard-fails
        logging.warning(
            "portfolio-intraday failed (range=%s): %s", range_, exc, exc_info=True
        )
        return {"points": []}


def _last_trading_day():
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


@app.post("/run-benchmark")
async def run_benchmark(
    p: Dict = None, _: None = Depends(require_engine_key)  # noqa: B008
):
    p = p or {}
    start_date = p.get("start_date", "2025-01-01")
    end_date = p.get("end_date", _last_trading_day())
    initial_capital = float(p.get("initial_capital", 100000))
    symbol_sample_mode = p.get("symbol_sample_mode", "sp500")
    engine.run_benchmark_in_thread(
        start_date, end_date, initial_capital, symbol_sample_mode
    )
    return {
        "status": "success",
        "message": f"Benchmark started. End date: {end_date}.",
    }  # noqa: E501


@app.post("/run-simulation")
async def run_sim(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    # Defense-in-depth: the Simulation/backtest page is entitlement-gated (central switch
    # core/entitlement/tier.py: simulation_enabled=False for all tiers while the backtest
    # runtime is hardened). Refuse a direct POST when disabled so the thread never starts.
    # Mirrors the /api/live/enable allow_live gate; no-op once a tier re-enables it.
    from core.entitlement import resolve_entitlement

    if not resolve_entitlement().simulation_enabled:
        raise HTTPException(
            status_code=403,
            detail="[Entitlement] the Simulation/backtest page is disabled for this edition.",
        )

    symbol_sample_mode = p.get("symbol_sample_mode", "full_market")
    engine.run_simulation_in_thread(
        p["start_date"],
        p["end_date"],
        p["initial_capital"],
        symbol_sample_mode,  # noqa: E501
    )
    return {"status": "success"}


@app.get("/simulation-result")
async def simulation_result(_: None = Depends(require_engine_key)):  # noqa: B008
    """SIM-1 T1 (#1484): the last backtest result — the Console's reload-safe poll target.  # noqa: E501

    Dual-Design Option B: rather than rely on the ephemeral ``simulation_status`` event stream, the  # noqa: E501
    Console polls this until done, so a page reload mid-backtest still recovers the status/result.  # noqa: E501
    Returns ``{"status":"running"}`` while a sim runs, the stored result when complete, else idle.  # noqa: E501
    """
    if getattr(engine, "is_simulation", False):
        return {"status": "running"}
    return getattr(engine, "last_simulation_result", None) or {
        "status": "idle"
    }  # noqa: E501


@app.post("/run-learning")
async def run_learn(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    engine.run_learning_in_thread(
        p["start_date"], p["end_date"], p["initial_capital"]
    )  # noqa: E501
    return {"status": "success"}


# --- Epic INF-9: E2E Testing Force Cycle ---


@app.post("/api/v1/engine/force-cycle")
async def force_cycle(
    p: Dict, request: Request, _: None = Depends(require_engine_key)  # noqa: B008
):
    """
    Synchronous 'Force Cycle' for end-to-end testing (Audit Gates).
    Evaluates a single symbol deterministically on a specific past target_date.
    Returns the generated session_id and the strategy's signal.
    """
    # SEC M7: validate the caller-supplied symbol (defense-in-depth). The endpoint is  # noqa: E501
    # require_engine_key + localhost, but an unbounded/crafted string must never reach  # noqa: E501
    # the data provider. Ticker shape: starts alpha, then alnum/./-, <= 10 chars.  # noqa: E501
    symbol = str(p.get("symbol", "AAPL")).strip().upper()
    if not (
        1 <= len(symbol) <= 10
        and symbol[0].isalpha()
        and all(c.isalnum() or c in ".-" for c in symbol)
    ):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    target_date = p.get("target_date")

    if not target_date:
        target_date = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()  # noqa: E501

    if not engine or not engine.data_provider:
        raise HTTPException(
            status_code=503, detail="Engine/DataProvider not available."
        )

    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the cycle).  # noqa: E501
    bump_usage("force_cycles")

    # Fetch T-1 (or target_date) exact daily close (determinism!)
    end_dt = datetime.fromisoformat(target_date)
    if not end_dt.tzinfo:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    try:
        # Request a chunk of data, ending exactly at T-1
        df = engine.data_provider.get_data(symbol, end_dt, days=10)
        if df is None or df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No historical data found for {symbol}.",  # noqa: E501
            )

        latest_row = df.iloc[-1]
        mock_ohlc = {
            "open": float(latest_row["open"]),
            "high": float(latest_row["high"]),
            "low": float(latest_row["low"]),
            "close": float(latest_row["close"]),
            "volume": float(latest_row["volume"]),
            "timestamp": end_dt.isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Data Provider Error: {e}"
        )  # noqa: E501

    # MiFID II Audit Log
    api_key_header = request.headers.get("x-bot-api-key", "")
    key_ident = (
        api_key_header[:6] + "..."
        if api_key_header and len(api_key_header) > 6
        else "internal"
    )
    logging.info(
        f"MiFID Audit: Force Cycle triggered for {symbol} at target datum {target_date}. Caller Key: {key_ident}"  # noqa: E501
    )

    import uuid

    from core.orchestration.graph import build_symbol_eval_graph

    # Instantiate the LangGraph evaluator with mocked input
    state = {
        "symbol": symbol,
        "ohlc": mock_ohlc,
        "market_regime": getattr(engine, "cached_regime", {"regime": "bull"}),
        "_is_simulation": True,  # Prevent actual execution
        "error": None,
    }

    graph = build_symbol_eval_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Run graph evaluation (T-1 deterministic input)
        final_state = await graph.ainvoke(state, config)
    except Exception as e:
        return {"status": "error", "message": f"LangGraph Error: {e}"}

    session_id = final_state.get("session_id")
    signal_obj = final_state.get("signal")
    signal_action = (
        getattr(signal_obj, "action", "NONE") if signal_obj else "NONE"
    )  # noqa: E501

    return {
        "status": "success",
        "session_id": session_id,
        "signal": signal_action,
        "target_date": target_date,
        "timestamp": mock_ohlc["timestamp"],
        "close_price": mock_ohlc["close"],
        "round_table_scores": final_state.get("round_table_scores", []),
    }


# --- Chat ---


@app.post(
    "/chat",
    dependencies=[
        Depends(require_engine_key),
        Depends(verify_user_id_sig),
    ],  # noqa: E501
)
async def chat(p: Dict):
    message = (p.get("message") or "").strip()
    if not message:
        return {
            "reply": "Please ask a question.",
            "message": "Please ask a question.",
        }  # noqa: E501
    try:
        # XAI-T9a (#1401): when the glass-box core is enabled (OSS desktop sets
        # XAI_AGENT_CORE), route through the 4-domain router. The core is built per call so  # noqa: E501
        # the live engine.specialist_registry (set later, and only when the registry is  # noqa: E501
        # enabled) is always current — a core cached at warm-up would pin stock_research to  # noqa: E501
        # an empty registry. On any XAI-path error, fall through to the legacy chat so the  # noqa: E501
        # flag-on path is never worse than flag-off. A dormant core (flag off, the default)  # noqa: E501
        # yields None, keeping the legacy path byte-identical.
        if is_agent_core_enabled():
            try:
                core = boot_xai_runtime(
                    specialist_registry=getattr(
                        engine, "specialist_registry", None
                    )  # noqa: E501
                )
                xai_reply = await answer_via_xai(message, core=core)
                if xai_reply is not None:
                    return {"reply": xai_reply, "message": xai_reply}
            except Exception:
                logging.exception(
                    "XAI chat path failed — falling back to legacy chat."
                )  # noqa: E501
        context = engine.get_chat_context()
        reply = answer_chat_with_fallback(context, message)
        if not reply:
            reply = "I couldn't generate an answer right now."
        return {"reply": reply, "message": reply}
    except Exception as e:
        logging.error("Chat failed: %s", e, exc_info=True)
        reply = "Sorry — I couldn't process that right now. Please try again."
        return {"reply": reply, "message": "internal_error"}


# --- WebSocket ---


def _ws_origin_allowed(origin: str) -> bool:
    o = origin.lower()
    return ("://127.0.0.1" in o) or ("://localhost" in o) or ("://[::1]" in o)


@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    # C3/H4 (#2368): the WS handshake carries no X-Engine-Key (browsers can't set WS headers), so a
    # bare accept() left the live engine stream cross-origin readable + hijackable (single global
    # callback). On the desktop (DEPLOYMENT_MODE=LOCAL — the only place the loopback attack applies)
    # require the engine key as a ?key= query param and validate the Origin BEFORE accepting. Cloud
    # (non-LOCAL) is unchanged — no new requirement, no behaviour break (edition-neutral gate).
    if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
        from core.auth import engine_key_ok

        if not engine_key_ok(websocket.query_params.get("key")):
            await websocket.close(code=1008)
            return
        _origin = websocket.headers.get("origin")
        if _origin and not _ws_origin_allowed(_origin):
            await websocket.close(code=1008)
            return
    await websocket.accept()

    async def cb(data):
        try:
            await websocket.send_json(data)
        except Exception:
            pass

    engine.set_update_callback(cb, asyncio.get_running_loop())
    try:
        await websocket.send_json(
            {"type": "log", "data": {"message": "Connected"}}
        )  # noqa: E501
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        engine.set_update_callback(None, None)


# --- Entry Point ---


# ---------------------------------------------------------------------------
# Symbol Universe API (Task #361 — OTel Gherkin requirement)
# ---------------------------------------------------------------------------


@app.get(
    "/api/v2/universe",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_symbol_universe(request: Request):
    """Return the current symbol universe used by the trading engine.

    Task #361 Gherkin:
      Given  a request to /api/v2/universe
      When   the OTel SDK is initialised as the very first import
      Then   every Span must carry: db.statement, user.id, response.body_length,  # noqa: E501
             service.version (Git SHA)

    Span attributes are added automatically by OtelSpanMiddleware.
    user.id is read from the X-User-Id header injected by serve_public_api.py.
    service.version comes from the GIT_COMMIT env var via core.telemetry.
    """
    from core.telemetry import get_service_version

    if engine is None:
        return {
            "status": "starting",
            "symbols": [],
            "count": 0,
            "service_version": get_service_version(),
            "message": "Engine is still initialising — retry shortly.",
        }

    # Prefer the live universe from the last market scan
    symbols: list[str] = list(getattr(engine, "_last_top_picks", []))

    # Fall back to the watchlist / configured symbols if scan hasn't run yet
    if not symbols and hasattr(engine, "symbols"):
        symbols = list(engine.symbols or [])

    return {
        "status": "ok",
        "symbols": symbols,
        "count": len(symbols),
        "service_version": get_service_version(),
    }


def _find_engine_port():
    base = getattr(config, "ENGINE_PORT", 8001)
    for port in range(base, min(base + 10, 8010)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return base


# ── G1b (#1050): desktop-console read-only routes ──────────────────────────
# Ported from the production bundle (DTO key-set frozen as a fixture, pinned
# by tests/unit/test_g1b_console_routes.py). Round-Table nomenclature per the
# maintainer directive — the bundle's /senate-* paths are NOT carried over.
# Read-only display layer: specialist_registry (None → documented empty state
# while disabled on main) and the G1a recent-decisions store. No order-path
# access.


def _serialize_specialist_report(sym: str, r, *, full: bool = False) -> Dict:
    """Bundle-DTO-compatible report serialization (exact key-set contract).

    Fields main's SpecialistReport does not carry yet (about/company_summary/
    edge_signals/headlines — insight-quality features still bundle-only) are
    emitted with empty defaults so the console contract holds.

    REPORTS-UX Wave 1 (#2710): ``full=False`` (the default, used by the bulk
    /specialist-reports list) keeps the note-body char caps so a ~500-symbol
    payload stays light — byte-identical to before. ``full=True`` (the
    per-symbol GET /specialist-report/{symbol}) serves the body UNCAPPED so the
    expanded reader never truncates.
    """

    def _round_or_none(value, digits):
        return round(value, digits) if value is not None else None

    updated_at = getattr(r, "updated_at", None)
    # PR-review P0-1: NEVER use `or` as numeric fallback — 0.0 is a legitimate,
    # maximally-bearish sentiment and `0.0 or 50.0` would silently mask it as
    # neutral (financial edge-case governance rule).
    sentiment = getattr(r, "sentiment_score", None)
    insider_total = getattr(r, "insider_trades_total", None)
    # P0-1 (T-SER #1266): preserve the historical 0.0-when-absent fallback but stop  # noqa: E501
    # `or`-masking a legitimate 0.0 confidence (0.0 is a real value, not a default).  # noqa: E501
    confidence = getattr(r, "confidence", None)
    # #2587 dead-source honesty: names from _SPECIALIST_SOURCE_NAMES whose fetch
    # FAILED this cycle (HTTP error / exception), as opposed to "checked and found
    # nothing". Their counts serialize as null so the card renders "—" instead of a
    # fabricated 0. Legacy reports without the field keep exact int counts.
    unavailable = set(getattr(r, "sources_unavailable", None) or [])
    result = {
        "symbol": sym,
        "sentiment_score": round(
            sentiment if sentiment is not None else 50.0, 1
        ),  # noqa: E501
        "recommendation": getattr(r, "recommendation", "hold"),
        "confidence": round(confidence, 3) if confidence is not None else 0.0,
        "escalate": bool(getattr(r, "escalate", False)),
        "escalate_reason": getattr(r, "escalate_reason", "") or "",
        "reasons": (getattr(r, "reasons", None) or [])[:5],
        "about": (
            getattr(r, "about", "")
            or getattr(r, "company_summary", "")
            or f"{sym}: overview unavailable this cycle."
        )[:900],
        "company_summary": getattr(r, "company_summary", "") or "",
        "edge_signals": getattr(r, "edge_signals", None) or [],
        "investment_thesis": (getattr(r, "investment_thesis", "") or "")[
            :1500
        ],  # noqa: E501
        "bull_case": (getattr(r, "bull_case", "") or "")[:1000],
        "bear_case": (getattr(r, "bear_case", "") or "")[:1000],
        "news_summary": (getattr(r, "news_summary", "") or "")[:1500],
        "headlines": (getattr(r, "headlines", None) or [])[:8],
        "alternative_signals": (getattr(r, "alternative_signals", "") or "")[
            :800
        ],  # noqa: E501
        "insider_trades_count": (
            insider_total
            if insider_total is not None
            else len(getattr(r, "insider_trades", None) or [])
        ),
        "political_trades_count": (
            None
            if "congressional_trades" in unavailable
            else len(getattr(r, "political_trades", None) or [])
        ),
        "material_events_count": len(
            getattr(r, "material_events", None) or []
        ),  # noqa: E501
        "reddit_mentions": (
            None
            if "reddit_mentions" in unavailable
            else getattr(r, "reddit_mentions", 0) or 0
        ),
        "wiki_spike": bool(getattr(r, "wiki_spike", False)),
        "short_interest_pct": getattr(r, "short_interest_pct", None),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "ml_direction": getattr(r, "ml_direction", "unavailable"),
        "ml_confidence": _round_or_none(getattr(r, "ml_confidence", None), 3),
        "ml_base_return_pct": _round_or_none(
            getattr(r, "ml_base_return_pct", None), 2
        ),  # noqa: E501
        "ml_bear_return_pct": _round_or_none(
            getattr(r, "ml_bear_return_pct", None), 2
        ),  # noqa: E501
        "ml_bull_return_pct": _round_or_none(
            getattr(r, "ml_bull_return_pct", None), 2
        ),  # noqa: E501
        "signal_quality": getattr(r, "signal_quality", "llm_only"),
        "walkforward_ic": _round_or_none(
            getattr(r, "walkforward_ic", None), 3
        ),  # noqa: E501
        "walkforward_sharpe": _round_or_none(
            getattr(r, "walkforward_sharpe", None), 2
        ),  # noqa: E501
        "ml_attention_features": getattr(r, "ml_attention_features", None)
        or [],  # noqa: E501
        # Group-B keys (T-SER #1266) - additive (+7). Producers: T2 (pros/cons/
        # summary), T6a (data_quality/degraded), later TA stage (rsi_14/macd_signal).  # noqa: E501
        # data_quality uses _round_or_none NOT `or` - 0.0 is a legitimate low-integrity  # noqa: E501
        # value (P0-1). summary capped [:1500] like news_summary/investment_thesis.  # noqa: E501
        "pros": getattr(r, "pros", None) or [],
        "cons": getattr(r, "cons", None) or [],
        "summary": (getattr(r, "summary", "") or "")[:1500],
        "data_quality": _round_or_none(getattr(r, "data_quality", 1.0), 3),
        "degraded": bool(getattr(r, "degraded", False)),
        "rsi_14": _round_or_none(getattr(r, "rsi_14", None), 1),
        "macd_signal": getattr(r, "macd_signal", None),
    }
    # RPAR-1 (#1262) Abschluss / #1490: deterministic, bundle-free report-quality badge. Additive  # noqa: E501
    # keys ONLY when enabled -> the exact key-set contract + BORA byte-identity hold with flag OFF.  # noqa: E501
    if getattr(config.get_config(), "REPORT_QUALITY_BADGE_ENABLED", False):
        from core.specialist.report_quality import compute_report_quality

        score, label = compute_report_quality(r)
        result["report_quality"] = score
        result["report_quality_label"] = label
    # RPT-GOLD (#1998): the full auditable Markdown note + mechanical audit verdict.
    # Additive keys ONLY when the report generator flag is ON -> the exact key-set
    # contract + BORA byte-identity hold with the flag OFF (same pattern as the
    # report-quality badge above). getattr defaults keep this safe even before the
    # SpecialistReport DTO carries the fields (RPT-6 #2075).
    if getattr(config.get_config(), "REPORT_GENERATOR_V2_ENABLED", False):
        result["report_markdown"] = getattr(r, "report_markdown", None)
        result["report_audit_ok"] = getattr(r, "report_audit_ok", None)
        result["report_audit_summary"] = getattr(r, "report_audit_summary", None)
        # The note's own cross-sectional verdict ("buy"/"hold"/"sell" or None when it
        # abstains). The reader badge reads THIS, not `recommendation` above — so an
        # abstaining note (report-only path leaves `recommendation` at the "hold"
        # default) surfaces null and the pill shows nothing. Additive + flag-gated,
        # so the key-set contract + BORA byte-identity hold with the flag OFF.
        result["report_recommendation"] = getattr(r, "report_recommendation", None)
    # Rich-synthesis card (Direction A): the grounding citations + dropped-claim
    # ledger + non-directional HAR-RV volatility band. Additive keys ONLY when the
    # card flag is ON -> the exact key-set contract + BORA byte-identity hold with
    # the flag OFF. The flag is hard-coupled to SPECIALIST_GROUNDING_ENABLED (config
    # _enforce_card_grounding), so ungrounded prose can never reach these keys.
    # P0-1: vol-band values use getattr defaults, NEVER `or` — a 0.0 base is real.
    if getattr(config.get_config(), "SPECIALIST_SYNTHESIS_CARD_ENABLED", False):
        result["synthesis_citations"] = getattr(r, "synthesis_citations", None) or []
        result["synthesis_dropped"] = getattr(r, "synthesis_dropped", None) or []
        result["grounding_ok"] = getattr(r, "grounding_ok", None)
        result["vol_band_low_pct"] = _round_or_none(
            getattr(r, "vol_band_low_pct", None), 2
        )
        result["vol_band_base_pct"] = _round_or_none(
            getattr(r, "vol_band_base_pct", None), 2
        )
        result["vol_band_high_pct"] = _round_or_none(
            getattr(r, "vol_band_high_pct", None), 2
        )
        result["vol_band_sigma_pct"] = _round_or_none(
            getattr(r, "vol_band_sigma_pct", None), 2
        )
        result["scenario_basis"] = getattr(r, "scenario_basis", "") or ""
        # Card model view: the LSTM cross-section standing — THE single rank
        # definition (core/report/lstm_panel_store.cross_section_standing), the
        # same one the auditable report and the Round-Table LSTM vote read, so
        # the card can never tell the user a standing the board didn't see.
        # Absent from the panel -> honest None triple (abstain, never guess).
        # #2587: the store method is ``cross_section_at`` (``cross_section`` lives
        # only on the EngineLstmPanelReader adapter). The old call raised
        # AttributeError into the except below -> permanent "—" on the coverage
        # card while the Round-Table verdict (agents.py, cross_section_at) showed
        # the rank on the same page.
        # #2680: read the ONE ranking seam (``active_cross_section``) — when the
        # smoothing flag is on, the board votes on the trend table, so the card MUST
        # show the same basis or it re-opens exactly the divergence #2587 closed.
        try:
            from core.report.lstm_panel_store import (
                active_cross_section,
                cross_section_standing,
                get_store,
            )

            _xsec = active_cross_section(get_store(), datetime.now(timezone.utc))
            _pct, _rank, _n = cross_section_standing(_xsec, r.symbol)
        except Exception as _exc:  # noqa: BLE001 — panel unavailable = abstain
            logger.warning(
                "Specialist synthesis card panel unavailable for %s: %s", r.symbol, _exc
            )
            _pct, _rank, _n = None, None, None
        result["lstm_xsec_percentile"] = _round_or_none(_pct, 1)
        result["lstm_xsec_rank"] = _rank
        result["lstm_xsec_universe"] = _n
        # OWN-MODEL price range (HAR-RV on last close) + hard price stats.
        result["last_close"] = _round_or_none(getattr(r, "last_close", None), 2)
        result["price_band_low"] = _round_or_none(getattr(r, "price_band_low", None), 2)
        result["price_band_high"] = _round_or_none(
            getattr(r, "price_band_high", None), 2
        )
        result["chg_20d_pct"] = _round_or_none(getattr(r, "chg_20d_pct", None), 1)
        result["dist_52w_high_pct"] = _round_or_none(
            getattr(r, "dist_52w_high_pct", None), 1
        )
    # RQ-1 B2 (#1522): per-source freshness ("as of" + stale badge). Additive keys ONLY when  # noqa: E501
    # enabled -> exact key-set / BORA byte-identity hold with the flag OFF (same contract as  # noqa: E501
    # the report-quality badge above). "as of" = newest filing date per source; data_stale  # noqa: E501
    # flips when even the freshest source is older than the SLA. From the report's own lists.  # noqa: E501
    if getattr(config.get_config(), "SPECIALIST_FRESHNESS_ENABLED", False):

        def _as_of(filings):
            dates = [
                f.get("filed", "")
                for f in (filings or [])
                if isinstance(f, dict) and f.get("filed")
            ]
            return max(dates) if dates else None

        _ins = _as_of(getattr(r, "insider_trades", None))
        _evt = _as_of(getattr(r, "material_events", None))
        _act = _as_of(getattr(r, "activist_stakes", None))
        _newest = max([d for d in (_ins, _evt, _act) if d], default=None)
        _sla = int(
            getattr(config.get_config(), "SPECIALIST_FRESHNESS_SLA_DAYS", 30)
        )  # noqa: E501
        _cutoff = (datetime.now(timezone.utc) - timedelta(days=_sla)).strftime(
            "%Y-%m-%d"
        )
        result["insider_as_of"] = _ins
        result["material_events_as_of"] = _evt
        result["activist_as_of"] = _act
        result["data_stale"] = bool(_newest) and _newest < _cutoff
    if full:
        # REPORTS-UX Wave 1 (#2710): serve the note body UNCAPPED for the
        # per-symbol reader so it never truncates. These keys already exist above
        # (capped); here they are overwritten with the full value. The list path
        # (full=False, the default) is untouched -> byte-identical DTO + BORA parity.
        result["about"] = (
            getattr(r, "about", "")
            or getattr(r, "company_summary", "")
            or f"{sym}: overview unavailable this cycle."
        )
        result["investment_thesis"] = getattr(r, "investment_thesis", "") or ""
        result["bull_case"] = getattr(r, "bull_case", "") or ""
        result["bear_case"] = getattr(r, "bear_case", "") or ""
        result["news_summary"] = getattr(r, "news_summary", "") or ""
        result["alternative_signals"] = getattr(r, "alternative_signals", "") or ""
        result["summary"] = getattr(r, "summary", "") or ""
    return result


@app.get("/specialist-report/{symbol}")
async def get_specialist_report(
    symbol: str,
    fields: Optional[str] = None,
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """REPORTS-UX Wave 1 (#2710): the FULL, untruncated report for one symbol.

    The bulk /specialist-reports list caps the note body (a ~500-symbol payload
    must stay light); this per-symbol route serves it UNCAPPED (full=True) so the
    expanded reader never truncates. ``?fields=summary`` returns a light
    projection (no body) for cheap row-gating. 404 when the symbol has no cached
    report; documented empty-state when the registry is disabled (G1 pattern).
    """
    registry = getattr(engine, "specialist_registry", None)
    if registry is None:
        return {
            "status": "unavailable",
            "message": "StockSpecialistRegistry not running on this deployment.",  # noqa: E501
            "report": None,
        }
    r = registry.get_report(symbol.upper())
    if r is None:
        raise HTTPException(
            status_code=404, detail=f"No cached report for {symbol.upper()}."
        )
    if fields == "summary":
        _ua = getattr(r, "updated_at", None)
        _conf = getattr(r, "confidence", None)
        return {
            "status": "ok",
            "report": {
                "symbol": symbol.upper(),
                "recommendation": getattr(r, "recommendation", "hold"),
                "confidence": round(_conf, 3) if _conf is not None else 0.0,
                "escalate": bool(getattr(r, "escalate", False)),
                "updated_at": _ua.isoformat() if hasattr(_ua, "isoformat") else None,
                "data_stale": bool(getattr(r, "data_stale", False)),
                "report_quality": getattr(r, "report_quality", None),
            },
        }
    return {
        "status": "ok",
        "report": _serialize_specialist_report(symbol.upper(), r, full=True),
    }


@app.get("/specialist-reports")
async def get_specialist_reports(_: None = Depends(require_engine_key)):  # noqa: B008
    """Cached SpecialistReports for the desktop console cards.

    Documented empty state (G1 spec): while the StockSpecialistRegistry is
    disabled on main (`base.py _init_specialist_registry` → None), this
    returns 200 with an empty list — a stable API contract before data flows.
    """
    registry = getattr(engine, "specialist_registry", None)
    if registry is None:
        return {
            "status": "unavailable",
            "message": "StockSpecialistRegistry not running on this deployment.",  # noqa: E501
            "reports": [],
            "registry_status": {},
        }
    all_reports = registry.get_all_reports()
    escalations = registry.get_escalations()
    status_info = registry.get_status()
    reports_out = [
        _serialize_specialist_report(sym, r)
        for sym, r in sorted(all_reports.items())  # noqa: E501
    ]
    return {
        "status": "ok",
        "total": len(reports_out),
        "escalations": len(escalations),
        "registry_status": status_info,
        "reports": reports_out,
    }


@app.get("/api/entitlement/status")
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
        "paywall_enabled": bool(getattr(config.get_config(), "PAYWALL_ENABLED", False)),
        # GTM-2 (#1809) T5: the Lemon Squeezy hosted checkout URL for "Get Senior"; None
        # until ops provisions it → the console falls back to the key-activation panel.
        "checkout_url": (
            getattr(config.get_config(), "LEMONSQUEEZY_CHECKOUT_URL", "") or ""
        ).strip()
        or None,
        # GTM-2 (#1809): Finance-controlled display price for the paid Senior card (one
        # config source, not hardcoded in the UI). None if unset → card shows a placeholder.
        "price_display": (
            getattr(config.get_config(), "LEMONSQUEEZY_PRICE_DISPLAY", "") or ""
        ).strip()
        or None,
    }


@app.get("/round-table-decisions")
async def get_round_table_decisions_route(
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """Latest Round-Table decision per symbol (newest first, G1a store)."""
    from core.round_table.recent_decisions import (  # noqa: E501
        get_recent_round_table_decisions,
    )

    decisions = get_recent_round_table_decisions(limit=200)
    return {"status": "ok", "total": len(decisions), "decisions": decisions}


@app.get("/round-table/{symbol}")
async def get_round_table_for_symbol(
    symbol: str, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Latest Round-Table verdict for one symbol, in the console's senators
    shape. Error-shaped (but 200) with empty senators when no decision exists
    — never raises."""
    from core.round_table.recent_decisions import get_round_table_decision

    sym = (symbol or "").strip().upper()
    empty = {"status": "error", "symbol": sym, "senators": [], "score": None}
    if not sym:
        return empty
    latest = get_round_table_decision(sym)
    if latest is None:
        return empty
    senators = []
    for v in latest.get("votes", []) or []:
        signal = str(v.get("signal", "")).upper()
        vote = {"BUY": "BULL", "SELL": "BEAR", "HOLD": "ABSTAIN"}.get(
            signal, "ABSTAIN"
        )  # noqa: E501
        senators.append(
            {
                "name": str(v.get("agent_name") or v.get("name") or ""),
                "vote": vote,
                "score": v.get("score"),
                "weight": v.get("weight"),
                "reasoning": v.get("reasoning") or "",
                "vetoed": bool(v.get("vetoed", False)),
            }
        )
    return {
        "status": "ok",
        "symbol": sym,
        "senators": senators,
        "score": latest.get("consensus_score"),
        "signal_action": latest.get("signal_action"),
        "timestamp": latest.get("timestamp"),
    }


# ── HITL human-in-the-loop API (PR-0a-ii-6, EU AI Act Art. 14) ──────────────────  # noqa: E501
# The frozen cross-lane contract for the frontend Decisions-approval + Policy-settings UI  # noqa: E501
# (Session A). Auth-gated like the sibling console GETs (X-Engine-Key via require_engine_key).  # noqa: E501
# The whole surface is DORMANT in effect: the queue is only ever populated when HITL_ENABLED.  # noqa: E501
from typing import Any, List, Optional  # noqa: E402

from pydantic import ConfigDict, Field  # noqa: E402


class HitlQueueItemDTO(BaseModel):
    """One order awaiting human approval (the pending-queue item the UI renders)."""  # noqa: E501

    approval_id: str
    user_id: str
    symbol: str
    action: str
    qty: float
    price: float
    conviction: float
    target_weight: float
    created_at: str


class HitlPendingResponse(BaseModel):
    items: List[HitlQueueItemDTO]


class HitlPolicyDTO(BaseModel):
    """The full HITL policy (GET response). ``HITL_ENABLED`` is shown read-only."""  # noqa: E501

    HITL_ENABLED: bool
    HITL_MAX_VALUE_PER_TRADE: float
    HITL_MAX_VALUE_PER_DAY: float
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    HITL_EXPIRY_SECONDS: int


class HitlPolicyUpdateDTO(BaseModel):
    """The runtime-adjustable limits (POST body). ``extra="forbid"`` ⇒ a POST that includes  # noqa: E501
    ``HITL_ENABLED`` (or any unknown key) is rejected with HTTP 422 — enabling HITL is the  # noqa: E501
    env+redeploy step (C2/M5), never an API toggle, so the flag is structurally unsettable.  # noqa: E501
    """

    model_config = ConfigDict(extra="forbid")

    HITL_MAX_VALUE_PER_TRADE: float = Field(ge=0)
    HITL_MAX_VALUE_PER_DAY: float = Field(ge=0)
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    # ge=1 .. le=86_400 (24h): an approval window must be bounded — a near-infinite expiry would  # noqa: E501
    # quietly defeat the "a human must act in bounded time" posture.
    HITL_EXPIRY_SECONDS: int = Field(ge=1, le=86_400)


class HitlApproveRequest(BaseModel):
    approval_id: str


class HitlRejectRequest(BaseModel):
    approval_id: str
    reason: Optional[str] = None


class HitlActionResponse(BaseModel):
    success: bool
    approval_id: Optional[str] = None
    detail: Optional[str] = None


def _hitl_policy_dto() -> HitlPolicyDTO:
    from core import hitl_gate

    return HitlPolicyDTO(**hitl_gate.policy_snapshot())


# ── #2653 (Epic #2655, Weg 1): portfolio sector-constraint governance ────────────
# DEDICATED endpoint — deliberately NOT the iron-dome policy path: sector caps are
# portfolio-sizing governance; the Iron Dome's compliance hard-floors stay a separate,
# untouched object (epic guardrail). Reuses the four-eyes MACHINERY (distinct approver in
# the enterprise edition) and the Art-14 WORM chain (strict write BEFORE the mutation).


class PortfolioConstraintsProposeRequest(BaseModel):
    """Manual propose path (the EOD assessment of #2652 stages its own pending)."""

    caps: dict
    actor: str = Field(min_length=1)


class PortfolioConstraintsApproveRequest(BaseModel):
    token: str = Field(min_length=1)
    approver: str = Field(min_length=1)


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


@app.get("/api/admin/portfolio-constraints/pending")
async def get_portfolio_constraints_pending(
    _: None = Depends(require_engine_key),
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


@app.post("/api/admin/portfolio-constraints/propose")
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


@app.post("/api/admin/portfolio-constraints/approve")
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
    await log_policy_event(
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


@app.get("/api/hitl/pending", response_model=HitlPendingResponse)
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


@app.post("/api/hitl/approve", response_model=HitlActionResponse)
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
    bump_usage("hitl_approvals")
    return HitlActionResponse(
        success=True,
        approval_id=body.approval_id,
        detail="approved; queued for execution",
    )


@app.post("/api/hitl/reject", response_model=HitlActionResponse)
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


# ── LIVE-1 T4 (#1427): Art.-14 live-trading enablement WORM endpoints ───────────  # noqa: E501
# Record a deliberate enable/disable decision on the SAME tamper-evident SHA-256 chain as the  # noqa: E501
# HITL audits, BEFORE the desktop shell is allowed to flip SHADOW_MODE off (audit-before-enable).  # noqa: E501
# The shell verifies the record via audit-chain.cjs `verifyAuditChain` and only then boots live.  # noqa: E501


class PortfolioShapeRequest(BaseModel):
    """#3132 — a deliberate portfolio-structure change plus the operator's acknowledgement.

    Values are CLAMPED server-side, never rejected: a value outside the range is an
    operating question, not an attack. The console's sliders are convenience — a direct API
    call must see the same bounds (``core.portfolio_shape``).
    """

    positions: Optional[float] = None
    hold_days: Optional[float] = None
    vol_target: Optional[float] = None
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class PortfolioShapeResponse(BaseModel):
    success: bool
    applied: dict
    changes: list
    detail: str


class SettingsDeviationItem(BaseModel):
    """One default-deviation the operator decided about — str-only (WORM verbatim)."""

    key: str = Field(min_length=1)
    current: str
    default: str


class SettingsDeviationAckRequest(BaseModel):
    """#3156 (UXC-1 S3) — the operator's keep/reset decision at the live switch."""

    decision: str = Field(min_length=1)
    deviations: List[SettingsDeviationItem] = Field(default_factory=list)
    nonce: str = Field(min_length=1)
    switch_id: Optional[str] = None


class SettingsDeviationAckResponse(BaseModel):
    success: bool
    decision: str
    detail: str


class TradingSettingsRequest(BaseModel):
    """#3155 (UXC-1 S2) — a deliberate settings change plus the operator's acknowledgement.

    ``settings`` holds partial registry keys (``core.trading_settings.REGISTRY``).
    Values are CLAMPED server-side, never rejected — but an UNKNOWN key (incl. any
    compliance guardrail like ``HARD_STOP_LOSS_PCT``) is a 422, never silently ignored.
    """

    settings: Dict[str, Any] = Field(default_factory=dict)
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class TradingSettingsResponse(BaseModel):
    success: bool
    applied: dict
    changes: list
    detail: str


class LiveEnableRequest(BaseModel):
    """Operator's deliberate Art.-14 acknowledgement + a unique nonce (replay-distinct)."""  # noqa: E501

    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class LiveEnableResponse(BaseModel):
    success: bool
    action: str
    detail: Optional[str] = None


class SwitchReconcileRequest(BaseModel):
    """#2277 INC-3 (§4b R4): the console's post-switch reconcile telemetry — turned into a
    ``health_reconcile`` span so a failed Live⇄Paper reconcile is reconstructable from the export.

    Every field is optional so the fail-open frontend post never 422s (a telemetry post must never
    block the switch)."""

    switch_id: Optional[str] = None
    attempts: int = 0
    settled_status: Optional[str] = None
    paper_trading: Optional[bool] = None


async def _enforce_replay_distinct_nonce(action: str, nonce: str) -> None:
    """Make ``LiveEnableRequest.nonce``'s documented "replay-distinct" invariant real (LSR R7, #2255).

    Fixes audit #18: previously the nonce was passed straight to the WORM append with no uniqueness
    check, so a replayed nonce silently wrote a second ``live_enablement`` record. Here we scan the
    chain **once** and, if the nonce is already present, raise **409 BEFORE any WORM write** — so a
    duplicate record can never be appended. Emits ``live_enable.nonce_rejected{action}`` on rejection
    (I-3, matches the ``order_executor`` span style). ``action`` ∈ {enable, disable}.
    """
    if nonce in await hitl_gate.collect_live_enablement_nonces():
        with tracer.start_as_current_span("live_enable.nonce_rejected") as span:
            span.set_attribute("action", action)
        raise HTTPException(
            status_code=409,
            detail="nonce already used (replay-distinct)",
        )


@app.post(
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
    await _enforce_replay_distinct_nonce("enable", body.nonce)

    # #2277 INC-1: the nonce IS the switch_id — the correlation id that the UI also threads onto the
    # engine restart (AAA_SWITCH_ID) and every boot/degrade/revoke span. Emit a domain `live.enable`
    # span carrying it so the diagnose-export can join the authoritative enable action to the boot it
    # armed. Best-effort: the span must never fail the audited enable.
    with _tracer.start_as_current_span("live.enable") as _span:
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


@app.post(
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
    await _enforce_replay_distinct_nonce("portfolio_shape", body.nonce)

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


@app.get("/api/trading-settings")
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


@app.post(
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
    await _enforce_replay_distinct_nonce("trading_settings", body.nonce)

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
        with _tracer.start_as_current_span("settings.apply") as _span:
            _span.set_attribute("keys", ",".join(sorted(c["key"] for c in changes)))
            _span.set_attribute("nonce", body.nonce)
            _span.set_attribute("restart_required", "true")
            _span.set_attribute("actor", "operator")
    except (
        Exception
    ) as exc:  # noqa: BLE001 — telemetry must never break the audited path
        logger.warning(
            "[SETTINGS] settings.apply span failed (apply unaffected): %s", exc
        )

    return TradingSettingsResponse(
        success=True,
        applied=new,
        changes=changes,
        detail="trading-settings change recorded on the WORM chain",
    )


@app.post(
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
    await _enforce_replay_distinct_nonce("settings_deviation_ack", body.nonce)

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
        with _tracer.start_as_current_span("settings.deviation_ack") as _span:
            _span.set_attribute("decision", body.decision)
            _span.set_attribute("nonce", body.nonce)
            _span.set_attribute("switch_id", switch_id)
            _span.set_attribute("keys", ",".join(sorted(d["key"] for d in deviations)))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — telemetry must never break the audited path
        logger.warning(
            "[SETTINGS] settings.deviation_ack span failed (record unaffected): %s", exc
        )

    return SettingsDeviationAckResponse(
        success=True,
        decision=body.decision,
        detail="deviation decision recorded on the WORM chain",
    )


@app.post(
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
    await _enforce_replay_distinct_nonce("disable", body.nonce)

    _reason = "live trading disabled by operator"
    _actor = "operator"

    # 1) Trip the existing kill-switch: _local_halted=True synchronously (every subsequent
    #    check_halt raises → no new submit) AND async_mass_cancel (aborts resting/in-flight
    #    orders). This is the load-bearing stop; force_paper alone cannot redirect self.api.
    with _tracer.start_as_current_span("live_disable.kill_switch_tripped") as _span:
        _span.set_attribute("reason", _reason)
        _span.set_attribute("actor", _actor)
        # #2277 INC-1: nonce == switch_id — join this disable action to the restart it triggered.
        _span.set_attribute("switch_id", body.nonce)
        # #2467 FIX 4b: fail_closed=False — this endpoint force-papers (step 2) + writes the WORM
        # disable (step 3) itself, so trip() must NOT duplicate them.
        kill_switch.trip(reason=_reason, fail_closed=False)

    # 2) Belt-and-suspenders: future / next-cycle clients select the paper account.
    with _tracer.start_as_current_span("live_disable.forced_paper") as _span:
        _span.set_attribute("reason", _reason)
        _span.set_attribute("switch_id", body.nonce)
        config.force_paper_trading(reason=_reason)

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


@app.post("/api/telemetry/switch-reconcile", status_code=202)
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
        with _tracer.start_as_current_span("health_reconcile") as _span:
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


@app.get("/api/hitl/policy", response_model=HitlPolicyDTO)
async def hitl_get_policy(_: None = Depends(require_engine_key)):  # noqa: B008
    """The current HITL autonomy policy (all six values; ``HITL_ENABLED`` read-only)."""  # noqa: E501
    return _hitl_policy_dto()


@app.post("/api/hitl/policy", response_model=HitlPolicyDTO)
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


# PR F: seed the anonymous api-hit counter with the app's registered route
# TEMPLATES (machine names only). Bounded — only these templates are ever counted;  # noqa: E501
# a raw path with IDs / an unknown route is ignored. Runs at import time (after all  # noqa: E501
# routes are declared) so the hot-path bump never has to introspect the router.
try:
    register_api_routes(
        getattr(r, "path", None)
        for r in app.router.routes
        if getattr(r, "path", None)  # noqa: E501
    )
except Exception:  # noqa: BLE001 — route registration must never break import
    pass


def main():
    port = _find_engine_port()
    host = os.environ.get("ENGINE_HOST", "127.0.0.1")
    logging.info("Engine starting on http://%s:%s", host, port)
    # INC-1a (#2215): bind-retry on 10048/EADDRINUSE — see uvicorn_boot for the desktop
    # restart TIME_WAIT race. Same protection as the `python -m core.engine` entrypoint.
    from core.engine.uvicorn_boot import run_with_bind_retry

    run_with_bind_retry(app, host, port)


if __name__ == "__main__":
    main()
