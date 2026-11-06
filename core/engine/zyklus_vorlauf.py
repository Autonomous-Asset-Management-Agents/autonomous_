# core/engine/zyklus_vorlauf.py
# #4250 (H-2i, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Zyklus-Vorlauf der Handelsschleife (Schreibberechtigung, NY-Kalendertag,
# Kill Switch, Marktuhr, Bericht bei geschlossenem Markt, Schutz vor dem Konsens).
"""Zyklus-Vorlauf: die vier Schritte, die jeder Durchlauf vor dem Konsens nimmt.

#4250 (H-2i) zieht vier Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): die Vorbereitung des Zyklus
(Schreibberechtigung, NY-Kalendertag, Bar-Cache, Kill Switch), die Pruefung der Marktzeit
(Marktuhr, HITL-Drain, aktive Strategie), den Report-only-Modus bei geschlossenem Markt und den
Schutz vor dem Konsens (Drawdown, Broker- und Positions-Stops, Ausstiegsrunde,
Wiedereinstiegs-Sperre; #3380, #3604).

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Abweichung: Die Patch-Ziele der
Tests (``kill_switch``, ``BYPASS_MARKET_HOURS``, ``engine_now``, ``CompositionRoot``,
``get_config``) liest das Modul zur Laufzeit als ``_tl.<name>`` am Kernmodul, damit Patches auf
``core.engine.trading_loop`` weiter wirken (#4184 §3). Ebenso ``asyncio``: Die Tests tauschen
``asyncio.sleep`` mit ``schlaf_nur_im_modul("core.engine.trading_loop")`` nur in der Referenz
des Kerns (``tests/helpers/schlaf.py``); ein eigenes ``import asyncio`` hier schliefe echt (etwa
60 s beim Halt, #3380). Der ``_tl``-Import steht am Dateiende,
damit der Kreis Kern <-> Vorlauf in beiden Ladereihenfolgen traegt. ``TradingLoopMixin`` erbt
``ZyklusVorlaufMixin``; der Dirigent ``live_trading_loop`` ruft die Schritte ueber die MRO.

Plan: ``docs/4250-*/implementation_plan.md``.
"""

import logging

import pytz

from core.engine.zyklus import Zyklus, ZyklusZustand


class ZyklusVorlaufMixin:
    """#4250 (H-2i): Zyklus-Vorlauf der Handelsschleife; Basis von ``TradingLoopMixin``."""

    async def _zyklus_vorbereiten(self, z: ZyklusZustand) -> Zyklus:
        """#4007: Schreibberechtigung, NY-Kalendertag, Bar-Cache, Kill Switch (``STOPP``)."""
        # #3453: Fehlt die Berechtigung (z. B. nach einem Absturz hielt die alte Instanz
        # sie noch bis zu 60 s), wird sie bei jedem Zyklusbeginn erneut versucht.
        await self._sichere_schreibberechtigung()
        # Calendar day check in NY Timezone (FIND-01)
        ny_date_rolled = False
        try:
            ny_tz = pytz.timezone("America/New_York")
            current_ny_date = _tl.engine_now(ny_tz).date()
            if getattr(self, "_last_trading_day", None) != current_ny_date:
                ny_date_rolled = True
                previous_trading_day = getattr(self, "_last_trading_day", None)
                self._last_trading_day = current_ny_date
                if (
                    hasattr(self, "compliance_guardian")
                    and self.compliance_guardian is not None
                ):
                    self.compliance_guardian.reset_daily_limit()
                    logging.info("Engine: Daily limit reset for new NY calendar day.")
                # FIND-01 / N3: clear YESTERDAY's HITL day-notional key on the NY-date
                # change (dormant unless HITL_ENABLED; no-op on first boot).
                await self._hitl_day_rollover(previous_trading_day)
        except Exception:
            logging.exception("Error checking NY timezone calendar day")

        # LSTM-panel-starvation fix (Part 2): warm the daily-bar cache ONCE per
        # trading day, off the hot path. ``_last_trading_day`` is unset at boot, so
        # the FIRST iteration rolls -> this is also the engine-start warm-up; every
        # later NY-date change re-warms for the new day. Flag-gated + fail-safe
        # inside the helper (a warm-up failure never breaks the loop). Resolve the
        # active strategy's universe the same way the cycle does (:787-790).
        if ny_date_rolled:
            _lock = getattr(self, "strategy_lock", None)
            if _lock is not None:
                with _lock:
                    _warm_strat = self.active_strategy
            else:
                _warm_strat = getattr(self, "active_strategy", None)
            await self._warm_lstm_bar_cache(_warm_strat)

        # Kill Switch
        if _tl.kill_switch is not None and _tl.kill_switch.is_halted():
            logging.error("Kill Switch is HALTED. Stopping engine loop.")
            self._shutdown_event.set()
            self.strategy_running.clear()
            return Zyklus.STOPP
        return Zyklus.WEITER

    async def _marktzeit_pruefen(self, z: ZyklusZustand) -> Zyklus:
        """#4007: Marktuhr, HITL-Drain, aktive Strategie, Entry-Time-Abgleich.

        ``STOPP`` bei Shutdown nach der Uhrpruefung, ``NAECHSTER`` ohne aktive Strategie.
        """
        # Market Hours Check
        # INC-6 (continuous operation, Dual-Design Option A): a CLOSED market no
        # longer sleep-SKIPS the whole cycle (the old `sleep(300); continue`).
        # Research/analysis/report-evaluation runs CONTINUOUSLY — only ORDER
        # PLACEMENT stays market-gated, enforced downstream at the order step
        # (order_executor._market_closed_blocks_order → records
        # `blocked:market_closed`, never submits to the broker unless
        # BYPASS_MARKET_HOURS). Here we merely flag the closed cycle so we (a) run
        # the analysis at a MODEST cadence (300s end-of-cycle sleep, not 60s) and
        # (b) skip the HITL-approval drain (approvals execute only in an open cycle).
        z.cycle_market_closed = False
        # F3: True only after a successful get_clock. A clock-read failure leaves
        # z.cycle_market_closed False too — it must NOT be mistaken for a confirmed open
        # (which would re-arm the once-per-closed-period report latch mid-closure).
        clock_ok = False
        try:
            clock = await _tl.asyncio.to_thread(self.api.get_clock)
            z.clock = (
                clock  # #4007: der Bericht bei geschlossenem Markt liest next_open
            )
            clock_ok = True
            # PR B (fail-safe): cache market-open so /engine-diagnostics can read
            # it WITHOUT a live broker call. Pure observation — never gates flow.
            try:
                self._last_market_open = bool(clock.is_open)
            except Exception:
                pass
            if not clock.is_open and not _tl.BYPASS_MARKET_HOURS:
                z.cycle_market_closed = True
                next_open = (
                    clock.next_open.strftime("%Y-%m-%d %H:%M:%S UTC")
                    if clock.next_open
                    else "Unknown Time"
                )
                self._log_strategy_thought(
                    f"🌙 Market is CLOSED. Next open: {next_open}. Running "
                    "research/analysis only — order placement is gated until open."
                )
        except Exception as e:
            logging.warning("Failed to check market clock: %s", e)

        if self._shutdown_event.is_set():
            return Zyklus.STOPP

        # Drain HITL-approved orders every cycle (PR-0a-ii-5b / C3). After the kill-switch
        # + market-hours checks above, so approvals execute only in a live, open cycle.
        # Dormant unless HITL_ENABLED. INC-6: the closed-market cycle no longer
        # `continue`s, so gate the drain explicitly — a human-approved order is an
        # ORDER, hence market-gated exactly like the autonomous path.
        # Re-arm the once-per-closed-period report pass ONLY on a CONFIRMED open (F3:
        # gate on clock_ok + is_open, not `not z.cycle_market_closed`, so a transient
        # clock-read blip mid-closure never re-arms → never triggers a 2nd panel pass).
        if clock_ok and clock.is_open:
            self._closed_panel_refreshed = False
        if not z.cycle_market_closed:
            await self._drain_hitl_approvals()

        z.local_active_strategy = None
        z.symbols_to_process = []

        with self.strategy_lock:
            if self.active_strategy:
                z.local_active_strategy = self.active_strategy
                z.symbols_to_process = list(self.active_strategy.symbols)

        if not z.local_active_strategy:
            await _tl.asyncio.sleep(5)
            return Zyklus.NAECHSTER

        # #2046: durable entry-time on desktop. The #2042 reconcile hook runs only on the
        # tenant path (order_executor), which the desktop/OSS fallback never enters — so
        # days_held would stay 0. Reconcile the active strategy's PM once/session. It MUST run
        # HERE — BEFORE the market-closed report-only `continue` below — otherwise a restart
        # while the market is closed never reconciles, leaving EVERY position at "0 days" until
        # the next open. Read-only vs the broker + once/session-guarded → safe + idempotent on
        # both the open and the closed path.
        await self._reconcile_active_strategy_entry_time()
        return Zyklus.WEITER

    async def _markt_geschlossen_berichten(self, z: ZyklusZustand) -> Zyklus:
        """#4007: Markt zu -> hoechstens ein Report-Pass, dann Leerlauf (``NAECHSTER``)."""
        # --- Market-closed report-only mode (owner 2026-07-26) -------------------------
        # Trade only during market hours. When the market is closed, run exactly ONE
        # full-universe LSTM rank/panel refresh (so the on-demand reports stay COMPLETE
        # 24/7) then idle until near open — the round-table deep-eval + the whole order/
        # trade machinery below (risk-exits, consensus, execution, HITL) are skipped, so
        # the GPU rests. Flag-gated; OFF (MARKET_CLOSED_REPORT_ONLY=False) or
        # BYPASS_MARKET_HOURS restores INC-6 continuous market-closed analysis.
        from core.engine.closed_market_mode import (
            closed_idle_seconds,
            should_refresh_closed_panel,
            should_run_report_only,
        )

        if should_run_report_only(
            z.cycle_market_closed,
            _tl.get_config().MARKET_CLOSED_REPORT_ONLY,
            _tl.BYPASS_MARKET_HOURS,
        ):
            if should_refresh_closed_panel(
                getattr(self, "_closed_panel_refreshed", False)
            ):
                # F1: latch (and log) ONLY when the pass actually produced a rank — a
                # transient full-universe fetch failure (RemoteDisconnect) must retry on
                # the next closed wake, not latch an empty report for the whole closure.
                produced = await self._run_closed_report_pass(
                    z.local_active_strategy, z.symbols_to_process
                )
                if produced:
                    self._closed_panel_refreshed = True
                    self._log_strategy_thought(
                        "🌙 Markt geschlossen — EIN vollständiger Report-/Panel-Pass "
                        "gelaufen; Reports bleiben 24/7 live, Trading startet automatisch "
                        "zum nächsten Open."
                    )
            # F2: keep the loop-liveness timestamp fresh while legitimately idle, so the
            # stall monitor (evaluate_stall suppresses on market_closed) does not fire a
            # spurious loop_stalled CRITICAL from a stale timestamp at the market-open
            # transition. Advancing it each closed wake bounds the age to the idle cadence.
            self._last_cycle_details = {
                **(getattr(self, "_last_cycle_details", None) or {}),
                "timestamp": _tl.CompositionRoot.get_instance().clock_port.time(),
            }
            _sto = None
            try:
                _no = getattr(z.clock, "next_open", None)
                if _no is not None:
                    _sto = float(
                        (
                            _no - _tl.CompositionRoot.get_instance().clock_port.now()
                        ).total_seconds()
                    )
            except Exception:
                _sto = None
            await _tl.asyncio.sleep(closed_idle_seconds(_sto))
            return Zyklus.NAECHSTER
        # ------------------------------------------------------------------------------
        return Zyklus.WEITER

    async def _schutz_vor_konsens(self, z: ZyklusZustand) -> Zyklus:
        """#4007: Drawdown, Broker- und Positions-Stops, Ausstiegs-Haken, Wiedereinstiegs-Sperre.

        ``NAECHSTER`` bei Halt (erst nach der Stop-Pflege, #3380) oder ohne Symbole.
        """
        # #2978: feed the account-wide drawdown breaker with live equity BEFORE
        # the halt gate below, so the 7% portfolio-stop / daily circuit breaker
        # can latch trading_halted in THIS cycle (halt-only, allow_unlock=False).
        await self._update_live_account_equity(z.local_active_strategy)

        rm = getattr(z.local_active_strategy, "risk_manager", None)
        _halted = bool(rm and rm.trading_halted)

        # #3380 (ARC-E1.4): Ein Halt stoppt neue EINSTIEGE — nicht den Schutz
        # offener Positionen. Frueher stand hier ein `continue`, das die
        # Positions-Stops uebersprang: Solange der Halt stand, hatte eine offene
        # Position keine Schwelle mehr, und niemand sah es. Genau der Zustand, in
        # dem Schutz am noetigsten ist.
        #
        # Die Reihenfolge allein genuegt nicht: Der Stop-SELL laeuft danach in das
        # Kill-Switch-Tor des Order-Pfades (order_executor.py, `check_halt`), weil
        # risk_konto.py::_konto_breaker den Halt setzt und im selben Block den
        # Kill-Switch ausloest. Die zugehoerige Freistellung fuer Schutz-Exits steht
        # dort — beide Tore gehoeren zusammen.
        if _halted:
            logging.warning(
                "[Halt] Handel gehalten — keine neuen Einstiege. Die Stops offener "
                "Positionen werden weiter gepflegt (#3380)."
            )

        # #2066: per-position risk-exit BEFORE the consensus pass. Held positions that
        # trip a stop are sold via the compliance-gated executor and excluded from the
        # consensus this cycle (so the round table can't re-buy a stopped-out loser).
        # #3382: Bevor die Engine selbst prueft — sorge dafuer, dass beim Broker
        # ein Stop LIEGT. Der Prozess-Stop unten schuetzt nur, solange dieser
        # Prozess laeuft; der Broker-Stop schuetzt auch, wenn er es nicht tut.
        try:
            await self._maintain_broker_stops()
        except Exception as e:  # noqa: BLE001 — darf den Zyklus nie stoppen
            logging.warning("[BrokerStops] Pflege fehlgeschlagen: %s", e, exc_info=True)

        try:
            stopped_symbols = await self._run_position_stop_checks()
        except (
            Exception
        ) as e:  # noqa: BLE001 — a stop-check failure must not kill the loop
            logging.warning(
                "[StopLoss] pre-loop stop check failed: %s", e, exc_info=True
            )
            stopped_symbols = set()

        # #3380: Erst NACH der Stop-Pflege wird der gehaltene Zyklus beendet. Der
        # Rest des Zyklus — Konsens, Einstiege — bleibt bei gesetztem Halt aus.
        if _halted:
            await _tl.asyncio.sleep(60)
            return Zyklus.NAECHSTER

        if stopped_symbols:
            z.symbols_to_process = [
                s for s in z.symbols_to_process if s not in stopped_symbols
            ]

        # #sell-decisions: flag-gated per-cycle exit hook (ROTATION + TRIM). Runs
        # AFTER the price-stop pass and shares its acted-set (a stopped name is never
        # also rotated/trimmed); its exited names are excluded from the consensus so
        # the round table cannot re-buy a just-sold position. Fail-safe: a hook
        # failure is logged and swallowed — it must never kill the loop.
        try:
            exited_symbols = await self._run_deconcentration_and_rotation_exits(
                already_acted=stopped_symbols,
                now=_tl.CompositionRoot.get_instance().clock_port.now(),
            )
        except (
            Exception
        ) as e:  # noqa: BLE001 — an exit-hook failure must not kill the loop
            logging.warning(
                "[ExitHook] deconcentration/rotation hook failed: %s",
                e,
                exc_info=True,
            )
            exited_symbols = set()
        if exited_symbols:
            z.symbols_to_process = [
                s for s in z.symbols_to_process if s not in exited_symbols
            ]

        # #3604 (was #2554): keep FULLY exited names out of BUY re-entry for
        # REENTRY_LOCKOUT_DAYS trading days - the churn-carousel brake the BUY-side
        # cooldowns (which never key on an exit) don't provide.
        reentry_locked = self._stopout_reentry_locked(
            set(stopped_symbols or ()) | set(exited_symbols or ()),
            now=_tl.CompositionRoot.get_instance().clock_port.now(),
        )
        if reentry_locked:
            # #3604: a locked candidate is dropped BEFORE the consensus, so no
            # decision record exists - stamp the outcome badge so the Decisions
            # page shows WHY the name was not bought (audit reason).
            try:
                from core.engine.order_executor import _rec_outcome

                _store = getattr(self, "_reentry_store", None)
                for _sym in z.symbols_to_process:
                    if _sym in reentry_locked:
                        _entry = _store.entry(_sym) if _store else None
                        _rec_outcome(
                            _sym,
                            "blocked:reentry_lockout",
                            (
                                f"fully exited {str(_entry.get('exited_at', '?'))[:16]}, "
                                f"no re-buy before {_entry.get('until', '?')}"
                                if _entry
                                else "re-entry lockout"
                            ),
                        )
            except Exception:  # noqa: BLE001 - observation must never alter the flow
                logging.warning("[ReentryLockout] outcome badge failed.", exc_info=True)
            z.symbols_to_process = [
                s for s in z.symbols_to_process if s not in reentry_locked
            ]
        symbols_to_process = z.symbols_to_process
        if not symbols_to_process:
            self._log_strategy_thought("Waiting for symbols from scanner...")
            await _tl.asyncio.sleep(10)
            return Zyklus.NAECHSTER
        return Zyklus.WEITER


# #4250 (H-2i): Kern-Namen (kill_switch, BYPASS_MARKET_HOURS, engine_now, CompositionRoot,
# get_config, asyncio) liest das Modul ueber _tl, damit die Patch-Ziele der Tests wirken (#4184 §3). Am
# Dateiende: der Kreis Kern <-> Vorlauf traegt so in beiden Ladereihenfolgen (Muster
# core/engine/schleifen_start.py).
from core.engine import trading_loop as _tl  # noqa: E402
