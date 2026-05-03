# core/engine/trading_loop.py
# Epic 1.4 / Issue #217 — LangGraph-Dispatch für Non-LSTM-Strategien
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Epic 2.3-Pre / PR-A — Graceful Handover via AgentRegistry
# Verantwortlichkeit: Live Trading Loop, Snapshot-Fetch, LangGraph-Task-Dispatch

import asyncio
import logging
import time
from datetime import datetime, timezone

from alpaca.data.requests import StockSnapshotRequest
from alpaca.common.exceptions import APIError

from config import (
    ALPACA_DATA_FEED,
)  # ML-1: SIP = consolidated NBBO (MiFID II best-execution)
from core.events import SignalEvent
from core.exceptions import BrokerConnectionError
from core.notifier import send_slack_alert

# Layer 2: Per-Symbol Evaluation Timeout (MiFID II Art. 17)
_SYMBOL_EVAL_TIMEOUT_SEC = 45.0

try:
    from core.telemetry import get_tracer
except ImportError:  # pragma: no cover

    def get_tracer(name):  # type: ignore[misc]
        from contextlib import nullcontext

        class _Noop:
            def start_as_current_span(self, *a, **kw):
                return nullcontext()

        return _Noop()


try:
    from core.orchestration.graph import build_symbol_eval_graph
except ImportError:  # pragma: no cover
    build_symbol_eval_graph = None  # type: ignore[assignment]

try:
    from core.kill_switch import kill_switch
except ImportError:  # pragma: no cover
    kill_switch = None  # type: ignore[assignment]


def _extract_ohlc_from_snapshot(snapshot_obj) -> tuple[dict, float]:
    """
    Extrahiert OHLC + Live-Preis aus einem Alpaca-Snapshot-Objekt.

    KRITISCH: Nutzt latest_trade.price als 'close' (live Intraday-Preis).
    daily_bar liefert die GESTRIGE EOD-Bar — sie darf NICHT als close verwendet
    werden, da sonst alle Zyklen identische stale Preise bekommen.

    Priorität:
        1. latest_trade.price → live 'close' (primär)
        2. daily_bar          → O/H/L-Referenz (gestern), H/L werden auf live Preis erweitert
        3. Fallback           → daily_bar.close wenn kein latest_trade vorhanden (Markt zu)
        4. Nullen             → wenn weder Bar noch Trade

    Returns:
        (ohlc_dict, price) — price == ohlc['close']
    """
    trade = getattr(snapshot_obj, "latest_trade", None)
    bar = getattr(snapshot_obj, "daily_bar", None)

    if trade is not None:
        # Live Intraday-Preis
        live_price = float(getattr(trade, "price", getattr(trade, "p", 0.0)))

        if bar is not None:
            bar_open = float(getattr(bar, "open", getattr(bar, "o", live_price)))
            bar_high = float(getattr(bar, "high", getattr(bar, "h", live_price)))
            bar_low = float(getattr(bar, "low", getattr(bar, "l", live_price)))
            bar_volume = float(getattr(bar, "volume", getattr(bar, "v", 0.0)))
            ohlc = {
                "open": bar_open,
                # H/L auf live Preis erweitern wenn er outside der EOD-Range liegt
                "high": max(bar_high, live_price),
                "low": min(bar_low, live_price),
                "close": live_price,  # ← LIVE PREIS (nicht gestern!)
                "volume": bar_volume,
            }
        else:
            # Kein daily_bar — nur latest_trade vorhanden
            vol = float(trade.size) if hasattr(trade, "size") else 0.0
            ohlc = {
                "open": live_price,
                "high": live_price,
                "low": live_price,
                "close": live_price,
                "volume": vol,
            }
        return ohlc, live_price

    elif bar is not None:
        # Kein live Trade (Markt geschlossen) → daily_bar als Fallback
        bar_close = float(getattr(bar, "close", getattr(bar, "c", 0.0)))
        ohlc = {
            "open": float(getattr(bar, "open", getattr(bar, "o", 0.0))),
            "high": float(getattr(bar, "high", getattr(bar, "h", 0.0))),
            "low": float(getattr(bar, "low", getattr(bar, "l", 0.0))),
            "close": bar_close,
            "volume": float(getattr(bar, "volume", getattr(bar, "v", 0.0))),
        }
        return ohlc, bar_close

    else:
        # Weder Trade noch Bar
        return {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0}, 0.0


class TradingLoopMixin:
    """
    Mixin für BotEngine: async Live-Trading-Loop.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    def run_strategy_async_wrapper(self):
        """Thread-Target: Erstellt asyncio-Event-Loop und führt live_trading_loop aus."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.live_trading_loop())
        except Exception as e:
            logging.error(f"Async wrapper exception: {e}", exc_info=True)
        finally:
            try:
                loop.close()
            except Exception as e:
                logging.error("Error closing async loop: %s", e)

    async def _startup_health_check(self) -> None:
        """
        Startzeit-Dependency-Check.
        No-op Basisimplementierung — wird in BotEngine mit Redis/Gemini Checks überschrieben.
        Direkte TradingLoopMixin-Instanzen (z.B. in Unit Tests) überspringen den Check.
        """

    async def live_trading_loop(self):  # noqa: C901
        """
        Hauptschleife für Live-Trading:
        1. Kill-Switch prüfen
        2. Markt-Stunden prüfen (sleep 300s wenn zu)
        3. Snapshots fetchen
        4a. LSTMDynamic: sequenziell in Rank-Reihenfolge (Cash-Deduplication)
        4b. Andere Strategien: LangGraph-Dispatch via asyncio.gather (parallel)
        5. Latenz messen
        """
        logging.info("Live trading loop started.")
        self._skipped_symbols.clear()

        # ADR: Startup Health Check — verhindert degradierten Start (Watermelon Effect).
        # Kritische Dependencies (Redis, Gemini) werden vor dem ersten Cycle geprüft.
        # Bei Failure: RuntimeError wird geworfen → except Exception in run_strategy_async_wrapper
        # fängt diesen und stoppt den Bot sauber.
        try:
            await self._startup_health_check()
        except RuntimeError as e:
            logging.critical(
                "🚨 Startup health check failed — aborting trading loop: %s", e
            )
            self.strategy_running.clear()
            return

        while self.strategy_running.is_set() and not self._shutdown_event.is_set():
            # Kill Switch
            if kill_switch is not None and kill_switch.is_halted():
                logging.error("Kill Switch is HALTED. Stopping engine loop.")
                self._shutdown_event.set()
                self.strategy_running.clear()
                break

            # Market Hours Check
            try:
                clock = self.api.get_clock()
                if not clock.is_open:
                    next_open = (
                        clock.next_open.strftime("%Y-%m-%d %H:%M:%S UTC")
                        if clock.next_open
                        else "Unknown Time"
                    )
                    self._log_strategy_thought(
                        f"😴 Market is CLOSED. Next open: {next_open}. Sleeping for 5 mins..."
                    )
                    await asyncio.sleep(300)
                    continue
            except Exception as e:
                logging.warning("Failed to check market clock: %s", e)

            if self._shutdown_event.is_set():
                break

            local_active_strategy = None
            symbols_to_process = []

            with self.strategy_lock:
                if self.active_strategy:
                    local_active_strategy = self.active_strategy
                    symbols_to_process = list(self.active_strategy.symbols)

            if not local_active_strategy:
                await asyncio.sleep(5)
                continue

            rm = getattr(local_active_strategy, "risk_manager", None)
            if rm and rm.trading_halted:
                await asyncio.sleep(60)
                continue

            if not symbols_to_process:
                self._log_strategy_thought("Waiting for symbols from scanner...")
                await asyncio.sleep(10)
                continue

            # Latency measurement
            t_start = time.perf_counter()
            t_data_fetched = t_start
            t_strategy_done = t_start

            _ = getattr(local_active_strategy, "strategy_name", "unknown")
            _ = get_tracer("aaa-engine")
            try:
                current_time_utc = datetime.now(timezone.utc)
                if len(symbols_to_process) > 200:
                    symbols_to_process = symbols_to_process[:200]

                logging.info(
                    f"Engine: Processing {len(symbols_to_process)} symbols: {symbols_to_process[:5]}..."
                )

                try:
                    request_params = StockSnapshotRequest(
                        symbol_or_symbols=symbols_to_process, feed=ALPACA_DATA_FEED
                    )
                    logging.warning(
                        "FETCH_START: Requesting snapshots for %s...",
                        symbols_to_process[:5],
                    )

                    snapshots = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.data_api.get_stock_snapshot, request_params
                        ),
                        timeout=15.0,
                    )

                    t_data_fetched = time.perf_counter()
                    logging.warning(
                        f"FETCH_SUCCESS: Fetched {len(snapshots)} snapshots."
                    )
                except asyncio.TimeoutError:
                    logging.error(
                        "FETCH_TIMEOUT: 15s elapsed getting snapshots from Alpaca API!"
                    )
                    snapshots = {}
                except Exception as e:
                    logging.error("FETCH_ERROR: Failed to fetch snapshots: %s", e)
                    snapshots = {}

                if hasattr(local_active_strategy, "update_lstm_rankings"):
                    await local_active_strategy.update_lstm_rankings(
                        symbols_to_process,
                        snapshots,
                        self.current_market_data,
                        current_time_utc,
                    )

                # LSTMDynamic: process in rank order
                symbols_order = symbols_to_process
                if (
                    getattr(local_active_strategy, "strategy_name", None)
                    == "LSTMDynamic"
                ):
                    rank_cache = getattr(local_active_strategy, "_lstm_rank_cache", [])
                    if rank_cache:
                        rank_order = [s for s, _ in rank_cache]
                        symbols_order = [
                            s for s in rank_order if s in symbols_to_process
                        ]
                        symbols_order += [
                            s for s in symbols_to_process if s not in symbols_order
                        ]

                is_lstm = (
                    getattr(local_active_strategy, "strategy_name", None)
                    == "LSTMDynamic"
                )

                # Snapshots → SymbolEvalState-Dicts (nur Skalare, kein DataFrame)
                graph_states = []
                for symbol in symbols_order:
                    if self._shutdown_event.is_set():
                        break

                    if symbol not in snapshots:
                        if symbol not in self._skipped_symbols:
                            self._log_strategy_thought(
                                f"❌ {symbol}: Not in snapshots response"
                            )
                            self._skipped_symbols.add(symbol)
                        continue

                    snapshot_obj = snapshots[symbol]
                    if (
                        not hasattr(snapshot_obj, "latest_trade")
                        or snapshot_obj.latest_trade is None
                    ):
                        if symbol not in self._skipped_symbols:
                            self._log_strategy_thought(
                                f"❌ {symbol}: No latest_trade data"
                            )
                            self._skipped_symbols.add(symbol)
                        continue

                    ohlc, price = _extract_ohlc_from_snapshot(snapshot_obj)
                    logging.debug(
                        "%s: live_price=%.2f (open=%.2f high=%.2f low=%.2f vol=%.0f)",
                        symbol,
                        price,
                        ohlc["open"],
                        ohlc["high"],
                        ohlc["low"],
                        ohlc["volume"],
                    )

                    # --- Epic 3: Data Integrity Guard (Fail-Fast) ---
                    # Flat-Candle (O=H=L=C) mit Volumen kann nicht real sein.
                    # Verhindert, dass korrupte Alpaca-Daten in den Round Table fließen.
                    if ohlc["high"] == ohlc["low"] and ohlc["volume"] > 0 and price > 0:
                        logging.warning(
                            f"[{symbol}] Flat-Candle detektiert (O=H=L=C={price:.2f}) "
                            f"mit vol={ohlc['volume']:.0f}. Symbol wird für diesen Zyklus übersprungen."
                        )
                        if symbol not in self._skipped_symbols:
                            self._skipped_symbols.add(symbol)
                        continue

                    logging.info("✅ %s: Valid data - Price $%.2f", symbol, price)
                    graph_states.append(
                        {
                            "symbol": symbol,
                            "ohlc": ohlc,
                            "market_data_keys": [],
                            "current_time": current_time_utc.isoformat(),
                            "signal": None,
                            "error": None,
                        }
                    )

                    if symbol in self._skipped_symbols:
                        self._skipped_symbols.remove(symbol)

                logging.info(
                    "Engine: %d graph_states prepared (shutdown=%s)",
                    len(graph_states),
                    self._shutdown_event.is_set(),
                )
                if self._shutdown_event.is_set():
                    break

                if graph_states:

                    if is_lstm:
                        # LSTMDynamic: sequenziell — jeder Buy sieht aktuelle Buying Power
                        for state in graph_states:
                            if self._shutdown_event.is_set():
                                break
                            try:
                                res = await local_active_strategy.run_for_symbol(
                                    state["symbol"],
                                    state["ohlc"],
                                    self.current_market_data,
                                    current_time_utc,
                                )
                                if isinstance(res, SignalEvent):
                                    await self._process_signal_event(res)
                            except Exception as e:
                                logging.error(
                                    "Error in sequential strategy task for %s: %s",
                                    state["symbol"],
                                    e,
                                )
                    else:
                        # Non-LSTM: LangGraph-Dispatch parallel via asyncio.gather
                        # Layer 2: Per-Symbol timeout (MiFID II Art. 17)
                        _graph = (
                            build_symbol_eval_graph()
                            if build_symbol_eval_graph
                            else None
                        )
                        results = []  # default empty for CycleWatchdog
                        if _graph is not None:
                            logging.warning(
                                "🚀 TRADING LOOP: EXECUTION GRAPH FOR %d SYMBOLS STARTED!",
                                len(graph_states),
                            )
                            results = await asyncio.gather(
                                *[
                                    asyncio.wait_for(
                                        _graph.ainvoke(
                                            state,
                                            config={
                                                "configurable": {
                                                    "market_data": self.current_market_data
                                                }
                                            },
                                        ),
                                        timeout=_SYMBOL_EVAL_TIMEOUT_SEC,
                                    )
                                    for state in graph_states
                                ],
                                return_exceptions=True,
                            )
                        else:
                            # Fallback: direktes gather wenn LangGraph nicht verfügbar
                            results = await asyncio.gather(
                                *[
                                    asyncio.wait_for(
                                        local_active_strategy.run_for_symbol(
                                            s["symbol"],
                                            s["ohlc"],
                                            self.current_market_data,
                                            current_time_utc,
                                        ),
                                        timeout=_SYMBOL_EVAL_TIMEOUT_SEC,
                                    )
                                    for s in graph_states
                                ],
                                return_exceptions=True,
                            )

                        for i, res in enumerate(results):
                            if isinstance(res, asyncio.TimeoutError):
                                _sym = (
                                    graph_states[i]["symbol"]
                                    if i < len(graph_states)
                                    else "?"
                                )
                                logging.warning(
                                    "MIFID_AUDIT[%s] SYMBOL_TIMEOUT: Evaluation "
                                    "exceeded %.0fs — skipped",
                                    _sym,
                                    _SYMBOL_EVAL_TIMEOUT_SEC,
                                )
                            elif isinstance(res, Exception):
                                logging.error(
                                    "Error in graph dispatch idx %s: %s", i, res
                                )
                            elif isinstance(res, dict) and isinstance(
                                res.get("signal"), SignalEvent
                            ):
                                await self._process_signal_event(res["signal"])
                            elif isinstance(res, SignalEvent):
                                await self._process_signal_event(res)

                t_strategy_done = time.perf_counter()

                # Record Latency
                total_cycle_ms = (t_strategy_done - t_start) * 1000
                data_fetch_ms = (t_data_fetched - t_start) * 1000
                strategy_exec_ms = (t_strategy_done - t_data_fetched) * 1000

                self._cycle_latencies.append(total_cycle_ms)
                self._last_cycle_details = {
                    "total_ms": round(total_cycle_ms, 2),
                    "data_fetch_ms": round(data_fetch_ms, 2),
                    "strategy_exec_ms": round(strategy_exec_ms, 2),
                    "symbols_processed": len(symbols_to_process),
                    "timestamp": time.time(),
                }

                self.cloud_logger.log_latency_metric(
                    total_ms=total_cycle_ms,
                    data_fetch_ms=data_fetch_ms,
                    strategy_exec_ms=strategy_exec_ms,
                    symbol_count=len(symbols_to_process),
                )

                logging.info(
                    f"⏱️ Cycle Latency: {total_cycle_ms:.1f}ms (Data: {data_fetch_ms:.1f}ms, "
                    f"Exec: {strategy_exec_ms:.1f}ms) for {len(symbols_to_process)} symbols"
                )

                if total_cycle_ms > 2000:
                    logging.warning(
                        f"⚠️ HIGH LATENCY: Cycle took {total_cycle_ms:.1f}ms (>2000ms)"
                    )
                    send_slack_alert(
                        f"⚠️ *High Engine Latency*: {total_cycle_ms:.1f}ms "
                        f"(Data: {data_fetch_ms:.1f}ms, Exec: {strategy_exec_ms:.1f}ms)",
                        level="warning",
                    )

                # CycleWatchdog: Track whether the cycle completed any evaluations
                # HOLD/VETO = healthy (system working). Empty = timeout/crash.
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    if not is_lstm and graph_states:
                        _completed_evals = sum(
                            1 for r in results if isinstance(r, dict) and "signal" in r
                        )
                        if _completed_evals == 0:
                            self._cycle_watchdog.record_empty_cycle(len(graph_states))
                        else:
                            self._cycle_watchdog.record_successful_cycle()
                    elif is_lstm:
                        # LSTM sequential path always completes
                        self._cycle_watchdog.record_successful_cycle()

            except APIError as e:
                logging.error("Alpaca API Error live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(20)
            except BrokerConnectionError as e:
                logging.error("Broker connection lost in live loop: %s", e)
                send_slack_alert(f"❌ *Broker Connection Error*: {e}", level="error")
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(30)
            except Exception as e:
                logging.error("Unexpected error live loop: %s", e, exc_info=True)
                send_slack_alert(
                    f"❌ *Critical Error in Live Loop*: {e}", level="error"
                )
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(30)

            if self._shutdown_event.is_set():
                break

            # --- Cycle-Boundary: Graceful Handover prüfen ---
            registry = getattr(self, "agent_registry", None)
            if registry is not None and registry.has_pending_swap():
                await self._perform_graceful_handover()

            await asyncio.sleep(60)

        logging.info("Live trading loop exited.")

    async def _perform_graceful_handover(self) -> None:
        """
        Führt den ausstehenden Strategie-Swap am Cycle-Ende durch.

        Ablauf:
            1. Offene Positionen vom Broker holen
            2. Neue Strategy über on_positions_received() informieren
            3. commit_swap() in Registry ausführen (atomarer Wechsel)
            4. active_strategy-Shim synchronisieren (Backward-Compat)
            5. Slack-Benachrichtigung

        Fehler-Isolation: Exception → Logging, KEIN Swap-Commit (Kapitalschutz).
        """
        registry = getattr(self, "agent_registry", None)
        if registry is None or not registry.has_pending_swap():
            return

        old_strategy = registry.get_active()
        old_name = getattr(old_strategy, "strategy_name", "unknown")

        try:
            # 1. Offene Positionen vom Broker holen
            open_positions = []
            if getattr(self, "api", None) is not None:
                try:
                    open_positions = self.api.get_all_positions()
                except Exception as e:
                    logging.warning(
                        "Graceful Handover: get_all_positions failed: %s", e
                    )

            # 2. Pending Strategy ermitteln
            pending_name = registry._pending_name  # noqa: SLF001
            pending_strategy = registry._strategies.get(pending_name)  # noqa: SLF001

            # 3. Neue Strategy über Positionen informieren
            if pending_strategy is not None and open_positions:
                if hasattr(pending_strategy, "on_positions_received"):
                    pending_strategy.on_positions_received(open_positions)
                    logging.info(
                        "Graceful Handover: %d offene Positionen an '%s' übergeben.",
                        len(open_positions),
                        pending_name,
                    )

            # 4. Tatsächlicher Swap (atomar in Registry)
            registry.commit_swap()

            # 5. Backward-Compat: active_strategy-Shim synchronisieren
            if hasattr(self, "strategy_lock"):
                with self.strategy_lock:
                    self.active_strategy = registry.get_active()

            new_name = getattr(registry.get_active(), "strategy_name", pending_name)
            self._log_strategy_thought(
                f"🔄 Graceful Handover abgeschlossen: '{old_name}' → '{new_name}' "
                f"({len(open_positions)} offene Positionen übergeben)"
            )
            send_slack_alert(
                f"🔄 *Strategy Hot-Swap*: `{old_name}` → `{new_name}` "
                f"({len(open_positions)} Positionen übergeben)",
                level="info",
            )

        except Exception as e:
            # Kapitalschutz: Bei Fehler KEIN commit — System bleibt auf alter Strategy
            logging.error(
                "Graceful Handover FEHLGESCHLAGEN für '%s': %s. "
                "Aktive Strategy bleibt '%s'.",
                getattr(registry, "_pending_name", "?"),
                e,
                old_name,
                exc_info=True,
            )
            send_slack_alert(
                f"❌ *Graceful Handover FEHLER*: {e}. "
                f"Aktive Strategy bleibt `{old_name}`.",
                level="error",
            )
            # _pending_name bleibt gesetzt — nächster Cycle versucht erneut
