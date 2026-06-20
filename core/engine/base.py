# core/engine/base.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# BotEngine: Koordinationsklasse (schlanke Shell) — kombiniert alle Mixins

import asyncio
import json
import logging
import os
import threading
from collections import deque
from datetime import date, datetime
from typing import Any, Deque, Dict, List, Optional, Set

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

import config
from core.agent_registry import AgentRegistry, set_global_registry
from core.ai_components import (
    AILearningEngine,
    AIMarketScanner,
    MarketRegimeModel,
    NewsProcessor,
)
from core.ai_rules import AILearnedRules
from core.cloud_logger import get_cloud_logger
from core.compliance import ComplianceGuardian
from core.data_provider import HistoricalDataProvider
from core.exceptions import BrokerConnectionError, RiskLimitExceeded
from core.llm.health import (  # G4a-3: shared Ollama probe + provider resolution
    ollama_reachable,
    resolved_provider_name,
)
from core.notifier import send_slack_alert
from core.redis_client import RedisClient
from core.risk_manager import RiskManager
from core.specialist_registry import StockSpecialistRegistry
from core.strategies import BaseStrategy
from core.structured_logging import setup_logging

from .monitor_loop import MonitorLoopMixin
from .news_poller import NewsPollerMixin
from .order_executor import OrderExecutorMixin
from .simulation_runner import SimulationRunnerMixin
from .trading_loop import TradingLoopMixin

setup_logging()


class BotEngine(
    TradingLoopMixin,
    MonitorLoopMixin,
    NewsPollerMixin,
    OrderExecutorMixin,
    SimulationRunnerMixin,
):
    """
    Bot Engine — schlanke Koordinationsklasse.
    Alle Loops/Executor/Simulation-Logik in Mixins ausgelagert (Epic 1.7 / PR-C).
    """

    def validate_dependencies(self) -> None:
        """
        FAIL-FAST Architektur: Verhindert den Systemstart (Watermelon Effect),
        wenn essenzielle APIs fehlen. Wirft einen RuntimeError statt stillem Fallback.

        Provider-aware (G4a-3, ADR-014): NUR LLM_PROVIDER=ollama (Desktop, lokales
        LLM) ist von der GEMINI_API_KEY-Pflicht befreit — ein noch nicht
        gestarteter lokaler Daemon darf den Engine-Init nicht bricken. Jeder
        andere Wert (``gemini`` ODER ein Tippfehler wie ``ollam``) fällt im
        Provider-Seam auf den Gemini-Pfad zurück und braucht den Key → FAIL-FAST
        beim Init via WHITELIST (`== "ollama"`), nicht via blindem `!= "gemini"`,
        das einen Tippfehler still durchließe und erst in _check_llm crasht. Der
        Default-Pfad (unset/"gemini") ist byte-identisch.
        """
        provider = resolved_provider_name()
        if provider == "ollama":
            return
        gemini_key = config.get_secret_str(getattr(config, "GEMINI_API_KEY", None))
        if not gemini_key:
            raise RuntimeError(
                "CRITICAL DEPENDENCY MISSING: GEMINI_API_KEY. "
                "Cloud Run Service benötigt dieses Secret zwingend für den LLM-RoundTable!"
            )

    # --- Startup Health Checks ---

    async def _check_redis(self) -> bool:
        """Prüft ob Redis erreichbar ist (PING). Gibt False zurück bei Fehler."""
        try:
            r = await RedisClient.get_redis()
            if r is None:
                return False
            await r.ping()
            return True
        except Exception as e:
            import traceback

            logging.warning(
                "Startup check [redis] FAILED: %s\n%s", e, traceback.format_exc()
            )
            return False

    async def _check_llm(self) -> bool:
        """Prüft ob der konfigurierte LLM-Provider erreichbar ist (1 Probe).

        Provider-aware (G4a-3): LLM_PROVIDER=ollama → GET {OLLAMA_BASE_URL}/api/tags;
        sonst Gemini-Ping (byte-identisch zur bisherigen _check_gemini-Logik).
        Liefert IMMER einen bool (jede Exception → False) — eine entwichene
        Exception würde sonst den Startup-Check zum Absturz bringen."""
        provider = resolved_provider_name()
        if provider == "ollama":
            return await ollama_reachable()
        try:
            from google import genai

            client = genai.Client(api_key=config.get_secret_str(config.GEMINI_API_KEY))
            model_name = getattr(config, "GEMINI_MODEL_NAME", "gemini-2.5-flash")
            response = await asyncio.to_thread(
                client.models.generate_content, model=model_name, contents="ping"
            )
            return response is not None
        except Exception as e:
            logging.warning("Startup check [gemini] FAILED: %s", e)
            return False

    def _check_model_files(self) -> bool:
        """Prüft ob RL-Modell-Datei vorhanden ist (os.path.exists)."""
        try:
            data_dir = getattr(config, "DATA_DIR", "/app/data")
            if data_dir == "/app/data" and os.name == "nt":
                data_dir = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..", "..", "data")
                )
            rl_version = getattr(config, "RL_MODEL_VERSION", "rl_agent_v5")
            rl_path = os.path.join(data_dir, f"{rl_version}.zip")
            return os.path.exists(rl_path)
        except Exception as e:
            logging.warning("Startup check [rl_model] FAILED: %s", e)
            return False

    async def _startup_health_check(self) -> None:
        """
        Strikter Async-Startup-Check vor dem ersten Trading Cycle.
        Verhindert degradierten Start (Watermelon Effect).

        Kritisch (→ RuntimeError):  redis, llm
        Warning (degraded mode):    rl_model
        """
        # Critical checks — bot MUST NOT start without these
        critical_results = {
            "redis": await self._check_redis(),
            "llm": await self._check_llm(),
        }
        failed_critical = [k for k, v in critical_results.items() if not v]

        # Non-critical check — bot can run in degraded mode
        model_ok = self._check_model_files()
        if not model_ok:
            logging.warning(
                "⚠️  Startup check [rl_model]: RL model file not found — "
                "bot will run in DEGRADED MODE (HOLD-only until model is available)."
            )

        if failed_critical:
            msg = f"STARTUP HEALTH CHECK FAILED: {failed_critical}"
            logging.critical("🚨 %s", msg)
            send_slack_alert(f"🚨 *{msg}*", level="error")
            raise RuntimeError(msg)

        logging.info(
            "✅ Startup health check PASSED (redis=✓, llm=✓, rl_model=%s)",
            "✓" if model_ok else "⚠️ missing",
        )

    def __init__(
        self,
        trading_client: Optional[TradingClient] = None,
        data_client: Optional[StockHistoricalDataClient] = None,
    ):
        self.validate_dependencies()

        self.api = trading_client
        self.data_api = data_client
        self._shutdown_event = threading.Event()
        self.update_callback = None
        self.main_loop = None

        self.data_provider = HistoricalDataProvider(
            api=self.data_api, trading_api=self.api
        )
        self.news_processor = NewsProcessor()
        self.regime_model = MarketRegimeModel(self.data_provider)

        # --- Compliance Guardian ---
        self.compliance_guardian = None
        # GAP1-Residual (fail-closed): fallback True — a config variant that
        # LACKS the field must never silently disable order compliance. The
        # explicit operator opt-out stays: ENABLE_COMPLIANCE_GUARDIAN=False.
        if getattr(config, "ENABLE_COMPLIANCE_GUARDIAN", True):
            self.compliance_guardian = ComplianceGuardian()
            self.compliance_guardian.max_order_value = getattr(
                config, "COMPLIANCE_MAX_ORDER_VALUE", 10000.0
            )
            self.compliance_guardian.max_daily_trades = getattr(
                config, "COMPLIANCE_MAX_DAILY_TRADES", 50
            )
            logging.info(
                "ComplianceGuardian ACTIVE (max_order=%.0f, max_daily=%d)",
                self.compliance_guardian.max_order_value,
                self.compliance_guardian.max_daily_trades,
            )

        self._skipped_symbols: Set[str] = set()
        self._last_scan_time = 0

        # Signal emitter shim (Qt-like for Scanner/Learning callbacks)
        class _SignalEmitter:
            def __init__(self, engine, signal_name):
                self.engine = engine
                self.signal_name = signal_name

            def emit(self, *args):
                try:
                    if self.signal_name == "scanner_progress":
                        self.engine._send_update_threadsafe(
                            "scanner_progress", {"current": args[0], "total": args[1]}
                        )
                    elif self.signal_name == "scanner_complete":
                        self.engine._send_update_threadsafe("scanner_complete", args[0])
                    elif self.signal_name == "ai_learning_update":
                        self.engine._send_update_threadsafe(
                            "ai_learning_update", {"message": args[0]}
                        )
                    elif self.signal_name == "ai_learning_complete":
                        self.engine._send_update_threadsafe(
                            "ai_learning_complete", args[0]
                        )
                    elif self.signal_name == "error_message":
                        self.engine._send_update_threadsafe(
                            "error_message", {"title": args[0], "message": args[1]}
                        )
                except Exception as e:
                    logging.error(f"Error emitting signal {self.signal_name}: {e}")

        class EngineSignals:
            def __init__(self, engine):
                self.engine = engine
                self._emitters = {}

            def __getattr__(self, name):
                if name not in self._emitters:
                    self._emitters[name] = _SignalEmitter(self.engine, name)
                return self._emitters[name]

        self.signals = EngineSignals(self)

        self.market_scanner = AIMarketScanner(
            self.signals, self.data_provider, self.news_processor, self._shutdown_event
        )
        self.learning_engine = AILearningEngine(self.signals)
        self.ai_rules = AILearnedRules()

        self.active_strategy: Optional[BaseStrategy] = None
        self.strategy_thread: Optional[threading.Thread] = None
        self.strategy_running = threading.Event()
        self.strategy_lock = threading.Lock()

        # AgentRegistry: dynamisches Strategy-Management (Epic 2.3-Pre)
        self.agent_registry = AgentRegistry()
        set_global_registry(self.agent_registry)

        # Boot Round Table Engine (DI Factory)
        from core.round_table.runner import boot_engine

        boot_engine(os.getenv("ENTERPRISE_LICENSE_KEY"))

        # StockSpecialistRegistry (Epic 3.3): injiziert in SpecialistAlphaAgent.
        # Fix: set_specialist_registry() wurde im Produktionscode nie aufgerufen →
        # SpecialistAlphaAgent voted immer neutral 0.5. Hier initialisieren + injizieren.
        self._init_specialist_registry()

        self.monitor_thread: Optional[threading.Thread] = None
        self.monitor_running = threading.Event()

        self.news_thread: Optional[threading.Thread] = None
        self.news_running = threading.Event()

        self.is_simulation = False
        self.current_market_data: Dict[str, Any] = {}
        self.live_risk_manager: Optional[RiskManager] = None
        self.sim_risk_manager: Optional[RiskManager] = None
        self.current_sim_strategy: Optional[BaseStrategy] = None
        self.live_universe: List[str] = []

        self.simulation_running = False
        self.simulation = None
        self.current_strategy_class = None
        self._last_top_picks: List[Dict[str, Any]] = []
        self._last_round_table_state: List[Dict[str, Any]] = []
        self._last_live_equity_write_date: Optional[date] = None
        self._recent_news_cache: List[Dict[str, Any]] = []
        self._recent_news_cache_max = 100
        self._cycle_latencies: Deque[float] = deque(maxlen=100)
        self._last_cycle_details: Dict[str, float] = {}
        self._last_round_table_state: List[Dict[str, Any]] = []
        self.cloud_logger = get_cloud_logger()

        logging.info("Bot Engine Initialized.")
        self._start_alpaca_news_polling()

        if getattr(config, "ENABLE_HEARTBEAT", False):
            self._start_heartbeat_monitor()

        if getattr(config, "AUTO_START_STRATEGY", False):
            logging.info("AUTO_START_STRATEGY is True. Initiating live strategy...")
            threading.Timer(5.0, self.start_live_strategy).start()

    # --- Infrastructure ---

    def set_update_callback(self, callback, loop):
        self.update_callback = callback
        self.main_loop = loop

    def _interruptible_pause(self, seconds: float) -> bool:
        """Wait up to ``seconds`` but return IMMEDIATELY once shutdown is signalled.

        BUG-AI-003/006/S01 (#1232): a bare ``time.sleep(N)`` ignores the shutdown event
        and blocks engine teardown for up to its full duration (the 60s warm-up / news
        sleeps were the worst). ``Event.wait`` returns at once when the event is set.
        Returns True if shutdown fired during the wait.
        """
        if not hasattr(self, "_shutdown_event"):
            import time

            time.sleep(seconds)
            return False
        return self._shutdown_event.wait(seconds)

    def _send_update_threadsafe(self, signal_name: str, data: Any):
        if self.main_loop and self.update_callback:
            try:
                update_message = {"type": signal_name, "data": data}
                asyncio.run_coroutine_threadsafe(
                    self.update_callback(update_message), self.main_loop
                )
            except Exception as e:
                logging.warning("Failed to send update '%s': %s", signal_name, e)

    def _schedule_specialist_warmup_check(self) -> None:
        """
        Startet einen Background-Thread der nach 60s prüft ob StockSpecialistRegistry
        Reports gecacht hat. Wenn 0 Reports → SpecialistAlphaAgent bleibt neutral 0.5.

        MiFID II / Observability: Frühzeitige Diagnostik bei Warm-up-Problemen.
        """

        def _check() -> None:
            self._interruptible_pause(60)  # #1232: interruptible on shutdown
            if self.specialist_registry is None:
                return
            cached = len(getattr(self.specialist_registry, "_reports", {}))
            logging.info(
                "SpecialistRegistry: %d reports nach 60s warm-up gecacht", cached
            )
            if cached == 0:
                logging.warning(
                    "⚠️ SpecialistRegistry: 0 reports nach 60s — "
                    "SpecialistAlphaAgent bleibt neutral 0.5"
                )

        threading.Thread(target=_check, daemon=True, name="SpecialistWarmup").start()

    def _init_specialist_registry(self) -> None:
        """
        StockSpecialistRegistry wurde manuell komplett deaktiviert (USER REQUEST),
        um unnötigen Gemini-API Ressourcenverbrauch zu stoppen, da der Specialist
        momentan keinen wesentlichen Einfluss auf den Konsens hat.

        RPAR-#1284 / G1b: Re-Enable ist flag-gated (SPECIALIST_REGISTRY_ENABLED,
        default OFF) und sitzt in start_live_strategy NACH der live_universe-Befüllung
        (das Universe existiert hier im __init__ noch nicht). Diese Methode bleibt der
        unveränderte None-Disabler -> OFF-Pfad byte-identisch.
        """
        self.specialist_registry = None
        logging.info(
            "StockSpecialistRegistry is GLOBALLY DISABLED to save Gemini API resources."
        )
        return

    def _register_high_priority_symbols_at_startup(self, symbols: list) -> None:
        """
        Markiert alle initialen Symbole als HIGH-PRIORITY im StockSpecialistRegistry.

        Ohne diesen Aufruf würden alle Symbole in der normalen Rotation starten:
        Refresh-Intervall = 43200s / n_symbols → bei 50 Symbolen ~14 Minuten pro Symbol.
        Mit HIGH-PRIORITY: alle Symbole werden beim ersten Tick (10s) als stale erkannt
        und innerhalb von max. ~4 Minuten einmalig aufgefrischt.

        Ergebnis: SpecialistAlphaAgent hat bereits im 2. oder 3. Zyklus echte Reports
        statt >10 Minuten neutral zu bleiben.
        """
        if self.specialist_registry is None or not symbols:
            return
        try:
            self.specialist_registry.update_priority(symbols)
            logging.info(
                "SpecialistRegistry: %d Symbole als HIGH-PRIORITY markiert — "
                "erster Refresh nach ~10s (statt ~14min).",
                len(symbols),
            )
        except Exception as e:
            logging.warning(
                "SpecialistRegistry.update_priority fehlgeschlagen (nicht-kritisch): %s",
                e,
            )

    def _log_strategy_thought(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}"
        self._send_update_threadsafe("ai_thought", {"message": formatted_msg})
        logging.info("[AI THOUGHT] %s", message)

    def _append_live_equity_to_benchmark(self) -> None:
        if not self.api or self.is_simulation:
            return
        try:
            r = RedisClient.get_sync_redis()
            acc = self.api.get_account()
            live_equity = float(acc.equity or 0)
            if live_equity <= 0:
                return
            data_str = r.get("benchmark_equity_data")
            if data_str:
                data = json.loads(data_str)
            else:
                data = {"points": []}
            points = data.get("points", [])
            if not isinstance(points, list):
                points = []
                data["points"] = points
            today_str = date.today().strftime("%Y-%m-%d")
            if points and points[-1].get("date") == today_str:
                points[-1]["equity"] = round(live_equity, 2)
            else:
                points.append({"date": today_str, "equity": round(live_equity, 2)})
            r.set("benchmark_equity_data", json.dumps(data))
            self._last_live_equity_write_date = date.today()
            logging.info(
                f"Live equity updated in Redis benchmark: {today_str} ${live_equity:,.2f}"
            )

            # Trigger daily snapshot event for DB persistence (MiFID II WORM compliance)
            import uuid
            from datetime import timezone

            try:
                open_positions = self.api.get_all_positions()
            except Exception as pos_err:
                logging.warning(
                    "Failed to fetch open positions for daily portfolio snapshot: %s",
                    pos_err,
                )
                open_positions = []

            snapshot = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_equity": live_equity,
                "cash": float(acc.cash or 0),
                "positions_json": [
                    {"symbol": p.symbol, "qty": float(p.qty)} for p in open_positions
                ],
                "strategy_name": getattr(config, "ACTIVE_STRATEGY", "RLAgent"),
                "is_simulation": False,
            }
            get_cloud_logger().log_portfolio_snapshot(snapshot)

        except Exception as e:
            logging.warning(
                "Live equity append to benchmark failed: %s", e, exc_info=True
            )

    def _start_heartbeat_monitor(self):
        def heartbeat_thread():
            interval = getattr(config, "HEARTBEAT_INTERVAL_HOURS", 6) * 3600
            logging.info(
                f"Heartbeat monitor started (Interval: {config.HEARTBEAT_INTERVAL_HOURS}h)."
            )
            while not self._shutdown_event.is_set():
                try:
                    status_msg = "Bot is ACTIVE. "
                    if self.strategy_running.is_set():
                        status_msg += "Strategy: RUNNING. "
                    else:
                        status_msg += "Strategy: STOPPED. "
                    if self.api:
                        acc = self.api.get_account()
                        status_msg += f"Equity: ${float(acc.equity):,.2f}"
                    send_slack_alert(f"💓 *Heartbeat*: {status_msg}", level="info")
                except Exception as e:
                    logging.error("Heartbeat loop error: %s", e)
                if self._shutdown_event.wait(interval):
                    break

        threading.Thread(
            target=heartbeat_thread, daemon=True, name="HeartbeatThread"
        ).start()

    # --- Strategy Lifecycle ---

    def start_live_strategy(self):
        logging.info("Engine: Received start_live_strategy command...")
        send_slack_alert("🚀 *Engine starting live strategy...*", level="success")
        if self._shutdown_event.is_set():
            self._shutdown_event.clear()

        # --- Fix #277: Reset stale Kill Switch state from Redis ---
        # If the Kill Switch was tripped in a previous session (e.g. before a Cloud Run
        # restart), `system_halted=true` persists in Redis and will immediately halt the
        # trading loop after start. An explicit start_live_strategy() call represents
        # operator intent — it must clear the stale halt so the loop can run.
        try:
            from core.kill_switch import kill_switch as _ks

            if _ks is not None and _ks.is_halted():
                logging.warning(
                    "Kill Switch was HALTED (stale Redis state). "
                    "Resetting before starting strategy — operator intent."
                )
                _ks.reset()
        except Exception as _ks_err:
            logging.warning("Could not reset Kill Switch: %s", _ks_err)

        if self.api is None:
            logging.error("Engine: Cannot start, Alpaca API not connected.")
            self._send_update_threadsafe(
                "error_message",
                {"title": "API Error", "message": "Alpaca API not connected."},
            )
            return False
        self.stop_strategy()

        # FIX: stop_strategy() sets _shutdown_event. We MUST clear it here so the NEW threads don't immediately exit.
        self._shutdown_event.clear()

        self.is_simulation = False
        self._send_update_threadsafe("clear_chart", {})
        self._send_update_threadsafe(
            "strategy_update",
            {"active": True, "mode": "LIVE", "strategy": "Initializing..."},
        )

        try:
            from core.latency_watchdog import latency_watchdog

            latency_watchdog.start()
        except ImportError:
            pass

        try:
            from core.cycle_watchdog import cycle_watchdog

            self._cycle_watchdog = cycle_watchdog
        except ImportError:
            self._cycle_watchdog = None

        if config.ENVIRONMENT == "production":
            logging.info("Engine: Fetching S&P 500 universe for live trading...")
            self.live_universe = self.data_provider.get_sp500_symbols()
        else:
            logging.info("Engine: Using DEFAULT_SYMBOLS for local development.")
            self.live_universe = config.DEFAULT_SYMBOLS
        logging.info(f"Engine: Universe set to {len(self.live_universe)} symbols.")

        # RPAR-#1284 / G1b - flag-gated StockSpecialistRegistry activation (default OFF).
        # MUST run here, AFTER self.live_universe is fully populated above: the registry
        # has to see the real S&P-500/DEFAULT_SYMBOLS universe, not the empty list it has
        # in __init__ (where _init_specialist_registry runs before live_universe exists).
        # When OFF (default), this block is skipped -> specialist_registry stays the None
        # set by _init_specialist_registry -> byte-identical to today. When ON, it
        # constructs + starts the registry and wires the start path (.start() + priority +
        # warmup) that the prod code never called - the P1 decision-path flip, human-gated.
        if config.get_config().SPECIALIST_REGISTRY_ENABLED:
            try:
                gemini_key = config.get_secret_str(
                    getattr(config, "GEMINI_API_KEY", None)
                )
                self.specialist_registry = StockSpecialistRegistry(
                    self.live_universe, gemini_key
                )
                self.specialist_registry.start()
                self._register_high_priority_symbols_at_startup(self.live_universe)
                self._schedule_specialist_warmup_check()
                logging.info(
                    "SpecialistRegistry: ENABLED (SPECIALIST_REGISTRY_ENABLED=True) - "
                    "constructed with %d symbols, started, high-priority registered.",
                    len(self.live_universe),
                )
            except Exception as exc:
                # Boot resilience (#1361 review): on the OSS edition the registry's
                # LLM/model dependencies may be absent (ollama, no Gemini key, no local
                # model), so construction/start can throw. Degrade to None instead of
                # crashing the engine boot -> SpecialistAlphaAgent stays excluded
                # (weight 0), exactly as when the flag is OFF. (Exception, not
                # BaseException, so Ctrl-C / SystemExit still propagate.)
                self.specialist_registry = None
                logging.warning(
                    "SpecialistRegistry: ENABLED but initialization failed (%s) - "
                    "degrading to None; SpecialistAlphaAgent stays excluded. "
                    "Engine boot continues.",
                    exc,
                )

        self.current_market_data = {}

        try:
            live_equity = float(self.api.get_account().equity)
            self.live_risk_manager = RiskManager(self.api, live_equity)
            self.live_risk_manager.reset_daily_limit(live_equity)
            # Fix #ADR-C04: Reset ComplianceGuardian daily trade counter for the new
            # trading day. Without this, daily_trades persists across calendar days in
            # long-running containers (min-instances=1), blocking all trades from day 2.
            if self.compliance_guardian is not None:
                self.compliance_guardian.reset_daily_limit()
                logging.info(
                    "Engine: ComplianceGuardian daily trade counter reset for new session."
                )
        except APIError as e:
            raise BrokerConnectionError(
                f"Cannot fetch account for live strategy: {e}"
            ) from e
        except RiskLimitExceeded:
            raise
        except Exception as e:
            logging.error("Failed init live RM: %s", e)
            self._send_update_threadsafe(
                "error_message", {"title": "Error", "message": "Could not init RM."}
            )
            self.stop_strategy()
            return False

        self.strategy_running.set()
        self.strategy_thread = threading.Thread(
            target=self.run_strategy_async_wrapper, daemon=True, name="StrategyThread"
        )
        self.strategy_thread.start()

        self.monitor_running.set()
        self.monitor_thread = threading.Thread(
            target=self.run_strategy_monitor_loop, daemon=True, name="MonitorThread"
        )
        self.monitor_thread.start()

        logging.info("Engine: Live strategy and monitor threads started.")
        return True

    def stop_strategy(self):
        if (
            not self.strategy_running.is_set()
            and not self.monitor_running.is_set()
            and not self.is_simulation
        ):
            return
        logging.info("Engine: Stopping strategy and monitor...")
        try:
            from core.latency_watchdog import latency_watchdog

            latency_watchdog.stop()
        except ImportError:
            pass
        self._shutdown_event.set()
        self.strategy_running.clear()
        self.monitor_running.clear()
        threads = [self.strategy_thread, self.monitor_thread]
        current_thread = threading.current_thread()
        for t in threads:
            if t and t.is_alive():
                if t is current_thread:
                    logging.debug(
                        f"Skipping join for {t.name} as it's the current thread."
                    )
                else:
                    logging.debug(f"Waiting for {t.name}...")
                    t.join(3)
            if t and t.is_alive() and t is not current_thread:
                logging.warning(f"{t.name} did not exit cleanly.")
        with self.strategy_lock:
            self.active_strategy = None
        self.is_simulation = False
        self._send_update_threadsafe("strategy_update", {"active": False})

    # --- Chat Context ---

    def get_chat_context(self) -> str:
        parts = []
        strategy_name = getattr(config, "ACTIVE_STRATEGY", "RLAgent")
        parts.append(f"Current strategy: {strategy_name}.")

        active = getattr(self, "active_strategy", None)
        if active and hasattr(active, "portfolio_manager") and active.portfolio_manager:
            pm = active.portfolio_manager
            try:
                summary = pm.get_portfolio_summary()
                parts.append(f"Portfolio: {summary.get('summary', 'N/A')}")
                for sym, score in list(pm._position_scores.items())[:20]:
                    parts.append(
                        f"  - {sym}: qty={score.qty}, value=${score.market_value:.2f}, "
                        f"PnL%={score.unrealized_pnl_pct:.2f}, score={score.total_score:.2f}"
                    )
            except Exception as e:
                parts.append(f"Portfolio summary error: {e}")
        elif self.api:
            try:
                positions = self.api.get_all_positions()
                acc = self.api.get_account()
                equity = float(acc.equity or 0)
                parts.append(
                    f"Live account equity: ${equity:.2f}. Positions: {len(positions)}."
                )
                for p in positions[:20]:
                    parts.append(
                        f"  - {p.symbol}: qty={p.qty}, value=${float(p.market_value or 0):.2f}"
                    )
            except Exception:
                parts.append("Live account: unable to fetch.")
        else:
            parts.append("No active portfolio or live account.")

        picks = getattr(self, "_last_top_picks", [])
        if picks:
            parts.append("Recent scanner top picks (symbol, score, reason):")
            for p in picks[:10]:
                sym = p.get("symbol", "?")
                score = p.get("score", 0)
                reason = p.get("reason", "") or p.get("momentum_reason", "")
                parts.append(f"  - {sym}: {score:.2f} | {reason[:80]}")

        news = getattr(self, "_recent_news_cache", [])
        if news:
            parts.append("Recent news/trends (headline, sentiment, symbols):")
            for item in news[-25:]:
                ts = item.get("timestamp", "")[:16] if item.get("timestamp") else ""
                head = (item.get("headline") or "N/A")[:120]
                sent = item.get("sentiment", "neut")
                score = item.get("score", 0)
                syms = ",".join(item.get("symbols", [])[:5])
                parts.append(f"  [{ts}] ({sent}:{score:.2f}) {head} ({syms})")
        else:
            parts.append("No recent news in cache yet.")

        try:
            ti_path = os.path.join(config.DATA_DIR, "trade_intelligence.json")
            if os.path.isfile(ti_path):
                with open(ti_path, "r") as f:
                    ti = json.load(f)
                completed = ti.get("completed_trades", [])
                symbols = list(ti.get("symbol_intelligence", {}).keys())
                parts.append(
                    f"Trade intelligence: {len(completed)} completed trades, "
                    f"{len(symbols)} symbols with history. "
                    f"Symbols: {', '.join(symbols[:15]) or 'none'}."
                )
        except Exception:
            pass

        parts.append(
            "When answering: use this data first. For market trends, earnings, politics, "
            "or general questions beyond this data, use your general knowledge and say "
            "so briefly. Consider earnings calls and macro/political events when relevant to a symbol."
        )
        return "\n".join(parts)
