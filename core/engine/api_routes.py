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
from typing import Any, Dict, Optional

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
from core.contracts.abgleich import ReconciliationReleaseRequest, SwitchReconcileRequest
from core.contracts.abrechnung import ActivateRequest, CheckoutRequest, SwapRequest
from core.contracts.einstellungen import (
    SettingsDeviationAckRequest,
    SettingsDeviationAckResponse,
    TradingSettingsRequest,
    TradingSettingsResponse,
)
from core.contracts.hitl import (
    HitlActionResponse,
    HitlApproveRequest,
    HitlPendingResponse,
    HitlPolicyDTO,
    HitlPolicyUpdateDTO,
    HitlQueueItemDTO,
    HitlRejectRequest,
)
from core.contracts.live import LiveEnableRequest, LiveEnableResponse
from core.contracts.portfolio import (
    PortfolioConstraintsApproveRequest,
    PortfolioConstraintsProposeRequest,
    PortfolioShapeRequest,
    PortfolioShapeResponse,
)
from core.database.session import ensure_local_db_ready
from core.engine.broker_stop_pflege import letzter_stand as _collect_broker_stops
from core.engine.routes import binde_ein
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
from core.usage_counters import bump_usage, register_api_routes  # noqa: E501
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

# get_service_version und engine_diagnostics samt Sammlern liegen seit #4115 (G-4j) im
# Diagnose-Router (core/engine/routes/diagnose.py).


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
    # #3608: clock und trade_intelligence sind seit der Clock-Injection (#3561/#3563/#3564)
    # verpflichtende Argumente von BotEngine. Sie kommen aus dem CompositionRoot — laut dessen
    # eigener Beschreibung die EINZIGE Stelle, an der die Editionsweichen ausgewertet werden.
    # Fehlten sie, warf der Boot einen TypeError und /health blieb dauerhaft "error": genau das
    # meldete der C3-Smoke-Test des 0.5.1-rc1-Builds.
    from core.composition.root import CompositionRoot

    _root = CompositionRoot.get_instance()
    # #2548 C1 — Sim-Day boot seam: under SIM_MODE build the OFFLINE sim clients via the factory
    # (VirtualLiveBroker + SimDataClient) and skip the real-client construction AND the get_account()
    # network probe entirely, so the primary engine boot path never touches real Alpaca (fail-closed).
    # This is where the sim actually activates on the desktop/uvicorn path.
    if getattr(config, "SIM_MODE", False) is True:
        trading_api = create_trading_client()
        data_api = create_data_client()
        engine = BotEngine(
            clock=_root.clock_port,
            trade_intelligence=_root.trade_intelligence,
            trading_client=trading_api,
            data_client=data_api,
        )
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
    engine = BotEngine(
        clock=_root.clock_port,
        trade_intelligence=_root.trade_intelligence,
        trading_client=trading_api,
        data_client=data_api,
    )
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
# /ready, /health/readiness, /health, /system-health und /health/deep liegen seit #4073
# (G-4g) im Health-Router (core/engine/routes/health.py).


# --- ADR-OBS-01: GET /engine-diagnostics ---
# Liegt seit #4115 (G-4j) samt _safe_collect und den _collect_*-Sammlern im
# Diagnose-Router (core/engine/routes/diagnose.py). _collect_broker_stops bleibt hier,
# der Router liest es ueber ``ar``.


# --- INF-8: Staging Quality Gate ---
# Liegt seit #4071 (G-4e) im Admin-Router (core/engine/routes/admin.py).


# --- Strategy Control ---
# /strategy und /set-strategy liegen in core/engine/routes/steuerung.py (#4069, G-4c).
# /market-regime und /risk-limits liegen seit #4073 (G-4g) im Health-Router.


# --- Hot-Swap API (Epic 2.3-Pre / PR-C) ---
# /api/strategy/swap liegt in core/engine/routes/steuerung.py (#4069, G-4c) und liest
# get_global_registry und get_cloud_logger ueber ``ar`` — Tests patchen sie hier.

from core.agent_registry import get_global_registry  # noqa: E402
from core.cloud_logger import get_cloud_logger  # noqa: E402
from core.exceptions import SwapInProgressError  # noqa: E402


def verify_firebase_token(request: Request) -> dict:
    """Auth-Guard: verifiziert Auth-Token.

    In Tests wird diese Funktion via pytest.monkeypatch oder patch() überschrieben.  # noqa: E501
    In Produktion nutzt sie den AuthProvider.
    """
    from core.auth_interfaces import get_auth_provider

    user_context = get_auth_provider().verify_token(request)
    return {"uid": user_context.uid}


# --- GTM-1 (#1840) / GTM-2 (#1809): Entitlement-Routen ---
# Liegen seit #4071 (G-4e) im Admin-Router (core/engine/routes/admin.py).


# --- Diagnostics ---
# /diagnostics liegt seit #4073 (G-4g) im Health-Router.


# ---------------------------------------------------------------------------
# #3430: Bedienstelle der Abgleich-Sperre (#3389). Die Routen liegen seit #4068 im
# Kapitalpfad-Router (core/engine/routes/kapitalpfad.py); der Urheber bleibt hier.
# ---------------------------------------------------------------------------

#: Urheber, wenn die Kennung nicht signiert ist (Desktop, LocalMockAuth, From-Source).
#: Das Protokoll nennt ihn so, wie er ist, statt eine Person vorzutaeuschen.
LOKALER_BEDIENER = "lokaler Bediener (Kennung ungeprüft)"


# ADR-SEC-06: die Iron-Dome-Admin-Routen und ihre Vier-Augen-Helfer liegen seit #4071
# (G-4e) im Admin-Router (core/engine/routes/admin.py). _load_iron_dome_policy_value
# bleibt hier, weil auch Kapitalpfad und Betrieb ihn lesen.


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


# --- Market Data & Portfolio ---
# Die lesenden Portfolio-Routen liegen in core/engine/routes/portfolio.py (#4070, G-4d);
# _resolve_orders_client bleibt hier, weil auch der Kapitalpfad ihn liest.


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


# /compliance-status liegt seit #4073 (G-4g) im Health-Router.


# _reconstruct_spy_points und _get_inception_equity liegen seit #4074 (G-4h) in
# routes/benchmark_daten.py.


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


# _read_metrics_db_or_fallback und _return_metrics_from_points liegen seit #4074 (G-4h) in
# routes/benchmark_daten.py.


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


# Die Baseline-Routen, GET /benchmark-equity und POST /run-benchmark liegen seit #4074 (G-4h)
# und #4075 (G-4i) im Benchmark-Router (core/engine/routes/benchmark.py).


# Simulation, Force-Cycle und Chat liegen seit #4072 (G-4f) im Agenten-Router
# (core/engine/routes/agenten.py).


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
    from core.composition.root import CompositionRoot

    if CompositionRoot.get_instance().is_local_desktop:
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


# GET /api/v2/universe liegt seit #4072 (G-4f) im Agenten-Router.


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


# Specialist- und Round-Table-Routen samt _serialize_specialist_report liegen seit #4072
# (G-4f) im Agenten-Router (core/engine/routes/agenten.py).
# GET /api/entitlement/status liegt seit #4071 (G-4e) im Admin-Router.


# ── HITL human-in-the-loop API (PR-0a-ii-6, EU AI Act Art. 14) ──────────────────  # noqa: E501
# The frozen cross-lane contract for the frontend Decisions-approval + Policy-settings UI  # noqa: E501
# (Session A). Auth-gated like the sibling console GETs (X-Engine-Key via require_engine_key).  # noqa: E501
# The whole surface is DORMANT in effect: the queue is only ever populated when HITL_ENABLED.  # noqa: E501
# Die Routen liegen in core/engine/routes/steuerung.py (#4069, G-4c).
from typing import Any, List, Optional  # noqa: E402

# ── #2653 (Epic #2655, Weg 1): portfolio sector-constraint governance ────────────
# Die Routen liegen seit #4071 (G-4e) im Admin-Router (core/engine/routes/admin.py).


# ── LIVE-1 T4 (#1427): Art.-14 live-trading enablement WORM endpoints ───────────  # noqa: E501
# Record a deliberate enable/disable decision on the SAME tamper-evident SHA-256 chain as the  # noqa: E501
# HITL audits, BEFORE the desktop shell is allowed to flip SHADOW_MODE off (audit-before-enable).  # noqa: E501
# The shell verifies the record via audit-chain.cjs `verifyAuditChain` and only then boots live.  # noqa: E501


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


# GET /api/regime-preview liegt seit #4073 (G-4g) im Health-Router.
# POST /api/telemetry/switch-reconcile liegt seit #4071 (G-4e) im Admin-Router.


binde_ein(app)  # #3827: Router aus core/engine/routes/ — vor der Zaehler-Saat unten.
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
