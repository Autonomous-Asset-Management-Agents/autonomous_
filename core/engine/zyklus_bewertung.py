# core/engine/zyklus_bewertung.py
# #4252 (H-2k, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Bewertung und Ausführung der Handelsschleife (Symbolvorbereitung,
# Round-Table-Fan-out, Signal-Übergabe, Latenz-Auswertung, Zyklusgrenze).
"""Bewertung und Ausführung: was jeder offene Zyklus nach dem Kontext je Symbol tut.

#4252 (H-2k) zieht sieben Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): die Symbolvorbereitung samt
HITL-Überspringen, den SymbolEvalState je Symbol (Kursalter #3381, Flat-Candle-Wächter,
Kanäle), die Round-Table-Bewertung mit dem Fan-out unter Zeitlimit (Span ``trading.cycle``),
die Übergabe der Signale an ``_process_signal_event``, die Latenz-Auswertung und die
Zyklusgrenze (Graceful Handover, Sim-Treiber, Takt-Schlaf). Es ist der letzte Umzug von H-2.

Die Rümpfe sind wortgleich mit dem Stand vor dem Umbau. Abweichung: Die Patch-Ziele der
Tests (``get_config``, ``build_symbol_eval_graph``, ``_symbol_eval_timeout``,
``_extract_ohlc_from_snapshot``, ``engine_now``, ``CompositionRoot``) liest das Modul zur
Laufzeit als ``_tl.<name>`` am Kernmodul, damit Patches auf ``core.engine.trading_loop``
weiter wirken (#4184 §3). Ebenso die OTel-Objekte, die im Kern bleiben (``_obs_tracer``,
``_otel_trace``, ``_run_symbol_eval``; §6): Tracer-Name ``trading.loop``, Span-Namen und
Attribute ändern sich so nicht. ``produce_news_headlines`` bleibt ``try``-Import im Kern (§1).
``time`` und ``asyncio`` sind frei importiert — die Latenz-Tests patchen ``time.perf_counter``
global. Die Log-Zeilen bleiben am Root-Logger. Der ``_tl``-Import steht am Dateiende, damit der
Kreis Kern <-> Bewertung in beiden Ladereihenfolgen trägt. ``TradingLoopMixin`` erbt
``ZyklusBewertungMixin``; der Dirigent ``live_trading_loop`` ruft die Schritte über die MRO.

Plan: ``docs/4252-*/implementation_plan.md``.
"""

import asyncio
import logging
import time
from datetime import timezone

from core.cloud_logger import DecisionContext
from core.engine.loop_counters import _bump_loop_counter
from core.engine.symbol_schluessel import (
    _implied_vol_state_keys,
    _position_context_state_keys,
    _quality_state_keys,
    _regime_conditioner_state_keys,
    _risk_reversal_state_keys,
)
from core.engine.time_budget import current_time_budget, is_stale_quote
from core.engine.zyklus import Zyklus, ZyklusZustand
from core.events import SignalEvent


class ZyklusBewertungMixin:
    """#4252 (H-2k): Bewertung und Ausführung der Handelsschleife; Basis von ``TradingLoopMixin``."""

    async def _symbole_vorbereiten(self, z: ZyklusZustand) -> Zyklus:
        """#4009: Snapshots → ``z.graph_states``; ``STOPP``, wenn danach Shutdown gesetzt ist."""
        # Snapshots → SymbolEvalState-Dicts (nur Skalare, kein DataFrame)
        z.graph_states = []
        for symbol in z.symbols_order:
            if self._shutdown_event.is_set():
                break

            # C4: skip a symbol already awaiting human approval — avoids a wasted
            # analysis for a signal the queue would dedup. Dormant unless HITL_ENABLED.
            if await self._hitl_symbol_pending(symbol):
                continue

            _symbol_state = await self._symbol_zustand(z, symbol)
            if _symbol_state is None:
                continue
            z.graph_states.append(_symbol_state)

            if symbol in self._skipped_symbols:
                self._skipped_symbols.remove(symbol)

        logging.info(
            "Engine: %d graph_states prepared (shutdown=%s)",
            len(z.graph_states),
            self._shutdown_event.is_set(),
        )
        if self._shutdown_event.is_set():
            return Zyklus.STOPP
        return Zyklus.WEITER

    async def _symbol_zustand(self, z: ZyklusZustand, symbol: str) -> dict | None:
        """#4009: SymbolEvalState-Dict eines Symbols; ``None`` = in diesem Zyklus übersprungen."""
        if symbol not in z.snapshots:
            if symbol not in self._skipped_symbols:
                self._log_strategy_thought(f"❌ {symbol}: Not in snapshots response")
                self._skipped_symbols.add(symbol)
            return None

        snapshot_obj = z.snapshots[symbol]
        if (
            not hasattr(snapshot_obj, "latest_trade")
            or snapshot_obj.latest_trade is None
        ):
            if symbol not in self._skipped_symbols:
                self._log_strategy_thought(f"❌ {symbol}: No latest_trade data")
                self._skipped_symbols.add(symbol)
            return None

        ohlc, price, quote_ts = _tl._extract_ohlc_from_snapshot(snapshot_obj)
        logging.debug(
            "%s: live_price=%.2f (open=%.2f high=%.2f low=%.2f vol=%.0f)",
            symbol,
            price,
            ohlc["open"],
            ohlc["high"],
            ohlc["low"],
            ohlc["volume"],
        )

        # --- #3381: Enthaltung statt Entscheidung auf altem Preis ---
        # Derselbe Preis speist Round Table, Sizing UND Compliance. Ist er alt,
        # ist die ganze Kette falsch — still, weil bis hier kein Alter mitlief.
        # Die Enthaltung trifft nur dieses Symbol; der Zyklus laeuft weiter.
        # engine_now() OHNE tz liefert naiv-LOKALE Zeit (core/sim/clock.py:264,
        # `datetime.now(None)`). Gegen einen UTC-Zeitstempel verglichen ergaebe
        # das den Zonenversatz als Alter — in Berlin im September glatte 7200 s,
        # also Dauer-Enthaltung. Deshalb hier ausdruecklich UTC anfordern; unter
        # SIM_MODE rechnet dieselbe Zeile die ET-behaftete Sim-Zeit um.
        _now_utc = _tl.engine_now(timezone.utc)
        if is_stale_quote(quote_ts, _now_utc, _tl.get_config().MAX_QUOTE_AGE_SECONDS):
            if symbol not in self._skipped_symbols:
                _age = (
                    "unbekannt"
                    if quote_ts is None
                    else f"{(_now_utc - (quote_ts if quote_ts.tzinfo else quote_ts.replace(tzinfo=timezone.utc))).total_seconds():.0f}s"
                )
                logging.warning(
                    "[%s] stale_quote: Kursalter %s > %.0fs — Enthaltung "
                    "fuer diesen Zyklus, keine Order.",
                    symbol,
                    _age,
                    _tl.get_config().MAX_QUOTE_AGE_SECONDS,
                )
                self._log_strategy_thought(
                    f"⏳ {symbol}: Kursbild zu alt ({_age}) — Enthaltung (stale_quote)"
                )
                self._skipped_symbols.add(symbol)
            return None

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
            return None

        logging.info("✅ %s: Valid data - Price $%.2f", symbol, price)
        _symbol_state = {
            "symbol": symbol,
            "ohlc": ohlc,
            "market_data_keys": [],
            "current_time": z.current_time_utc.isoformat(),
            "signal": None,
            "error": None,
            # #1949: "vix"/"regime" are declared LangGraph channels;
            # values are flag-gated (OFF → None = old dropped-key
            # behaviour, byte-identical).
            **_regime_conditioner_state_keys(
                getattr(self, "current_market_data", None)
            ),
            # GAP9: same per-cycle snapshot for every symbol (None when the
            # feature is off → runner falls back to {} = today's behaviour).
            "_portfolio_context": z.portfolio_context,
            # #2672: flag-gated position context from the shared per-cycle
            # broker snapshot (OFF → all five channels None = byte-identical;
            # unconfirmed book → None fields + confirmed=False, fail-closed).
            **_position_context_state_keys(
                z.positions_by_symbol, z.position_confirmed, symbol
            ),
            # #3038: flag-gated implied-volatility channels (OFF →
            # all three None, no network I/O = byte-identical).
            **_implied_vol_state_keys(symbol, price),
            **_risk_reversal_state_keys(symbol, price),
            # #3275: flag-gated composite-quality channels (OFF → all
            # three None, no work = byte-identical). Feed-derived, so it
            # also works under SIM (PIT read AS OF current_time_utc).
            **_quality_state_keys(symbol, price, z.current_time_utc),
        }
        # RTR-1 (#1948, dormant default OFF): point-in-time news channel.
        # The producer is flag-FIRST (returns None without network I/O
        # while NEWS_SENTIMENT_NLP_ENABLED is off) → the key is only
        # added when the feature is armed; the dormant state dict stays
        # byte-identical to today. [] (flag on, fetch failed/empty) is
        # passed through so NewsSentimentAgent abstains transparently.
        if _tl.produce_news_headlines is not None:
            try:
                _news_headlines = await _tl.produce_news_headlines(
                    symbol, z.current_time_utc
                )
            except Exception:  # noqa: BLE001 — news must never cost a cycle
                logging.warning(
                    "News headlines producer failed for %s — no news "
                    "this cycle (agent will abstain).",
                    symbol,
                    exc_info=True,
                )
                # None (not []): a producer crash must not add the state
                # key — keeps the dormant path byte-identical even then.
                # When armed, the agent abstains on the missing channel
                # with its own WARNING (fail-transparent either way).
                _news_headlines = None
            if _news_headlines is not None:
                _symbol_state["news_headlines"] = _news_headlines
        return _symbol_state

    async def _round_table_bewerten(self, z: ZyklusZustand) -> None:
        """#4009: Round-Table-Dispatch je Symbol mit Zeitlimit; füllt ``z.results``."""
        if not z.graph_states:
            return
        # Epic 3375: is_lstm Fallback-Pfad abgerissen (LSTMDynamic ist nur noch Ranker/Stimme).
        # Non-LSTM: LangGraph-Dispatch parallel via asyncio.gather
        # Layer 2: Per-Symbol timeout (MiFID II Art. 17)
        _graph = _tl.build_symbol_eval_graph() if _tl.build_symbol_eval_graph else None
        z.results = []  # default empty for CycleWatchdog
        if _graph is not None:
            await self._round_table_ausrollen(z, _graph)
        else:
            # Epic 3375 (#3399): Wenn der Graph nicht geladen werden kann,
            # System enthält sich. Kein Fallback auf run_for_symbol, um Doppel-Orders zu vermeiden.
            logging.warning(
                "MIFID_AUDIT: Round Table Graph konnte nicht geladen werden. System enthält sich."
            )
            z.results = [
                {
                    "symbol": s["symbol"],
                    "signal": SignalEvent(
                        symbol=s["symbol"],
                        action="HOLD",
                        decision_context=DecisionContext(
                            reasoning_summary="abstain:council_unavailable",
                            action="HOLD",
                        ),
                    ),
                }
                for s in z.graph_states
            ]

    async def _round_table_ausrollen(self, z: ZyklusZustand, _graph) -> None:
        """#4009: Fan-out über den geladenen Graphen, je Symbol mit Zeitlimit."""
        # OBS-4 (#2637): one trading.cycle root per fan-out; every
        # symbol evaluates inside a trading.symbol_eval child so the
        # round-table model.inference/risk spans join one trade trace.
        _cycle_span = None
        _cycle_ctx = None
        if _tl._otel_trace is not None:
            try:
                _cycle_span = _tl._obs_tracer.start_span("trading.cycle")
                _cycle_span.set_attribute("cycle.symbol_count", len(z.graph_states))
                _cycle_ctx = _tl._otel_trace.set_span_in_context(_cycle_span)
            except Exception:
                logging.warning(
                    "Telemetry span error: failed to start cycle span",
                    exc_info=True,
                )
                _cycle_span = _cycle_ctx = None

        logging.warning(
            "🚀 TRADING LOOP: EXECUTION GRAPH FOR %d SYMBOLS STARTED!",
            len(z.graph_states),
        )
        if _tl.get_config().FULL_UNIVERSE_TRADING_ENABLED:
            # Phase B (ADR-FU01): with the full universe (~500) the
            # gather below would open ~500 concurrent graph
            # invocations x 9 round-table agents (~4,500 in-flight
            # LLM calls) against ONE local Ollama and exhaust its
            # connection pool — an outage, not a cost. Today's
            # 200-truncation is the only thing bounding it, and Phase B
            # removes that, so the bound must be replaced, not dropped.
            # Mirrors the scanner's proven per-loop semaphore.
            # The per-symbol timeout starts AFTER a slot is acquired,
            # so a queued symbol is never timed out for merely waiting.
            _sem = asyncio.Semaphore(
                max(
                    1,
                    int(_tl.get_config().FULL_UNIVERSE_EVAL_CONCURRENCY),
                )
            )

            # _sem and _graph are bound as defaults so the closure
            # captures THIS cycle's objects, not whatever the names
            # point at when the coroutine finally runs (flake8 B023).
            # Benign today — the gather completes inside this
            # iteration — but a late-binding closure over a loop
            # variable is a trap that only shows up once someone
            # moves the await, and then every symbol would evaluate
            # against the last cycle's graph.
            # #3381 Layer 3: Zyklusgrenze. Bewusst KEIN harter Abbruch
            # des laufenden Zyklus — der hinterliesse angefangene
            # Auswertungen und halb gefuellten Zustand. Stattdessen wird
            # nach Ablauf des Budgets kein WEITERES Symbol mehr gestartet:
            # was laeuft, laeuft unter seiner Symbol-Grenze zu Ende, die
            # Warteschlange wird abgeraeumt. Die Grenze wirkt damit dort,
            # wo ein Zyklus tatsaechlich entgleist — in der Schlange hinter
            # dem Semaphor. Der Zweig ohne Semaphor (unten) hat keine
            # Schlange und damit nichts abzuraeumen.
            _cycle_deadline = time.monotonic() + current_time_budget()[2]

            async def _bounded_eval(
                state,
                _sem=_sem,
                _graph=_graph,
                _cycle_ctx=_cycle_ctx,
                _deadline=_cycle_deadline,
            ):
                async with _sem:
                    if time.monotonic() > _deadline:
                        logging.warning(
                            "MIFID_AUDIT[%s] CYCLE_TIMEOUT: Zyklusbudget "
                            "%.0fs erschoepft — Symbol nicht mehr gestartet",
                            state.get("symbol", "?"),
                            current_time_budget()[2],
                        )
                        return None
                    return await _tl._run_symbol_eval(
                        _tl._obs_tracer,
                        _cycle_ctx,
                        state.get("symbol", "?"),
                        lambda st=state, _g=_graph: asyncio.wait_for(
                            _g.ainvoke(
                                st,
                                config={
                                    "configurable": {
                                        "market_data": self.current_market_data
                                    }
                                },
                            ),
                            timeout=_tl._symbol_eval_timeout(),
                        ),
                    )

            z.results = await asyncio.gather(
                *[_bounded_eval(state) for state in z.graph_states],
                return_exceptions=True,
            )
        else:
            z.results = await asyncio.gather(
                *[
                    _tl._run_symbol_eval(
                        _tl._obs_tracer,
                        _cycle_ctx,
                        state.get("symbol", "?"),
                        lambda st=state, _g=_graph: asyncio.wait_for(
                            _g.ainvoke(
                                st,
                                config={
                                    "configurable": {
                                        "market_data": self.current_market_data
                                    }
                                },
                            ),
                            timeout=_tl._symbol_eval_timeout(),
                        ),
                    )
                    for state in z.graph_states
                ],
                return_exceptions=True,
            )
        if _cycle_span is not None:
            try:
                _cycle_span.end()
            except Exception:
                logging.warning(
                    "Telemetry span error: failed to end cycle span",
                    exc_info=True,
                )

    async def _signale_ausfuehren(self, z: ZyklusZustand) -> None:
        """#4009: jedes Signal in unveränderter Reihenfolge an ``_process_signal_event``."""
        for i, res in enumerate(z.results):
            if isinstance(res, asyncio.TimeoutError):
                _sym = z.graph_states[i]["symbol"] if i < len(z.graph_states) else "?"
                logging.warning(
                    "MIFID_AUDIT[%s] SYMBOL_TIMEOUT: Evaluation "
                    "exceeded %.0fs — skipped",
                    _sym,
                    _tl._symbol_eval_timeout(),
                )
            elif isinstance(res, Exception):
                logging.error("Error in graph dispatch idx %s: %s", i, res)
            elif isinstance(res, dict) and isinstance(res.get("signal"), SignalEvent):
                await self._process_signal_event(res["signal"])
                if res.get("round_table_scores"):
                    self._last_round_table_state = res["round_table_scores"]
            elif isinstance(res, SignalEvent):
                await self._process_signal_event(res)

            # Cache the Round Table state even if no signal was generated (e.g. HOLD/VETO)
            if isinstance(res, dict) and res.get("round_table_scores"):
                self._last_round_table_state = res["round_table_scores"]
                # #2630: retarget specialist reports to positions ∪ top-N consensus
                await self._reconcile_specialist_coverage()

            # #3180 producer seam: publish THIS symbol's live blend-consensus
            # (∈[0,1], the same score BUY/SELL thresholds compare against) to the
            # PM store so the OPINION exit gate can retain high-consensus names.
            # Fail-safe: guarded, never raises into the loop; store OFF-by-default
            # (the gate only reads this when CONSENSUS_RETENTION_THRESHOLD>0).
            if isinstance(res, dict) and res.get("consensus_ranking") is not None:
                _cons_sym = (
                    z.graph_states[i]["symbol"] if i < len(z.graph_states) else None
                )
                _cons_pm = getattr(
                    getattr(self, "active_strategy", None),
                    "portfolio_manager",
                    None,
                )
                if (
                    _cons_sym
                    and _cons_pm is not None
                    and hasattr(_cons_pm, "set_live_consensus")
                ):
                    _cons_pm.set_live_consensus(_cons_sym, res["consensus_ranking"])

    async def _zyklus_auswerten(self, z: ZyklusZustand) -> None:
        """#4008: Latenz erfassen, CycleWatchdog, Zyklus-Zähler."""
        z.t_strategy_done = time.perf_counter()

        # Record Latency
        total_cycle_ms = (z.t_strategy_done - z.t_start) * 1000
        data_fetch_ms = (z.t_data_fetched - z.t_start) * 1000
        strategy_exec_ms = (z.t_strategy_done - z.t_data_fetched) * 1000

        self._cycle_latencies.append(total_cycle_ms)
        self._last_cycle_details = {
            "total_ms": round(total_cycle_ms, 2),
            "data_fetch_ms": round(data_fetch_ms, 2),
            "strategy_exec_ms": round(strategy_exec_ms, 2),
            "symbols_processed": len(z.symbols_to_process),
            "timestamp": _tl.CompositionRoot.get_instance().clock_port.time(),
        }

        self.cloud_logger.log_latency_metric(
            total_ms=total_cycle_ms,
            data_fetch_ms=data_fetch_ms,
            strategy_exec_ms=strategy_exec_ms,
            symbol_count=len(z.symbols_to_process),
        )

        logging.info(
            f"⏱️ Cycle Latency: {total_cycle_ms:.1f}ms (Data: {data_fetch_ms:.1f}ms, "
            f"Exec: {strategy_exec_ms:.1f}ms) for {len(z.symbols_to_process)} symbols"
        )

        if total_cycle_ms > 2000:
            logging.warning(
                f"⚠️ HIGH LATENCY: Cycle took {total_cycle_ms:.1f}ms (>2000ms)"
            )
            # PR B (fail-safe): pure-observation high-latency counter.
            _bump_loop_counter(self, "_high_latency_cycles")

        # CycleWatchdog: Track whether the cycle completed any evaluations
        # HOLD/VETO = healthy (system working). Empty = timeout/crash.
        if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
            if z.graph_states:
                _completed_evals = sum(
                    1 for r in z.results if isinstance(r, dict) and "signal" in r
                )
                if _completed_evals == 0:
                    self._cycle_watchdog.record_empty_cycle(len(z.graph_states))
                else:
                    self._cycle_watchdog.record_successful_cycle()

        # PR B (fail-safe): monotone trading-cycle liveness counter, bumped
        # once per completed cycle. Pure observation — never gates flow.
        _bump_loop_counter(self, "_cycles_completed")

    async def _zyklusgrenze(self, z: ZyklusZustand) -> None:
        """#4008: Graceful Handover, Sim-Treiber (Mark-to-Market) bzw. Takt-Schlaf."""
        # --- Cycle-Boundary: Graceful Handover prüfen ---
        registry = getattr(self, "agent_registry", None)
        if registry is not None and registry.has_pending_swap():
            await self._perform_graceful_handover()

        # #2548 sim driver: under SIM_MODE advance the virtual clock one cadence step and
        # terminate at the window's end (flat-out — no real sleep). Byte-identical when off.
        if getattr(_tl.get_config(), "SIM_MODE", False):
            from core.sim.clock import get_sim_clock

            sim_clock = get_sim_clock()
            # #2693: advance() returns True when it rolled into a NEW trading day. The
            # per-day bookkeeping (close the equity point, re-seed the rank panel for the new
            # day) hangs off the clock's ``on_new_day`` hook, which the sim runner registers —
            # so this loop stays free of sim bookkeeping and the sim keeps its own concerns.
            # A multi-day run keeps ``is_open`` True across the rollover; it only goes False
            # after the LAST day, which is what still terminates the run below.
            rolled = sim_clock.advance()
            if rolled:
                logging.info(
                    "Sim: rolled into %s (day %d/%d).",
                    sim_clock.current_time.date(),
                    sim_clock.days_completed,
                    sim_clock.n_days,
                )
            # #2632: re-price the book at the NEW sim time, before the next cycle reads it.
            # Held positions otherwise keep their entry price for the whole run, so equity
            # rewards selling and the exit logic sees unrealized_plpc == 0 forever. Marking
            # once at the end would fix the reported P&L but leave every intra-run exit
            # decision blind, which is the half that matters for an exit A/B.
            _mark = getattr(getattr(self, "api", None), "mark_to_market", None)
            if _mark is not None:
                try:
                    _mark()
                except Exception:  # noqa: BLE001 — a mark must never cost a cycle
                    logging.warning(
                        "Sim: mark-to-market failed this cycle — positions keep their "
                        "previous prices.",
                        exc_info=True,
                    )
            # Count completed sim cycles (surfaced as SimResult.n_cycles).
            self._sim_cycles = getattr(self, "_sim_cycles", 0) + 1
            if not sim_clock.is_open:
                self.strategy_running.clear()
                # #2630 sim decision-grade: signal TRUE window completion on a DEDICATED
                # event. The runner must not wait on strategy_running — the monitor's initial
                # strategy switch (None→RLAgent) transiently clears it, which the runner would
                # otherwise mistake for "sim done" (→ the 0-cycle premature exit).
                ev = getattr(self, "_sim_complete", None)
                if ev is not None:
                    ev.set()
            await _tl.asyncio.sleep(0)
        else:
            # INC-6: modest cadence when the market is closed (research still ran this
            # cycle, but there is nothing time-critical to react to) — 300s vs the
            # normal 60s live cadence.
            await _tl.asyncio.sleep(300 if z.cycle_market_closed else 60)


# #4252 (H-2k): Kern-Namen (get_config, build_symbol_eval_graph, _symbol_eval_timeout,
# _extract_ohlc_from_snapshot, engine_now, CompositionRoot, _obs_tracer, _otel_trace,
# _run_symbol_eval, produce_news_headlines) liest das Modul ueber _tl, damit die Patch-Ziele
# der Tests wirken (#4184 §3, §6). Am Dateiende: der Kreis Kern <-> Bewertung traegt so in
# beiden Ladereihenfolgen (Muster core/engine/zyklus_kontext.py).
from core.engine import trading_loop as _tl  # noqa: E402
