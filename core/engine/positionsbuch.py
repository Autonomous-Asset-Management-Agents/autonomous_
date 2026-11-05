# core/engine/positionsbuch.py
# #4246 (H-2e, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: das Positionsbuch der Handelsschleife (HWM, Einstiegszeiten,
# Wiedereinstiegs-Sperre, Abgleich der Einstiegszeit der aktiven Strategie).
"""Das Positionsbuch: was die Handelsschleife je Zyklus ueber gehaltene Positionen fortschreibt.

#4246 (H-2e) zieht fuenf Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): den Abgleich der Einstiegszeit der
aktiven Strategie (#2046/#2965), die High-Water-Marks (#2557), den Positions-Schnappschuss und
die Wiedereinstiegs-Sperre (#3604/#3655) sowie die Einstiegszeiten (#2555/#3317). Dazu die
Konstante ``ENTRY_TIME_RECONCILE_COOLDOWN_S``, deren einziger Leser hier liegt.

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Einzige Abweichung: Die Patch-Ziele
der Tests (``get_config``, ``engine_now``, ``CompositionRoot``, ``load_position_hwm``,
``save_position_hwm``) liest das Modul zur Laufzeit als ``_tl.<name>`` am Kernmodul, damit
Patches auf ``core.engine.trading_loop`` weiter wirken (#4184 §3). Der ``_tl``-Import steht am
Dateiende, damit der Kreis Kern <-> Positionsbuch in beiden Ladereihenfolgen traegt.
``TradingLoopMixin`` erbt ``PositionsbuchMixin``; Aufrufe und Instanz-Mocks loesen ueber die MRO auf.

Plan: ``docs/4246-*/implementation_plan.md``.
"""

import logging
from datetime import timezone

# #2965: cooldown between per-cycle entry-time reconciles (flag ON only).
# One broker positions call per window — context: BUG-AI-105 already accepts
# an N+1 positions refetch per SIZING call, so this is comparatively cheap.
ENTRY_TIME_RECONCILE_COOLDOWN_S = 300.0


class PositionsbuchMixin:
    """#4246 (H-2e): Positionsbuch der Handelsschleife, Basis von ``TradingLoopMixin``."""

    async def _reconcile_active_strategy_entry_time(self) -> None:
        """#2046: reconcile the ACTIVE strategy's PortfolioManager entry-time once per
        session from Alpaca fills.

        The #2042 durable-entry-time reconcile is only hooked on the tenant path
        (order_executor.py:434 / ``_execute_tenant_order``), which the desktop/OSS
        fallback path never enters — so on desktop ``_trade_history`` stays empty and
        ``days_held`` stays 0. Here we reconcile the reachable canonical desktop PM
        (``active_strategy.portfolio_manager``, which carries a broker ``client``,
        rl_strategy.py:284). Fail-safe: no-op without a PM/client; once per PM instance
        (a strategy swap re-reconciles the new PM). Read-only vs the broker.
        """
        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        if pm is None or getattr(pm, "client", None) is None:
            return
        if not hasattr(self, "_strategy_pm_reconciled"):
            self._strategy_pm_reconciled: set = set()
        # #2965 (flag-dark): with ENTRY_TIME_RECONCILE_PER_CYCLE the once-per-PM
        # latch becomes a cooldown — mid-session foreign/executor fills get a
        # durable entry-time too, not only the boot snapshot. The reconcile is
        # delta-cheap (skips symbols already in _trade_history; page cap holds),
        # so flag ON costs one get_all_positions call per cooldown window.
        from core.entry_time_bridge import per_cycle_enabled

        if per_cycle_enabled():
            import time as _time

            from core.composition.root import CompositionRoot

            if not hasattr(self, "_strategy_pm_reconcile_ts"):
                self._strategy_pm_reconcile_ts: dict = {}
            # None-sentinel, NOT 0.0: time.monotonic() is seconds-since-boot on
            # Linux — on a fresh container/VM it is itself < the cooldown, so a
            # 0.0 default would skip the very FIRST reconcile after boot (caught
            # by CI on a fresh GKE runner).
            _last = self._strategy_pm_reconcile_ts.get(id(pm))
            if (
                _last is not None
                and _time.monotonic() - _last < ENTRY_TIME_RECONCILE_COOLDOWN_S
            ):
                return
            self._strategy_pm_reconcile_ts[id(pm)] = _time.monotonic()
        elif id(pm) in self._strategy_pm_reconciled:
            return
        else:
            self._strategy_pm_reconciled.add(id(pm))
        from core.engine import entry_time_reconcile as _etr

        await _etr.reconcile_entry_time_from_alpaca(pm)

    async def _ratchet_high_water_marks(self, positions):
        """#desktop-exit: maintain a per-symbol high-water-mark (the peak price seen while we hold it)
        so the per-cycle trailing stop can measure a REAL drawdown from the peak — the fix for held
        winners never being trimmed (evaluate_position_stop otherwise defaults hwm=current → drawdown 0).

        Flag-gated (``POSITION_EXIT_HWM_TRAILING_ENABLED``): OFF → returns ``None`` → plan_position_stops
        falls back to today's byte-identical behaviour. Ratchets UP only (``max``) and rebuilds from the
        currently-held set each cycle, so a symbol no longer held is evicted (no leak, no stale peak).

        #2557: when ``POSITION_EXIT_HWM_PERSIST_ENABLED`` is ALSO on, the map survives the daily desktop
        restart — the first call after boot (``_position_high_water_marks`` unset) seeds ``prev`` from the
        durable store instead of ``{}``, and the freshly rebuilt map is best-effort persisted each cycle
        (prune is free: ``current`` is rebuilt from held positions, so dropped symbols leave the row).
        Fail-SAFE: persist OFF, or an empty/None/failed store → ``prev = {}`` → byte-identical to today
        (a missing peak only makes the trail STRICTER, never a false exit); a store error can never kill
        the loop."""
        cfg = _tl.get_config()
        if not getattr(cfg, "POSITION_EXIT_HWM_TRAILING_ENABLED", False):
            return None
        persist = bool(getattr(cfg, "POSITION_EXIT_HWM_PERSIST_ENABLED", False))
        prev = getattr(self, "_position_high_water_marks", None)
        if prev is None:
            # First call after boot: seed from the durable store (persist ON) or start empty.
            if persist:
                try:
                    prev = await _tl.load_position_hwm()
                except (
                    Exception
                ):  # noqa: BLE001 — persistence must never break the loop
                    logging.warning(
                        "[HWM] load_position_hwm failed; seeding empty map (trail re-measures "
                        "from current price)",
                        exc_info=True,
                    )
                    prev = {}
            else:
                prev = {}
        prev = prev or {}
        current = {}
        for p in positions or []:
            sym = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
            raw = (
                p.get("current_price")
                if isinstance(p, dict)
                else getattr(p, "current_price", None)
            )
            try:
                price = float(raw)
            except (TypeError, ValueError):
                price = 0.0
            if sym and price > 0:
                current[sym] = max(float(prev.get(sym, 0.0)), price)
        self._position_high_water_marks = current
        if persist:
            try:
                await _tl.save_position_hwm(current)
            except Exception:  # noqa: BLE001 — persistence must never break the loop
                logging.warning(
                    "[HWM] save_position_hwm failed; peak will re-seed from current on next boot",
                    exc_info=True,
                )
        return current

    def _note_position_snapshot(self, positions_by_symbol, confirmed):
        """#3604: remember the held set of a CONFIRMED broker snapshot so the next cycle
        can see a full exit that no stop/rotation set reports — a SELL vote or a manual
        sell. Unconfirmed = unknown, never "flat": nothing is recorded as vanished."""
        if not confirmed:
            return
        held = {str(s).upper() for s in (positions_by_symbol or {})}
        prev = getattr(self, "_reentry_prev_held", None)
        vanished = (prev - held) if prev is not None else set()
        self._reentry_vanished = getattr(self, "_reentry_vanished", set()) | vanished
        self._reentry_prev_held = held

    def _stopout_reentry_locked(self, just_exited, now=None):
        """#3604 (replaces the #2554 minute brake): keep a FULLY exited name out of BUY
        re-entry for ``REENTRY_LOCKOUT_DAYS`` trading days — whatever the exit reason
        (stop, trailing, rotation, displacement, SELL vote, manual sell). Measured: the
        next-morning re-buy lost -1,769 $ (n=14) while 4 hours only covered the
        same-afternoon carousel.

        Arms every symbol in ``just_exited`` (stop / rotation sets of this cycle) minus
        this cycle's PARTIAL exits (trims), plus the names that vanished from the confirmed
        position snapshot since the last cycle. Persisted (core/engine/reentry_lockout.py)
        so the restart every settings apply triggers does not clear it. ``0`` => disabled,
        nothing read or written. Exit-side only: never blocks a sell.
        """
        cfg = _tl.get_config()
        days = int(cfg.REENTRY_LOCKOUT_DAYS or 0)
        # #3655: the slot a LOSS stop freed stays held for hold_days trading days.
        hold_days = int(cfg.STOP_EXIT_SLOT_HOLD_DAYS or 0)
        loss_exits = {
            str(s).upper() for s in (getattr(self, "_stop_loss_exits", set()) or ())
        }
        self._stop_loss_exits = set()
        if days <= 0 and hold_days <= 0:
            return set()
        now = now or _tl.CompositionRoot.get_instance().clock_port.now()
        store = getattr(self, "_reentry_store", None)
        if store is None:
            from core.engine.reentry_lockout import shared_store

            store = shared_store()
            self._reentry_store = store
        partial = {
            str(s).upper()
            for s in (getattr(self, "_reentry_partial_exits", set()) or ())
        }
        vanished = set(getattr(self, "_reentry_vanished", set()) or ())
        self._reentry_vanished = set()
        self._reentry_partial_exits = set()
        full_exits = (
            {str(s).upper() for s in (just_exited or ()) if s} | vanished
        ) - partial
        try:
            if full_exits:
                locked = store.arm(
                    full_exits,
                    now,
                    days,
                    hold_days=hold_days,
                    stop_exits=loss_exits & full_exits,
                )
                logging.warning(
                    "[ReentryLockout] %s fully exited - no re-buy for %d trading "
                    "day(s); slot held for %d day(s) after a loss stop (%s).",
                    ", ".join(sorted(full_exits)),
                    days,
                    hold_days,
                    ", ".join(sorted(loss_exits & full_exits)) or "-",
                )
            else:
                locked = store.active(now)
        except Exception:  # noqa: BLE001 - a side-store must never break the loop
            logging.warning(
                "[ReentryLockout] store failed - no lockout this cycle (fail-open).",
                exc_info=True,
            )
            return set()
        return locked

    def _ratchet_entry_times(self, positions, trade_history):
        """#2555: maintain the CURRENT holding's entry-time per symbol so the per-cycle
        stop measures ``hours_held`` from the fresh (re)buy — not ``min(_trade_history)``,
        which returns the OLDEST trade in the 30-day window and goes stale across a sold-
        then-rebought symbol (``[old_entry, sell, new_buy]`` → ``min`` = old_entry →
        inflated hours_held → panic-protection bypassed + loss-cut tightened on what is
        really a fresh position).

        Flag-gated (``POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED``): OFF → returns ``None``
        → plan_position_stops falls back to ``min(_trade_history)`` (byte-identical to
        today). Locks each symbol's entry at first observation and holds it while the
        position is continuously held; a symbol no longer held is evicted, so a later
        rebuy re-stamps a fresh entry. On the FIRST cycle the held set is seeded from
        ``trade_history`` (the #1994 fill-reconcile has written the true entry there);
        every later new appearance is an in-session (re)open → stamped ``now()``. No
        broker calls; never mutates ``_trade_history``.
        """
        if not getattr(
            _tl.get_config(), "POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED", False
        ):
            return None
        prev = getattr(self, "_position_entry_times", None)
        first_cycle = prev is None
        prev = prev or {}
        # #3317: engine_now, nicht datetime.now — unter SIM_MODE stempelte die WANDUHR die
        # Eintrittszeit, waehrend evaluate_position_stop die Haltedauer dagegen ebenfalls
        # gegen die Wanduhr misst. Ein Sim-Lauf dauert real Stunden, also blieb hours_held
        # bei ~0, der Zeit-Multiplikator (>=4h/24h/72h) griff NIE, und die -4-%-Verluststufe
        # (base_score 70) erreichte die Hartstop-Schwelle 90 nicht — nur der -8-%-Hartstop
        # feuerte, weil er 100 direkt zurueckgibt. Fehlerklasse wie #3119. Ohne SIM_MODE ist
        # engine_now byte-identisch zu datetime.now(tz) (clock.py:263-264) — Live unberuehrt.
        now = _tl.engine_now(timezone.utc)
        current = {}
        for p in positions or []:
            sym = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
            if not sym:
                continue
            if sym in prev:
                current[sym] = prev[sym]
            elif first_cycle:
                times = (trade_history or {}).get(sym)
                current[sym] = min(times) if times else now
            else:
                current[sym] = now
        self._position_entry_times = current
        return current


# #4246 (H-2e): Kern-Namen (get_config, engine_now, CompositionRoot, load_position_hwm,
# save_position_hwm) liest das Positionsbuch ueber _tl, damit die Patch-Ziele der Tests wirken
# (#4184 §3). Am Dateiende: der Kreis Kern <-> Positionsbuch traegt so in beiden
# Ladereihenfolgen (Muster core/specialist/quellen_edgar.py).
from core.engine import trading_loop as _tl  # noqa: E402
