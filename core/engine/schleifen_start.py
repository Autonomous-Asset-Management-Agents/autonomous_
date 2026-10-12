# core/engine/schleifen_start.py
# #4248 (H-2g, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Start, Lease und Uebergabe der Handelsschleife (Schreibberechtigung und
# Lease-Erneuerung, Start-Abgleich mit Ereignisstrom, Outbox-Abgleich, Health-Check, Swap).
"""Start, Lease und Uebergabe: was die Handelsschleife vor und zwischen den Zyklen tut.

#4248 (H-2g) zieht neun Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): die Schreibberechtigung und die
Lease-Erneuerung (#3453), den Start-Abgleich mit Ereignisstrom (#3389/#3491/#3588), den
Outbox-Abgleich nach einem Neustart (#3449), den Health-Check (No-op, ``BotEngine``
ueberschreibt ihn) und die Strategie-Uebergabe an der Zyklusgrenze.

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Abweichung: Die Patch-Ziele der
Tests (``get_config``, ``CompositionRoot``) liest das Modul zur Laufzeit als ``_tl.<name>`` am
Kernmodul, damit Patches auf ``core.engine.trading_loop`` weiter wirken (#4184 §3). Der
``_tl``-Import steht am Dateiende, damit der Kreis Kern <-> Start in beiden Ladereihenfolgen
traegt. ``TradingLoopMixin`` erbt ``StartMixin``; Aufrufe und Instanz-Mocks loesen ueber die
MRO auf.

Plan: ``docs/4248-*/implementation_plan.md``.
"""

import asyncio
import logging


class StartMixin:
    """#4248 (H-2g): Start, Lease, Uebergabe der Handelsschleife; Basis von ``TradingLoopMixin``."""

    async def _sichere_schreibberechtigung(self) -> bool:
        """#3453: Erwirbt die Schreibberechtigung fuer das Konto dieser Engine, wenn sie fehlt.

        Nur Vorsorge: Die verbindliche Pruefung steht vor jeder Order
        (``_sende_durchs_tor``). Ohne Berechtigung laeuft der Zyklus weiter und entscheidet —
        gesendet wird nichts, und das wird dort gemeldet.
        """
        from core import lease

        if not lease.aktiv():
            return True
        konto = lease.konto_schluessel(
            None, paper=bool(getattr(_tl.get_config(), "PAPER_TRADING", True))
        )
        try:
            return await lease.sichere_berechtigung(konto)
        except Exception:  # noqa: BLE001 — die Pruefung vor der Order haelt zurueck
            logging.exception("EngineLease: Erwerb fuer %s gescheitert.", konto)
            return False

    def _starte_lease_erneuerung(self) -> None:
        """#3453: Erneuerung als Aufgabe im Ring des StrategyThread — kein eigener Thread."""
        from core import lease

        if not lease.aktiv():
            return
        self._lease_seit = _tl.CompositionRoot.get_instance().clock_port.time()
        self._lease_aufgabe = asyncio.create_task(self._lease_erneuerung())

    async def _lease_erneuerung(self) -> None:
        from core import lease

        while self.strategy_running.is_set() and not self._shutdown_event.is_set():
            await asyncio.sleep(lease.TAKT_SEKUNDEN)
            try:
                await lease.erneuere_auf_diesem_ring(
                    fortschritt=self._lease_fortschritt
                )
            except Exception:  # noqa: BLE001 — die naechste Order erwirbt neu
                logging.exception("EngineLease: Erneuerungsschritt gescheitert.")

    def _lease_fortschritt(self) -> bool:
        """Fortschritt heisst: Der Zyklus hat sich innerhalb der Frist gemeldet.

        Vor dem ersten Zyklus zaehlt der Start der Erneuerung als letzte Meldung — sonst
        verloere eine frisch gestartete Engine ihre Berechtigung, bevor sie arbeiten kann.
        """
        from core.lease import FORTSCHRITT_MAX_ALTER_SEKUNDEN

        stempel = (getattr(self, "_last_cycle_details", None) or {}).get(
            "timestamp"
        ) or getattr(self, "_lease_seit", None)
        return (
            stempel is not None
            and _tl.CompositionRoot.get_instance().clock_port.time() - float(stempel)
            < FORTSCHRITT_MAX_ALTER_SEKUNDEN
        )

    def _start_fill_stream(self) -> None:
        """Abonniert den Handelsereignis-Strom, wenn Zugangsdaten vorliegen (#3389).

        Fail-soft und still bei fehlenden Zugangsdaten: ohne Strom bleibt der periodische
        Lauf, und der ist die tragende Stufe. Ein fehlender Strom ist kein Defekt, ein
        stiller Absturz waere einer.
        """
        cfg = _tl.get_config()
        # #3588: Der Strom war im Bestand wirkungslos (Handler keine Koroutine, Schleife
        # nie gestartet). Repariert wird er hinter einem eigenen Schalter eingefuehrt:
        # dunkel per Default, weil erst gemessen werden muss, wie er sich bei
        # Verbindungsabbruch verhaelt. Der periodische Lauf bleibt die tragende Stufe.
        if not getattr(cfg, "RECONCILIATION_STREAM_ENABLED", False):
            logging.info(
                "Ereignisstrom nicht abonniert (RECONCILIATION_STREAM_ENABLED=false) — "
                "der periodische Lauf ist die einzige Stufe (#3588)."
            )
            return
        key = getattr(cfg, "ALPACA_API_KEY", None)
        secret = getattr(cfg, "ALPACA_SECRET_KEY", None)
        if not key or not secret:
            logging.info(
                "Ereignisstrom nicht abonniert (keine Zugangsdaten) — der periodische "
                "Abgleich bleibt die tragende Stufe."
            )
            return

        def _fabrik():
            from alpaca.trading.stream import TradingStream

            return TradingStream(
                str(getattr(key, "get_secret_value", lambda: key)()),
                str(getattr(secret, "get_secret_value", lambda: secret)()),
                # #3627: Hier stand ``ALPACA_PAPER`` — ein Name, den die Konfiguration
                # nie kannte. Der Strom ging damit IMMER zum Paper-Endpunkt, auch im
                # Live-Betrieb, und mit Live-Zugangsdaten schlaegt die Anmeldung dort
                # fehl: kein Strom, nur der periodische Lauf. Der Name der Wahrheit
                # heisst ``PAPER_TRADING``.
                paper=bool(getattr(cfg, "PAPER_TRADING", True)),
            )

        self.reconciler.start_fill_stream(_fabrik)

    async def _start_reconciliation(self) -> None:
        """Start-Abgleich + periodischer Lauf (#3389).

        Der periodische Lauf ist die **tragende** Stufe. Ein Ereignisstrom
        (``TradingStream``) waere die schnellere, aber er ist im Bestand nirgends
        abonniert und sein Verhalten bei Verbindungsabbruch ist nicht geprueft (Plan
        #3389 §8) — er kommt als eigener Schritt, wenn er gemessen ist.
        """
        if not getattr(_tl.get_config(), "RECONCILIATION_ENABLED", True):
            logging.warning(
                "Reconciliation ist abgeschaltet (RECONCILIATION_ENABLED=false) — "
                "es findet KEIN Abgleich mit der Broker-Wahrheit statt."
            )
            return

        from core.reconciliation import setze_aktiven

        try:
            from core.reconciliation import ReconciliationService

            self.reconciler = ReconciliationService(
                self.api, getattr(self, "redis_client", None)
            )
            # #3491: ab jetzt fragt die Absendestelle diesen Abgleich — auch schon vor dem
            # ersten Lauf (Owner-Entscheid 18.09.: bis dahin sind Einstiege gesperrt).
            setze_aktiven(self.reconciler)
            record = await self.reconciler.run_once()
            logging.warning(
                "Start-Abgleich: %d Order(s), %d Position(en) verglichen — %s",
                record.broker_orders,
                record.broker_positions,
                "sauber" if record.clean else f"{len(record.breaks)} Abweichung(en)",
            )
            # #3389/#3433: der Ereignisstrom als ZUGABE. Er meldet Ausfuehrungen sofort,
            # statt bis zum naechsten periodischen Lauf zu warten. Sein Verhalten bei
            # Verbindungsabbruch ist nicht gemessen (Plan §8) — deshalb haengt er am
            # selben Eintrag wie der Lauf, und der Lauf bleibt die tragende Stufe.
            self._start_fill_stream()

            self._reconciler_task = asyncio.create_task(
                self.reconciler.run_loop(
                    interval_s=int(
                        getattr(_tl.get_config(), "RECONCILIATION_INTERVAL_SECONDS", 30)
                    )
                )
            )
        except Exception as exc:
            # Fail-soft, siehe Aufrufstelle. Sichtbar, nicht still (CODING_POLICY §5.6).
            self.reconciler = None
            setze_aktiven(None)
            logging.error(
                "Start-Abgleich fehlgeschlagen: %s — der Handel laeuft OHNE Abgleich "
                "gegen die Broker-Wahrheit weiter (#3389).",
                exc,
                exc_info=True,
            )

    async def _start_outbox_abgleich(self) -> None:
        """#3449: Abgleich der Outbox nach einem Neustart — ohne zu senden.

        Owner-Entscheid vom 18.09.2026: Kennt der Broker den Schluessel, ist der Intent
        bestaetigt; kennt er ihn nicht, wird er verworfen und der naechste Zyklus entscheidet
        frisch. Gesendet wird hier nichts.

        Nur Intents des Kontos ``global``: Dort ist ``self.api`` sicher der Client, mit dem
        gesendet wurde. Mit ``active_uid`` und Konto-Zuordnung sendet der globale Pfad ueber
        einen anderen Client (``order_executor.py``, ``resolved_client``) — dort waere ein
        echter Auftrag „unbekannt" und wuerde faelschlich verworfen. Solche Intents bleiben
        liegen und werden gemeldet.

        Fail-soft wie der Start-Abgleich darueber: Ein Fehler haelt den Handel nicht an.
        """
        try:
            from core.engine.order_executor import _outbox_sitzung
            from core.outbox import abgleichen

            client = getattr(self, "api", None)
            if client is None or not hasattr(client, "get_order_by_client_id"):
                return
            async with _outbox_sitzung() as outbox:
                if outbox is None:
                    return
                bericht = await abgleichen(outbox, client, konten={"global"})
            if bericht.bestaetigt or bericht.verworfen or bericht.ungeklaert:
                logging.warning(
                    "Outbox-Abgleich beim Start: %d bestaetigt, %d verworfen (nie "
                    "nachgesendet), %d ungeklaert, %d anderer Konten (#3449).",
                    bericht.bestaetigt,
                    bericht.verworfen,
                    bericht.ungeklaert,
                    bericht.fremd,
                )
            elif bericht.fremd:
                logging.warning(
                    "Outbox-Abgleich beim Start: %d unbestaetigte Intent(s) anderer Konten "
                    "nicht abgeglichen — dafuer fehlt hier der passende Client (#3449).",
                    bericht.fremd,
                )
        except Exception:  # noqa: BLE001 — fail-soft, siehe Docstring
            logging.exception(
                "Outbox-Abgleich beim Start fehlgeschlagen — der Handel laeuft "
                "weiter (#3449)."
            )

    async def _startup_health_check(self) -> None:
        """
        Startzeit-Dependency-Check.
        No-op Basisimplementierung — wird in BotEngine mit Redis/Gemini Checks überschrieben.
        Direkte TradingLoopMixin-Instanzen (z.B. in Unit Tests) überspringen den Check.
        """

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
                    open_positions = await asyncio.to_thread(self.api.get_all_positions)
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
            # _pending_name bleibt gesetzt — nächster Cycle versucht erneut


# #4248 (H-2g): Kern-Namen (get_config, CompositionRoot) liest das Modul ueber _tl, damit die
# Patch-Ziele der Tests wirken (#4184 §3). Am Dateiende: der Kreis Kern <-> Start traegt so in
# beiden Ladereihenfolgen (Muster core/engine/schleifen_stops.py).
from core.engine import trading_loop as _tl  # noqa: E402
