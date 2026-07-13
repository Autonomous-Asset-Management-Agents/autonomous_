# core/engine/simulation_runner.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: Simulation, Backtest, Benchmark, Learning-Integration

import asyncio
import csv
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks

import config
import core.strategies as strategies
from core.data_provider import survivorship_adjusted
from core.entitlement import resolve_entitlement
from core.performance_metrics import compute_performance_metrics, log_metrics
from core.redis_client import RedisClient
from core.risk_manager import RiskManager
from core.simulation import RealisticSimulationClient
from core.simulation_adapter import SimulationAdapter
from core.strategies import RLStrategy


def _clamp_backtest_start(start_date):
    """GTM-1 (#1800) Brick-5: clamp a backtest ``start_date`` to the tier's licensed
    look-back window — LOCAL desktop only.

    Non-LOCAL deployments (Cloud/Dev/CI) resolve to the full/unlimited bundle, so this is
    a byte-identical pass-through there. On LOCAL, if the tier caps ``backtest_months``,
    a start_date earlier than ``now - backtest_months`` is pulled forward to that boundary
    (months approximated as 30 days). Unparseable input is returned unchanged so a clamp
    never crashes a backtest. Iron Dome / risk / kill-switch are untouched.
    """
    if os.getenv("DEPLOYMENT_MODE", "").upper() != "LOCAL":
        return start_date

    months = resolve_entitlement().backtest_months
    if not months:
        return start_date

    try:
        parsed = datetime.fromisoformat(str(start_date)).date()
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(str(start_date), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            logging.warning(
                "[Entitlement] backtest start_date %r unparseable — not clamped.",
                start_date,
            )
            return start_date

    boundary = (datetime.now(timezone.utc) - timedelta(days=months * 30)).date()
    if parsed < boundary:
        logging.info(
            "[Entitlement] LOCAL tier caps backtest to %d months — clamping "
            "start_date %s → %s.",
            months,
            parsed.isoformat(),
            boundary.isoformat(),
        )
        return boundary.isoformat()
    return start_date


class SimulationRunnerMixin:
    """
    Mixin für BotEngine: Backtest, Benchmark, Learning-Simulation.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    # SIM-1 T1 (#1484): the last completed backtest result, served by GET /simulation-result
    # (the Console's reload-safe poll target). None until the first run completes.
    last_simulation_result: Optional[Dict[str, Any]] = None

    def start_simulation(
        self,
        background_tasks: BackgroundTasks,
        initial_capital: float = 10000.0,
        universe_type: str = "sp500",
    ) -> Dict[str, Any]:
        if self.simulation_running:
            return {"status": "error", "message": "Simulation already running"}

        self.simulation_running = True
        logging.info("Setting up simulation for universe: %s", universe_type)

        if universe_type == "sp500":
            symbols = self.data_provider.get_sp500_symbols()
            if not symbols:
                logging.warning("S&P 500 list empty. Defaulting to top 50 from cache.")
                symbols = self.data_provider.get_all_symbols()[:50]
        elif universe_type == "nasdaq":
            symbols = self.data_provider.get_nasdaq_symbols()
        else:
            symbols = self.data_provider.get_all_symbols()

        logging.info(f"Simulation Universe Size: {len(symbols)} symbols.")

        self.simulation = RealisticSimulationClient(
            symbols=symbols,
            initial_capital=initial_capital,
            data_provider=self.data_provider,
            api=self.api,
        )

        background_tasks.add_task(self._run_simulation_task)
        return {
            "status": "success",
            "message": f"Simulation started with {len(symbols)} symbols ({universe_type})",
        }

    def _run_simulation_task(self):
        try:
            if self.simulation:
                pass  # Placeholder bis SimulationClient vollständig integriert
        except Exception as e:
            logging.error("Simulation task error: %s", e)
        finally:
            self.simulation_running = False

    def run_simulation_in_thread(
        self, start_date, end_date, initial_capital, symbol_sample_mode, ui_source="sim"
    ):
        if self.is_simulation:
            return
        self.is_simulation = True
        self._shutdown_event.clear()
        threading.Thread(
            target=self._run_full_simulation_thread,
            args=(
                start_date,
                end_date,
                initial_capital,
                symbol_sample_mode,
                self._shutdown_event,
                ui_source,
                False,
            ),
            daemon=True,
        ).start()

    def run_benchmark_in_thread(
        self,
        start_date: str,
        end_date: str,
        initial_capital: float,
        symbol_sample_mode: str = "sp500",
    ):
        """Run a serious benchmark simulation (v3 RLAgent) and save equity curve."""
        if self.is_simulation:
            return
        self.is_simulation = True
        self._shutdown_event.clear()
        threading.Thread(
            target=self._run_full_simulation_thread,
            args=(
                start_date,
                end_date,
                initial_capital,
                symbol_sample_mode,
                self._shutdown_event,
                "sim",
                True,
            ),
            daemon=True,
        ).start()

    def run_learning_in_thread(self, start_date, end_date, initial_capital):
        if self.is_simulation:
            return
        self.is_simulation = True
        self._shutdown_event.clear()
        threading.Thread(
            target=self._run_simulation_and_learning_thread,
            args=(start_date, end_date, initial_capital, self._shutdown_event),
            daemon=True,
        ).start()

    def _run_simulation_and_learning_thread(self, start, end, cap, event):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._send_update_threadsafe(
                "ai_learning_update", {"message": "Starting simulation..."}
            )
            self._run_full_simulation_thread(
                start, end, cap, "full_market", event, ui_source="learn"
            )
            if not event.is_set():
                self._send_update_threadsafe(
                    "ai_learning_update", {"message": "Analyzing results..."}
                )
                loop.run_until_complete(
                    self.learning_engine.run_learning_analysis(
                        self.data_provider, self.news_processor, event
                    )
                )
        finally:
            self.is_simulation = False
            loop.close()

    def _run_full_simulation_thread(
        self,
        start_date,
        end_date,
        initial_capital,
        symbol_sample_mode,
        shutdown_event,
        ui_source="sim",
        save_benchmark_file=False,
    ):
        # GTM-1 (#1800) Brick-5: clamp start_date to the tier's licensed look-back on the
        # LOCAL desktop. Single convergence point for all three entry points
        # (run_simulation/benchmark/learning_in_thread). No-op on Cloud/Dev/CI.
        start_date = _clamp_backtest_start(start_date)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def emit_status(status_data):
            signal = (
                "ai_learning_update" if ui_source == "learn" else "simulation_status"
            )
            self._send_update_threadsafe(
                signal,
                (
                    status_data
                    if ui_source != "learn"
                    else {"message": status_data.get("message", str(status_data))}
                ),
            )

        try:
            self.market_scanner.set_simulation_mode(True)
            sim_client = RealisticSimulationClient(
                api=self.api, initial_cash=initial_capital
            )
            client_adapter = SimulationAdapter(sim_client)
            emit_status({"status": "loading_data", "message": "Loading data..."})

            def progress_cb(prog, date):
                if not shutdown_event.is_set():
                    emit_status(
                        {"status": "running", "progress": prog, "current_date": date}
                    )

            def strategy_cb(client, date):
                if shutdown_event.is_set():
                    raise InterruptedError()
                try:
                    if not getattr(self, "sim_risk_manager", None):
                        self.sim_risk_manager = RiskManager(
                            client_adapter, initial_capital
                        )
                    self.sim_risk_manager.update_account_equity(
                        client.get_account().equity
                    )

                    market_regime = self.regime_model.get_market_regime(
                        date, sim_client=client
                    )
                    market_data = {
                        "vix": market_regime.get("value"),
                        "regime": market_regime.get("regime"),
                    }
                    rec = loop.run_until_complete(
                        self.market_scanner.scan_market(
                            date, market_regime, sim_client=client
                        )
                    )

                    logging.info(
                        f"[SIM] Date: {date.strftime('%Y-%m-%d')}, Scanner returned: {type(rec)}, Value: {rec is not None}"
                    )

                    if rec:
                        target_stocks = [
                            s["symbol"] for s in rec.get("top_stocks", [])
                        ] or config.DEFAULT_SYMBOLS
                        logging.info(
                            f"[SIM] Trading {len(target_stocks)} stocks: {target_stocks[:5]}..."
                        )
                        SimStrategyClass = strategies.STRATEGY_CLASSES.get(
                            getattr(config, "ACTIVE_STRATEGY", "RLAgent"), RLStrategy
                        )
                        if (
                            not self.current_sim_strategy
                            or type(self.current_sim_strategy).__name__
                            != SimStrategyClass.__name__
                        ):
                            self.current_sim_strategy = SimStrategyClass(
                                client=client_adapter,
                                symbols=target_stocks,
                                running_event=None,
                                total_capital=client.get_account().equity,
                                risk_manager=self.sim_risk_manager,
                                data_provider=self.data_provider,
                            )
                        else:
                            self.current_sim_strategy.symbols = target_stocks

                        for pos in client_adapter.list_positions():
                            if pos.symbol not in target_stocks:
                                loop.run_until_complete(
                                    client_adapter.submit_order(
                                        pos.symbol, abs(pos.qty), "sell"
                                    )
                                )

                        snapshots = client_adapter.get_snapshots(target_stocks)
                        if hasattr(self.current_sim_strategy, "update_lstm_rankings"):
                            loop.run_until_complete(
                                self.current_sim_strategy.update_lstm_rankings(
                                    target_stocks, snapshots, market_data, date
                                )
                            )
                        tasks = []
                        for sym in target_stocks:
                            if sym in snapshots:
                                s = snapshots[sym]
                                ohlc = {
                                    "open": s.latest_trade.o,
                                    "high": s.latest_trade.h,
                                    "low": s.latest_trade.l,
                                    "close": s.latest_trade.c,
                                    "volume": s.latest_trade.v,
                                }
                                tasks.append(
                                    self.current_sim_strategy.run_for_symbol(
                                        sym, ohlc, market_data, date
                                    )
                                )
                        if tasks:
                            loop.run_until_complete(asyncio.gather(*tasks))

                        if getattr(config, "SIMULATION_FALLBACK_BUY", False):
                            positions = client.list_positions()
                            if (
                                not positions
                                and len(client.pending_orders) == 0
                                and target_stocks
                                and snapshots
                            ):
                                try:
                                    account = client.get_account()
                                    cash = float(account.cash or 0)
                                    if cash > 100:
                                        per_symbol_pct = 0.02
                                        for sym in target_stocks[:5]:
                                            if sym not in snapshots:
                                                continue
                                            lt = getattr(
                                                snapshots[sym], "latest_trade", None
                                            )
                                            price = (
                                                getattr(lt, "c", None)
                                                if lt is not None
                                                else None
                                            )
                                            if not price or price <= 0:
                                                continue
                                            qty = (cash * per_symbol_pct) / price
                                            if qty >= 0.001:
                                                client.submit_order(
                                                    sym, round(qty, 6), "buy"
                                                )
                                except Exception as e:
                                    logging.debug("[SIM] Fallback buy skipped: %s", e)
                    else:
                        logging.info(
                            f"[SIM] No scanner recommendations for {date.strftime('%Y-%m-%d')}"
                        )
                except Exception as e:
                    logging.error(f"[SIM] Strategy callback error: {e}", exc_info=True)

            results = sim_client.run_simulation(
                start_date, end_date, strategy_cb, symbol_sample_mode, progress_cb
            )
            if not shutdown_event.is_set():
                initial_cap = results.get("initial_cash", 100000)
                metrics = compute_performance_metrics(
                    results.get("daily_equity", []),
                    results.get("trades", []),
                    initial_cap,
                )
                log_metrics(metrics, "Simulation")
                emit_status(
                    {
                        "status": "complete",
                        "final_equity": results["final_equity"],
                        "total_return": results["total_return"],
                        "trades_count": len(results["trades"]),
                        "metrics": metrics,
                    }
                )
                spy_points, spy_first_close = self._compute_spy_equity_curve(
                    sim_client, results.get("initial_cash", 100000)
                )
                # SIM-1 T1 (#1484): cache the completed result so GET /simulation-result is
                # reload-safe (the Console polls it; the engine holds it in memory while up).
                self.last_simulation_result = {
                    "status": "complete",
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "initial_capital": results.get("initial_cash", 100000),
                    "strategy_equity": results.get("daily_equity", []),
                    "spy_equity": spy_points or [],
                    "final_equity": results.get("final_equity"),
                    "total_return": results.get("total_return"),
                    "trades_count": len(results.get("trades", [])),
                    # SIM-1 T2 (#1485): honest survivorship signal — True only for the S&P 500
                    # universe with the point-in-time membership CSV applied; otherwise the Console
                    # flags the limitation rather than silently using the current index.
                    "survivorship_adjusted": survivorship_adjusted(
                        symbol_sample_mode,
                        self.data_provider.has_point_in_time_membership(),
                    ),
                    "metrics": metrics,
                }
                initial_cap = results.get("initial_cash", 100000)
                strategy_return = results.get("total_return", 0.0)
                if spy_points and len(spy_points) > 0:
                    spy_final = spy_points[-1].get("equity", initial_cap)
                    spy_return = (
                        (spy_final - initial_cap) / initial_cap * 100.0
                        if initial_cap
                        else 0.0
                    )
                    outperformance = strategy_return - spy_return
                    logging.info(
                        f"Benchmark comparison: Strategy {strategy_return:.2f}%, S&P500 {spy_return:.2f}%, Outperformance {outperformance:+.2f}%"
                    )
                    # SIM honest-metrics: surface the benchmark return + alpha (already computed —
                    # was only logged) so the Console shows outperformance, not just raw return.
                    self.last_simulation_result["spy_return"] = round(spy_return, 2)
                    self.last_simulation_result["outperformance"] = round(
                        outperformance, 2
                    )
                if save_benchmark_file:
                    self._save_benchmark_equity(
                        results,
                        start_date,
                        end_date,
                        spy_points=spy_points,
                        spy_first_close=spy_first_close,
                        metrics=metrics,
                    )
                if spy_points and results.get("daily_equity"):
                    self._save_benchmark_comparison_csv(
                        results["daily_equity"], spy_points, initial_cap
                    )
        except Exception as e:
            emit_status({"status": "error", "message": str(e)})
        finally:
            self.is_simulation = False
            self.market_scanner.set_simulation_mode(False)
            loop.close()

    def _compute_spy_equity_curve(self, sim_client, initial_capital: float):
        """Build S&P500 buy-and-hold equity curve for the same dates as the simulation."""
        out = []
        first_close = None
        try:
            spy_df = getattr(sim_client, "simulation_data", {}).get("SPY")
            date_range = getattr(sim_client, "date_range", None)
            if (
                spy_df is None
                or spy_df.empty
                or not date_range
                or "close" not in spy_df.columns
            ):
                return out, first_close
            first_date = date_range[0]
            if first_date not in spy_df.index:
                return out, first_close
            first_close = float(spy_df.loc[first_date]["close"])
            if first_close <= 0:
                return out, None
            for d in date_range:
                if d not in spy_df.index:
                    continue
                close = float(spy_df.loc[d]["close"])
                equity = initial_capital * (close / first_close)
                out.append({"date": d.strftime("%Y-%m-%d"), "equity": round(equity, 2)})
        except Exception as e:
            logging.debug("SPY equity curve skipped: %s", e)
        return out, first_close

    def _save_benchmark_equity(
        self,
        results: Dict,
        start_date: str,
        end_date: str,
        spy_points: Optional[List[Dict]] = None,
        spy_first_close: Optional[float] = None,
        metrics: Optional[Dict] = None,
    ):
        """Persist daily equity from a simulation and optional S&P500 curve for the Web UI."""
        try:
            out = {
                "start_date": start_date,
                "end_date": end_date,
                "strategy": getattr(config, "ACTIVE_STRATEGY", "RLAgent"),
                "initial_capital": results.get("initial_cash", 0),
                "final_equity": results.get("final_equity", 0),
                "points": results.get("daily_equity", []),
            }
            if metrics:
                out["metrics"] = metrics
            if spy_points:
                out["spy_points"] = spy_points
            if spy_first_close is not None:
                out["spy_first_close"] = spy_first_close

            r = RedisClient.get_sync_redis()
            r.set("benchmark_equity_data", json.dumps(out))
            logging.info(
                f"Benchmark equity saved to Redis ({len(out['points'])} strategy, "
                f"{len(out.get('spy_points', []))} S&P500 points)."
            )
        except Exception as e:
            logging.error("Failed to save benchmark equity: %s", e)

    def _save_benchmark_comparison_csv(
        self,
        portfolio_points: List[Dict],
        spy_points: List[Dict],
        initial_capital: float,
    ):
        """Write Date, AI_Bot_Equity, SPY_Equity CSV for benchmark comparison."""
        path = getattr(config, "BENCHMARK_COMPARISON_CSV", None)
        if not path or not portfolio_points or not spy_points:
            return
        try:
            spy_by_date = {p["date"]: p["equity"] for p in spy_points}
            rows = [["Date", "AI_Bot_Equity", "SPY_Equity"]]
            last_spy = initial_capital
            for pt in portfolio_points:
                d = pt.get("date", "")
                eq = pt.get("equity", 0)
                sp = spy_by_date.get(d, last_spy)
                last_spy = sp
                rows.append([d, f"{eq:.2f}", f"{sp:.2f}"])
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            logging.info("Benchmark comparison CSV saved to %s.", path)
        except Exception as e:
            logging.debug("Failed to save benchmark comparison CSV: %s", e)
