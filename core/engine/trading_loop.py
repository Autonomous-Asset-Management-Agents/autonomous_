# core/engine/trading_loop.py
# Epic 1.4 / Issue #217 — LangGraph-Dispatch für Non-LSTM-Strategien
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Epic 2.3-Pre / PR-A — Graceful Handover via AgentRegistry
# Verantwortlichkeit: Live Trading Loop, Snapshot-Fetch, LangGraph-Task-Dispatch

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.data.requests import StockSnapshotRequest  # noqa: F401  # Patch-Ziel (H-2)

from config import BYPASS_MARKET_HOURS  # noqa: F401  # Patch-Ziel (H-2)
from config import get_config  # ML-1: SIP = consolidated NBBO (MiFID II best-execution)
from core.cloud_logger import DecisionContext  # noqa: F401  # Patch-Ziel (H-2)
from core.composition.root import CompositionRoot  # noqa: F401  # Patch-Ziel (H-2)

# TODO(MOD-1): migrate BYPASS_MARKET_HOURS to RuntimeConfigState when that lands
from core.engine.ausstieg_hebel import AusstiegsHebelMixin  # #3823: Ausstiegsrunde
from core.engine.hwm_store import (  # noqa: F401  # Patch-Ziel (H-2)
    load_position_hwm,
    save_position_hwm,
)
from core.engine.marktdaten import MarktdatenMixin  # #4249 (H-2h): Marktdaten und Konto
from core.engine.portfolio_context import (  # noqa: F401  # Patch-Ziel (H-2)
    build_portfolio_context,
)
from core.engine.positionsbuch import PositionsbuchMixin  # #4246 (H-2e)
from core.engine.schleifen_start import StartMixin  # #4248 (H-2g)
from core.engine.schleifen_stops import StopsMixin  # #4247 (H-2f): Stops
from core.engine.symbol_schluessel import (  # noqa: F401  # Testleser (#4244, H-2c)
    _implied_vol_state_keys,
    _position_context_state_keys,
    _quality_state_keys,
    _regime_conditioner_state_keys,
    _risk_reversal_state_keys,
)
from core.engine.time_budget import (  # #3381: Staffelung Agent<Symbol<Zyklus
    current_time_budget,
)
from core.engine.zyklus import Zyklus, ZyklusZustand  # #4244 (H-2c): umgezogen
from core.engine.zyklus_bewertung import ZyklusBewertungMixin  # #4252 (H-2k): Bewertung
from core.engine.zyklus_kontext import ZyklusKontextMixin  # #4251 (H-2j): Kontext
from core.engine.zyklus_vorlauf import ZyklusVorlaufMixin  # #4250 (H-2i)
from core.events import SignalEvent  # noqa: F401  # Patch-Ziel (H-2)
from core.exceptions import BrokerConnectionError
from core.position_stop import plan_position_stops  # noqa: F401  # Patch-Ziel (H-2)
from core.sim.clock import (  # noqa: F401  # Patch-Ziel (H-2), #2548: SIM_MODE → sim time
    engine_now,
)

# Layer 2: Per-Symbol Evaluation Timeout (MiFID II Art. 17)
# #3381: war eine Modulkonstante von 45,0 s und lag damit UNTER der Agenten-Grenze
# (round_table/runner.py, 60,0 s) — das Symbol brach ab, waehrend seine Agenten noch
# arbeiteten. Der Wert kommt jetzt aus der geordneten Staffelung Agent < Symbol < Zyklus.
# Module logger for NEW code (review #3480 FINDING-02); the legacy call sites in this
# file still use the root logger and are out of scope here.
logger = logging.getLogger(__name__)


def _symbol_eval_timeout() -> float:
    """Layer 2 der Staffelung, geordnet gegen Layer 1 und 3 (#3381)."""
    return current_time_budget()[1]


async def _fetch_position_snapshot(api) -> tuple:
    """#2672: ONE authoritative broker-position snapshot per cycle.

    Returns ``(positions_by_symbol, confirmed)`` where ``positions_by_symbol``
    maps ``symbol -> {"qty", "avg_entry", "unrealized_pnl"}`` (floats — the SDK
    returns strings) and ``confirmed`` says whether the broker actually answered.

    DELIBERATELY UNGATED: no feature flag, no ROUND_TABLE_TOP_K_EVAL coupling
    (Archon audit on #2672, Critical 1 — the rank funnel's own fetch sat inside
    ``if _top_k > 0`` and would have silently starved every consumer the moment
    an operator zeroed a GPU tuning flag). Consumers decide flag-gated USE via
    ``_position_context_state_keys``; the rank funnel shares this snapshot.

    Fail-closed: no client / broker error -> ``({}, False)`` + WARNING (§5.6).
    ``confirmed=False`` means "unknown", NEVER "flat" — a consumer must not
    treat an unconfirmed book as empty.
    """
    if api is None:
        logging.warning(
            "[PositionSnapshot] no broker client — position context UNCONFIRMED "
            "this cycle (consumers must treat the book as unknown, not flat)."
        )
        return {}, False
    try:
        positions = await asyncio.to_thread(api.get_all_positions)
    except Exception as exc:  # noqa: BLE001 — any broker failure ⇒ unconfirmed
        logging.warning(
            "[PositionSnapshot] get_all_positions failed (%s) — position context "
            "UNCONFIRMED this cycle (unknown, not flat).",
            exc,
        )
        return {}, False
    by_symbol = {}
    for pos in positions or []:
        symbol = getattr(pos, "symbol", None)
        if not symbol:
            continue
        try:
            by_symbol[str(symbol)] = {
                "qty": float(getattr(pos, "qty", 0.0) or 0.0),
                "avg_entry": float(getattr(pos, "avg_entry_price", 0.0) or 0.0),
                "unrealized_pnl": float(getattr(pos, "unrealized_pl", 0.0) or 0.0),
            }
        except (TypeError, ValueError):
            # One malformed row must not void the whole book — log + skip (§5.6).
            logging.warning(
                "[PositionSnapshot] malformed position row for %s — skipped.", symbol
            )
    return by_symbol, True


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
    from core.orchestration.graph import (  # noqa: F401  # Patch-Ziel (H-2)
        build_symbol_eval_graph,
    )
except ImportError:  # pragma: no cover
    build_symbol_eval_graph = None  # type: ignore[assignment]

# OBS-4 (#2637): the trade path (model.inference / risk / order / execution.block)
# ends its spans on background threads (asyncio.to_thread) and had no common
# parent, so each was a detached root. A per-cycle ``trading.cycle`` span + a
# per-symbol ``trading.symbol_eval`` child give the whole cycle ONE trace.
try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]

_obs_tracer = get_tracer("trading.loop")


async def _run_symbol_eval(tracer, cycle_ctx, symbol, coro_factory):
    """Run one symbol's evaluation inside a ``trading.symbol_eval`` span parented to
    ``cycle_ctx`` (the ``trading.cycle`` root). Because the span is *current* while
    ``coro_factory()`` runs, the round-table ``model.inference`` spans — created on a
    worker thread via ``asyncio.to_thread`` (which copies the OTel context) — join the
    same trace. ``coro_factory`` returns the awaitable (called lazily inside the span).
    PURE OBSERVATION: the awaited result is returned unchanged; span errors never
    alter behaviour."""
    with tracer.start_as_current_span("trading.symbol_eval", context=cycle_ctx) as span:
        if span is not None:
            try:
                span.set_attribute("symbol", symbol)
            except Exception:
                logging.debug(
                    "Telemetry span error: failed to set symbol attribute",
                    exc_info=True,
                )
        return await coro_factory()


try:
    from core.kill_switch import kill_switch  # noqa: F401  # Patch-Ziel (H-2)
except ImportError:  # pragma: no cover
    kill_switch = None  # type: ignore[assignment]

# RTR-1 (#1948): FREE point-in-time news producer (Google-News-RSS). Flag-FIRST
# inside the producer — NEWS_SENTIMENT_NLP_ENABLED off (default) returns None
# without any network I/O, and no state key is added (byte-identical dormant path).
try:
    from core.nlp.headlines import (  # noqa: F401  # Patch-Ziel (H-2)
        produce_news_headlines,
    )
except ImportError:  # pragma: no cover
    produce_news_headlines = None  # type: ignore[assignment]


def _quote_timestamp(source) -> Optional[datetime]:
    """Zeitstempel einer Preisquelle, ohne Annahme über das Feld (#3381).

    Alpaca nennt das Feld ``timestamp``, einige Bar-Formen ``t``. Fehlt beides, ist das
    Alter unbekannt — und ``None`` sagt genau das, statt es zu raten.
    """
    ts = getattr(source, "timestamp", None)
    if ts is None:
        ts = getattr(source, "t", None)
    return ts if isinstance(ts, datetime) else None


def _extract_ohlc_from_snapshot(snapshot_obj) -> tuple[dict, float, Optional[datetime]]:
    """
    Extrahiert OHLC + Live-Preis + **Alter der Preisquelle** aus einem Alpaca-Snapshot.

    KRITISCH: Nutzt latest_trade.price als 'close' (live Intraday-Preis).
    daily_bar liefert die GESTRIGE EOD-Bar — sie darf NICHT als close verwendet
    werden, da sonst alle Zyklen identische stale Preise bekommen.

    Priorität:
        1. latest_trade.price → live 'close' (primär)
        2. daily_bar          → O/H/L-Referenz (gestern), H/L werden auf live Preis erweitert
        3. Fallback           → daily_bar.close wenn kein latest_trade vorhanden (Markt zu)
        4. Nullen             → wenn weder Bar noch Trade

    #3381: Der dritte Rückgabewert ist der Zeitstempel *derselben* Quelle, aus der der
    Preis stammt. Er entsteht hier, weil hier die Quelle noch bekannt ist — danach ist
    nur noch ein ``float`` unterwegs und das Alter wäre nicht mehr feststellbar. Der
    Aufrufer vergleicht ihn gegen ``engine_now()`` und enthält sich bei ``stale_quote``.

    Returns:
        (ohlc_dict, price, quote_ts) — price == ohlc['close']; quote_ts ist ``None``,
        wenn keine Quelle ein Alter mitführt.
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
        return ohlc, live_price, _quote_timestamp(trade)

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
        return ohlc, bar_close, _quote_timestamp(bar)

    else:
        # Weder Trade noch Bar — kein Preis und kein Alter.
        return (
            {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0},
            0.0,
            None,
        )


class TradingLoopMixin(
    StartMixin,
    PositionsbuchMixin,
    StopsMixin,
    MarktdatenMixin,
    ZyklusVorlaufMixin,
    ZyklusKontextMixin,
    ZyklusBewertungMixin,
    AusstiegsHebelMixin,
):
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
            # #3453: Auf DIESEM Ring freigeben — die Berechtigung lebt hier. Sonst haelt eine
            # gestoppte Engine das Konto bis zum Ablauf der Frist fest.
            try:
                aufgabe = getattr(self, "_lease_aufgabe", None)
                if aufgabe is not None and not aufgabe.done():
                    aufgabe.cancel()
                    loop.run_until_complete(
                        asyncio.gather(aufgabe, return_exceptions=True)
                    )
                from core.lease import beende_auf_diesem_ring

                loop.run_until_complete(beende_auf_diesem_ring())
            except Exception:  # noqa: BLE001 — sie laeuft nach der Frist von selbst ab
                logging.exception("EngineLease: Freigabe beim Stopp gescheitert.")
            # #3588: Die Empfangsschleife des Ereignisstroms haengt an diesem Ring —
            # ohne Stopp bliebe eine Aufgabe auf einem geschlossenen Loop zurueck.
            try:
                abgleich = getattr(self, "reconciler", None)
                if (
                    abgleich is not None
                    and getattr(abgleich, "_stream", None) is not None
                ):
                    loop.run_until_complete(abgleich.stop_fill_stream())
            except (
                Exception
            ):  # noqa: BLE001 — der Stopp darf das Herunterfahren nie halten
                logging.exception(
                    "Ereignisstrom: Stopp beim Herunterfahren gescheitert."
                )
            try:
                loop.close()
            except Exception as e:
                logging.error("Error closing async loop: %s", e)

    async def _drain_hitl_approvals(self) -> int:
        """Execute every human-approved order waiting in the HITL queue (PR-0a-ii-5b, Art. 14).

        Called once per trading cycle (C3). Dormant unless HITL_ENABLED. Each approved payload
        is CLAIMED (approved→inflight) then executed via execute_approved_order, which BYPASSES
        the gate (N1) so an approved order is never re-queued, and acked once it reaches a
        definitive outcome. Crash-safe: a mid-execution crash leaves an inflight marker that is
        surfaced (not silently lost) next cycle. Best-effort: a bad payload is logged, never
        crashes the loop. Returns the count executed.
        """
        if not get_config().HITL_ENABLED:
            return 0
        from core.hitl_queue import HitlQueue

        # Surface any approval claimed-but-never-acked last time — i.e. the engine crashed or
        # redeployed mid-execution. Loud, operator-verifies-at-broker; NOT auto-re-executed
        # (a blind re-run could place the order twice without broker-side idempotency).
        try:
            await HitlQueue.recover_orphaned_inflight()
        except Exception as exc:
            logging.error("[HITL] drain: recover_orphaned_inflight failed: %s", exc)

        try:
            approved = await HitlQueue.claim_approved()
        except Exception as exc:
            logging.error("[HITL] drain: claim_approved failed: %s", exc)
            return 0
        drained = 0
        for payload in approved:
            approval_id = payload.get("approval_id", "")
            try:
                await self.execute_approved_order(payload)
                drained += 1
            except Exception as exc:
                logging.error(
                    "[HITL] drain: execute failed for %s: %s",
                    payload.get("symbol", "?"),
                    exc,
                )
            finally:
                # Definitive outcome reached (submitted, Iron-Dome-rejected, or a handled
                # error) → clear the inflight marker. A hard crash before this point leaves the
                # marker for recover_orphaned_inflight() next cycle.
                try:
                    await HitlQueue.ack_inflight(approval_id)
                except Exception as exc:
                    logging.error(
                        "[HITL] drain: ack_inflight failed for %s: %s", approval_id, exc
                    )
        if drained:
            logging.info("[HITL] drained %d approved order(s) this cycle.", drained)
        return drained

    async def _hitl_day_rollover(self, previous_day) -> None:
        """On an NY-date change, clear YESTERDAY's HITL day-notional key (N3 — never today's).

        Dormant unless HITL_ENABLED; no-op on first boot (previous_day None), so a cold start
        cannot wipe today's accumulated autonomous budget.
        """
        if previous_day is None or not get_config().HITL_ENABLED:
            return
        from core.hitl_day_notional import HitlDayNotional

        try:
            await HitlDayNotional.rollover(previous_day.isoformat())
        except Exception as exc:
            logging.error("[HITL] day-notional rollover failed: %s", exc)

    async def _hitl_symbol_pending(self, symbol: str) -> bool:
        """True if `symbol` already has a live human-approval pending for the active user.

        The per-cycle dispatch skips such a symbol (C4) to avoid a wasted ~45s analysis for a
        signal the queue would dedup. Dormant unless HITL_ENABLED.
        """
        if not get_config().HITL_ENABLED:
            return False
        from core.hitl_queue import HitlQueue

        try:
            user_id = getattr(self, "active_uid", None) or "global"
            return await HitlQueue.has_pending(symbol, user_id)
        except Exception as exc:
            logging.error("[HITL] has_pending check failed for %s: %s", symbol, exc)
            return False

    async def live_trading_loop(self):
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

        # #3389: Start-Abgleich gegen die Broker-Wahrheit, BEVOR der erste Zyklus laeuft.
        # Bis #3389 hatte der ReconciliationService keinen einzigen Produktionsaufrufer —
        # er existierte seit Epic 2.3-Pre und lief nie. Ein Zyklus, der vor dem Abgleich
        # eine Order absetzt, entscheidet auf einem Bestand, den niemand gegen den Broker
        # gehalten hat.
        #
        # Fail-soft: scheitert der Abgleich, laeuft der Handel weiter. Das ist Absicht —
        # die Sperrwirkung ist noch nicht gemessen (RECONCILIATION_BLOCK_ON_BREAK
        # default aus, Plan #3389 §8); den Handel an einem ungetesteten Abgleich
        # aufzuhaengen, waere die groessere Kapitalwirkung.
        await self._start_reconciliation()
        # #3449: unbestaetigte Order-Intents aus einem abgestuerzten Lauf beim Broker
        # abgleichen — BEVOR der erste Zyklus neu entscheidet. Nie blind nachsenden.
        await self._start_outbox_abgleich()
        # #3453: Schreibberechtigung vor dem ersten Zyklus, dann Erneuerung im selben Ring.
        await self._sichere_schreibberechtigung()
        self._starte_lease_erneuerung()

        while self.strategy_running.is_set() and not self._shutdown_event.is_set():
            z = ZyklusZustand()  # #4007: je Durchlauf frisch
            ergebnis = await self._zyklus_vorbereiten(z)
            if ergebnis is Zyklus.STOPP:
                break
            ergebnis = await self._marktzeit_pruefen(z)
            if ergebnis is Zyklus.STOPP:
                break
            if ergebnis is Zyklus.NAECHSTER:
                continue
            ergebnis = await self._markt_geschlossen_berichten(z)
            if ergebnis is Zyklus.NAECHSTER:
                continue
            ergebnis = await self._schutz_vor_konsens(z)
            if ergebnis is Zyklus.NAECHSTER:
                continue

            _ = getattr(z.local_active_strategy, "strategy_name", "unknown")
            _ = get_tracer("aaa-engine")
            try:
                await self._zyklus_kontext_aufbauen(z)
                ergebnis = await self._symbole_vorbereiten(z)
                if ergebnis is Zyklus.STOPP:
                    break
                await self._round_table_bewerten(z)
                await self._signale_ausfuehren(z)
                await self._zyklus_auswerten(z)

            except APIError as e:
                logging.error("Alpaca API Error live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(z.graph_states) if z.graph_states else 0
                    )
                await asyncio.sleep(20)
            except BrokerConnectionError as e:
                logging.error("Broker connection lost in live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(z.graph_states) if z.graph_states else 0
                    )
                await asyncio.sleep(30)
            except Exception as e:
                logging.error("Unexpected error live loop: %s", e, exc_info=True)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(z.graph_states) if z.graph_states else 0
                    )
                await asyncio.sleep(30)

            if self._shutdown_event.is_set():
                break

            await self._zyklusgrenze(z)

        logging.info("Live trading loop exited.")
