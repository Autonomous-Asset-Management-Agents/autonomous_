# core/engine/schleifen_stops.py
# #4247 (H-2f, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: die Stops der Handelsschleife (Broker-Stop-Pflege, Meldung liegender
# Stops bei abgeschalteter Pflege, Positions-Stops vor der Konsens-Runde).
"""Die Stops: was die Handelsschleife je Zyklus zum Schutz gehaltener Positionen tut.

#4247 (H-2f) zieht drei Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): die Meldung liegender Stops bei
abgeschalteter Pflege (#3589), die Broker-Stop-Pflege (#3382/#3976) und die Positions-Stops
(#2066). Der eine Storno-Verweis ``api.cancel_order_by_id`` zieht mit (Vertrag
``broker_aufrufer``).

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Abweichungen: Die Patch-Ziele der
Tests (``get_config``, ``engine_now``, ``plan_position_stops``) liest das Modul zur Laufzeit
als ``_tl.<name>`` am Kernmodul, damit Patches auf ``core.engine.trading_loop`` weiter wirken
(#4184 §3). Der funktionslokale Import des Kill-Switch heisst hier ``_kill_switch``: Der Halt
wird wie vorher zur Aufrufzeit aus ``core.kill_switch`` gelesen, nicht aus dem Modul-Global
des Kerns. Der ``_tl``-Import steht am Dateiende, damit der Kreis Kern <-> Stops in beiden
Ladereihenfolgen traegt. ``TradingLoopMixin`` erbt ``StopsMixin``; Aufrufe und Instanz-Mocks
loesen ueber die MRO auf.

Plan: ``docs/4247-*/implementation_plan.md``.
"""

import asyncio
import logging
from datetime import timezone

from core.cloud_logger import DecisionContext
from core.events import SignalEvent


class StopsMixin:
    """#4247 (H-2f): Stops der Handelsschleife, Basis von ``TradingLoopMixin``."""

    async def _melde_liegende_stops_einmal(self, api) -> None:
        """#3589: Was liegt beim Broker, obwohl die Pflege aus ist?

        Einmal je Prozess, nicht je Zyklus: Die Antwort aendert sich ohne Pflege nicht
        von selbst, und ein Abruf je Zyklus waere Last ohne Erkenntnis. Fail-soft — ein
        gescheiterter Abruf darf den Zyklus nicht anhalten; er ist nur eine Meldung.
        """
        if api is None or getattr(self, "_broker_stop_rollback_gemeldet", False):
            return
        self._broker_stop_rollback_gemeldet = True
        try:
            offene = await asyncio.to_thread(api.get_orders)
        except Exception as exc:  # noqa: BLE001 — eine Meldung darf nie blockieren
            logging.warning(
                "[BrokerStops] Pflege aus; liegende Stops nicht abrufbar (%s).", exc
            )
            return
        liegende = [
            o for o in (offene or []) if getattr(o, "stop_price", None) is not None
        ]
        if not liegende:
            logging.info(
                "[BrokerStops] Pflege aus (BROKER_STOPS_ENABLED=false) — es liegt kein "
                "Stop beim Broker."
            )
            return
        logging.warning(
            "[BrokerStops] Pflege aus (BROKER_STOPS_ENABLED=false), aber %d Stop(s) "
            "liegen weiter beim Broker und koennen ausloesen: %s. Sie werden NICHT "
            "abgeraeumt — das geschieht nur auf ausdruecklichen Befehl (#3589).",
            len(liegende),
            ", ".join(
                f"{getattr(o, 'symbol', '?')} {getattr(o, 'qty', '?')} @ "
                f"{getattr(o, 'stop_price', '?')} (id {getattr(o, 'id', '?')})"
                for o in liegende
            ),
        )

    async def _maintain_broker_stops(self) -> None:
        """#3382: haelt fuer jede Position einen Stop beim Broker.

        Ganze Stuecke bekommen einen GTC-Stop, der Bruchstueck-Rest einen Tages-Stop —
        Alpaca nimmt fraktionale Orders nur als Tages-Order an. Der Tages-Stop wird zu
        jedem Sitzungsbeginn erneuert; das getragene Restrisiko ist damit auf weniger als
        ein Stueck je Position begrenzt und wird ausgewiesen, nicht versteckt.

        Schalter ``BROKER_STOPS_ENABLED`` (Registry-Setting; #3632: Auslieferung AN,
        Owner-Freigabe 24.09.2026). Vorbedingung war der Abgleich aus #3389, der laeuft:
        sonst fuellt der Broker den Stop, die Engine erfaehrt es nicht und verkauft ein
        zweites Mal. Der Idempotenz-Schluessel faengt die identische Wiederholung — nicht
        den zweiten Verkauf aus einer anderen Quelle. Seit Smart Exit entfernt ist, ist
        diese Order der Deckel fuer den Fehlerfall des Intelligent Exit.
        """
        cfg = _tl.get_config()
        api = getattr(self, "api", None)
        if not bool(cfg.BROKER_STOPS_ENABLED):
            # #3589 (Owner-Entscheid 25.09.2026, Rollback-Variante B3): Der Schalter
            # schaltet die PFLEGE ab, nicht den SCHUTZ. Was beim Broker liegt, liegt
            # weiter und kann ausloesen — das ist gewollt (ein Abschalten der Pflege
            # soll keine Position ungeschuetzt machen), war aber unsichtbar: Die Pflege
            # kehrte stumm zurueck. Jetzt nennt sie einmal je Prozess, was liegen
            # bleibt. Abgeraeumt wird nur auf ausdruecklichen Befehl (Storno je Order
            # ueber die Engine-API) — nie als Nebenwirkung eines Schalters.
            await self._melde_liegende_stops_einmal(api)
            return

        if api is None:
            return

        from core.broker_stops import plan_broker_stops
        from core.engine import broker_stop_pflege
        from core.kill_switch import kill_switch as _kill_switch

        positions = await asyncio.to_thread(api.get_all_positions)

        # Review #3432 (P1): Der Abruf der liegenden Orders darf NICHT still scheitern.
        # Eine leere Liste hiesse fuer den Planer "es liegt kein Stop" — er legte dann
        # Stops doppelt an und raeumte bestehende ab, beides auf Basis einer Annahme
        # statt einer Beobachtung. Derselbe Fehler wie im Abgleich vor #3389, wo ein
        # Broker-Ausfall als "nichts beim Broker" durchging.
        #
        # Die konservative Richtung ist, diesen Durchgang AUSZULASSEN: was liegt, bleibt
        # liegen. Der Schutz aus dem letzten Zyklus ist besser als einer, der auf einer
        # nicht gelesenen Liste beruht.
        try:
            offene = await asyncio.to_thread(api.get_orders)
        except Exception as exc:
            logging.error(
                "[BrokerStops] Liegende Orders nicht abrufbar (%s) — Pflege in diesem "
                "Zyklus AUSGELASSEN. Bestehende Stops bleiben unangetastet; es wird "
                "weder gelegt noch storniert (#3382).",
                exc,
                exc_info=True,
            )
            return

        liegende = [
            o for o in (offene or []) if getattr(o, "stop_price", None) is not None
        ]

        heute = _tl.engine_now(timezone.utc).date()
        plan = plan_broker_stops(
            positions or [],
            stop_loss_pct=float(cfg.STOP_LOSS_PCT),
            existing_stops=liegende,
            session_date=heute,
            existing_session_date=getattr(self, "_broker_stop_session", None),
        )
        self._broker_stop_session = heute

        # #3976: stornieren, Stornierung bestaetigen (kurze Frist), legen — mit Ersatz-
        # Schluessel, wenn der Broker den alten als verbraucht meldet. Der eine Storno-
        # Zugriff bleibt hier (Architektur-Vertrag); das Modul ruft nur, was es bekommt.
        # Durch das Tor, als Schutz-Exit: protokolliert, nie blockiert (#3379/#3380).
        gescheitert = await broker_stop_pflege.fuehre_plan_aus(
            api,
            plan,
            liegende,
            storno=api.cancel_order_by_id,
            halted=_kill_switch.is_halted(None),
        )
        ungeschuetzt = list(plan.unprotected) + gescheitert
        broker_stop_pflege.merke_stand(
            [getattr(p, "symbol", "") for p in (positions or [])], ungeschuetzt, heute
        )
        if ungeschuetzt:
            # Ein Alarm, der die Menge und den Grund nennt — sonst weiss niemand, wie
            # gross das getragene Risiko ist. #3976: auch gescheiterte Anlagen zaehlen.
            for symbol, grund in ungeschuetzt:
                logging.error(
                    "[BrokerStops] UNGESCHUETZT: %s — %s (#3382)", symbol, grund
                )
            self._log_strategy_thought(
                f"⚠️ {len({s for s, _ in ungeschuetzt})} Position(en) ohne Broker-Stop: "
                + ", ".join(sorted({s for s, _ in ungeschuetzt}))
            )

    async def _run_position_stop_checks(self) -> set:
        """#2066: pre-loop per-position risk-exit on the live (round-table) path.

        Fetch held broker positions and, for each that trips a stop
        (``plan_position_stops`` → ``evaluate_position_stop`` on the broker-authoritative
        ``avg_entry_price`` + reconciled ``entry_time`` #2046), dispatch a SELL through the
        compliance-gated executor (``triggered_by_stop=True`` → the #2065 exit-exemption
        lets a large exit through) and return the set of stopped symbols so the consensus
        pass skips them this cycle. Fail-safe: any error returns an empty set — a stop-check
        failure must never block the trading loop.
        """
        api = getattr(self, "api", None)
        if api is None:
            return set()
        try:
            positions = await asyncio.to_thread(api.get_all_positions)
        except Exception as e:  # noqa: BLE001 — never let a fetch error kill the loop
            logging.warning("[StopLoss] get_all_positions failed: %s", e)
            return set()

        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        trade_history = getattr(pm, "_trade_history", {}) if pm is not None else {}
        high_water_marks = await self._ratchet_high_water_marks(positions)
        entry_times = self._ratchet_entry_times(positions, trade_history)
        plans = _tl.plan_position_stops(
            positions or [],
            trade_history or {},
            high_water_marks=high_water_marks,
            entry_times=entry_times,
            # #3317: ohne now= faellt evaluate_position_stop auf datetime.now zurueck
            # (position_stop.py:75) und misst die Haltedauer gegen die Wanduhr — im Sim die
            # falsche Uhr. Muss GEMEINSAM mit dem Stempel oben umgestellt werden: nur die
            # Messung umzustellen liefert ein negatives now-entry (auf 0 geklemmt), nur den
            # Stempel umzustellen laesst hours_held auf Sim-Datum-vs-heute explodieren.
            now=_tl.engine_now(timezone.utc),
        )

        # #3180: an OPINION exit (take-profit / weighted-composite) that leaks through the
        # position-stop pass is gate-eligible; RISK stops (hard/trailing/loss) never are.
        from core.consensus_retention import consensus_retention_veto

        stopped: set = set()
        for p in plans:
            if consensus_retention_veto(p["symbol"], p.get("tier", "risk"), pm):
                logging.info(
                    "[StopLoss] %s: OPINION exit SUPPRESSED — live round-table "
                    "consensus still retains the name (#3180 gate)",
                    p["symbol"],
                )
                continue
            ctx = DecisionContext(
                symbol=p["symbol"],
                action="SELL",
                current_price=p["current_price"],
                in_position=True,
                position_qty=p["qty"],
                position_avg_price=p["avg_entry_price"],
                triggered_by_stop=True,
                stop_type=(p["reason"] or "")[:64],
                risk_approved=True,
                portfolio_approved=True,
                intelligence_approved=True,
            )
            event = SignalEvent(
                symbol=p["symbol"],
                action="SELL",
                decision_context=ctx,
                suggested_quantity=0.0,
            )
            await self._process_signal_event(event)
            stopped.add(p["symbol"])
            # #3655: a LOSS stop (loss cut / hard stop) also holds the slot it frees;
            # trailing profit exits do not (the name is a winner, the replacement
            # finding concerns weak days). Consumed by _stopout_reentry_locked.
            _reason_uc = str(p["reason"] or "").upper()
            if "LOSS CUT" in _reason_uc or "HARD STOP" in _reason_uc:
                _loss = getattr(self, "_stop_loss_exits", None)
                if _loss is None:
                    _loss = self._stop_loss_exits = set()
                _loss.add(str(p["symbol"]).upper())
            logging.info(
                "[StopLoss] %s: risk-exit fired → SELL dispatched (%s)",
                p["symbol"],
                p["reason"],
            )
        return stopped


# #4247 (H-2f): Kern-Namen (get_config, engine_now, plan_position_stops) liest das Modul
# ueber _tl, damit die Patch-Ziele der Tests wirken (#4184 §3). Am Dateiende: der Kreis
# Kern <-> Stops traegt so in beiden Ladereihenfolgen (Muster core/specialist/quellen_edgar.py).
from core.engine import trading_loop as _tl  # noqa: E402
