# core/engine/monitor_loop.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: AI Strategy Monitor Loop (Regime-Scan, Strategy-Switch, Symbol-Update)

import asyncio
import logging
import threading
import time
from datetime import datetime

from alpaca.trading.requests import ClosePositionRequest

import config
import core.strategies as strategies
from core.engine.equity_fallback import resolve_equity
from core.engine.loop_counters import _bump_loop_counter
from core.notifier import send_slack_alert
from core.risk_manager import RiskManager
from core.strategies import RLStrategy


class MonitorLoopMixin:
    """
    Mixin für BotEngine: Periodischer Strategy-Monitor.
    Überwacht Marktregime, wechselt Strategien, aktualisiert Symbole.
    """

    def run_strategy_monitor_loop(self):
        logging.info("AI Strategy Monitor loop started.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self.monitor_running.is_set() and not self._shutdown_event.is_set():
                start_time = time.time()
                try:
                    self._skipped_symbols.clear()

                    logging.info("AI Monitor: Starting assessment cycle...")
                    self._log_strategy_thought("Starting Periodic Market Scan...")

                    current_time = datetime.now()
                    market_regime = self.regime_model.get_market_regime(
                        current_time, sim_client=None
                    )
                    self.current_market_data["vix"] = market_regime.get("value")
                    self.current_market_data["regime"] = market_regime.get(
                        "regime", "Unknown"
                    )

                    vix_val = market_regime.get("value")
                    vix_str = f"{vix_val:.2f}" if vix_val is not None else "N/A"
                    self._log_strategy_thought(
                        f"Market Regime is '{market_regime.get('regime')}' (VIX: {vix_str})."
                    )
                    self._last_scan_time = time.time()

                    if self._shutdown_event.is_set():
                        break

                    universe = (
                        self.live_universe
                        if self.live_universe
                        else config.DEFAULT_SYMBOLS
                    )
                    scanner_recommendation = loop.run_until_complete(
                        self.market_scanner.scan_market(
                            current_time,
                            market_regime,
                            sim_client=None,
                            live_symbols=universe,
                        )
                    )
                    if self._shutdown_event.is_set():
                        break
                    if not scanner_recommendation:
                        raise Exception("Scanner failed.")

                    # PR B (fail-safe): monotone monitor-scan liveness counter, bumped
                    # once the market scan completes. Pure observation — never gates flow.
                    _bump_loop_counter(self, "_scans_completed")

                    recommendation_confidence = scanner_recommendation.get(
                        "recommendation_confidence", "medium"
                    )
                    top_stocks_list = scanner_recommendation.get("top_stocks", [])
                    self._last_top_picks = list(top_stocks_list)[:10]
                    target_stocks = [
                        s["symbol"] for s in top_stocks_list
                    ] or config.DEFAULT_SYMBOLS

                    # ---------------------------------------------------------
                    # CRITICAL FIX: Append currently held open positions to `target_stocks`.
                    # Because `symbols_to_process` in trading_loop.py is strictly set to this list,
                    # if a held position falls out of the top 10 scanner results it would NEVER
                    # be evaluated again -> no SELL signal could ever be generated.
                    # ---------------------------------------------------------
                    if self.api and not self.is_simulation:
                        try:
                            _open_positions = self.api.get_all_positions()
                            _open_symbols = [
                                p.symbol if hasattr(p, "symbol") else p.get("symbol")
                                for p in _open_positions
                            ]
                            for sym in _open_symbols:
                                if (
                                    sym
                                    and isinstance(sym, str)
                                    and sym not in target_stocks
                                ):
                                    target_stocks.append(sym)
                            if _open_symbols:
                                logging.info(
                                    f"AI Monitor: Appended {len(_open_symbols)} open positions to target_stocks to ensure SELL evaluation."
                                )
                        except Exception as e:
                            logging.error(
                                f"Failed to fetch open positions for target_stocks append: {e}"
                            )

                    self._log_strategy_thought(
                        f"Scanner identified top candidates: {target_stocks[:5]}..."
                    )

                    target_strategy_name = getattr(config, "ACTIVE_STRATEGY", "RLAgent")
                    current_strategy_name = None
                    with self.strategy_lock:
                        current_strategy_name = (
                            self.active_strategy.strategy_name
                            if self.active_strategy
                            else None
                        )

                    TargetStrategyClass = strategies.STRATEGY_CLASSES.get(
                        target_strategy_name, RLStrategy
                    )

                    if current_strategy_name != target_strategy_name:
                        logging.warning(
                            f"--- STRATEGY SWITCH: {current_strategy_name} -> {target_strategy_name} ---"
                        )
                        self.strategy_running.clear()
                        if self.strategy_thread and self.strategy_thread.is_alive():
                            self.strategy_thread.join(10)
                        with self.strategy_lock:
                            self.active_strategy = None

                        skip_liquidation = (
                            getattr(
                                config, "STRATEGY_SWITCH_WITHOUT_LIQUIDATION", False
                            )
                            and target_strategy_name in ("RLAgent", "LSTMDynamic")
                            and (
                                current_strategy_name in ("RLAgent", "LSTMDynamic")
                                or current_strategy_name is None
                            )
                        )
                        if not skip_liquidation:
                            try:
                                if self.api:
                                    self.api.cancel_orders()
                                    self._interruptible_pause(2)  # #1232
                                    self.api.close_all_positions(
                                        ClosePositionRequest(cancel_orders=True)
                                    )
                                    self._interruptible_pause(5)  # #1232
                            except Exception as e:
                                logging.error("Liquidation failed: %s", e)

                        if self._shutdown_event.is_set():
                            break
                        try:
                            # BUG-AI-S01 (#1232): never size off a hardcoded equity.
                            current_equity = resolve_equity(
                                self.api, config.get_config().DEFAULT_EQUITY
                            )
                            if not self.live_risk_manager:
                                self.live_risk_manager = RiskManager(
                                    self.api, current_equity
                                )
                            with self.strategy_lock:
                                self.active_strategy = TargetStrategyClass(
                                    client=self.api,
                                    symbols=target_stocks,
                                    running_event=self.strategy_running,
                                    total_capital=current_equity,
                                    risk_manager=self.live_risk_manager,
                                    data_provider=self.data_provider,
                                    thought_callback=self._log_strategy_thought,
                                    compliance_guardian=self.compliance_guardian,
                                )
                                if self.active_strategy:
                                    self.active_strategy.current_recommendation_confidence = (
                                        recommendation_confidence
                                    )
                                    # Fix: Register active strategy in AgentRegistry so that
                                    # LSTMSignalAgent and RLConfidenceAgent in the Round Table
                                    # can access it via get_global_registry().get_active().
                                    # Without this, registry.get_active() always returns None.
                                    _reg = getattr(self, "agent_registry", None)
                                    if _reg is not None:
                                        _strat_name = getattr(
                                            self.active_strategy,
                                            "strategy_name",
                                            TargetStrategyClass.__name__,
                                        )
                                        _reg.register(
                                            _strat_name,
                                            self.active_strategy,
                                            set_active=True,
                                        )
                                        logging.info(
                                            "AgentRegistry: '%s' registered as active "
                                            "strategy for Round Table voting.",
                                            _strat_name,
                                        )
                                    # Fix: Inject SpecialistRegistry into SpecialistAlphaAgent
                                    _spec_reg = getattr(
                                        self, "specialist_registry", None
                                    )
                                    if _spec_reg is not None:
                                        try:
                                            from core.round_table.agents import (
                                                set_specialist_registry,
                                            )

                                            set_specialist_registry(_spec_reg)
                                            logging.info(
                                                "SpecialistAlphaAgent: StockSpecialistRegistry injected."
                                            )
                                        except Exception as _inj_err:
                                            logging.warning(
                                                "Failed to inject SpecialistRegistry: %s",
                                                _inj_err,
                                            )
                            self.strategy_running.set()
                            self.strategy_thread = threading.Thread(
                                target=self.run_strategy_async_wrapper,
                                daemon=True,
                                name="StrategyThread",
                            )
                            self.strategy_thread.start()
                            self._send_update_threadsafe(
                                "strategy_update",
                                {
                                    "active": True,
                                    "mode": "LIVE",
                                    "strategy": TargetStrategyClass.__name__,
                                },
                            )
                            logging.info(
                                f"AI Monitor: Switched to '{TargetStrategyClass.__name__}'."
                            )
                        except Exception as e:
                            logging.error(
                                f"Failed start new strategy: {e}", exc_info=True
                            )
                            self.stop_strategy()
                    else:
                        should_update = False
                        with self.strategy_lock:
                            if self.active_strategy:
                                self.active_strategy.thought_callback = (
                                    self._log_strategy_thought
                                )
                                if set(self.active_strategy.symbols) != set(
                                    target_stocks
                                ):
                                    self.active_strategy.symbols = target_stocks
                                    should_update = True
                                self.active_strategy.current_recommendation_confidence = (
                                    recommendation_confidence
                                )
                        if should_update:
                            logging.info(
                                f"AI Monitor: Updated stock list for {current_strategy_name}: {target_stocks}"
                            )
                        else:
                            logging.info(
                                f"AI Monitor: Conditions stable ({current_strategy_name})."
                            )

                    if (
                        not self.is_simulation
                        and self.api
                        and datetime.now().date() != self._last_live_equity_write_date
                    ):
                        self._append_live_equity_to_benchmark()
                except Exception as e:
                    logging.error(f"Error in monitor cycle: {e}", exc_info=True)
                    send_slack_alert(
                        f"⚠️ *Warning in Monitor Cycle*: {e}", level="warning"
                    )
                elapsed = time.time() - start_time
                wait_time = max(10, config.STRATEGY_MONITOR_INTERVAL_SECONDS - elapsed)
                self._shutdown_event.wait(wait_time)
        except Exception as e:
            logging.error(f"Unexpected error in monitor loop: {e}", exc_info=True)
        finally:
            logging.info("AI Strategy Monitor loop exited.")
            loop.close()
