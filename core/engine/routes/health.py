"""Health- und Status-Router (#4073, ARC-E6 G-4g).

Liveness und Readiness (``/ready``, ``/health/readiness``, ``/health``), System-Health
(``/system-health``, ``/health/deep``) und die lesenden Status-Routen (``/market-regime``,
``/risk-limits``, ``/diagnostics``, ``/compliance-status``, ``/api/regime-preview``): alles, was
beantwortet, in welchem Zustand die Engine ist. Keine dieser Routen hat eine Schreibwirkung.
Die Routen und ``compute_overall_status`` sind unveraendert aus ``api_routes.py`` umgezogen;
die HTTP-Flaeche bleibt gleich (``tests/unit/test_api_flaeche_schnappschuss.py``).
``compute_overall_status`` nutzt auch ``engine_diagnostics`` im Diagnose-Router
(``core/engine/routes/diagnose.py``, #4115 G-4j) und importiert es von hier.

Geteilten Zustand und ueber ``api_routes`` gepatchte Namen (``engine``, ``engine_init_error``,
``config``, ``RedisClient``, ``_load_iron_dome_policy_value``, ``_START_TIME``) liest das Modul
zur Laufzeit ueber ``ar`` (Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen. ``engine_init_error`` setzt
``_init_engine_async`` zur Laufzeit neu; nur der Zugriff ueber ``ar`` sieht den aktuellen Wert.

Plan: ``docs/4073-g4g-health-status-router/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timezone

import psutil
from fastapi import APIRouter, Depends, Response

from core.auth import require_engine_key, verify_user_id_sig
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.governance.iron_dome_policy import load_policy
from core.strategies import _rl_agent_file
from models.torch_model import get_lstm_paths

router = APIRouter()
ROUTER.append(router)


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


@router.get("/ready")
async def ready_check():
    return {"status": "ready"}


@router.get("/health/readiness")
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


@router.get("/health")
async def health_check():
    # Return 200 immediately while engine is still starting up.
    # Cloud Run TCP probe only checks port reachability, not response body.
    if ar.engine is None:
        # Local import: the healthy branch below re-imports `config`, which makes the name
        # function-local for the whole scope — reference it here without a matching local bind
        # and Python raises UnboundLocalError. The module-level `import config` is shadowed.
        import config

        # A captured fatal init failure turns the eternal "starting" into an honest, diagnosable
        # error the console/diagnostics can surface (instead of a silent never-ready engine).
        if ar.engine_init_error is not None:
            return {
                "status": "error",
                "detail": ar.engine_init_error,
                "redis": "unknown",
                "timestamp": time.time(),
                "version": "2.5.0",
                "strategy_running": False,
                "system_halted": False,
                "paper_trading": getattr(config, "PAPER_TRADING", True),
                "llm_ready": False,
                "startup_blocked_reason": None,
            }

        return {
            "status": "starting",
            "redis": "unknown",
            "timestamp": time.time(),
            "version": "2.5.0",
            "strategy_running": False,
            "system_halted": False,
            "llm_ready": False,
            "startup_blocked_reason": None,
            # LSR R4 (#2251, audit #08): carry the account truth even in the post-restart boot
            # window. config.PAPER_TRADING is fixed at spawn time (before engine init), so the
            # "starting" body can report it — otherwise the console's `h.paper_trading ?? true`
            # reconcile silently defaults a booting-live engine to Paper (audit #05/#16).
            "paper_trading": getattr(config, "PAPER_TRADING", True),
        }
    redis_healthy = await ar.RedisClient.check_health()
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

    # #3373: the persistence counters the CloudLogger already keeps but nobody read. A
    # schema drift made EVERY `decisions` insert fail for seven days (2026-09-11..17) —
    # the rows went to the JSONL fallback and the desktop, which has no engine log on
    # disk, showed nothing. Pure observation: `status` is unchanged; any failure here
    # degrades to None and never breaks /health.
    try:
        from core.cloud_logger import get_cloud_logger

        _stats = get_cloud_logger().get_stats()
        persistence = {
            "errors": int(_stats.get("errors", 0)),
            "fallback_writes": int(_stats.get("fallback_writes", 0)),
            "decisions_logged": int(_stats.get("decisions_logged", 0)),
        }
    except Exception:
        persistence = None

    # B11: the startup wait state. `llm_ready` is True once the startup check verified the
    # LLM provider; `startup_blocked_reason` says WHY the loop is idle ("llm_unreachable"
    # while it waits for Ollama, "redis_unreachable" after the hard abort, else None) so
    # the console can show a hint instead of a bare "Idle". Coerced: an engine object
    # without the fields (older build, a mock) reads as not-ready / no reason.
    _blocked = getattr(ar.engine, "_startup_blocked_reason", None)
    startup_blocked_reason = _blocked if isinstance(_blocked, str) else None
    llm_ready = getattr(ar.engine, "_llm_ready", False) is True

    return {
        "status": "healthy",
        "redis": "connected" if redis_healthy else "disconnected",
        "timestamp": time.time(),
        "version": "2.5.0",
        "strategy_running": ar.engine.strategy_running.is_set(),
        "llm_ready": llm_ready,
        "startup_blocked_reason": startup_blocked_reason,
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
        "persistence": persistence,
    }


@router.get("/system-health")
async def system_health(
    _: None = Depends(require_engine_key),  # noqa: B008
):  # Auth required
    cpu_pct = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    uptime_seconds = int(time.time() - ar._START_TIME)
    if ar.engine is None:
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
        sum(ar.engine._cycle_latencies) / len(ar.engine._cycle_latencies)
        if ar.engine._cycle_latencies
        else 0
    )
    max_latency = (
        max(ar.engine._cycle_latencies) if ar.engine._cycle_latencies else 0
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
            "last_cycle": ar.engine._last_cycle_details,
        },
        "timestamp": time.time(),
    }


@router.get("/health/deep")
async def deep_health(response: Response):
    from core.cloud_logger import get_cloud_logger

    alpaca_status = "unavailable"
    alpaca_details = {}
    is_market_open = False
    if ar.engine is not None and ar.engine.api:
        try:
            # Offload the synchronous Alpaca REST round-trips so a slow broker call cannot block the
            # single uvicorn event loop (which would stall EVERY concurrent request, incl. /health).
            acc = await asyncio.to_thread(ar.engine.api.get_account)
            alpaca_status = "ok" if acc.status == "ACTIVE" else acc.status
            # SECURITY: Kein Equity in unauthentifiziertem Response — nur funded/unfunded  # noqa: E501
            alpaca_details = {
                "status": acc.status,
                "is_funded": float(acc.equity) > 0,
            }  # noqa: E501
            clock = await asyncio.to_thread(ar.engine.api.get_clock)
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
    models_status["gemini"] = "ok" if ar.config.GEMINI_AVAILABLE else "disabled"
    # G4a-3: additive — surfaces the resolved LLM provider for the console/UI
    # (Gemini API key vs desktop Ollama). The "gemini" key above is kept for
    # console-contract compatibility.
    models_status["llm_provider"] = (
        (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    )

    lstm_loaded = False
    rl_loaded = False
    if ar.engine is not None and ar.engine.active_strategy:
        lstm_loaded = (
            getattr(ar.engine.active_strategy, "torch_model", None) is not None
        )  # noqa: E501
        rl_loaded = (
            getattr(ar.engine.active_strategy, "rl_model", None) is not None
        )  # noqa: E501
    else:
        try:
            lstm_paths = get_lstm_paths()
            lstm_loaded = all(os.path.exists(p) for p in lstm_paths)
            rl_file = _rl_agent_file(
                getattr(ar.config, "RL_MODEL_VERSION", "rl_agent_v3_dsr")
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
    if ar.engine is not None:
        strategy_running = ar.engine.strategy_running.is_set()
        last_scan_age = time.time() - ar.engine._last_scan_time
        scan_active = last_scan_age < (
            ar.config.STRATEGY_MONITOR_INTERVAL_SECONDS * 1.5
        )  # noqa: E501
    else:
        strategy_running = False
        last_scan_age = None
        scan_active = False

    # Reuse the shared pure verdict (ADR-OBS-01 §7) — identical precedence to before.  # noqa: E501
    components_degraded = alpaca_status != "ok" or "missing" in models_status.values()
    overall_status = compute_overall_status(
        {
            "engine_ready": ar.engine is not None,
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


@router.get("/market-regime")
async def get_market_regime():
    """Read-only: the engine's latest market regime + VIX (cached by the monitor loop).  # noqa: E501

    Surfaces ``engine.current_market_data`` so the demo snapshot runner (#1618) can report real  # noqa: E501
    values instead of defaults. No state change; defaults cleanly while the engine is starting.  # noqa: E501
    """
    if ar.engine is None:
        return {"regime": "Ranging", "vix": None, "indicator": "Unavailable"}
    md = getattr(ar.engine, "current_market_data", None) or {}
    return {
        "regime": md.get("regime", "Ranging"),
        "vix": md.get("vix"),
        "indicator": "live" if md.get("regime") else "Default",
    }


@router.get("/risk-limits")
async def get_risk_limits():
    """Read-only: the authoritative daily-drawdown limit as a PERCENT (e.g. 17.5).  # noqa: E501

    R6-3c (#1698): sources the LIVE Iron Dome policy (same path as the admin write handler) so  # noqa: E501
    the public demo snapshot can render the drawdown card. FAIL-CLOSED — on ANY error return  # noqa: E501
    ``None`` (never fabricate a percentage, never 500 the snapshot); the card is then omitted.  # noqa: E501
    """
    try:
        stored = await ar._load_iron_dome_policy_value()
        policy = load_policy(stored)
        return {
            "daily_drawdown_limit_pct": round(policy.daily_drawdown_pct * 100.0, 2)
        }  # noqa: E501
    except (
        Exception
    ):  # noqa: BLE001 — fail-closed: never fabricate, never 500 the snapshot
        return {"daily_drawdown_limit_pct": None}


@router.get(
    "/diagnostics",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def diagnostics():
    rl_loaded = False
    if ar.engine.active_strategy and hasattr(ar.engine.active_strategy, "rl_model"):
        rl_loaded = ar.engine.active_strategy.rl_model is not None
    lstm_loaded = False
    if ar.engine.active_strategy and hasattr(
        ar.engine.active_strategy, "torch_model"
    ):  # noqa: E501
        lstm_loaded = ar.engine.active_strategy.torch_model is not None
    return {
        "alpaca_connected": ar.engine.api is not None,
        "strategy_running": ar.engine.strategy_running.is_set(),
        "active_strategy": (
            type(ar.engine.active_strategy).__name__
            if ar.engine.active_strategy
            else None  # noqa: E501
        ),
        "lstm_model_loaded": lstm_loaded,
        "rl_model_loaded": rl_loaded,
        "has_portfolio_manager": bool(
            getattr(ar.engine.active_strategy, "portfolio_manager", None)
        ),
        "has_trade_intelligence": bool(
            getattr(ar.engine.active_strategy, "trade_intelligence", None)
        ),
        # SECURITY: API-Key-Details nicht exponieren
        "config_api_key_set": bool(ar.config.API_KEY),
    }


@router.get(
    "/compliance-status",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_compliance_status():
    if not ar.engine.compliance_guardian:
        return {
            "status": "success",
            "enabled": False,
            "message": "ComplianceGuardian is disabled.",
        }
    g = ar.engine.compliance_guardian
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


@router.get("/api/regime-preview")
async def regime_preview_get(_: None = Depends(require_engine_key)):  # noqa: B008
    """Read-only preview of the regime-beyond-VIX signal (#3361, Epic #2963).

    Shows the operator what the sizing throttle WOULD do at the current settings —
    also while ``REGIME_THROTTLE_ENABLED`` is off, so the signal can be observed dark
    before it is armed. Computed at most once per day on request (the order path itself
    only ever reads the cache). Changes no setting, writes nothing to the audit chain,
    places no order. Any data gap reads as ``available: false`` / ``factor: 1.0``.
    Purely factual — no recommendation.
    """
    try:
        import config as _cfg
        from core.engine.regime_signal import (
            ensure_fresh_state,
            provider_fetch,
            regime_reading,
        )
        from core.sim.clock import engine_now

        cfg = _cfg.get_config()
        now = engine_now(timezone.utc)
        state = None
        if not getattr(cfg, "SIM_MODE", False):
            state = await asyncio.to_thread(
                ensure_fresh_state,
                provider_fetch(ar.engine.data_provider, now),
                now.date(),
            )
        reading = regime_reading(
            now.date(),
            int(getattr(cfg, "REGIME_RISKOFF_PERCENTILE", 75)),
            float(getattr(cfg, "REGIME_THROTTLE_SIZE_FACTOR", 0.5)),
            state=state,
        )
        reading["enabled"] = bool(getattr(cfg, "REGIME_THROTTLE_ENABLED", True))
        reading["size_factor_setting"] = float(
            getattr(cfg, "REGIME_THROTTLE_SIZE_FACTOR", 0.5)
        )
        return {"status": "success", **reading}
    except Exception as exc:  # noqa: BLE001 — a preview must never 500 the console
        logging.warning("regime-preview failed (%s).", exc, exc_info=True)
        return {
            "status": "error",
            "available": False,
            "would_throttle": False,
            "factor": 1.0,
            "message": "Regime signal unavailable.",
        }
