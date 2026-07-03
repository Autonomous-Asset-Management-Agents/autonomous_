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
from core.telemetry import init_telemetry  # noqa: E402 (intentional first import)

init_telemetry(service_name="aaa-backend")

import asyncio
import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Dict

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
    Request,
    Response,
    WebSocket,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

import config
import core.strategies as strategies
from core.ai_components import answer_chat_with_fallback
from core.auth import require_engine_key, verify_user_id_sig
from core.database.session import ensure_local_db_ready
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


def _init_trading_clients():
    """Synchronous helper: connect to Alpaca and construct BotEngine.

    Called from the lifespan handler via create_task so the event loop
    (and uvicorn's port-8080 bind) is never blocked.
    """
    global trading_api, data_api, engine
    if config.API_KEY:
        try:
            is_paper = "paper" in config.BASE_URL.lower()
            api_key_str = config.get_secret_str(config.API_KEY)
            api_secret_str = config.get_secret_str(config.API_SECRET)

            trading_api = TradingClient(
                api_key_str, api_secret_str, paper=is_paper
            )  # noqa: E501
            data_api = StockHistoricalDataClient(api_key_str, api_secret_str)
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
    await _init_engine_impl()


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
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Kein DELETE/PUT/PATCH exponiert
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Bot-Api-Key",
        "X-Engine-Key",
    ],  # noqa: E501
)
app.add_middleware(OtelSpanMiddleware)  # Task #361: spans for every request


# --- Health & Diagnostics ---


@app.get("/ready")
async def ready_check():
    return {"status": "ready"}


@app.get("/health")
async def health_check():
    # Return 200 immediately while engine is still starting up.
    # Cloud Run TCP probe only checks port reachability, not response body.
    if engine is None:
        return {
            "status": "starting",
            "redis": "unknown",
            "timestamp": time.time(),
            "version": "2.5.0",
            "strategy_running": False,
            "system_halted": False,
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
            acc = engine.api.get_account()
            alpaca_status = "ok" if acc.status == "ACTIVE" else acc.status
            # SECURITY: Kein Equity in unauthentifiziertem Response — nur funded/unfunded  # noqa: E501
            alpaca_details = {
                "status": acc.status,
                "is_funded": float(acc.equity) > 0,
            }  # noqa: E501
            clock = engine.api.get_clock()
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
        lstm_paths = get_lstm_paths()
        lstm_loaded = all(os.path.exists(p) for p in lstm_paths)
        rl_file = _rl_agent_file(
            getattr(config, "RL_MODEL_VERSION", "rl_agent_v3_dsr")
        )  # noqa: E501
        rl_loaded = os.path.exists(rl_file)

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
    overall_status = compute_overall_status(
        {
            "engine_ready": engine is not None,
            "components_degraded": (
                alpaca_status != "ok" or "missing" in models_status.values()
            ),
            "strategy_running": strategy_running,
            "is_market_open": is_market_open,
            "scan_active": scan_active,
        }
    )
    # /health/deep (unlike /engine-diagnostics) attaches a 500 on any non-healthy,  # noqa: E501
    # non-starting verdict — preserving the original critical_failure semantics.  # noqa: E501
    if overall_status in ("degraded", "inactive", "stalled"):
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return {
        "status": overall_status,
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

    from core.cycle_watchdog import cycle_watchdog
    from core.latency_watchdog import latency_watchdog
    from core.ml_watchdog import ml_watchdog

    return {
        "cycle": _one(cycle_watchdog.status),
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
    from core.llm.health import resolved_provider_name
    from core.llm.telemetry import get_llm_counters

    out = dict(get_llm_counters())
    out["llm_provider"] = resolved_provider_name()
    out["llm_model_name"] = getattr(config, "GEMINI_MODEL_NAME", None)
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
    result = engine.start_live_strategy()
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


@app.get(
    "/recent-trades",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_recent_trades(request: Request, limit: int = 20):
    """Return the last N filled orders from Alpaca."""
    try:
        api_client = engine.api
        user_id = request.headers.get("X-User-Id")
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
            except Exception:
                pass

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        orders = await asyncio.to_thread(api_client.get_orders, req)
        filled = [
            o
            for o in orders
            if str(getattr(o, "status", "")).lower() == "filled"  # noqa: E501
        ]
        filled = sorted(
            filled,
            key=lambda o: getattr(o, "filled_at", None)
            or getattr(o, "submitted_at", None)
            or "",
            reverse=True,
        )[:limit]

        trades = []
        for o in filled:
            filled_at = getattr(o, "filled_at", None) or getattr(
                o, "submitted_at", None
            )
            trades.append(
                {
                    "id": str(getattr(o, "id", "")),
                    "symbol": str(getattr(o, "symbol", "")),
                    "side": str(getattr(o, "side", "")).lower(),
                    "qty": float(
                        getattr(o, "filled_qty", None)
                        or getattr(o, "qty", 0)
                        or 0  # noqa: E501
                    ),
                    "price": float(getattr(o, "filled_avg_price", None) or 0),
                    "filled_at": str(filled_at) if filled_at else None,
                }
            )

        return {"status": "success", "trades": trades}
    except Exception as e:
        logging.error("recent_trades failed: %s", e, exc_info=True)
        return {"status": "error", "trades": [], "message": "internal_error"}


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
        if api_client:
            try:
                acc = api_client.get_account()
                positions = api_client.get_all_positions()
                real_equity = float(acc.equity or 0)
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
                logging.debug("Live account fallback failed: %s", api_err)
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
                        "summary": summary,
                        "positions": final_positions,
                        "equity": final_equity,
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
                    "summary": None,
                    "positions": real_positions,
                    "equity": real_equity,
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
        return {"status": "error", "message": "internal_error"}


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
        spy_df = engine.data_provider.get_data(
            "SPY", now_utc, days=max(10, elapsed_days + 5)
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
            spy_first_close = spy_map.get(first_date) or float(spy_df.iloc[0]["close"])
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


@app.get(
    "/benchmark-equity",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_benchmark_equity():
    try:
        r = RedisClient.get_sync_redis()
        data_str = r.get("benchmark_equity_data")
        _cached = None
        if data_str:
            try:
                _cached = __import__("json").loads(data_str)
            except Exception:
                _cached = None
        # Rebuild fully from the DB when the cache is absent OR "thin". _append_live_equity_to_
        # benchmark (core/engine/base.py) warms this key every engine cycle with `points` only
        # (no initial_capital / spy_points), which otherwise pre-empts this reconstruction — so
        # the handler returned a points-only cache forever: NO S&P line + a history truncated to
        # base.py's per-cycle appends. A missing initial_capital marks a thin cache -> rebuild.
        if not _cached or not _cached.get("initial_capital"):
            # Fallback to database query if Redis cache is empty/cold
            import json
            from datetime import timezone

            import sqlalchemy as sa

            from core.database.models import PortfolioSnapshot
            from core.database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                dialect = session.bind.dialect.name
                if dialect == "postgresql":
                    stmt = (
                        sa.select(PortfolioSnapshot)
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
                        .order_by(PortfolioSnapshot.timestamp.asc())
                    )
                    result = await session.execute(stmt)
                    records = list(result.scalars().all())

            if not records:
                return {
                    "points": [],
                    "spy_points": [],
                    "strategy": "RLAgent",
                    "initial_capital": None,
                    "message": "No benchmark run yet.",
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

            spy_points, spy_first_close = _reconstruct_spy_points(
                points, initial_capital
            )

            reconstructed_data = {
                "points": points,
                "spy_points": spy_points,
                "spy_first_close": spy_first_close,
                "initial_capital": initial_capital,
                "start_date": earliest_snap.timestamp.strftime("%Y-%m-%d"),
                "end_date": records[-1].timestamp.strftime("%Y-%m-%d"),
                "strategy": earliest_snap.strategy_name or "RLAgent",
                "final_equity": records[-1].total_equity,
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
                "start_date": earliest_snap.timestamp.strftime("%Y-%m-%d"),
                "end_date": records[-1].timestamp.strftime("%Y-%m-%d"),
                "strategy": earliest_snap.strategy_name or "RLAgent",
                "initial_capital": initial_capital,
                "final_equity": records[-1].total_equity,
            }

        data = _cached
        points = list(data.get("points", []))
        spy_points = list(data.get("spy_points", []))
        initial_capital = data.get("initial_capital")
        today_str = date.today().strftime("%Y-%m-%d")

        if engine.api and initial_capital:
            try:
                acc = engine.api.get_account()
                live_equity = float(acc.equity or 0)
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
            spy_points, spy_first_close = _reconstruct_spy_points(
                points, initial_capital
            )
            if spy_points:
                data["spy_points"] = spy_points
                data["spy_first_close"] = spy_first_close
                try:
                    r.set("benchmark_equity_data", __import__("json").dumps(data))
                except Exception as heal_err:
                    logging.warning("Failed to heal benchmark cache: %s", heal_err)
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

        return {
            "points": points,
            "spy_points": spy_points,
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "strategy": data.get("strategy", "RLAgent"),
            "initial_capital": initial_capital,
            "final_equity": data.get("final_equity"),
        }
    except Exception as e:
        logging.error("benchmark_equity failed: %s", e, exc_info=True)
        return {"points": [], "spy_points": [], "message": "internal_error"}


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


@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
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


def _serialize_specialist_report(sym: str, r) -> Dict:
    """Bundle-DTO-compatible report serialization (exact key-set contract).

    Fields main's SpecialistReport does not carry yet (about/company_summary/
    edge_signals/headlines — insight-quality features still bundle-only) are
    emitted with empty defaults so the console contract holds.
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
        "political_trades_count": len(
            getattr(r, "political_trades", None) or []
        ),  # noqa: E501
        "material_events_count": len(
            getattr(r, "material_events", None) or []
        ),  # noqa: E501
        "reddit_mentions": getattr(r, "reddit_mentions", 0) or 0,
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
    return result


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
from typing import List, Optional  # noqa: E402

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


class LiveEnableRequest(BaseModel):
    """Operator's deliberate Art.-14 acknowledgement + a unique nonce (replay-distinct)."""  # noqa: E501

    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class LiveEnableResponse(BaseModel):
    success: bool
    action: str
    detail: Optional[str] = None


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

    await hitl_gate.log_live_enablement_event(
        action="enable",
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        strict=True,
    )
    return LiveEnableResponse(
        success=True,
        action="enable",
        detail="live-enablement recorded on the WORM chain",
    )


@app.post(
    "/api/live/disable", response_model=LiveEnableResponse, status_code=201
)  # noqa: E501
async def live_disable(
    body: LiveEnableRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Revoke live trading — a ``disable`` event on the same WORM chain. ``verifyAuditChain`` treats  # noqa: E501
    a later disable as revoking an earlier enable, so the engine returns to fail-closed paper.  # noqa: E501
    """
    from core import hitl_gate

    await hitl_gate.log_live_enablement_event(
        action="disable",
        acknowledgment=body.acknowledgment,
        nonce=body.nonce,
        strict=True,
    )
    return LiveEnableResponse(
        success=True,
        action="disable",
        detail="live-disable recorded on the WORM chain",  # noqa: E501
    )


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
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
