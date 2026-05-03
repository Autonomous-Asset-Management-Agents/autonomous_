# core/engine/api_routes.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: FastAPI-App, alle HTTP-Endpoints und WebSocket

# Task #361: OTel SDK MUST be initialised before any other import
from core.telemetry import init_telemetry  # noqa: E402 (intentional first import)

init_telemetry(service_name="aaa-backend")

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import socket
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict

import psutil
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    Response,
    WebSocket,
    status,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import config
from core.auth import require_engine_key, verify_user_id_sig
from core.redis_client import RedisClient
from core.structured_logging import setup_logging
from core.otel_middleware import OtelSpanMiddleware
from core.user_wallet_store import wallet_store
from core.secret_manager_utils import oauth_secrets
import core.strategies as strategies
from core.strategies import _rl_agent_file
from models.torch_model import get_lstm_paths
from core.ai_components import answer_chat_with_fallback

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
            trading_api = TradingClient(
                config.API_KEY, config.API_SECRET, paper=is_paper
            )
            data_api = StockHistoricalDataClient(config.API_KEY, config.API_SECRET)
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
    from core.database.session import AsyncSessionLocal
    from core.database.models import SystemConfig
    import sqlalchemy as sa

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


async def _init_engine_async():
    await _fetch_and_apply_remote_config()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_trading_clients)


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
        from core.database.session import engine as db_engine, cleanup_engine_connector

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
    _: None = Depends(require_engine_key),
):  # noqa: B008  # Auth required
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

from pydantic import BaseModel  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from core.agent_registry import get_global_registry  # noqa: E402
from core.exceptions import SwapInProgressError  # noqa: E402


from core.cloud_logger import get_cloud_logger  # noqa: E402


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


@app.post("/api/strategy/swap")
async def strategy_swap(
    req: SwapRequest,
    request: Request,
    _: dict = Depends(verify_firebase_token),
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

        orders = await asyncio.to_thread(api_client.get_orders)
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

                return {
                    "status": "success",
                    "summary": summary,
                    "positions": final_positions,
                    "equity": final_equity,
                    "recent_debates": debates,
                    "rebalance_recommendations": rebalance_recs,
                    "message": api_message,
                }

        if real_positions is not None:
            return {
                "status": "success",
                "summary": None,
                "positions": real_positions,
                "equity": real_equity,
                "message": api_message,
            }

        return {
            "status": "success",
            "summary": None,
            "positions": [],
            "message": api_message,
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
            return {
                "points": [],
                "spy_points": [],
                "strategy": "RLAgent",
                "initial_capital": None,
                "message": "No benchmark run yet.",
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
    p: Dict = None, _: None = Depends(require_engine_key)
):  # noqa: B008
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
    p: Dict, request: Request, _: None = Depends(require_engine_key)
):  # noqa: B008
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

    from core.orchestration.graph import build_symbol_eval_graph
    import uuid

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


def main():
    port = _find_engine_port()
    host = os.environ.get("ENGINE_HOST", "127.0.0.1")
    logging.info("Engine starting on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
