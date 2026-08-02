# core/specialist_registry.py
# Epic 3.3 — Stock Specialist System: Priority-based background scheduler
# Policy: CODING_POLICY.md §1 Compliance-First, §5 KI-Agenten-Lifecycle
"""
Stock Specialist Registry
=========================
Manages StockSpecialistAgent instances for a configurable universe of symbols,
running them continuously in a background thread with a priority-based
refresh scheduler.

Priority logic:
  - HIGH priority: Round Table top candidates + current holdings → refresh every 2h
  - NORMAL priority: remaining watchlist symbols → full cycle every 12h

Cost model (free APIs + single Gemini text call per refresh):
  - ~$0.0004 per Gemini synthesis call (NO Search Grounding)
  - 100 symbols × ~17 refreshes/day ≈ $0.07/day (vs $87+/day with Search Grounding)

Thread safety: all reads/writes to _reports use a threading.Lock.
"""

import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.stock_specialist import SpecialistReport, StockSpecialistAgent


def _report_cache_dir() -> Path:
    """Disk home of persisted specialist reports (REPORT_CACHE_PERSIST).

    Prefers the app's user-data dir (survives engine-code overlays); falls back
    to the repo-local ``data/`` dir on bare checkouts.
    """
    base = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    root = Path(base) if base else Path(__file__).resolve().parent.parent / "data"
    return root / "report_cache"


logger = logging.getLogger(__name__)


def _persist_enabled() -> bool:
    try:
        import config

        return bool(getattr(config.get_config(), "REPORT_CACHE_PERSIST_ENABLED", False))
    except Exception as exc:  # noqa: BLE001 — persistence must never break the registry
        logger.warning("Failed to check REPORT_CACHE_PERSIST_ENABLED: %s", exc)
        return False


# Fallback constants (overridden by config values at runtime)
SCHEDULER_TICK_SECONDS = 10
MAX_CONCURRENT_GEMINI = 3
BATCH_SLEEP_SECONDS = 5.0


def _load_config_timing() -> tuple:
    """Read timing from config, fall back to defaults if not available."""
    try:
        import config as _cfg

        high_prio_hours = float(
            getattr(_cfg, "SPECIALIST_HIGH_PRIO_INTERVAL_HOURS", 2.0)
        )
        full_cycle_hours = float(getattr(_cfg, "SPECIALIST_FULL_CYCLE_HOURS", 12.0))
        return int(high_prio_hours * 3600), int(full_cycle_hours * 3600)
    except Exception as exc:
        logger.warning(
            "Failed to load config timing for specialist registry, falling back to defaults: %s",
            exc,
        )
        return 7200, 43200  # 2h, 12h defaults


class StockSpecialistRegistry:
    """
    Manages all StockSpecialistAgent instances and continuously refreshes
    their reports using a priority-based background scheduler.

    Usage:
        registry = StockSpecialistRegistry(symbols, gemini_api_key)
        registry.start()
        ...
        report = registry.get_report("AAPL")
        registry.stop()
    """

    def __init__(
        self, symbols: List[str], gemini_api_key: str, report_only: bool = False
    ):
        """
        Args:
            symbols:        Watch universe — defaults to active watchlist, not full S&P 500.
                            Agents are created lazily on first refresh, not upfront.
            gemini_api_key: API key for Gemini (from GEMINI_API_KEY env var).
            report_only:    A5 (RPT-8b) deterministic-first mode. When True, each
                            refresh calls ``agent.refresh_report_only()`` (auditable
                            v2 report, NO LLM, NO ``research()`` gather/Gemini) instead
                            of ``research()`` — the cheap whole-universe report path.
                            The reports carry only markdown + audit fields and never
                            feed the consensus (SPECIALIST_ALPHA_WEIGHT stays 0).
        """
        self._gemini_api_key = gemini_api_key
        self._report_only = bool(report_only)
        self._symbols: List[str] = [s.upper().strip() for s in symbols if s]

        # Load timing from config (env-overridable)
        self._high_prio_max_age, self._normal_prio_cycle = _load_config_timing()
        logger.info(
            f"SpecialistRegistry timing: high_prio={self._high_prio_max_age // 3600}h, "
            f"full_cycle={self._normal_prio_cycle // 3600}h"
        )

        # Agents created lazily on first refresh (avoids allocating 500 objects upfront)
        self._agents: Dict[str, StockSpecialistAgent] = {}

        # Shared report cache (symbol → latest SpecialistReport)
        self._reports: Dict[str, SpecialistReport] = {}
        self._lock = threading.Lock()

        # High-priority set (updated by engine/monitor)
        self._high_priority: Set[str] = set()

        # REPORT_CACHE_PERSIST: reload fresh-enough reports from disk so an app
        # restart shows the SAME cards (in-memory-only reports regenerated with
        # drifted news inputs on every launch — visibly different content).
        # Stale entries are skipped and regenerate on the normal schedule.
        if _persist_enabled():
            loaded = self._load_persisted_reports()
            if loaded:
                logger.info(
                    "SpecialistRegistry: restored %d persisted report(s) from %s",
                    loaded,
                    _report_cache_dir(),
                )
        self._priority_lock = threading.Lock()

        # Background thread control
        self._shutdown = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None

        # Normal-priority rotation index
        self._normal_rotation_index: int = 0
        self._last_normal_tick: float = 0.0

        logger.info(
            f"StockSpecialistRegistry created: {len(self._symbols)} symbols, "
            f"Gemini key={'set' if gemini_api_key else 'MISSING'}"
        )

    # ─────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────

    def _persist_report(self, report: SpecialistReport) -> None:
        """Write one report as JSON to the disk cache. Best-effort: any failure
        is logged at WARNING (§5.6) and never disturbs the refresh loop."""
        if not _persist_enabled():
            return
        try:
            d = _report_cache_dir()
            d.mkdir(parents=True, exist_ok=True)
            payload = dataclasses.asdict(report)
            path = d / f"{report.symbol.upper()}.json"
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SpecialistRegistry: report persist failed for %s: %s",
                getattr(report, "symbol", "?"),
                exc,
            )

    def _load_persisted_reports(self) -> int:
        """Load fresh-enough persisted reports into ``_reports``. Returns count.

        Freshness bar = the high-prio refresh age (default 2h): anything older
        regenerates on the normal schedule instead of resurrecting stale prose.
        Unknown JSON keys are dropped (schema drift across versions must never
        crash the boot); a malformed file is skipped with a WARNING.
        """
        d = _report_cache_dir()
        if not d.is_dir():
            return 0
        field_names = {f.name for f in dataclasses.fields(SpecialistReport)}
        max_age = timedelta(seconds=self._high_prio_max_age)
        now = datetime.now(timezone.utc)
        count = 0
        for path in sorted(d.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if not isinstance(raw, dict) or not raw.get("symbol"):
                    continue
                ts = raw.get("updated_at")
                updated_at = None
                if isinstance(ts, str):
                    try:
                        updated_at = datetime.fromisoformat(ts)
                    except ValueError:
                        updated_at = None
                if updated_at is None or updated_at.tzinfo is None:
                    continue  # unknown age -> let it regenerate
                if now - updated_at > max_age:
                    continue  # stale -> regenerate on schedule
                data = {k: v for k, v in raw.items() if k in field_names}
                data["updated_at"] = updated_at
                report = SpecialistReport(**data)
                with self._lock:
                    self._reports[report.symbol.upper().strip()] = report
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SpecialistRegistry: skipping persisted report %s: %s",
                    path.name,
                    exc,
                )
        return count

    def start(self) -> None:
        """Start the background refresh thread."""
        if self._refresh_thread and self._refresh_thread.is_alive():
            logger.warning("StockSpecialistRegistry already running")
            return
        self._shutdown.clear()
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop,
            daemon=True,
            name="SpecialistRegistryThread",
        )
        self._refresh_thread.start()
        logger.info("StockSpecialistRegistry started")

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it to exit."""
        self._shutdown.set()
        if self._refresh_thread:
            # Worst case: thread is mid-_shutdown.wait(SCHEDULER_TICK_SECONDS=10).
            # Setting the event wakes it immediately, so 15s is a very safe ceiling.
            self._refresh_thread.join(timeout=15)
        logger.info("StockSpecialistRegistry stopped")

    def add_symbol(self, symbol: str) -> None:
        """Add a new symbol to the universe (e.g. when watchlist changes)."""
        sym = symbol.upper().strip()
        if sym and sym not in self._symbols:
            self._symbols.append(sym)
            logger.info(f"StockSpecialistRegistry: added {sym} to universe")

    def remove_symbol(self, symbol: str) -> None:
        """Remove a symbol from the universe and drop its cached report."""
        sym = symbol.upper().strip()
        if sym in self._symbols:
            self._symbols.remove(sym)
        self._agents.pop(sym, None)
        with self._lock:
            self._reports.pop(sym, None)
        logger.info(f"StockSpecialistRegistry: removed {sym} from universe")

    def update_priority(self, symbols: List[str]) -> None:
        """
        Update the high-priority set (called by engine/monitor after Round Table).
        These symbols will be refreshed at most every 2 hours.
        """
        with self._priority_lock:
            self._high_priority = {s.upper().strip() for s in symbols if s}

    def get_report(self, symbol: str) -> Optional[SpecialistReport]:
        """Return the latest report for a symbol, or None if not yet researched."""
        with self._lock:
            return self._reports.get(symbol.upper().strip())

    def get_all_reports(self) -> Dict[str, SpecialistReport]:
        """Return a copy of all cached reports."""
        with self._lock:
            return dict(self._reports)

    def get_escalations(self) -> List[SpecialistReport]:
        """Return reports with escalate=True, sorted by sentiment_score descending."""
        with self._lock:
            escalated = [r for r in self._reports.values() if r.escalate]
        escalated.sort(key=lambda r: r.sentiment_score, reverse=True)
        return escalated

    def get_top_reports(self, symbols: List[str]) -> List[SpecialistReport]:
        """Return reports for the given symbols (skips missing ones)."""
        with self._lock:
            reports = []
            for sym in symbols:
                r = self._reports.get(sym.upper().strip())
                if r:
                    reports.append(r)
        return sorted(reports, key=lambda r: r.sentiment_score, reverse=True)

    def get_status(self) -> Dict:
        """Return registry status for monitoring/API."""
        with self._lock:
            total_reports = len(self._reports)
            escalations = sum(1 for r in self._reports.values() if r.escalate)
            alt_signal_count = sum(
                1
                for r in self._reports.values()
                if getattr(r, "alternative_signals", None)
            )
            activist_count = sum(
                1 for r in self._reports.values() if getattr(r, "activist_stakes", None)
            )
        with self._priority_lock:
            high_prio_count = len(self._high_priority)
        # Estimated daily cost: $0.0004 per Gemini call
        high_refreshes_per_day = (86400 / self._high_prio_max_age) * high_prio_count
        normal_refreshes_per_day = (86400 / self._normal_prio_cycle) * max(
            0, len(self._symbols) - high_prio_count
        )
        est_daily_cost_usd = (
            high_refreshes_per_day + normal_refreshes_per_day
        ) * 0.0004
        return {
            "total_symbols": len(self._symbols),
            "reports_cached": total_reports,
            "escalations": escalations,
            "alternative_signals_detected": alt_signal_count,
            "activist_stakes_detected": activist_count,
            "high_priority_symbols": high_prio_count,
            "running": bool(self._refresh_thread and self._refresh_thread.is_alive()),
            "est_daily_cost_usd": round(est_daily_cost_usd, 4),
        }

    # ─────────────────────────────────────────────────────────
    # Background refresh loop
    # ─────────────────────────────────────────────────────────

    def _refresh_loop(self) -> None:
        """
        Main scheduler loop running in a background daemon thread.

        Each tick (every SCHEDULER_TICK_SECONDS):
          1. Refresh any HIGH-priority symbols older than _high_prio_max_age (default 2h, config-driven)
          2. Advance the NORMAL-priority rotation by 1 symbol when enough time has elapsed
             such that all normal symbols complete a full cycle in _normal_prio_cycle (default 12h)
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("SpecialistRegistry refresh loop started")

        while not self._shutdown.is_set():
            try:
                # --- High-priority refresh ---
                high_prio_symbols = self._get_stale_high_priority()
                if high_prio_symbols:
                    logger.debug(
                        f"SpecialistRegistry: refreshing {len(high_prio_symbols)} high-prio symbols"
                    )
                    for sym in high_prio_symbols[
                        :3
                    ]:  # max 3 per tick to avoid blocking
                        if self._shutdown.is_set():
                            break
                        loop.run_until_complete(self._refresh_symbol(sym))
                        # Use shutdown.wait instead of time.sleep so stop() can
                        # interrupt this inter-symbol pause immediately.
                        self._shutdown.wait(BATCH_SLEEP_SECONDS)

                # --- Normal-priority rotation ---
                # Compute dynamic interval so full cycle = _normal_prio_cycle
                with self._priority_lock:
                    high_prio = set(self._high_priority)
                normal_symbols = [s for s in self._symbols if s not in high_prio]
                n_normal = len(normal_symbols)
                normal_interval = (
                    self._normal_prio_cycle / n_normal
                    if n_normal > 0
                    else self._normal_prio_cycle
                )

                now = time.time()
                if now - self._last_normal_tick >= normal_interval:
                    normal_sym = self._next_normal_symbol()
                    if normal_sym:
                        loop.run_until_complete(self._refresh_symbol(normal_sym))
                    self._last_normal_tick = now

            except Exception as e:
                logger.error(f"SpecialistRegistry refresh error: {e}", exc_info=True)

            self._shutdown.wait(SCHEDULER_TICK_SECONDS)

        loop.close()
        logger.info("SpecialistRegistry refresh loop exited")

    def _get_stale_high_priority(self) -> List[str]:
        """Return high-priority symbols whose reports are older than _high_prio_max_age (or missing)."""
        with self._priority_lock:
            high_prio = set(self._high_priority)

        stale = []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._high_prio_max_age)
        with self._lock:
            for sym in high_prio:
                report = self._reports.get(sym)
                if report is None or report.updated_at < cutoff:
                    stale.append(sym)
        return stale

    def _next_normal_symbol(self) -> Optional[str]:
        """
        Pick the next symbol from the normal (non-high-priority) rotation.
        Cycles through all symbols not in high_priority.
        """
        with self._priority_lock:
            high_prio = set(self._high_priority)

        normal_symbols = [s for s in self._symbols if s not in high_prio]
        if not normal_symbols:
            return None

        # Advance rotation index
        self._normal_rotation_index = self._normal_rotation_index % len(normal_symbols)
        sym = normal_symbols[self._normal_rotation_index]
        self._normal_rotation_index += 1
        return sym

    async def _refresh_symbol(self, symbol: str) -> None:
        """Refresh a single symbol's report."""
        agent = self._agents.get(symbol)
        if not agent:
            # Symbol not in registry — create agent on-the-fly
            agent = StockSpecialistAgent(symbol, self._gemini_api_key)
            self._agents[symbol] = agent

        try:
            if self._report_only:
                # A5: deterministic-first, LLM-free report (no research()/Gemini).
                report = agent.refresh_report_only()
            else:
                report = await agent.research()
            with self._lock:
                self._reports[symbol] = report
            # REPORT_CACHE_PERSIST (2026-07-23): reports live in memory only, so
            # an app restart regenerated EVERY card with drifted news inputs —
            # visibly different content each launch ("big inconsistency", Georg).
            # Persist each fresh report to disk; boot reloads anything younger
            # than the high-prio refresh age, so a restart shows the SAME cards
            # and only genuinely stale ones regenerate. Flag-gated, additive.
            self._persist_report(report)
            if report.escalate:
                logger.info(
                    f"[{symbol}] ESCALATED: {report.escalate_reason} "
                    f"(score={report.sentiment_score:.0f})"
                )
            else:
                logger.debug(
                    f"[{symbol}] Refreshed: {report.recommendation} "
                    f"score={report.sentiment_score:.0f}"
                )
        except Exception as e:
            logger.warning(f"[{symbol}] Refresh failed: {e}")
