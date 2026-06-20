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
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Dict

import psutil
import uvicorn
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from fastapi import Depends, FastAPI, Request, Response, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware

import config
import core.strategies as strategies
from core.ai_components import answer_chat_with_fallback
from core.auth import require_engine_key, verify_user_id_sig
from core.database.session import ensure_local_db_ready
from core.otel_middleware import OtelSpanMiddleware
from core.redis_client import RedisClient
from core.secret_manager_utils import oauth_secrets
from core.strategies import _rl_agent_file
from core.structured_logging import setup_logging
from core.user_wallet_store import wallet_store
from models.torch_model import get_lstm_paths

from .base import BotEngine

setup_logging()

_START_TIME = time.time()

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

            trading_api = TradingClient(api_key_str, api_secret_str, paper=is_paper)
            data_api = StockHistoricalDataClient(api_key_str, api_secret_str)
            acc = trading_api.get_account()
            logging.info(
                "Alpaca API connected. Status=%s equity=%s", acc.status, acc.equity
            )
        except Exception as _alpaca_err:
            logging.error(
                "Alpaca API init failed — engine clients will be None. Error: %s.",
                _alpaca_err,
            )
            trading_api = None
            data_api = None
    else:
        logging.warning(
            "ALPACA_API_KEY not set — live trading and portfolio data disabled."
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
            "Failed to fetch dynamic remote config from DB; using env vars. Error: %s",
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
    # sites although its docstring always said "called by engine startup code").
    # Fail-closed but LOUD (§5.6): a bootstrap failure (disk full, AV file lock)
    # must not become an invisible never-ready engine.
    try:
        await ensure_local_db_ready()
    except Exception as exc:
        logging.critical("G0a DB bootstrap failed — engine will NOT start: %s", exc)
        raise
    await _init_engine_impl()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: heavy init runs in background AFTER uvicorn binds port 8080.

    Cloud Run's TCP startup probe only checks that port 8080 is open.
    By firing initialize_engine_async as a non-blocking background task and yielding
    immediately, uvicorn opens port 8080 first, satisfying the probe.
    The config fetch and engine load continue initializing in the background.
    /health returns status='starting' until engine is ready.
    """
    asyncio.create_task(_init_engine_async())
    yield  # uvicorn binds port 8080 here — Cloud Run probe satisfied immediately
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
    allow_headers=["Authorization", "Content-Type", "X-Bot-Api-Key", "X-Engine-Key"],
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
        }
    redis_healthy = await RedisClient.check_health()
    return {
        "status": "healthy",
        "redis": "connected" if redis_healthy else "disconnected",
        "timestamp": time.time(),
        "version": "2.5.0",
        "strategy_running": engine.strategy_running.is_set(),
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
            "latency_metrics": {"avg_cycle_ms": 0, "max_cycle_ms": 0, "last_cycle": {}},
            "timestamp": time.time(),
        }
    avg_latency = (
        sum(engine._cycle_latencies) / len(engine._cycle_latencies)
        if engine._cycle_latencies
        else 0
    )
    max_latency = max(engine._cycle_latencies) if engine._cycle_latencies else 0
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
            # SECURITY: Kein Equity in unauthentifiziertem Response — nur funded/unfunded
            alpaca_details = {"status": acc.status, "is_funded": float(acc.equity) > 0}
            clock = engine.api.get_clock()
            is_market_open = clock.is_open
        except Exception as e:
            # Log the raw exception server-side; return a generic marker to
            # unauthenticated callers (see /health/deep is publicly proxied).
            logging.error("deep_health: Alpaca probe failed: %s", e, exc_info=True)
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
    if engine.active_strategy:
        lstm_loaded = getattr(engine.active_strategy, "torch_model", None) is not None
        rl_loaded = getattr(engine.active_strategy, "rl_model", None) is not None
    else:
        lstm_paths = get_lstm_paths()
        lstm_loaded = all(os.path.exists(p) for p in lstm_paths)
        rl_file = _rl_agent_file(getattr(config, "RL_MODEL_VERSION", "rl_agent_v3_dsr"))
        rl_loaded = os.path.exists(rl_file)

    models_status["lstm"] = "ok" if lstm_loaded else "missing"
    models_status["rl"] = "ok" if rl_loaded else "missing"

    strategy_running = engine.strategy_running.is_set()
    last_scan_age = time.time() - engine._last_scan_time
    scan_active = last_scan_age < (config.STRATEGY_MONITOR_INTERVAL_SECONDS * 1.5)

    overall_status = "healthy"
    critical_failure = False

    if alpaca_status != "ok" or "missing" in models_status.values():
        overall_status = "degraded"
        critical_failure = True
    if not strategy_running:
        overall_status = "inactive"
        critical_failure = True
    if is_market_open and not scan_active:
        overall_status = "stalled"
        critical_failure = True

    if critical_failure:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return {
        "status": overall_status,
        "timestamp": time.time(),
        "is_market_open": is_market_open,
        "strategy_running": strategy_running,
        "last_scan_age_seconds": round(last_scan_age, 1),
        "components": {
            "alpaca": {"status": alpaca_status, "details": alpaca_details},
            "cloud_sql": {"status": "ok" if cloud_sql_connected else "disconnected"},
            "models": models_status,
        },
        "version": "1.1.0",
    }


# --- INF-8: Staging Quality Gate ---


@app.get(
    "/staging-gate",
)
async def staging_gate():
    """INF-8: Deterministic staging health gate.

    Used by deploy-backend.yml smoke test to verify correct staging deployment.
    No auth required — internal staging use only (not proxied by aaa-api-public).

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
                "message": "SHADOW_MODE must be True on staging to prevent real order execution.",
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
                "message": "Redis ping failed — staging environment is not healthy.",
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
            "message": "Engine failed to start. Check Alpaca API connection and secrets.",
        }
    return {"status": "success", "message": "Live strategy started."}


@app.post("/stop")
async def stop(_: None = Depends(require_engine_key)):  # noqa: B008
    engine.stop_strategy()
    return {"status": "success"}


@app.get("/strategy")
async def get_strategy():
    return {"strategy": getattr(config, "ACTIVE_STRATEGY", "RLAgent")}


@app.post("/set-strategy")
async def set_strategy(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    name = (p.get("strategy") or "").strip()
    if name not in strategies.STRATEGY_CLASSES:
        return {
            "status": "error",
            "message": f"Unknown strategy. Use one of: {list(strategies.STRATEGY_CLASSES.keys())}",
        }
    config.ACTIVE_STRATEGY = name
    logging.info("Strategy mode set to: %s", name)
    return {"status": "success", "strategy": name}


# --- Hot-Swap API (Epic 2.3-Pre / PR-C) ---

from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core.agent_registry import get_global_registry  # noqa: E402
from core.cloud_logger import get_cloud_logger  # noqa: E402
from core.exceptions import SwapInProgressError  # noqa: E402


class SwapRequest(BaseModel):
    strategy_name: str
    shadow_mode: bool = False
    force: bool = False  # Bypass Position Lock (Shadow-Mode empfohlen bei force=True)


def verify_firebase_token(request: Request) -> dict:
    """Auth-Guard: verifiziert Auth-Token.

    In Tests wird diese Funktion via pytest.monkeypatch oder patch() überschrieben.
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
        wird der Swap mit HTTP 423 abgelehnt um Compliance-Risiken zu vermeiden.
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
                            f"Swap abgelehnt: {len(positions)} offene Position(en). "
                            "Nur bei leerem Portfolio erlaubt."
                        ),
                        "positions": [p.symbol for p in positions],
                        "hint": "Sende force=true um trotzdem zu swappen (shadow_mode=true empfohlen).",
                    },
                )
        except HTTPException:
            raise
        except Exception as pos_err:
            logging.warning(
                "Position Lock check failed — proceeding without lock: %s", pos_err
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
                f"Registrierte Strategies: {list(registry._strategies.keys())}",
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
    if engine.active_strategy and hasattr(engine.active_strategy, "torch_model"):
        lstm_loaded = engine.active_strategy.torch_model is not None
    return {
        "alpaca_connected": engine.api is not None,
        "strategy_running": engine.strategy_running.is_set(),
        "active_strategy": (
            type(engine.active_strategy).__name__ if engine.active_strategy else None
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
                            f"⚠️ {position.symbol}: Only {qty:.4f} fractional shares"
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
        was_halted = kill_switch.is_halted()
        kill_switch.reset()
        logging.info(
            "🔓 Kill Switch has been RESET via API. Was halted: %s", was_halted
        )
        return {
            "status": "success",
            "message": "Kill switch reset. Call /start-live to resume trading.",
            "was_halted": was_halted,
        }
    except Exception as e:
        logging.error("Failed to reset kill switch: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


# --- Market Data & Portfolio ---


@app.get(
    "/top-picks",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_top_picks():
    return {"status": "success", "picks": getattr(engine, "_last_top_picks", [])}


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
                    tokens = oauth_secrets.get_tokens(wallet["secret_manager_id"])
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
            o for o in orders if str(getattr(o, "status", "")).lower() == "filled"
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
                        getattr(o, "filled_qty", None) or getattr(o, "qty", 0) or 0
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
        return {"status": "success", "symbol": symbol, "range": period, "data": data}
    except Exception as e:
        logging.error("stock_history failed for %s: %s", symbol, e, exc_info=True)
        return {
            "status": "error",
            "symbol": symbol,
            "message": "internal_error",
            "data": [],
        }


@app.get(
    "/portfolio-summary",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_portfolio_summary(request: Request):  # noqa: C901
    try:
        active = getattr(engine, "active_strategy", None)
        user_id = request.headers.get("X-User-Id")

        # Fallback to the engine's global client, but try to load the user's personal client
        api_client = engine.api
        api_message = "No portfolio manager active"

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
                        api_message = "Live personal account"
            except Exception as e:
                logging.error(
                    f"Failed to fetch multi-tenant API client for {user_id}: {e}"
                )

        # If we have a personal api_client, fetch exact positions & equity first.
        # This overrides the Bot's "portfolio manager" score-based positions,
        # because the user dashboard should reflect reality.
        real_positions = None
        real_equity = None
        if api_client:
            try:
                acc = api_client.get_account()
                positions = api_client.get_all_positions()
                real_equity = float(acc.equity or 0)
                api_message = "Live account" if not user_id else "Live personal account"
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

                # Use real positions if available, otherwise fallback to bot's internal state
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
                    final_equity = (
                        None  # Bot scores don't represent total equity accurately
                    )

                total_pnl = (
                    sum(p.get("unrealized_pnl", 0) for p in final_positions)
                    if final_positions
                    else 0
                )
                return {
                    "status": "success",
                    "summary": summary,
                    "positions": final_positions,
                    "equity": final_equity,
                    "recent_debates": debates,
                    "rebalance_recommendations": rebalance_recs,
                    "agent_statuses": getattr(engine, "_last_round_table_state", []),
                    "message": api_message,
                    "total_unrealized_pnl": total_pnl,
                }

        if real_positions is not None:
            total_pnl = (
                sum(p.get("unrealized_pnl", 0) for p in real_positions)
                if real_positions
                else 0
            )
            return {
                "status": "success",
                "summary": None,
                "positions": real_positions,
                "equity": real_equity,
                "agent_statuses": getattr(engine, "_last_round_table_state", []),
                "message": api_message,
                "total_unrealized_pnl": total_pnl,
            }

        return {
            "status": "success",
            "summary": None,
            "positions": [],
            "agent_statuses": getattr(engine, "_last_round_table_state", []),
            "message": api_message,
            "total_unrealized_pnl": 0,
        }
    except Exception as e:
        logging.error("portfolio_summary failed: %s", e, exc_info=True)
        return {"status": "error", "message": "internal_error"}


@app.get(
    "/benchmark-equity",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_benchmark_equity():
    try:
        r = RedisClient.get_sync_redis()
        data_str = r.get("benchmark_equity_data")
        if not data_str:
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
                            sa.func.date_trunc("day", PortfolioSnapshot.timestamp)
                        )
                        .order_by(
                            sa.func.date_trunc("day", PortfolioSnapshot.timestamp),
                            PortfolioSnapshot.timestamp.desc(),
                        )
                    )
                    result = await session.execute(stmt)
                    records = list(result.scalars().all())
                    records.sort(key=lambda x: x.timestamp)
                else:
                    # SQLite: subquery max(timestamp) grouped by date(timestamp)
                    subq = (
                        sa.select(
                            sa.func.date(PortfolioSnapshot.timestamp).label(
                                "snapshot_date"
                            ),
                            sa.func.max(PortfolioSnapshot.timestamp).label("max_ts"),
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

            spy_points = []
            spy_first_close = 1.0
            try:
                t_start = earliest_snap.timestamp
                if t_start.tzinfo is None:
                    t_start = t_start.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                elapsed_days = (now_utc - t_start).days + 1

                spy_df = engine.data_provider.get_data(
                    "SPY", now_utc, days=max(10, elapsed_days + 5)
                )
                if (
                    spy_df is not None
                    and not spy_df.empty
                    and "close" in spy_df.columns
                ):
                    spy_df = spy_df.sort_index()
                    spy_map = {}
                    for idx, row in spy_df.iterrows():
                        spy_map[idx.strftime("%Y-%m-%d")] = float(row["close"])

                    earliest_date_str = t_start.strftime("%Y-%m-%d")
                    spy_first_close = spy_map.get(earliest_date_str)
                    if not spy_first_close:
                        spy_first_close = float(spy_df.iloc[0]["close"])

                    if spy_first_close > 0:
                        last_known_spy = spy_first_close
                        for p in points:
                            p_date = p["date"]
                            spy_close = spy_map.get(p_date, last_known_spy)
                            last_known_spy = spy_close
                            spy_points.append(
                                {
                                    "date": p_date,
                                    "equity": round(
                                        initial_capital * (spy_close / spy_first_close),
                                        2,
                                    ),
                                }
                            )
            except Exception as spy_err:
                logging.warning(
                    "Failed to reconstruct SPY points: %s", spy_err, exc_info=True
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
                    "Failed to save reconstructed benchmark data to Redis: %s", r_err
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

        data = __import__("json").loads(data_str)
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
                    points.append({"date": today_str, "equity": round(live_equity, 2)})
            except Exception:
                pass

        spy_first_close = data.get("spy_first_close")
        if spy_points and spy_first_close and initial_capital and spy_first_close > 0:
            try:
                end_dt = datetime.now(timezone.utc)
                spy_df = engine.data_provider.get_data("SPY", end_dt, days=10)
                if (
                    spy_df is not None
                    and not spy_df.empty
                    and "close" in spy_df.columns
                ):
                    last_close = float(spy_df.iloc[-1]["close"])
                    if last_close > 0 and spy_points[-1].get("date") != today_str:
                        spy_points.append(
                            {
                                "date": today_str,
                                "equity": round(
                                    initial_capital * (last_close / spy_first_close), 2
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
    return {"status": "success", "message": f"Benchmark started. End date: {end_date}."}


@app.post("/run-simulation")
async def run_sim(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    symbol_sample_mode = p.get("symbol_sample_mode", "full_market")
    engine.run_simulation_in_thread(
        p["start_date"], p["end_date"], p["initial_capital"], symbol_sample_mode
    )
    return {"status": "success"}


@app.post("/run-learning")
async def run_learn(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    engine.run_learning_in_thread(p["start_date"], p["end_date"], p["initial_capital"])
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
    symbol = p.get("symbol", "AAPL")
    target_date = p.get("target_date")

    if not target_date:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    if not engine or not engine.data_provider:
        raise HTTPException(
            status_code=503, detail="Engine/DataProvider not available."
        )

    # Fetch T-1 (or target_date) exact daily close (determinism!)
    end_dt = datetime.fromisoformat(target_date)
    if not end_dt.tzinfo:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    try:
        # Request a chunk of data, ending exactly at T-1
        df = engine.data_provider.get_data(symbol, end_dt, days=10)
        if df is None or df.empty:
            raise HTTPException(
                status_code=404, detail=f"No historical data found for {symbol}."
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
        raise HTTPException(status_code=500, detail=f"Data Provider Error: {e}")

    # MiFID II Audit Log
    api_key_header = request.headers.get("x-bot-api-key", "")
    key_ident = (
        api_key_header[:6] + "..."
        if api_key_header and len(api_key_header) > 6
        else "internal"
    )
    logging.info(
        f"MiFID Audit: Force Cycle triggered for {symbol} at target datum {target_date}. Caller Key: {key_ident}"
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
    signal_action = getattr(signal_obj, "action", "NONE") if signal_obj else "NONE"

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
    "/chat", dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)]
)
async def chat(p: Dict):
    message = (p.get("message") or "").strip()
    if not message:
        return {"reply": "Please ask a question.", "message": "Please ask a question."}
    try:
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
        await websocket.send_json({"type": "log", "data": {"message": "Connected"}})
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
      Then   every Span must carry: db.statement, user.id, response.body_length,
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
    # P0-1 (T-SER #1266): preserve the historical 0.0-when-absent fallback but stop
    # `or`-masking a legitimate 0.0 confidence (0.0 is a real value, not a default).
    confidence = getattr(r, "confidence", None)
    return {
        "symbol": sym,
        "sentiment_score": round(sentiment if sentiment is not None else 50.0, 1),
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
        "investment_thesis": (getattr(r, "investment_thesis", "") or "")[:1500],
        "bull_case": (getattr(r, "bull_case", "") or "")[:1000],
        "bear_case": (getattr(r, "bear_case", "") or "")[:1000],
        "news_summary": (getattr(r, "news_summary", "") or "")[:1500],
        "headlines": (getattr(r, "headlines", None) or [])[:8],
        "alternative_signals": (getattr(r, "alternative_signals", "") or "")[:800],
        "insider_trades_count": (
            insider_total
            if insider_total is not None
            else len(getattr(r, "insider_trades", None) or [])
        ),
        "political_trades_count": len(getattr(r, "political_trades", None) or []),
        "material_events_count": len(getattr(r, "material_events", None) or []),
        "reddit_mentions": getattr(r, "reddit_mentions", 0) or 0,
        "wiki_spike": bool(getattr(r, "wiki_spike", False)),
        "short_interest_pct": getattr(r, "short_interest_pct", None),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "ml_direction": getattr(r, "ml_direction", "unavailable"),
        "ml_confidence": _round_or_none(getattr(r, "ml_confidence", None), 3),
        "ml_base_return_pct": _round_or_none(getattr(r, "ml_base_return_pct", None), 2),
        "ml_bear_return_pct": _round_or_none(getattr(r, "ml_bear_return_pct", None), 2),
        "ml_bull_return_pct": _round_or_none(getattr(r, "ml_bull_return_pct", None), 2),
        "signal_quality": getattr(r, "signal_quality", "llm_only"),
        "walkforward_ic": _round_or_none(getattr(r, "walkforward_ic", None), 3),
        "walkforward_sharpe": _round_or_none(getattr(r, "walkforward_sharpe", None), 2),
        "ml_attention_features": getattr(r, "ml_attention_features", None) or [],
        # Group-B keys (T-SER #1266) - additive (+7). Producers: T2 (pros/cons/
        # summary), T6a (data_quality/degraded), later TA stage (rsi_14/macd_signal).
        # data_quality uses _round_or_none NOT `or` - 0.0 is a legitimate low-integrity
        # value (P0-1). summary capped [:1500] like news_summary/investment_thesis.
        "pros": getattr(r, "pros", None) or [],
        "cons": getattr(r, "cons", None) or [],
        "summary": (getattr(r, "summary", "") or "")[:1500],
        "data_quality": _round_or_none(getattr(r, "data_quality", 1.0), 3),
        "degraded": bool(getattr(r, "degraded", False)),
        "rsi_14": _round_or_none(getattr(r, "rsi_14", None), 1),
        "macd_signal": getattr(r, "macd_signal", None),
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
            "message": "StockSpecialistRegistry not running on this deployment.",
            "reports": [],
            "registry_status": {},
        }
    all_reports = registry.get_all_reports()
    escalations = registry.get_escalations()
    status_info = registry.get_status()
    reports_out = [
        _serialize_specialist_report(sym, r) for sym, r in sorted(all_reports.items())
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
    from core.round_table.recent_decisions import get_recent_round_table_decisions

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
        vote = {"BUY": "BULL", "SELL": "BEAR", "HOLD": "ABSTAIN"}.get(signal, "ABSTAIN")
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


# ── HITL human-in-the-loop API (PR-0a-ii-6, EU AI Act Art. 14) ──────────────────
# The frozen cross-lane contract for the frontend Decisions-approval + Policy-settings UI
# (Session A). Auth-gated like the sibling console GETs (X-Engine-Key via require_engine_key).
# The whole surface is DORMANT in effect: the queue is only ever populated when HITL_ENABLED.
from typing import List, Optional  # noqa: E402

from pydantic import ConfigDict, Field  # noqa: E402


class HitlQueueItemDTO(BaseModel):
    """One order awaiting human approval (the pending-queue item the UI renders)."""

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
    """The full HITL policy (GET response). ``HITL_ENABLED`` is shown read-only."""

    HITL_ENABLED: bool
    HITL_MAX_VALUE_PER_TRADE: float
    HITL_MAX_VALUE_PER_DAY: float
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    HITL_EXPIRY_SECONDS: int


class HitlPolicyUpdateDTO(BaseModel):
    """The runtime-adjustable limits (POST body). ``extra="forbid"`` ⇒ a POST that includes
    ``HITL_ENABLED`` (or any unknown key) is rejected with HTTP 422 — enabling HITL is the
    env+redeploy step (C2/M5), never an API toggle, so the flag is structurally unsettable.
    """

    model_config = ConfigDict(extra="forbid")

    HITL_MAX_VALUE_PER_TRADE: float = Field(ge=0)
    HITL_MAX_VALUE_PER_DAY: float = Field(ge=0)
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    # ge=1 .. le=86_400 (24h): an approval window must be bounded — a near-infinite expiry would
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
    """Approve a pending order — it moves to the approved set and the trading-loop drain
    executes it (which audits ``approved`` on execution, PR-0a-ii-5)."""
    from core.hitl_queue import HitlQueue

    payload = await HitlQueue.approve(body.approval_id)
    if payload is None:
        return HitlActionResponse(
            success=False, approval_id=body.approval_id, detail="not found or expired"
        )
    return HitlActionResponse(
        success=True,
        approval_id=body.approval_id,
        detail="approved; queued for execution",
    )


@app.post("/api/hitl/reject", response_model=HitlActionResponse)
async def hitl_reject(
    body: HitlRejectRequest, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Reject a pending order — removed from the queue and audited (never silently dropped)."""
    from core import hitl_gate
    from core.hitl_queue import HitlQueue
    from core.round_table.senate_log import HITLExecutionEvent

    pending = await HitlQueue.get_pending()
    item = next((p for p in pending if p.get("approval_id") == body.approval_id), None)
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


@app.get("/api/hitl/policy", response_model=HitlPolicyDTO)
async def hitl_get_policy(_: None = Depends(require_engine_key)):  # noqa: B008
    """The current HITL autonomy policy (all six values; ``HITL_ENABLED`` read-only)."""
    return _hitl_policy_dto()


@app.post("/api/hitl/policy", response_model=HitlPolicyDTO)
async def hitl_set_policy(
    body: HitlPolicyUpdateDTO, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Update the runtime-adjustable HITL limits. Writes an immutable HITLPolicyEvent (old→new)
    BEFORE mutating the running policy. ``HITL_ENABLED`` is not accepted (422, env-only).
    """
    import config
    from core import hitl_gate

    old = hitl_gate.policy_snapshot()
    updates = body.model_dump()
    new = {**old, **updates}
    # A policy change is operator-initiated and off the hot path → the Art-14 audit is BLOCKING:
    # if it cannot be persisted, refuse the mutation (503) rather than change the autonomy limits
    # with no immutable record of who/what/when. (Execution audits stay best-effort, by contrast.)
    try:
        await hitl_gate.log_policy_event(old, new, actor="api", strict=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="policy-change audit failed; change refused"
        ) from exc
    config.apply_hitl_policy_update(updates)
    return _hitl_policy_dto()


def main():
    port = _find_engine_port()
    host = os.environ.get("ENGINE_HOST", "127.0.0.1")
    logging.info("Engine starting on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
