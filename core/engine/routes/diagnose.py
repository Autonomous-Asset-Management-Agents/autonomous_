"""Engine-Diagnose-Router (#4115, ARC-E6 G-4j).

``GET /engine-diagnostics`` samt ``_safe_collect`` und den 17 ``_collect_*``-Sammlern: der tiefe,
immer-200 Engine-Zustand fuer die Konsole (ADR-OBS-01). Die Sammler haben genau einen Nutzer, die
Route; sie sind unveraendert aus ``api_routes.py`` umgezogen, die HTTP-Flaeche bleibt gleich
(``tests/unit/test_api_flaeche_schnappschuss.py``). ``compute_overall_status`` kommt aus
``core.engine.routes.health`` (G-4g).

Geteilten Zustand und ueber ``api_routes`` gepatchte Namen (``engine``, ``config``,
``_START_TIME``, ``_collect_broker_stops``) liest das Modul zur Laufzeit ueber ``ar``
(Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen. ``RedisClient`` und ``get_cloud_logger``
importiert ``_collect_db`` wie vor dem Umzug funktionslokal.

Plan: ``docs/4115-g4j-engine-diagnose-router/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timezone

import psutil
from fastapi import APIRouter, Depends

from core.auth import require_engine_key
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.engine.routes.health import compute_overall_status
from core.nlp.news_sentiment import backend_status as _collect_news_sentiment  # #3907
from core.usage_counters import get_usage_counters

router = APIRouter()
ROUTER.append(router)


# Static service version. No get_service_version() accessor existed in the codebase  # noqa: E501
# (both /health and /health/deep hardcoded their own strings); centralised here so  # noqa: E501
# /engine-diagnostics and /health/deep agree on one value.
_SERVICE_VERSION = "2.5.0"


def get_service_version() -> str:
    """The engine service version (single source for the diagnostics + health surfaces)."""  # noqa: E501
    return _SERVICE_VERSION


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
        "engine_ready": ar.engine is not None,
        "service_version": get_service_version(),
        "uptime_seconds": int(time.time() - ar._START_TIME),
        "paper_trading": getattr(ar.config, "PAPER_TRADING", True),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_pct": ram.percent,
    }


def _collect_loops() -> dict:
    """Trading/monitor loop liveness + scan freshness (null-safe when engine is None).  # noqa: E501

    PR B extends this with the "is the engine actually cycling" liveness that was  # noqa: E501
    missing during the halt incident: the monotone cycle/scan counters, the cycle-  # noqa: E501
    latency sample count, the high-latency count, the age of the most recent cycle,  # noqa: E501
    and the CACHED market-open flag (read WITHOUT a live-broker call)."""
    if ar.engine is None:
        return {
            "trading_loop_running": False,
            "monitor_loop_running": False,
            "last_scan_age_seconds": None,
            "scan_active": False,
            "active_strategy": None,
            "auto_start_strategy": getattr(
                ar.config, "AUTO_START_STRATEGY", False
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
    last_scan_age = time.time() - ar.engine._last_scan_time
    interval = getattr(ar.config, "STRATEGY_MONITOR_INTERVAL_SECONDS", 1800)
    active = getattr(ar.engine, "active_strategy", None)
    # last_cycle_age: wall-clock age of the most recent trading cycle, from the
    # timestamp stamped into _last_cycle_details (None until the first cycle runs).  # noqa: E501
    last_cycle_ts = (getattr(ar.engine, "_last_cycle_details", None) or {}).get(
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
        market_open=bool(getattr(ar.engine, "_last_market_open", False)),
        strategy_running=ar.engine.strategy_running.is_set(),
        stall_after_seconds=getattr(ar.config, "LOOP_STALL_AFTER_SECONDS", 5400),
    )
    return {
        "trading_loop_running": ar.engine.strategy_running.is_set(),
        "monitor_loop_running": ar.engine.monitor_running.is_set(),
        "last_scan_age_seconds": round(last_scan_age, 1),
        "scan_active": last_scan_age < (interval * 1.5),
        "active_strategy": type(active).__name__ if active else None,
        "auto_start_strategy": getattr(ar.config, "AUTO_START_STRATEGY", False),
        "cycles_completed": getattr(ar.engine, "_cycles_completed", None),
        "scans_completed": getattr(ar.engine, "_scans_completed", None),
        "cycle_sample_count": len(
            getattr(ar.engine, "_cycle_latencies", []) or []
        ),  # noqa: E501
        "high_latency_cycles": getattr(ar.engine, "_high_latency_cycles", None),
        "last_cycle_age_seconds": last_cycle_age,
        "is_market_open": getattr(ar.engine, "_last_market_open", None),
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
    if not getattr(ar.config, "HITL_ENABLED", False):
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
        "day_notional_limit": getattr(ar.config, "HITL_MAX_VALUE_PER_DAY", 0.0),
    }


def _collect_risk() -> dict:
    """Live RiskManager state (null-safe: absent manager/attributes → None)."""
    rm = (
        getattr(ar.engine, "live_risk_manager", None) if ar.engine is not None else None
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
        getattr(ar.engine, "compliance_guardian", None)
        if ar.engine is not None
        else None
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
    out["gemini_available"] = bool(getattr(ar.config, "GEMINI_AVAILABLE", False))
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
        getattr(ar.engine, "active_strategy", None) if ar.engine is not None else None
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
        getattr(ar.engine, "_scans_completed", None)
        if ar.engine is not None
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


@router.get("/engine-diagnostics", dependencies=[Depends(require_engine_key)])
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
            "engine_ready": ar.engine is not None,
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
        "engine_ready": ar.engine is not None,
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
        "news_sentiment": await _safe_collect(_collect_news_sentiment),  # #3907
        "data_providers": data_providers,
        "usage": await _safe_collect(_collect_usage),
        "broker_stops": await _safe_collect(ar._collect_broker_stops),  # #3976
    }
