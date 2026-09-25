# core/reconciliation.py
# Epic 2.3-Pre / PR-B — Reconciliation Loop (Watch-Compare-Act)
# #3389 (ARC-E2) — scharfgeschaltet: der Abgleich laeuft, korrigiert aber nichts.
#
# Verantwortlichkeit:
#   - Abgleich von internem Bot-State mit Broker-Realitaet (Alpaca)
#   - Befund festhalten (ReconciliationRecord, #3386) und melden
#   - Laeuft als eigenstaendiger async Task parallel zum TradingLoop
#
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First
# Getestet in: tests/unit/test_reconciliation.py, tests/unit/test_reconciler_armed.py

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from core.audit_paths import resolve_audit_log_path
from core.contracts import FillEvent, ReconciliationBreak, ReconciliationRecord
from core.sim.clock import engine_now  # #3317: Sim und Live auf derselben Uhr

logger = logging.getLogger(__name__)


# ADR-R03: Mengen-Toleranz des Abgleichs = 1e-6 Stueck.
# Basis: Bruchteilige Positionen bleiben (Owner-Entscheid — grosser Vorteil bei kleinen
#        Konten). Ein Vergleich, der auf ganze Stuecke rundet, machte aus JEDER
#        fraktionalen Position eine Dauer-Abweichung und aus der Sperre einen
#        Dauer-Stillstand. 1e-6 liegt unter der kleinsten Menge, die ein Broker annimmt
#        (0,001), und ueber der Rundungsunschaerfe von float — es trennt also
#        Rundungsrauschen von einer echten Differenz.
# Keine regulatorische Pflicht; eine Messgroesse des Abgleichs. Jaehrliche Pruefung.
QTY_EPSILON = 1e-6

# #3588: So viele zuletzt abgeschlossene Orders liest ein Lauf, um entgangene
# Ausfuehrungen nachzutragen. 50 deckt den Abstand zweier Laeufe (30 s) um ein Vielfaches
# ab; die Zahl begrenzt die Last, nicht die Aussage — was hier durchfaellt, faellt beim
# naechsten Lauf wieder an, solange es unbekannt ist.
ABGESCHLOSSENE_ORDERS_JE_LAUF = 50

# ADR-R04: Von der Abgleich-Sperre ausgenommene Intent-Arten.
# Basis: Eine Sperre, die den Notausgang mitsperrt, macht aus einem DATEN-Problem ein
#        KAPITAL-Problem. Die Liste ist deckungsgleich mit der Freistellungs-Matrix am
#        Tor (#3379/#3383) plus dem Schutz-Stop — zwei Listen fuer dieselbe Sache liefen
#        auseinander, sobald eine erweitert wird.
# EU AI Act Art. 14: eine Abweichung haelt neue RISIKOAUFNAHME an, nicht den Rueckzug.
NEVER_BLOCKED_ON_BREAK = frozenset(
    {"stop", "trim", "displacement", "panic", "breaker", "strategy_switch"}
)


#: #3491: der laufende Abgleich dieses Prozesses — fuer die Absendestelle, die statisch ist und
#: keine Engine kennt. Gesetzt vom Start-Abgleich der Handelsschleife, zurueckgenommen, wenn er
#: scheitert. ``None`` heisst: kein Abgleich, keine Pruefung (wie vor #3389).
_aktiver: "Optional[ReconciliationService]" = None


def setze_aktiven(dienst: "Optional[ReconciliationService]") -> None:
    global _aktiver
    _aktiver = dienst


def aktiver_abgleich() -> "Optional[ReconciliationService]":
    return _aktiver


class ReconciliationService:
    """Watch-Compare-Record-Loop fuer den Abgleich mit der Broker-Wahrheit.

    Lifecycle:
        service = ReconciliationService(api, redis_client)
        await service.run_once()                 # Start-Abgleich
        await service.run_loop(interval_s=30)    # als asyncio Task

    **Er korrigiert nichts.** Eine Abweichung heisst gerade, dass nicht feststeht, welche
    Seite recht hat; eine automatische Korrektur waere in diesem Moment ein Handel auf
    Verdacht, mit echtem Geld. Nach EU AI Act Art. 14 ist eine Bestandsaenderung eine
    Kapitalentscheidung und gehoert zum Menschen. Die frueher hier eingebaute
    Auto-Stornierung verwaister Orders ist mit #3389 entfallen (Plan §4, Option A).

    **Die Sperrwirkung ist getrennt schaltbar und standardmaessig aus.** Wie oft eine
    gemeldete Abweichung gutartig ist, ist nicht gemessen — der Dienst hatte bis #3389
    keinen Produktionsaufrufer. Eine Sperre mit unbekannter Fehlalarmquote legt den
    Handel still; deshalb erst beobachten und melden, dann scharfschalten.
    """

    def __init__(
        self,
        api: Any,
        redis_client: Any,
        *,
        block_on_break: Optional[bool] = None,
        known_positions: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Args:
            api:          Alpaca TradingClient-Instanz.
            redis_client: Redis-Connection (sync oder async-kompatibel).
            block_on_break: Sperrt eine Abweichung neue Einstiege? ``None`` liest die
                Konfiguration (Standard: aus).
            known_positions: Der interne Positionsstand ``{symbol: qty}``, gegen den
                verglichen wird.
        """
        self.api = api
        self.redis_client = redis_client
        self._internal_order_ids: Set[str] = set()
        self._known_positions: Dict[str, float] = dict(known_positions or {})
        self._running = False

        # Der erste Lauf uebernimmt den Bestand, statt ihn zu melden — siehe _compare.
        self._adopted = False
        self.reconciled_once = False

        self.entries_blocked = False
        self.last_record: Optional[ReconciliationRecord] = None
        # #3430: der Befund, der die Sperre haelt. Ein sauberer Folgelauf ersetzt
        # `last_record`, hebt die Sperre aber nicht auf — ohne diesen Satz saehe der
        # Bediener eine Sperre ohne Grund.
        self.block_record: Optional[ReconciliationRecord] = None
        self._block_on_break = block_on_break

        # #3677: Die Messwoche (ADR-R02) braucht einen Zaehler, der einen Neustart
        # ueberlebt. Das Engine-Log ist ein begrenzter RAM-Ring — eine Woche passt dort
        # nicht hinein. Ein nicht sauberer Lauf schreibt deshalb eine Zeile dorthin, wo
        # auch Compliance- und Kill-Switch-Protokoll liegen; `_laeufe` ist der Nenner,
        # ohne den eine Zahl von Befunden nichts sagt.
        self._laeufe = 0
        self._laufbuch_pfad = resolve_audit_log_path("reconciliation_runs.jsonl")

        # Die Fill-Seite. `fills` ist der Bestand, `_seen_fills` der Schluesselraum fuer
        # die Wiedererkennung — beide Wege (Ereignisstrom und periodischer Lauf) tragen
        # hier ein. Laufen sie getrennt, faende der Lauf jedes Mal, was der Strom laengst
        # gemeldet hat, und alarmierte bei jedem Durchgang.
        self.fills: List[FillEvent] = []
        self._seen_fills: Set[str] = set()
        self._stream = None
        # #3588: die Aufgabe, in der die Empfangsschleife des Stroms laeuft.
        self._stream_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_order(self, order_id: str) -> None:
        """Registriert eine Order als intern bekannt (nach Submit)."""
        self._internal_order_ids.add(order_id)

    def deregister_order(self, order_id: str) -> None:
        """Entfernt eine Order nach Fill oder Cancel."""
        self._internal_order_ids.discard(order_id)

    def set_known_positions(self, positions: Dict[str, float]) -> None:
        """Setzt den internen Positionsstand, gegen den verglichen wird."""
        self._known_positions = dict(positions or {})

    @property
    def block_on_break(self) -> bool:
        if self._block_on_break is not None:
            return bool(self._block_on_break)
        try:
            from config import get_config

            return bool(getattr(get_config(), "RECONCILIATION_BLOCK_ON_BREAK", False))
        except (
            Exception
        ):  # pragma: no cover - Konfiguration darf nie den Dienst brechen
            return False

    def blocks(self, intent_kind: str = "entry") -> bool:
        """Darf eine Bewegung dieser Art gerade NICHT herausgehen?

        Zwei Gruende halten einen Einstieg zurueck: der Start-Abgleich hat noch nicht
        stattgefunden, oder eine Abweichung steht offen. Beides trifft **nur Einstiege**;
        Schutz-Exits und die Ausnahmepfade passieren immer.
        """
        if intent_kind in NEVER_BLOCKED_ON_BREAK:
            return False
        if not self.reconciled_once:
            return True
        return self.entries_blocked

    def release_block(self, *, by: str) -> None:
        """Hebt die Sperre auf. Ausdruecklich nur von aussen — ein sauberer Folgelauf
        ist kein Mensch, und die Abweichung war nicht deshalb gutartig, weil sie beim
        naechsten Blick verschwunden war."""
        if self.entries_blocked:
            logger.warning(
                "ReconciliationService: Sperre aufgehoben durch %s (#3389).", by
            )
        self.entries_blocked = False
        self.block_record = None

    # ------------------------------------------------------------------
    # Fill-Seite
    # ------------------------------------------------------------------

    @staticmethod
    def _fill_id(order: Any) -> str:
        """Identitaet einer Ausfuehrung.

        Broker-Order-ID plus ausgefuehrte Menge: eine Teilausfuehrung derselben Order ist
        damit unterscheidbar, eine doppelt gemeldete nicht (#3386 verlangt genau das).
        """
        return f"{getattr(order, 'id', '?')}:{getattr(order, 'filled_qty', 0)}"

    @staticmethod
    def _status(order: Any) -> str:
        """Der Status als Kleinbuchstaben-Text — auch wenn er als Enum kommt.

        #3588 (gemessen 23.09.2026): ``alpaca-py`` liefert ``OrderStatus.FILLED``. Seit
        Python 3.11 gibt ``str()`` auf einem ``str``-Enum den Namen ``"OrderStatus.FILLED"``
        zurueck, nicht den Wert ``"filled"`` — jeder Vergleich gegen ``"filled"`` war damit
        falsch, und zwar still. Ergebnis der Messung: kein einziger Fill wurde erkannt,
        weder ueber den Ereignisstrom noch im periodischen Lauf.
        """
        roh = getattr(order, "status", "")
        return str(getattr(roh, "value", roh)).lower()

    def on_broker_fill(
        self, order: Any, *, decision_id: str = "", source: str = "stream"
    ) -> Optional[FillEvent]:
        """Traegt eine Ausfuehrung ein — der EINE Eintrag fuer beide Wege.

        Gibt ``None`` zurueck, wenn die Ausfuehrung bereits bekannt ist: ein doppelt
        eingetroffenes Ereignis darf keine zweite Buchung erzeugen.
        """
        fill_id = self._fill_id(order)
        if fill_id in self._seen_fills:
            return None

        qty = self._qty(getattr(order, "filled_qty", 0))
        preis = self._qty(getattr(order, "filled_avg_price", 0))
        if qty <= 0 or preis <= 0:
            return None

        coid = str(getattr(order, "client_order_id", "") or "")
        # Ohne mitgelieferte decision_id: aus dem Idempotenz-Schluessel zurueckrechnen
        # (#3387 baut ihn als `<leg>-<versuch>-<rumpf>`), sonst die Broker-Order-ID.
        if not decision_id:
            teile = coid.split("-", 2)
            decision_id = (
                teile[2]
                if len(teile) == 3 and teile[1].isdigit() and teile[2]
                else (coid or str(getattr(order, "id", "unbekannt")))
            )

        ereignis = FillEvent(
            fill_id=fill_id,
            broker_order_id=str(getattr(order, "id", "")),
            client_order_id=coid,
            decision_id=decision_id,
            symbol=str(getattr(order, "symbol", "") or "?"),
            side=(
                "sell"
                if str(getattr(order, "side", "")).lower().endswith("sell")
                else "buy"
            ),
            filled_qty=qty,
            price=preis,
            filled_at=getattr(order, "filled_at", None) or engine_now(),
        )
        self._seen_fills.add(fill_id)
        self.fills.append(ereignis)
        logger.info(
            "ReconciliationService: Fill %s %s %s @ %s (Quelle: %s, decision_id=%s)",
            ereignis.symbol,
            ereignis.side,
            ereignis.filled_qty,
            ereignis.price,
            source,
            ereignis.decision_id,
        )
        return ereignis

    def start_fill_stream(self, stream_factory) -> None:
        """Abonniert den Ereignisstrom des Brokers UND startet seine Empfangsschleife.

        Der Strom ist die **Zugabe**, nicht die tragende Stufe: was er verpasst, findet der
        periodische Lauf, und was er doppelt meldet, faellt in `on_broker_fill` heraus.

        #3588: Bis hierher war der Strom wirkungslos. ``subscribe_trade_updates`` legt in
        ``alpaca-py`` nur den Handler ab (``alpaca/trading/stream.py:113-125``) und
        verlangt eine **Koroutine** (``:215-225``) — der bisherige Handler war eine
        gewoehnliche Funktion, das Abonnement scheiterte also schon dort. Empfangen wird
        ausserdem erst in ``_run_forever`` (``:153``); dieser Aufruf fehlte ganz. Beides
        ist hier behoben: Koroutine als Handler, Schleife als eigene Aufgabe.

        ``stream_factory`` liefert das Stream-Objekt; die Naht bleibt aussen, damit der
        Dienst ohne Netz pruefbar ist.
        """

        async def _bei_handelsereignis(daten: Any) -> None:
            order = getattr(daten, "order", daten)
            if self._status(order) not in ("filled", "partially_filled"):
                return
            self.on_broker_fill(order, source="stream")

        try:
            self._stream = stream_factory()
            self._stream.subscribe_trade_updates(_bei_handelsereignis)
            self._stream_task = asyncio.create_task(self._fuehre_strom_aus())
        except Exception as exc:
            # Fail-soft: ohne Strom bleibt der periodische Lauf — sichtbar, nicht still.
            self._stream = None
            self._stream_task = None
            logger.error(
                "ReconciliationService: Ereignisstrom nicht abonniert (%s) — der "
                "periodische Lauf bleibt die einzige Stufe (#3389).",
                exc,
            )

    async def _fuehre_strom_aus(self) -> None:
        """Die Empfangsschleife. Endet sie, ist das sichtbar — nicht still (CODING_POLICY §5.6).

        Die Wiederverbindung nach einem Socket-Fehler bringt ``_run_forever`` selbst mit
        (``alpaca/trading/stream.py:164-187``); sie wird hier nicht nachgebaut.
        """
        strom = self._stream
        if strom is None:
            return
        try:
            await strom._run_forever()
            logger.warning(
                "ReconciliationService: Ereignisstrom beendet — ab jetzt traegt nur noch "
                "der periodische Lauf (#3588)."
            )
        except asyncio.CancelledError:
            raise
        except (
            Exception
        ) as exc:  # noqa: BLE001 — ein Seitenkanal darf den Abgleich nie stoppen
            logger.error(
                "ReconciliationService: Ereignisstrom abgebrochen (%s) — der periodische "
                "Lauf traegt weiter (#3588).",
                exc,
                exc_info=True,
            )

    async def stop_fill_stream(self) -> None:
        """Beendet Schleife und Verbindung, ohne eine Aufgabe zurueckzulassen."""
        strom, aufgabe = self._stream, self._stream_task
        self._stream = None
        self._stream_task = None
        if strom is not None:
            try:
                await strom.stop_ws()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Ereignisstrom liess sich nicht sauber schliessen", exc_info=True
                )
        if aufgabe is not None and not aufgabe.done():
            aufgabe.cancel()
            try:
                await aufgabe
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def stop(self) -> None:
        """Stoppt den Reconciliation-Loop."""
        self._running = False

    async def run_once(self) -> ReconciliationRecord:
        """Ein vollstaendiger Abgleich: holen, vergleichen, festhalten, melden."""
        run_id = f"rec-{uuid.uuid4()}"
        started = engine_now()
        broker_state = await self._watch()
        breaks = self._compare(broker_state)

        record = ReconciliationRecord(
            run_id=run_id,
            started_at=started,
            finished_at=engine_now(),
            broker_orders=len(broker_state.get("orders", [])),
            broker_positions=len(broker_state.get("positions", [])),
            engine_orders=len(self._internal_order_ids),
            engine_positions=len(self._known_positions),
            breaks=tuple(breaks),
        )

        self.last_record = record
        self.reconciled_once = True
        self._report(record)
        return record

    async def run_loop(self, interval_s: int = 30) -> None:
        """Hauptschleife: laeuft kontinuierlich, Abgleich alle interval_s."""
        self._running = True
        logger.info(
            "ReconciliationService: Loop gestartet (Interval: %ds, Sperre: %s).",
            interval_s,
            "an" if self.block_on_break else "aus (Beobachtung)",
        )
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Fehler-Isolation: Exception stoppt NICHT den TradingLoop
                logger.error("ReconciliationService: Loop-Fehler: %s", e, exc_info=True)
            await asyncio.sleep(interval_s)
        logger.info("ReconciliationService: Loop beendet.")

    # ------------------------------------------------------------------
    # Watch
    # ------------------------------------------------------------------

    async def _watch(self) -> Dict[str, List[Any]]:
        """Holt den aktuellen Broker-State von Alpaca.

        Ein Ausfall wird **gemerkt**, nicht als leere Liste weitergereicht: sonst meldete
        ein Netzausfall „nichts beim Broker" und der Abgleich faende reihenweise
        Abweichungen — oder, schlimmer, meldete „alles in Ordnung".
        """
        state: Dict[str, List[Any]] = {
            "orders": [],
            "abgeschlossene_orders": [],
            "positions": [],
            "errors": [],
        }

        try:
            state["orders"] = list(await asyncio.to_thread(self.api.get_orders) or [])
        except Exception as e:
            logger.warning("ReconciliationService._watch: get_orders failed: %s", e)
            state["errors"].append(("orders", str(e)))

        # #3588 (gemessen 23.09.2026): ``get_orders()`` ohne Filter liefert bei Alpaca nur
        # OFFENE Orders. Eine gefuellte Order stand damit in keiner Liste — der Zweig, der
        # entgangene Ausfuehrungen nachtraegt, konnte nie greifen. Die abgeschlossenen
        # Orders kommen deshalb GETRENNT: sie speisen die Fill-Seite, nicht den Vergleich
        # der offenen Orders (sonst meldete jede historische Order eine Abweichung).
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            state["abgeschlossene_orders"] = list(
                await asyncio.to_thread(
                    self.api.get_orders,
                    GetOrdersRequest(
                        status=QueryOrderStatus.CLOSED,
                        limit=ABGESCHLOSSENE_ORDERS_JE_LAUF,
                        direction="desc",
                    ),
                )
                or []
            )
        except Exception as e:
            logger.warning(
                "ReconciliationService._watch: abgeschlossene Orders nicht abrufbar: %s",
                e,
            )
            state["errors"].append(("abgeschlossene_orders", str(e)))

        try:
            state["positions"] = list(
                await asyncio.to_thread(self.api.get_all_positions) or []
            )
        except Exception as e:
            logger.warning(
                "ReconciliationService._watch: get_all_positions failed: %s", e
            )
            state["errors"].append(("positions", str(e)))

        return state

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    @staticmethod
    def _qty(value: Any) -> float:
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            return 0.0

    def _compare(self, broker_state: Dict[str, List[Any]]) -> List[ReconciliationBreak]:
        """Vergleicht Broker-Stand mit internem Stand — Orders **und** Positionen.

        Bis #3389 sah diese Methode ausschliesslich Orders an; ``position_mismatch`` war
        deklariert, aber unerreichbar.

        **Der erste Lauf uebernimmt den Bestand, statt ihn zu melden.** ``_internal_order_ids``
        und der Positionsstand sind nach einem Neustart leer — ohne diese Unterscheidung
        meldete der erste Lauf jedes Mal den gesamten offenen Bestand als Abweichung.
        """
        breaks: List[ReconciliationBreak] = []

        for feld, fehler in broker_state.get("errors", []):
            breaks.append(
                ReconciliationBreak(
                    kind="broker_unreachable",
                    detail=f"{feld}: {fehler}",
                    broker_side="nicht abrufbar",
                    engine_side="Vergleich nicht moeglich",
                )
            )

        orders = broker_state.get("orders", [])
        positions = broker_state.get("positions", [])

        if not self._adopted:
            self._adopted = True
            for order in orders:
                oid = getattr(order, "id", None)
                if oid:
                    self._internal_order_ids.add(str(oid))
            if not self._known_positions:
                self._known_positions = {
                    str(getattr(p, "symbol", "")): self._qty(getattr(p, "qty", 0))
                    for p in positions
                    if getattr(p, "symbol", None)
                }
            # Auch die abgeschlossenen Orders (#3588): Was VOR dem Start gefuellt wurde,
            # ist Bestand — kein entgangener Fill. Ohne diese Zeile meldete der zweite
            # Lauf die gesamte Handelshistorie als "missing_fill".
            for order in list(orders) + list(
                broker_state.get("abgeschlossene_orders", [])
            ):
                if self._status(order) in (
                    "filled",
                    "partially_filled",
                ):
                    self._seen_fills.add(self._fill_id(order))
            logger.warning(
                "ReconciliationService: Erster Lauf — %d Order(s) und %d Position(en) "
                "als Bestand uebernommen. Ab jetzt zaehlt jede Abweichung (#3389).",
                len(orders),
                len(positions),
            )
            return breaks

        # Entgangene Ausfuehrungen: eine beim Broker gefuellte Order, zu der kein Fill
        # vorliegt. Genau der Fall „der Ereignisstrom war unterbrochen" — der Lauf traegt
        # sie NACH und meldet sie. Melden allein heilt nichts.
        for order in list(orders) + list(broker_state.get("abgeschlossene_orders", [])):
            if self._status(order) not in ("filled", "partially_filled"):
                continue
            if self._fill_id(order) in self._seen_fills:
                continue
            ereignis = self.on_broker_fill(order, source="periodischer Lauf")
            if ereignis is not None:
                breaks.append(
                    ReconciliationBreak(
                        kind="missing_fill",
                        symbol=ereignis.symbol,
                        order_id=ereignis.broker_order_id,
                        broker_side=f"gefuellt {ereignis.filled_qty} @ {ereignis.price}",
                        engine_side="kein FillEvent — Ereignisstrom hat ihn nicht gemeldet",
                        detail="nachgetragen",
                        # #3589: geheilt — der Lauf hat den Fill soeben eingetragen. Der
                        # Befund bleibt sichtbar, sperrt aber keine Einstiege.
                        geheilt=True,
                    )
                )

        for order in orders:
            oid = getattr(order, "id", None)
            if oid and str(oid) not in self._internal_order_ids:
                breaks.append(
                    ReconciliationBreak(
                        kind="orphaned_order",
                        order_id=str(oid),
                        symbol=str(getattr(order, "symbol", "") or ""),
                        broker_side="offen beim Broker",
                        engine_side="nicht in den Buechern",
                    )
                )

        gesehen = set()
        for pos in positions:
            symbol = str(getattr(pos, "symbol", "") or "")
            if not symbol:
                continue
            gesehen.add(symbol)
            broker_qty = self._qty(getattr(pos, "qty", 0))

            if symbol not in self._known_positions:
                breaks.append(
                    ReconciliationBreak(
                        kind="unknown_position",
                        symbol=symbol,
                        broker_side=f"qty={broker_qty}",
                        engine_side="keine Position",
                    )
                )
                continue

            engine_qty = self._qty(self._known_positions[symbol])
            if abs(broker_qty - engine_qty) > QTY_EPSILON:
                breaks.append(
                    ReconciliationBreak(
                        kind="position_mismatch",
                        symbol=symbol,
                        broker_side=f"qty={broker_qty}",
                        engine_side=f"qty={engine_qty}",
                    )
                )

        for symbol, engine_qty in self._known_positions.items():
            if symbol not in gesehen and self._qty(engine_qty) > QTY_EPSILON:
                breaks.append(
                    ReconciliationBreak(
                        kind="position_mismatch",
                        symbol=symbol,
                        broker_side="keine Position",
                        engine_side=f"qty={self._qty(engine_qty)}",
                    )
                )

        return breaks

    # ------------------------------------------------------------------
    # Record & Alarm
    # ------------------------------------------------------------------

    def _laufbuch_zeile(
        self, record: ReconciliationRecord, offene: int, gesperrt: bool
    ) -> None:
        """#3677: eine Zeile je nicht sauberem Lauf, dauerhaft.

        Fail-soft und absichtlich still im Fehlerfall — Mitschreiben ist eine
        Beobachtung, kein Tor. Ein voller Datentraeger darf den Abgleich nicht anhalten.
        """
        arten: Dict[str, int] = {}
        for b in record.breaks:
            arten[b.kind] = arten.get(b.kind, 0) + 1
        zeile = {
            "lauf": record.run_id,
            "zeit": record.finished_at.isoformat(),
            "laeufe": self._laeufe,
            "arten": arten,
            "offen": offene,
            "geheilt": len(record.breaks) - offene,
            "gesperrt": gesperrt,
            "sperre_scharf": self.block_on_break,
            "verglichen": {
                "broker_orders": record.broker_orders,
                "broker_positionen": record.broker_positions,
            },
        }
        try:
            with open(self._laufbuch_pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — eine Beobachtung blockiert nie
            logger.warning(
                "ReconciliationService: Laufbuch nicht schreibbar (%s) — die Messwoche "
                "verliert diesen Lauf (#3677).",
                exc,
            )

    def _report(self, record: ReconciliationRecord) -> None:
        """Haelt den Befund fest und alarmiert — korrigiert aber nichts."""
        # #3677: VOR dem Abbruch fuer den sauberen Lauf — er ist der Nenner.
        self._laeufe += 1
        if record.clean:
            logger.info(
                "ReconciliationService: sauber (%d Order(s), %d Position(en) verglichen).",
                record.broker_orders,
                record.broker_positions,
            )
            return

        for b in record.breaks:
            logger.error(
                "ReconciliationService: ABWEICHUNG %s — %s | Broker: %s | Engine: %s %s",
                b.kind,
                b.symbol or b.order_id or "?",
                b.broker_side,
                b.engine_side,
                f"({b.detail})" if b.detail else "",
            )

        # #3589 (Owner-Entscheid 25.09.2026): Es sperren nur OFFENE Befunde. Ein
        # nachgetragener Fill ist eine Abweichung, die derselbe Lauf schon beseitigt hat;
        # sperrte sie, hielte ein einziger Verbindungsabriss den Handel an, bis ein
        # Mensch eine Sperre aufhebt, deren Ursache nicht mehr besteht.
        offene = [b for b in record.breaks if not b.geheilt]
        if not offene and record.breaks:
            logger.warning(
                "ReconciliationService: %d Abweichung(en), alle vom Lauf selbst "
                "nachgetragen — keine Sperre (#3589).",
                len(record.breaks),
            )
            self._laufbuch_zeile(record, offene=0, gesperrt=False)
            return

        if self.block_on_break:
            if not self.entries_blocked:
                logger.error(
                    "ReconciliationService: %d offene Abweichung(en) — neue EINSTIEGE "
                    "gesperrt. Schutz-Exits und Notfallpfade bleiben frei. Die Sperre "
                    "bleibt, bis ein Mensch sie aufhebt (#3389).",
                    len(offene),
                )
            self.entries_blocked = True
            self.block_record = record
            self._laufbuch_zeile(record, offene=len(offene), gesperrt=True)
        else:
            logger.warning(
                "ReconciliationService: %d Abweichung(en) — Sperrwirkung ist ABGESCHALTET "
                "(RECONCILIATION_BLOCK_ON_BREAK=false). Es wird beobachtet und gemeldet, "
                "nicht gesperrt.",
                len(record.breaks),
            )
            self._laufbuch_zeile(record, offene=len(offene), gesperrt=False)
