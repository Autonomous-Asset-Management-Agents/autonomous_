# core/engine/trading_loop.py
# Epic 1.4 / Issue #217 — LangGraph-Dispatch für Non-LSTM-Strategien
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Epic 2.3-Pre / PR-A — Graceful Handover via AgentRegistry
# Verantwortlichkeit: Live Trading Loop, Snapshot-Fetch, LangGraph-Task-Dispatch

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
from alpaca.common.exceptions import APIError
from alpaca.data.requests import StockSnapshotRequest

import config
from config import (  # ML-1: SIP = consolidated NBBO (MiFID II best-execution)
    BYPASS_MARKET_HOURS,
    get_config,
)
from core.cloud_logger import DecisionContext
from core.composition.root import CompositionRoot

# TODO(MOD-1): migrate BYPASS_MARKET_HOURS to RuntimeConfigState when that lands
from core.engine.equity_fallback import resolve_equity
from core.engine.hwm_store import load_position_hwm, save_position_hwm
from core.engine.loop_counters import _bump_loop_counter
from core.engine.portfolio_context import build_portfolio_context
from core.engine.time_budget import (  # #3381: Kursalter + Staffelung Agent<Symbol<Zyklus
    current_time_budget,
    is_stale_quote,
)
from core.events import SignalEvent
from core.exceptions import BrokerConnectionError
from core.position_stop import plan_position_stops
from core.sim.clock import (  # #2548: SIM_MODE → sim time; else datetime.now (byte-identical)
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


def _regime_conditioner_state_keys(market_data) -> dict:
    """#1949 (RTR-2): flag-gated "vix"/"regime" values for the SymbolEvalState seam.

    Both are now DECLARED LangGraph channels (core/orchestration/graph.py) because
    langgraph==1.0.10 silently drops undeclared input keys — the old top-level "vix"
    the loop emitted never reached any agent. To keep the dark ship byte-identical,
    the producer emits None for BOTH channels while REGIME_CONDITIONER_ENABLED is
    OFF (default): agents then observe exactly the old dropped-key behaviour
    (VIXAwareRiskAgent keeps abstaining). ON → the per-cycle MarketRegimeModel
    output (monitor_loop → current_market_data) reaches RegimeDetectionAgent (and
    VIXAwareRiskAgent — plan #1949 §12.5, documented joint consumption). Scalars
    only (§5.9). Invalid config fails safe to OFF.
    """
    try:
        # config.get_config() (not the from-import) — the same patchable seam the
        # finance-core helpers use (CODING_POLICY §2.10).
        enabled = bool(
            getattr(config.get_config(), "REGIME_CONDITIONER_ENABLED", False)
        )
    except (TypeError, ValueError):
        enabled = False
    if not enabled or not market_data:
        return {"vix": None, "regime": None}
    return {"vix": market_data.get("vix"), "regime": market_data.get("regime")}


#: #2672: the five DECLARED position-context channels of SymbolEvalState.
_POSITION_CONTEXT_CHANNELS = (
    "in_position",
    "position_qty",
    "position_avg_price",
    "unrealized_pnl",
    "position_context_confirmed",
)


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


def _implied_vol_state_keys(symbol, spot) -> dict:
    """#3038 (TRD-10): flag-gated producer for the three implied-volatility channels.

    House pattern #1949 (``_regime_conditioner_state_keys``): OFF (default) emits
    None for ALL three, without network I/O — agents observe exactly today's
    dropped-key behaviour, byte-identical.

    Thin by design. The fetch, the day cache and the previous-session reference
    live in ``core/implied_vol``; this is only the seam that hangs them into the
    per-symbol state. Never raises: an external dependency in the trading path
    must not be able to stop a cycle. On any failure the channels stay None and
    VIXAwareRiskAgent abstains (AC-5/AC-8) — no vote instead of a guessed one.

    Measured cost (RESULTS_ABRUFKOSTEN.md): 0.15 s per symbol filtered, 4.5 s for
    30 candidates = 0.5 % of a 900 s cycle, and only on the first cycle of a day.
    """
    try:
        from core.implied_vol import implied_vol_state_keys

        return implied_vol_state_keys(symbol, spot)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.warning(
            "[ImpliedVol] producer failed for %s (%s) - channels stay None, "
            "VIXAwareRiskAgent abstains.",
            symbol,
            exc,
        )
        return {
            "implied_vol": None,
            "implied_vol_reference": None,
            "implied_vol_reference_date": None,
        }


def _risk_reversal_state_keys(symbol, spot) -> dict:
    """#3095 (b): flag-gated producer for the three risk-reversal channels.

    Same house pattern as ``_implied_vol_state_keys``: OFF (default) emits None for
    ALL three, without network I/O — byte-identical. Thin seam; fetch/cache/reference
    live in ``core/options_skew``. Never raises: on any failure the channels stay
    None and UpsideSkewAgent abstains (no vote instead of a guessed one).
    """
    try:
        from core.options_skew import options_skew_state_keys

        return options_skew_state_keys(symbol, spot)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.exception(
            "[OptionsSkew] producer failed for %s (%s) - channels stay None, "
            "UpsideSkewAgent abstains.",
            symbol,
            exc,
        )
        return {
            "risk_reversal": None,
            "risk_reversal_reference": None,
            "risk_reversal_reference_date": None,
        }


def _quality_state_keys(symbol, spot, as_of) -> dict:
    """#3275: flag-gated producer for the three composite-quality channels.

    Same house pattern as ``_implied_vol_state_keys`` / ``_risk_reversal_state_keys``:
    OFF (default) emits None for ALL three, without work — byte-identical. Thin seam;
    the composite math, store and previous-session reference live in
    ``core/quality_score``. Never raises: on any failure the channels stay None and
    QualityAgent abstains (no vote instead of a guessed one).

    ``as_of`` is the evaluation datetime (state["current_time"]); the composite is read
    from the PIT feed AS OF that date (no look-ahead), so unlike IV/RR this works under
    SIM without an external corpus.
    """
    try:
        from core.quality_score import quality_state_keys

        tag = as_of.date() if hasattr(as_of, "date") else as_of
        return quality_state_keys(symbol, spot, tag)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.warning(
            "[Quality] producer failed for %s (%s) - channels stay None, "
            "QualityAgent abstains.",
            symbol,
            exc,
        )
        return {
            "quality_score": None,
            "quality_reference": None,
            "quality_reference_date": None,
        }


def _position_context_state_keys(positions_by_symbol, confirmed, symbol) -> dict:
    """#2672: flag-gated producer for the five position-context channels.

    House pattern #1949 (``_regime_conditioner_state_keys``): the channels are
    DECLARED in SymbolEvalState (langgraph==1.0.10 drops undeclared input keys),
    so the dark ship emits None for ALL of them while
    ROUND_TABLE_POSITION_CONTEXT_ENABLED is OFF (default) — agents and runner
    observe exactly today's dropped-key behaviour, byte-identical.

    ON + confirmed book: real values (a symbol absent from the book is genuinely
    flat -> False/0.0). ON + UNCONFIRMED book: position fields stay None
    ("unknown") — fail-closed, never fabricate "flat" from a broker outage.
    Invalid config fails safe to OFF.
    """
    try:
        enabled = bool(
            getattr(config.get_config(), "ROUND_TABLE_POSITION_CONTEXT_ENABLED", False)
        )
    except Exception:  # noqa: BLE001 — broken config seam must fail safe to OFF
        enabled = False
    if not enabled:
        return dict.fromkeys(_POSITION_CONTEXT_CHANNELS)
    if not confirmed:
        return {
            "in_position": None,
            "position_qty": None,
            "position_avg_price": None,
            "unrealized_pnl": None,
            "position_context_confirmed": False,
        }
    entry = (positions_by_symbol or {}).get(symbol)
    if entry is None:
        return {
            "in_position": False,
            "position_qty": 0.0,
            "position_avg_price": 0.0,
            "unrealized_pnl": 0.0,
            "position_context_confirmed": True,
        }
    return {
        "in_position": float(entry.get("qty", 0.0) or 0.0) > 0.0,
        "position_qty": float(entry.get("qty", 0.0) or 0.0),
        "position_avg_price": float(entry.get("avg_entry", 0.0) or 0.0),
        "unrealized_pnl": float(entry.get("unrealized_pnl", 0.0) or 0.0),
        "position_context_confirmed": True,
    }


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
    from core.kill_switch import kill_switch
except ImportError:  # pragma: no cover
    kill_switch = None  # type: ignore[assignment]

# RTR-1 (#1948): FREE point-in-time news producer (Google-News-RSS). Flag-FIRST
# inside the producer — NEWS_SENTIMENT_NLP_ENABLED off (default) returns None
# without any network I/O, and no state key is added (byte-identical dormant path).
try:
    from core.nlp.headlines import produce_news_headlines
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


# #2965: cooldown between per-cycle entry-time reconciles (flag ON only).
# One broker positions call per window — context: BUG-AI-105 already accepts
# an N+1 positions refetch per SIZING call, so this is comparatively cheap.
ENTRY_TIME_RECONCILE_COOLDOWN_S = 300.0


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
            None, paper=bool(getattr(get_config(), "PAPER_TRADING", True))
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
        self._lease_seit = CompositionRoot.get_instance().clock_port.time()
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
            and CompositionRoot.get_instance().clock_port.time() - float(stempel)
            < FORTSCHRITT_MAX_ALTER_SEKUNDEN
        )

    def _start_fill_stream(self) -> None:
        """Abonniert den Handelsereignis-Strom, wenn Zugangsdaten vorliegen (#3389).

        Fail-soft und still bei fehlenden Zugangsdaten: ohne Strom bleibt der periodische
        Lauf, und der ist die tragende Stufe. Ein fehlender Strom ist kein Defekt, ein
        stiller Absturz waere einer.
        """
        cfg = get_config()
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
        if not getattr(get_config(), "RECONCILIATION_ENABLED", True):
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
                        getattr(get_config(), "RECONCILIATION_INTERVAL_SECONDS", 30)
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
        cfg = get_config()
        if not getattr(cfg, "POSITION_EXIT_HWM_TRAILING_ENABLED", False):
            return None
        persist = bool(getattr(cfg, "POSITION_EXIT_HWM_PERSIST_ENABLED", False))
        prev = getattr(self, "_position_high_water_marks", None)
        if prev is None:
            # First call after boot: seed from the durable store (persist ON) or start empty.
            if persist:
                try:
                    prev = await load_position_hwm()
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
                await save_position_hwm(current)
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
        cfg = get_config()
        days = int(cfg.REENTRY_LOCKOUT_DAYS or 0)
        # #3655: the slot a LOSS stop freed stays held for hold_days trading days.
        hold_days = int(cfg.STOP_EXIT_SLOT_HOLD_DAYS or 0)
        loss_exits = {
            str(s).upper() for s in (getattr(self, "_stop_loss_exits", set()) or ())
        }
        self._stop_loss_exits = set()
        if days <= 0 and hold_days <= 0:
            return set()
        now = now or CompositionRoot.get_instance().clock_port.now()
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
            get_config(), "POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED", False
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
        now = engine_now(timezone.utc)
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

    async def _fetch_snapshots_chunked(
        self, symbols, *, per_chunk_timeout: float = 15.0
    ) -> dict:
        """Fetch snapshots for ``symbols`` in independent chunks so a single failing /
        timing-out request can no longer zero the WHOLE panel — an empty ``snapshots``
        makes ``update_lstm_rankings`` skip every symbol (``if symbol not in snapshots:
        continue``) → empty ranking → all-HOLD. Each chunk is its own
        ``StockSnapshotRequest``; a chunk that fails / times out drops only ITS symbols
        this cycle while the others still populate.

        The chunks run **concurrently** via ``asyncio.gather`` (Archon review): total
        wall-clock is bounded by ~ONE ``per_chunk_timeout``, not the sum over chunks, so
        a slow provider cannot serialise the fetch into a multi-minute loop stall.

        Logging (AGENTS.md Rule 5): a partial chunk failure is a GRACEFUL fallback (the
        other chunks still populate the panel) → **WARNING**. ``ERROR`` is reserved for a
        TOTAL outage — every chunk failed / returned nothing this cycle.

        ``SNAPSHOT_FETCH_CHUNK_SIZE`` <= 0 (or >= ``len(symbols)``) => one request,
        byte-identical to the pre-fix single-request path. Fail-safe (CODING_POLICY §5.6):
        NEVER raises; returns whatever merged successfully (possibly ``{}``)."""
        symbols = list(symbols or [])
        if not symbols:
            return {}
        try:
            size = int(getattr(get_config(), "SNAPSHOT_FETCH_CHUNK_SIZE", 100) or 0)
        except Exception:  # noqa: BLE001 — a bad flag must never break the fetch
            size = 100
        if size <= 0 or size >= len(symbols):
            chunks = [symbols]
        else:
            chunks = [symbols[i : i + size] for i in range(0, len(symbols), size)]

        async def _fetch_one(idx: int, chunk: list) -> dict:
            """One chunk. Never raises: a partial failure/timeout logs at WARNING (a
            graceful fallback — the other chunks recover the cycle) and returns {}."""
            try:
                request_params = StockSnapshotRequest(
                    symbol_or_symbols=chunk,
                    feed=config.ALPACA_DATA_FEED,
                )
                part = await asyncio.wait_for(
                    asyncio.to_thread(self.data_api.get_stock_snapshot, request_params),
                    timeout=per_chunk_timeout,
                )
                return part or {}
            except asyncio.TimeoutError:
                logging.warning(
                    "FETCH_CHUNK_TIMEOUT: snapshot chunk %d/%d (%d symbols) exceeded "
                    "%.0fs — other chunks unaffected.",
                    idx + 1,
                    len(chunks),
                    len(chunk),
                    per_chunk_timeout,
                )
                return {}
            except (
                Exception
            ) as e:  # noqa: BLE001 — one bad chunk must not zero the panel
                logging.warning(
                    "FETCH_CHUNK_ERROR: snapshot chunk %d/%d (%d symbols) failed (%s) — "
                    "other chunks unaffected.",
                    idx + 1,
                    len(chunks),
                    len(chunk),
                    e,
                )
                return {}

        # return_exceptions=True is belt-and-braces: _fetch_one already swallows every
        # error, so gather can never raise here (§5.6 — a fetch must never break a cycle).
        results = await asyncio.gather(
            *[_fetch_one(i, c) for i, c in enumerate(chunks)],
            return_exceptions=True,
        )
        merged: dict = {}
        for r in results:
            if isinstance(r, dict):
                merged.update(r)
        if not merged:
            # TOTAL outage — every chunk failed/empty this cycle. This is the only
            # ERROR-worthy case; the panel will be empty and the cycle degrades to HOLD.
            logging.error(
                "FETCH_TOTAL_OUTAGE: all %d snapshot chunk(s) returned nothing this "
                "cycle — panel empty for %d symbols.",
                len(chunks),
                len(symbols),
            )
        return merged

    async def _reconcile_specialist_coverage(self, base_watchlist=None) -> None:
        """Flag-gated: retarget the specialist registry's HIGH-PRIORITY set to
        base ∪ held positions ∪ top-N by round-table consensus_score, so the symbols we
        actually hold and the highest-conviction decisions always have a fresh report
        (Epic #1998 RPT-GOLD, sub #2630). Fail-open — never break the cycle.

        ``base_watchlist`` is captured ONCE (lazily, from the registry's startup high-priority
        set) so the union stays bounded and closed positions / stale top-N do not accumulate
        across cycles. Tests pass an explicit list. Reads config via the patchable
        ``config.get_config()`` seam; broker I/O runs off the event loop via ``asyncio.to_thread``.
        """
        cfg = config.get_config()
        if not getattr(cfg, "SPECIALIST_COVERAGE_DYNAMIC", False):
            return
        reg = self.specialist_registry
        if reg is None:
            return
        try:
            from core.engine.coverage_selector import select_specialist_coverage

            # Capture the ORIGINAL high-priority base once (before any dynamic addition).
            if base_watchlist is None:
                if getattr(self, "_specialist_base_watchlist", None) is None:
                    self._specialist_base_watchlist = list(
                        getattr(reg, "_high_priority", []) or []
                    )
                base_watchlist = self._specialist_base_watchlist

            positions: list = []
            if (
                getattr(cfg, "SPECIALIST_COVER_POSITIONS", True)
                and self.api is not None
            ):
                try:
                    # Alpaca get_all_positions is a BLOCKING HTTP call → off the event loop
                    # (same pattern as _run_position_stop_checks), never a bare sync call here.
                    positions_raw = await asyncio.to_thread(self.api.get_all_positions)
                    positions = [p.symbol for p in positions_raw]
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — degrade, never crash the cycle
                    logging.warning(
                        "Coverage: get_all_positions failed (%s) — positions skipped.",
                        exc,
                    )

            target = select_specialist_coverage(
                positions,
                list(getattr(self, "_last_round_table_state", None) or []),
                base_watchlist,
                int(getattr(cfg, "SPECIALIST_TOP_N_CONVICTION", 20)),
                bool(getattr(cfg, "SPECIALIST_COVER_POSITIONS", True)),
            )
            existing = set(getattr(reg, "_symbols", []))
            for sym in target:
                if sym not in existing:
                    reg.add_symbol(sym)
            reg.update_priority(target)
            logging.info(
                "Coverage: specialist high-priority retargeted to %d symbols "
                "(%d positions, top-%d consensus).",
                len(target),
                len(positions),
                int(getattr(cfg, "SPECIALIST_TOP_N_CONVICTION", 20)),
            )
        except Exception as exc:  # noqa: BLE001 — boot/cycle resilience
            logging.warning(
                "Coverage: reconcile failed (%s) — keeping current registry priority.",
                exc,
            )

    async def _run_closed_report_pass(
        self, local_active_strategy, symbols_to_process
    ) -> bool:
        """ONE full-universe LSTM rank/panel refresh for the market-closed report-only mode.

        Fetches snapshots for the WHOLE universe and runs the rank producer once, so the
        on-demand reports stay COMPLETE 24/7 while the market is closed. No round-table
        deep-eval, no orders. Mirrors the open-cycle fetch + rank-producer block (incl. the
        FULL_UNIVERSE 200-cap, so the closed report ranks the SAME universe as the open path).

        Returns True when the pass completed (a rank was produced, or no ranker is configured —
        a structural gap retrying cannot fix); False on a TRANSIENT failure (empty symbols /
        snapshot-fetch exception / rank exception). The caller latches ``_closed_panel_refreshed``
        only on True, so a transient failure — the full-universe snapshot fetch is a documented
        RemoteDisconnect risk — retries on the next closed wake (<=300s) instead of latching an
        empty report for the whole closed period, mirroring the open path's advance-on-success.
        """
        if not symbols_to_process:
            return False
        # Mirror the open path's universe exactly (trading_loop FULL_UNIVERSE branch): with the
        # flag OFF both cap at 200, so the report's "Platz X von N" denominator matches.
        if (
            not get_config().FULL_UNIVERSE_TRADING_ENABLED
            and len(symbols_to_process) > 200
        ):
            symbols_to_process = symbols_to_process[:200]
        current_time_utc = engine_now(timezone.utc)
        snapshots = await self._fetch_snapshots_chunked(
            symbols_to_process, per_chunk_timeout=15.0
        )
        if not snapshots:
            # Every chunk failed (a transient full outage) — retry on the next closed
            # wake rather than latch an empty report for the whole closed period.
            logging.warning(
                "Closed report pass: snapshot fetch returned nothing; retry next wake."
            )
            return False
        try:
            if hasattr(local_active_strategy, "update_lstm_rankings"):
                await local_active_strategy.update_lstm_rankings(
                    symbols_to_process,
                    snapshots,
                    self.current_market_data,
                    current_time_utc,
                )
            elif (
                get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
                and self._rank_panel_producer is not None
            ):
                await self._rank_panel_producer.update_lstm_rankings(
                    symbols_to_process,
                    snapshots,
                    self.current_market_data,
                    current_time_utc,
                )
        except Exception:  # noqa: BLE001
            logging.warning(
                "Closed report pass: panel rank failed; report rank will be an honest gap.",
                exc_info=True,
            )
            return False
        # #2630: market-closed carryover — research positions ∪ the last session's top-N consensus
        await self._reconcile_specialist_coverage()
        return True

    async def _update_live_account_equity(self, active_strategy) -> None:
        """#2978 (Epic #1891): feed the account-wide drawdown breaker on the LIVE
        path, once per cycle, BEFORE the trading_halted gate.

        The account-wide 7% portfolio-stop + progressive daily-drawdown circuit
        breaker live in ``RiskManager.update_account_equity`` — the only setter of
        ``trading_halted`` on this layer — but until now it was fed only by the Sim
        runner, leaving it structurally inert in live trading (MiFID II RTS 6
        pre-trade risk control that never fired). This mirrors
        ``simulation_runner``'s single ``update_account_equity`` call.

        Reads equity via the fail-safe house helper ``resolve_equity`` (live
        account equity, or ``DEFAULT_EQUITY`` with a WARNING) off the event loop
        via ``asyncio.to_thread`` (``api.get_account()`` is blocking). Calls the
        breaker with ``allow_unlock=False`` → halt-only: the live path can move
        ``trading_halted`` False→True but never auto-unlocks (EU AI Act Art. 14).

        Fail-safe (failure-direction: never crash the loop): ``rm is None`` → skip;
        any error → WARNING + continue. The breaker acts on exactly the instance
        (``active_strategy.risk_manager``) the halt gate reads two lines later.
        """
        rm = getattr(active_strategy, "risk_manager", None)
        if rm is None:
            return
        try:
            equity = await asyncio.to_thread(
                resolve_equity,
                getattr(self, "api", None),
                get_config().DEFAULT_EQUITY,
            )
            rm.update_account_equity(equity, allow_unlock=False)
        except (
            Exception
        ) as e:  # noqa: BLE001 — a breaker-feed error must not kill the loop
            logging.warning(
                "[AccountBreaker] live equity update failed; account-wide breaker "
                "not fed this cycle: %s",
                e,
                exc_info=True,
            )

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
        cfg = get_config()
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

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        from core.broker_stops import plan_broker_stops
        from core.contracts import OrderIntent
        from core.engine.order_executor import gateway_for
        from core.kill_switch import kill_switch

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

        heute = engine_now(timezone.utc).date()
        plan = plan_broker_stops(
            positions or [],
            stop_loss_pct=float(cfg.STOP_LOSS_PCT),
            existing_stops=liegende,
            session_date=heute,
            existing_session_date=getattr(self, "_broker_stop_session", None),
        )
        self._broker_stop_session = heute

        for order_id in plan.to_cancel:
            try:
                await asyncio.to_thread(api.cancel_order_by_id, order_id)
            except Exception as exc:
                logging.warning(
                    "[BrokerStops] Storno %s fehlgeschlagen: %s",
                    order_id,
                    exc,
                    exc_info=True,
                )

        for stop in plan.to_place:
            req = StopOrderRequest(
                symbol=stop.symbol,
                qty=stop.qty,
                side=OrderSide.SELL,
                time_in_force=(
                    TimeInForce.GTC if stop.time_in_force == "gtc" else TimeInForce.DAY
                ),
                stop_price=stop.stop_price,
                client_order_id=stop.client_order_id,
            )
            try:
                # Durch das Tor, nicht daran vorbei: ein Stop IST eine Order. Als
                # Schutz-Exit gekennzeichnet — er wird protokolliert, aber nie blockiert
                # (#3379/#3380). Sonst haette dieser PR den CH-2-Zaehler wieder erhoeht.
                await asyncio.to_thread(
                    gateway_for(api).submit,
                    OrderIntent(
                        decision_id=stop.decision_id,
                        symbol=stop.symbol,
                        side="sell",
                        qty=stop.qty,
                        intent_kind="stop",
                        is_protective_exit=True,
                        exit_kind="risk",
                        stop_price=stop.stop_price,
                        time_in_force=stop.time_in_force,
                        halted=kill_switch.is_halted(None),
                    ),
                    request=req,
                )
                logging.warning(
                    "[BrokerStops] %s: %s-Stop ueber %s @ %.2f gelegt (%s).",
                    stop.symbol,
                    stop.time_in_force.upper(),
                    stop.qty,
                    stop.stop_price,
                    stop.leg,
                )
            except Exception as exc:
                logging.error(
                    "[BrokerStops] %s: Stop konnte NICHT gelegt werden (%s) — die "
                    "Position ist ohne Broker-Schutz.",
                    stop.symbol,
                    exc,
                    exc_info=True,
                )

        if not plan.complete:
            # Ein Alarm, der die Menge und den Grund nennt — sonst weiss niemand, wie
            # gross das getragene Risiko ist.
            for symbol, grund in plan.unprotected:
                logging.error(
                    "[BrokerStops] UNGESCHUETZT: %s — %s (#3382)", symbol, grund
                )
            self._log_strategy_thought(
                f"⚠️ {len(plan.unprotected)} Position(en) ohne Broker-Stop: "
                + ", ".join(s for s, _ in plan.unprotected)
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
        plans = plan_position_stops(
            positions or [],
            trade_history or {},
            high_water_marks=high_water_marks,
            entry_times=entry_times,
            # #3317: ohne now= faellt evaluate_position_stop auf datetime.now zurueck
            # (position_stop.py:75) und misst die Haltedauer gegen die Wanduhr — im Sim die
            # falsche Uhr. Muss GEMEINSAM mit dem Stempel oben umgestellt werden: nur die
            # Messung umzustellen liefert ein negatives now-entry (auf 0 geklemmt), nur den
            # Stempel umzustellen laesst hours_held auf Sim-Datum-vs-heute explodieren.
            now=engine_now(timezone.utc),
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

    async def _run_deconcentration_and_rotation_exits(
        self, already_acted: set, now=None
    ) -> set:
        """#sell-decisions: flag-gated per-cycle exit hook — PRODUCES SELL decisions.

        A pre-consensus pass (right after ``_run_position_stop_checks``) with two
        independent, flag-gated levers that both dispatch through the verified-sound
        ``_process_signal_event`` seam and only ever REDUCE exposure:

        * **ROTATION** (``ROTATION_EXIT_ENABLED``): a held name that dropped out of the
          live LSTM top-N past hysteresis / min-hold is FULLY exited (qty 0 → the SELL
          branch fetches the whole open position). Fail-safe: rank ``None`` (symbol absent
          from the panel) → HOLD; a STALE panel (older than ``ROTATION_PANEL_MAX_AGE_DAYS``)
          skips the lever entirely (date-normalized — never ``date − datetime``).
        * **TRIM** (``DECONCENTRATION_TRIM_ENABLED``): an overweight name is PARTIALLY
          reduced by a dollar-fraction of its market value
          (``held_qty * min(1, |adjustment|/market_value)`` — no price division,
          ``market_value <= 0`` skipped, clamped to held so it can never oversell).

        Both SELLs carry ``triggered_by_stop=False`` (a rotation/trim is NOT a price
        stop → no WORM-audit contamination); execution is unaffected (that flag gates
        no order path — churn is BUY-only, the compliance exit-exemption keys on
        side + held_qty). Rotation runs first, then trim on what remains; a symbol is
        acted on at most once per cycle (shared ``already_acted`` set, unioned with this
        hook's own exits). Returns the exited-symbols set so the caller excludes them
        from the consensus (the round table cannot re-buy a just-sold name).

        Flags OFF ⇒ byte-identical no-op (early return; no panel/recommender call, no
        SELL). Fail-safe: each lever is wrapped so a lever error is logged and swallowed
        — a hook failure never kills the loop.
        """
        exited: set = set()
        cfg = get_config()
        # #3180 Consensus Retention Gate — an OPINION exit (rotation / trim / overflow) is
        # SUPPRESSED when the live round-table blend-consensus still rates the held name
        # highly. Default OFF (CONSENSUS_RETENTION_THRESHOLD<=0) ⇒ byte-identical; fail-open.
        from core.consensus_retention import consensus_retention_veto

        rotation_on = bool(getattr(cfg, "ROTATION_EXIT_ENABLED", False))
        trim_on = bool(getattr(cfg, "DECONCENTRATION_TRIM_ENABLED", False))
        if not rotation_on and not trim_on:
            return exited  # byte-identical no-op

        already_acted = set(already_acted or set())
        if now is None:
            now = CompositionRoot.get_instance().clock_port.now()

        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        if pm is None:
            return exited

        # ---- Lever A: ROTATION (thesis-invalidation first) ------------------
        if rotation_on:
            try:
                from core.report.lstm_panel_store import (
                    active_cross_section,
                    cross_section_standing,
                    get_store,
                )

                store = get_store()
                snap = store.latest_snapshot_date()  # a datetime.date (or None)
                max_age = int(getattr(cfg, "ROTATION_PANEL_MAX_AGE_DAYS", 3))
                # Refuse a STALE / empty panel — date-normalized (never date − datetime).
                if snap is not None and (now.date() - snap).days <= max_age:
                    try:
                        from core.strategies.lstm_strategy import LSTM_DYNAMIC_TOP_N
                    except Exception:  # pragma: no cover — constant fallback
                        LSTM_DYNAMIC_TOP_N = 10
                    min_hold_days = float(getattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 5.0))
                    hysteresis = float(
                        getattr(cfg, "SMART_EXIT_EXIT_RANK_HYSTERESIS", 3.0)
                    )
                    # #2680: the ONE ranking seam — the SELL trigger must read the SAME
                    # table the BUY vote reads. This lever was the second half of the
                    # 2026-08-05 IVZ churn: the raw point-in-time rank collapsed from
                    # top-10 to 242/501 within three hours and rotated the name straight
                    # back out. Flag off (default) → cross_section_at, byte-identical.
                    xsec = active_cross_section(store, now)
                    await asyncio.to_thread(pm.refresh_positions)
                    # Per-cycle cap (2026-07-28 incident): bound how many FULL exits this
                    # lever fires in ONE cycle so a ranking shift cannot flush the whole
                    # book at once. FLOORS to 1 (owner directive 2026-08-03): a value <=0 is
                    # NOT "unlimited" — that convention was a footgun (zeroing the cap re-opened
                    # the single-cycle flush). Disable rotation via ROTATION_EXIT_ENABLED, never
                    # by zeroing the cap.
                    rot_cap = int(getattr(cfg, "ROTATION_MAX_EXITS_PER_CYCLE", 1) or 1)
                    if rot_cap < 1:
                        rot_cap = 1
                    # Per-SESSION ceiling (2026-08-03): the per-cycle cap alone does NOT bound
                    # a whole-book flush — rotation exits are risk-reducing SELLs (exempt from
                    # the daily-trades cap) and the loop runs ~390 cycles/session, so a sustained
                    # ranking shift would drain the book one name per cycle over the day. This
                    # day-scoped counter caps CUMULATIVE rotation exits per trading day; it
                    # resets on date rollover. Protective stop-losses are unaffected. <=0 =
                    # unlimited (byte-identical rollback). Instance-attr (lazy: the mixin may be
                    # built via __new__, so read defensively and never assume __init__ ran).
                    # (#2886 audit note: the getattr fallback previously said 3 — stale
                    # since #2840 moved the config default to 2; the literal only ever
                    # fires if the config field is missing, but it must not contradict it.)
                    session_cap = int(
                        getattr(cfg, "ROTATION_MAX_EXITS_PER_SESSION", 2) or 0
                    )
                    _today = now.date()
                    if getattr(self, "_rotation_session_date", None) != _today:
                        self._rotation_session_date = _today
                        self._rotation_exits_session = 0
                    rot_exits = 0
                    for sym, score in list(getattr(pm, "_position_scores", {}).items()):
                        if sym in already_acted or sym in exited:
                            continue
                        _pct, rank, n = cross_section_standing(xsec, sym)
                        if rank is None:
                            continue  # fail-safe HOLD — never a false rotation sell
                        in_top_n = rank <= LSTM_DYNAMIC_TOP_N
                        days_held = float(getattr(score, "days_held", 0) or 0)
                        min_hold_ok = days_held >= min_hold_days
                        rank_collapsed = rank > LSTM_DYNAMIC_TOP_N * hysteresis
                        # #2711 hard gate: rank collapse may not override min-hold.
                        _hard_gate = bool(
                            getattr(cfg, "ROTATION_MIN_HOLD_HARD_GATE", True)
                        )
                        if (
                            not in_top_n
                            and rank > LSTM_DYNAMIC_TOP_N
                            and (min_hold_ok or (rank_collapsed and not _hard_gate))
                        ):
                            # #3180: retain a name the live round table still rates highly.
                            # Checked BEFORE the cap so a retained name never consumes a slot.
                            if consensus_retention_veto(sym, "rotation", pm):
                                logging.info(
                                    "[Rotation] %s: exit SUPPRESSED — live round-table "
                                    "consensus still retains the name (#3180 gate)",
                                    sym,
                                )
                                continue
                            if rot_cap > 0 and rot_exits >= rot_cap:
                                logging.warning(
                                    "[Rotation] per-cycle cap %d reached — deferring "
                                    "further eligible exits to the next cycle.",
                                    rot_cap,
                                )
                                break
                            if session_cap > 0 and (
                                int(getattr(self, "_rotation_exits_session", 0) or 0)
                                >= session_cap
                            ):
                                logging.warning(
                                    "[Rotation] per-session cap %d reached — no further "
                                    "rotation exits today (resets next trading day). "
                                    "Protective stop-losses are unaffected.",
                                    session_cap,
                                )
                                break
                            ctx = DecisionContext(
                                symbol=sym,
                                action="SELL",
                                current_price=float(
                                    getattr(score, "current_price", 0.0) or 0.0
                                ),
                                in_position=True,
                                position_qty=float(getattr(score, "qty", 0.0) or 0.0),
                                position_avg_price=float(
                                    getattr(score, "avg_entry", 0.0) or 0.0
                                ),
                                triggered_by_stop=False,
                                portfolio_reason=(
                                    f"rotation: dropped to rank {rank}/{n} "
                                    f"(out of top-{LSTM_DYNAMIC_TOP_N})"
                                ),
                            )
                            event = SignalEvent(
                                symbol=sym,
                                action="SELL",
                                decision_context=ctx,
                                suggested_quantity=0.0,  # full exit
                            )
                            await self._process_signal_event(event)
                            exited.add(sym)
                            rot_exits += 1
                            self._rotation_exits_session = (
                                int(getattr(self, "_rotation_exits_session", 0) or 0)
                                + 1
                            )
                            try:
                                pm.record_trade(sym, "sell")
                            except Exception:  # noqa: BLE001 — bookkeeping only
                                # CLAUDE.md §5.6: never swallow silently. This is
                                # cooldown/freshness bookkeeping AFTER the SELL was
                                # already dispatched — the exit stands; only the
                                # anti-churn cooldown may be missed, so log + continue.
                                logging.warning(
                                    "[Rotation] failed to record trade for %s "
                                    "(cooldown bookkeeping; SELL already dispatched)",
                                    sym,
                                    exc_info=True,
                                )
                            logging.info(
                                "[Rotation] %s: rank %s/%s out of top-%s → "
                                "full SELL dispatched",
                                sym,
                                rank,
                                n,
                                LSTM_DYNAMIC_TOP_N,
                            )

                    # #2886 (flag-gated): book_overflow unwind — if the book holds MORE
                    # than the cap (account-switch incident 14.08.: 12/10), wind the
                    # excess down through THIS same lever: weakest rank first, min-hold
                    # binding, sharing the per-cycle and per-session budgets above so
                    # rotation + unwind together can never exceed the incident caps.
                    # Panel freshness is already guaranteed here (same guarded block).
                    if bool(getattr(cfg, "BOOK_CAP_ENFORCEMENT_ENABLED", False)):
                        from core.engine.book_overflow import select_overflow_unwind

                        _held = dict(getattr(pm, "_position_scores", {}) or {})
                        _cap = int(getattr(pm, "max_positions", 10) or 10)
                        if len(_held) > _cap:
                            _cycle_left = max(0, rot_cap - rot_exits)
                            _sess_used = int(
                                getattr(self, "_rotation_exits_session", 0) or 0
                            )
                            _sess_left = (
                                max(0, session_cap - _sess_used)
                                if session_cap > 0
                                else _cycle_left
                            )
                            _budget = min(_cycle_left, _sess_left)
                            for sym in select_overflow_unwind(
                                {
                                    s: sc
                                    for s, sc in _held.items()
                                    if s not in already_acted and s not in exited
                                },
                                rank_of=lambda s: cross_section_standing(xsec, s)[1],
                                max_positions=_cap,
                                min_hold_days=min_hold_days,
                                budget=_budget,
                            ):
                                score = _held[sym]
                                # #3180: overflow unwind is an OPINION rank exit — retain a
                                # name the live round table still rates highly.
                                if consensus_retention_veto(sym, "book_overflow", pm):
                                    logging.info(
                                        "[BookOverflow] %s: unwind SUPPRESSED — live "
                                        "round-table consensus retains the name (#3180)",
                                        sym,
                                    )
                                    continue
                                ctx = DecisionContext(
                                    symbol=sym,
                                    action="SELL",
                                    current_price=float(
                                        getattr(score, "current_price", 0.0) or 0.0
                                    ),
                                    in_position=True,
                                    position_qty=float(
                                        getattr(score, "qty", 0.0) or 0.0
                                    ),
                                    position_avg_price=float(
                                        getattr(score, "avg_entry", 0.0) or 0.0
                                    ),
                                    triggered_by_stop=False,
                                    portfolio_reason=(
                                        f"book_overflow_unwind: {len(_held)}/{_cap} "
                                        f"positions held — orderly wind-down (#2886)"
                                    ),
                                )
                                await self._process_signal_event(
                                    SignalEvent(
                                        symbol=sym,
                                        action="SELL",
                                        decision_context=ctx,
                                        suggested_quantity=0.0,  # full exit
                                    )
                                )
                                exited.add(sym)
                                self._rotation_exits_session = (
                                    int(
                                        getattr(self, "_rotation_exits_session", 0) or 0
                                    )
                                    + 1
                                )
                                try:
                                    pm.record_trade(sym, "sell")
                                except Exception:  # noqa: BLE001 — bookkeeping only
                                    logging.warning(
                                        "[BookOverflow] failed to record trade for %s "
                                        "(cooldown bookkeeping; SELL already "
                                        "dispatched)",
                                        sym,
                                        exc_info=True,
                                    )
                                logging.warning(
                                    "[BookOverflow] %s: unwind SELL dispatched "
                                    "(%d/%d held) — #2886",
                                    sym,
                                    len(_held),
                                    _cap,
                                )
            except (
                Exception
            ) as e:  # noqa: BLE001 — a lever failure must not kill the loop
                logging.warning("[Rotation] exit lever failed: %s", e, exc_info=True)

        # ---- Lever B: TRIM (de-concentrate what remains) --------------------
        if trim_on:
            try:
                acted = already_acted | exited
                # #2714: day-scoped session cap for the TRIM lever (rotation had one).
                _trim_cap = int(getattr(cfg, "TRIM_MAX_EXITS_PER_SESSION", 3) or 0)
                _t_today = now.date()
                if getattr(self, "_trim_session_date", None) != _t_today:
                    self._trim_session_date = _t_today
                    self._trim_exits_session = 0
                # Fallback MUST equal the config default (#2784): a $50 fallback would
                # re-impose the floor that blocked every small account, and it fires
                # silently whenever cfg is a partial namespace / test double.
                min_order_value = float(getattr(cfg, "MIN_ORDER_VALUE_USD", 1.0))
                recs = await asyncio.to_thread(pm.get_rebalance_recommendations)
                scores = getattr(pm, "_position_scores", {})
                for rec in recs or ():
                    if (
                        _trim_cap > 0
                        and int(getattr(self, "_trim_exits_session", 0) or 0)
                        >= _trim_cap
                    ):
                        logging.warning(
                            "[Trim] per-session cap %d reached - no further trims "
                            "today (resets next trading day). Protective stops are "
                            "unaffected (#2714).",
                            _trim_cap,
                        )
                        break
                    if rec.get("action") != "REDUCE":
                        continue
                    sym = rec.get("symbol")
                    if sym in acted or sym in exited:
                        continue
                    mv = float(rec.get("market_value", 0.0) or 0.0)
                    if mv <= 0:
                        continue  # no price division; skip on non-positive market value
                    score = scores.get(sym)
                    held_qty = (
                        float(getattr(score, "qty", 0.0) or 0.0) if score else 0.0
                    )
                    if held_qty <= 0:
                        continue
                    adjustment = abs(float(rec.get("adjustment_value", 0.0) or 0.0))
                    # clamp to held → can never oversell (executor honors a partial verbatim)
                    trim_qty = held_qty * min(1.0, adjustment / mv)
                    if trim_qty <= 0:
                        continue
                    # Dust-skip only where a price is safely available.
                    price = (
                        float(getattr(score, "current_price", 0.0) or 0.0)
                        if score
                        else 0.0
                    )
                    if price > 0 and trim_qty * price < min_order_value:
                        continue
                    # #3180: trim must be consensus-aware like take-profit (owner directive)
                    # — retain a name the live round table still rates highly.
                    if consensus_retention_veto(sym, "trim", pm):
                        logging.info(
                            "[Trim] %s: trim SUPPRESSED — live round-table consensus "
                            "still retains the name (#3180 gate)",
                            sym,
                        )
                        continue
                    ctx = DecisionContext(
                        symbol=sym,
                        action="SELL",
                        current_price=price,
                        in_position=True,
                        position_qty=held_qty,
                        position_avg_price=(
                            float(getattr(score, "avg_entry", 0.0) or 0.0)
                            if score
                            else 0.0
                        ),
                        triggered_by_stop=False,
                        portfolio_reason=(
                            f"deconcentration_trim: drift "
                            f"{rec.get('drift_pct', 0.0):+.1f}% → target"
                        ),
                    )
                    event = SignalEvent(
                        symbol=sym,
                        action="SELL",
                        decision_context=ctx,
                        suggested_quantity=trim_qty,  # partial
                    )
                    await self._process_signal_event(event)
                    exited.add(sym)
                    # #3604: a trim is a PARTIAL exit - never a re-entry lockout.
                    self._reentry_partial_exits = getattr(
                        self, "_reentry_partial_exits", set()
                    ) | {sym}
                    try:
                        self._trim_exits_session = (
                            int(getattr(self, "_trim_exits_session", 0) or 0) + 1
                        )
                        pm.record_trade(sym, "sell")
                    except Exception:  # noqa: BLE001 — bookkeeping only
                        # CLAUDE.md §5.6: never swallow silently. Cooldown/freshness
                        # bookkeeping AFTER the SELL was already dispatched — the exit
                        # stands; only the anti-churn cooldown may be missed → log.
                        logging.warning(
                            "[Trim] failed to record trade for %s "
                            "(cooldown bookkeeping; SELL already dispatched)",
                            sym,
                            exc_info=True,
                        )
                    logging.info(
                        "[Trim] %s: trim %.4f of %.4f held (mv=%.2f adj=%.2f) → "
                        "partial SELL",
                        sym,
                        trim_qty,
                        held_qty,
                        mv,
                        adjustment,
                    )
            except (
                Exception
            ) as e:  # noqa: BLE001 — a lever failure must not kill the loop
                logging.warning(
                    "[Trim] de-concentration lever failed: %s", e, exc_info=True
                )

        return exited

    async def _warm_lstm_bar_cache(self, strat) -> None:
        """LSTM-panel-starvation fix (Part 2) — warm the daily-bar cache ONCE per
        trading day, OFF the hot path, so ``update_lstm_rankings``' per-symbol
        ``get_data`` reads become cache hits instead of a ~500-symbol live fetch
        storm that rate-limits the free IEX feed and empties the panel (-> all-HOLD).

        Flag-gated (``LSTM_BAR_STORE_WARMUP_ENABLED``) and fully fail-safe — a
        warm-up failure must NEVER break the loop; the per-symbol fetch path stays
        intact. The blocking batched fetch runs in a thread executor so it never
        blocks the event loop. This only WRITES the cache the read path already
        reads — ``update_lstm_rankings``' read loop is untouched.
        """
        try:
            if not get_config().LSTM_BAR_STORE_WARMUP_ENABLED:
                return
            if strat is None:
                return
            symbols = list(getattr(strat, "symbols", None) or [])
            if not symbols:
                return
            provider = getattr(strat, "data_provider", None) or getattr(
                self, "data_provider", None
            )
            if provider is None or not hasattr(provider, "warm_universe_cache"):
                return
            # SAME ``days`` _get_torch_prediction passes to get_data
            # (lstm_strategy.py:279) so the warmed cache keys match the per-cycle
            # read keys exactly.
            from core.strategies.lstm_strategy import SEQUENCE_LENGTH

            seq_len = getattr(strat, "sequence_length", None) or SEQUENCE_LENGTH
            days = seq_len + 200
            now = CompositionRoot.get_instance().clock_port.now()
            loop = asyncio.get_running_loop()
            warmed = await loop.run_in_executor(
                None, provider.warm_universe_cache, symbols, now, days
            )
            logging.info(
                "LSTM bar-store warm-up: %s/%d symbols warmed (days=%d).",
                warmed,
                len(symbols),
                days,
            )
        except Exception:  # noqa: BLE001 — warm-up must never break the trading loop
            logging.warning(
                "LSTM bar-store warm-up skipped (non-fatal).", exc_info=True
            )

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
            # #3453: Fehlt die Berechtigung (z. B. nach einem Absturz hielt die alte Instanz
            # sie noch bis zu 60 s), wird sie bei jedem Zyklusbeginn erneut versucht.
            await self._sichere_schreibberechtigung()
            # Calendar day check in NY Timezone (FIND-01)
            ny_date_rolled = False
            try:
                ny_tz = pytz.timezone("America/New_York")
                current_ny_date = engine_now(ny_tz).date()
                if getattr(self, "_last_trading_day", None) != current_ny_date:
                    ny_date_rolled = True
                    previous_trading_day = getattr(self, "_last_trading_day", None)
                    self._last_trading_day = current_ny_date
                    if (
                        hasattr(self, "compliance_guardian")
                        and self.compliance_guardian is not None
                    ):
                        self.compliance_guardian.reset_daily_limit()
                        logging.info(
                            "Engine: Daily limit reset for new NY calendar day."
                        )
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
            if kill_switch is not None and kill_switch.is_halted():
                logging.error("Kill Switch is HALTED. Stopping engine loop.")
                self._shutdown_event.set()
                self.strategy_running.clear()
                break

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
            cycle_market_closed = False
            # F3: True only after a successful get_clock. A clock-read failure leaves
            # cycle_market_closed False too — it must NOT be mistaken for a confirmed open
            # (which would re-arm the once-per-closed-period report latch mid-closure).
            clock_ok = False
            try:
                clock = await asyncio.to_thread(self.api.get_clock)
                clock_ok = True
                # PR B (fail-safe): cache market-open so /engine-diagnostics can read
                # it WITHOUT a live broker call. Pure observation — never gates flow.
                try:
                    self._last_market_open = bool(clock.is_open)
                except Exception:
                    pass
                if not clock.is_open and not BYPASS_MARKET_HOURS:
                    cycle_market_closed = True
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
                break

            # Drain HITL-approved orders every cycle (PR-0a-ii-5b / C3). After the kill-switch
            # + market-hours checks above, so approvals execute only in a live, open cycle.
            # Dormant unless HITL_ENABLED. INC-6: the closed-market cycle no longer
            # `continue`s, so gate the drain explicitly — a human-approved order is an
            # ORDER, hence market-gated exactly like the autonomous path.
            # Re-arm the once-per-closed-period report pass ONLY on a CONFIRMED open (F3:
            # gate on clock_ok + is_open, not `not cycle_market_closed`, so a transient
            # clock-read blip mid-closure never re-arms → never triggers a 2nd panel pass).
            if clock_ok and clock.is_open:
                self._closed_panel_refreshed = False
            if not cycle_market_closed:
                await self._drain_hitl_approvals()

            local_active_strategy = None
            symbols_to_process = []

            with self.strategy_lock:
                if self.active_strategy:
                    local_active_strategy = self.active_strategy
                    symbols_to_process = list(self.active_strategy.symbols)

            if not local_active_strategy:
                await asyncio.sleep(5)
                continue

            # #2046: durable entry-time on desktop. The #2042 reconcile hook runs only on the
            # tenant path (order_executor), which the desktop/OSS fallback never enters — so
            # days_held would stay 0. Reconcile the active strategy's PM once/session. It MUST run
            # HERE — BEFORE the market-closed report-only `continue` below — otherwise a restart
            # while the market is closed never reconciles, leaving EVERY position at "0 days" until
            # the next open. Read-only vs the broker + once/session-guarded → safe + idempotent on
            # both the open and the closed path.
            await self._reconcile_active_strategy_entry_time()

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
                cycle_market_closed,
                get_config().MARKET_CLOSED_REPORT_ONLY,
                BYPASS_MARKET_HOURS,
            ):
                if should_refresh_closed_panel(
                    getattr(self, "_closed_panel_refreshed", False)
                ):
                    # F1: latch (and log) ONLY when the pass actually produced a rank — a
                    # transient full-universe fetch failure (RemoteDisconnect) must retry on
                    # the next closed wake, not latch an empty report for the whole closure.
                    produced = await self._run_closed_report_pass(
                        local_active_strategy, symbols_to_process
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
                    "timestamp": CompositionRoot.get_instance().clock_port.time(),
                }
                _sto = None
                try:
                    _no = getattr(clock, "next_open", None)
                    if _no is not None:
                        _sto = float(
                            (
                                _no - CompositionRoot.get_instance().clock_port.now()
                            ).total_seconds()
                        )
                except Exception:
                    _sto = None
                await asyncio.sleep(closed_idle_seconds(_sto))
                continue
            # ------------------------------------------------------------------------------

            # #2978: feed the account-wide drawdown breaker with live equity BEFORE
            # the halt gate below, so the 7% portfolio-stop / daily circuit breaker
            # can latch trading_halted in THIS cycle (halt-only, allow_unlock=False).
            await self._update_live_account_equity(local_active_strategy)

            rm = getattr(local_active_strategy, "risk_manager", None)
            _halted = bool(rm and rm.trading_halted)

            # #3380 (ARC-E1.4): Ein Halt stoppt neue EINSTIEGE — nicht den Schutz
            # offener Positionen. Frueher stand hier ein `continue`, das die
            # Positions-Stops uebersprang: Solange der Halt stand, hatte eine offene
            # Position keine Schwelle mehr, und niemand sah es. Genau der Zustand, in
            # dem Schutz am noetigsten ist.
            #
            # Die Reihenfolge allein genuegt nicht: Der Stop-SELL laeuft danach in das
            # Kill-Switch-Tor des Order-Pfades (order_executor.py, `check_halt`), weil
            # risk_manager.py:751 den Halt setzt und im selben Block `:754` den
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
                logging.warning(
                    "[BrokerStops] Pflege fehlgeschlagen: %s", e, exc_info=True
                )

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
                await asyncio.sleep(60)
                continue

            if stopped_symbols:
                symbols_to_process = [
                    s for s in symbols_to_process if s not in stopped_symbols
                ]

            # #sell-decisions: flag-gated per-cycle exit hook (ROTATION + TRIM). Runs
            # AFTER the price-stop pass and shares its acted-set (a stopped name is never
            # also rotated/trimmed); its exited names are excluded from the consensus so
            # the round table cannot re-buy a just-sold position. Fail-safe: a hook
            # failure is logged and swallowed — it must never kill the loop.
            try:
                exited_symbols = await self._run_deconcentration_and_rotation_exits(
                    already_acted=stopped_symbols,
                    now=CompositionRoot.get_instance().clock_port.now(),
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
                symbols_to_process = [
                    s for s in symbols_to_process if s not in exited_symbols
                ]

            # #3604 (was #2554): keep FULLY exited names out of BUY re-entry for
            # REENTRY_LOCKOUT_DAYS trading days - the churn-carousel brake the BUY-side
            # cooldowns (which never key on an exit) don't provide.
            reentry_locked = self._stopout_reentry_locked(
                set(stopped_symbols or ()) | set(exited_symbols or ()),
                now=CompositionRoot.get_instance().clock_port.now(),
            )
            if reentry_locked:
                # #3604: a locked candidate is dropped BEFORE the consensus, so no
                # decision record exists - stamp the outcome badge so the Decisions
                # page shows WHY the name was not bought (audit reason).
                try:
                    from core.engine.order_executor import _rec_outcome

                    _store = getattr(self, "_reentry_store", None)
                    for _sym in symbols_to_process:
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
                except (
                    Exception
                ):  # noqa: BLE001 - observation must never alter the flow
                    logging.warning(
                        "[ReentryLockout] outcome badge failed.", exc_info=True
                    )
                symbols_to_process = [
                    s for s in symbols_to_process if s not in reentry_locked
                ]

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
                current_time_utc = engine_now(timezone.utc)
                # Phase B: this 200-truncation is a slice of an UNRANKED, scanner-ordered
                # list. With the full universe it would silently make "the whole universe"
                # mean "whichever 200 arrived first" — so it must not survive when the
                # flag is ON. The fan-out it incidentally bounded is replaced by the
                # ADR-FU01 semaphore below, never simply dropped. Flag OFF -> identical
                # truncation, identical constant, byte-identical behaviour.
                if not get_config().FULL_UNIVERSE_TRADING_ENABLED:
                    if len(symbols_to_process) > 200:
                        symbols_to_process = symbols_to_process[:200]

                logging.info(
                    f"Engine: Processing {len(symbols_to_process)} symbols: {symbols_to_process[:5]}..."
                )

                logging.warning(
                    "FETCH_START: Requesting snapshots for %s...",
                    symbols_to_process[:5],
                )
                snapshots = await self._fetch_snapshots_chunked(
                    symbols_to_process, per_chunk_timeout=15.0
                )
                t_data_fetched = time.perf_counter()
                logging.warning("FETCH_SUCCESS: Fetched %d snapshots.", len(snapshots))

                if hasattr(local_active_strategy, "update_lstm_rankings"):
                    await local_active_strategy.update_lstm_rankings(
                        symbols_to_process,
                        snapshots,
                        self.current_market_data,
                        current_time_utc,
                    )
                elif (
                    get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
                    and self._rank_panel_producer is not None
                ):
                    # The keystone of the auditable report: produce the cross-section
                    # panel even though the ACTIVE strategy cannot.
                    #
                    # The panel's only writer is LSTMDynamic.update_lstm_rankings, and
                    # ACTIVE_STRATEGY defaults to RLAgent — so the rank the report's
                    # recommendation LEADS WITH renders "Platz n/a von n/a" for every
                    # symbol in the shipped build. Not an edge case: the default.
                    #
                    # Dormancy is structural, not preserved: this `elif` can only run
                    # where the `if` above is FALSE — i.e. where zero code runs today.
                    # There is no old behaviour at this site to keep byte-identical.
                    #
                    # The producer RANKS, it never trades: run_for_symbol is never
                    # called on it, it is registered set_active=False so get_active()
                    # can never return it, and its _bought_this_window /
                    # high_water_marks / _entry_time stay empty by construction.
                    #
                    # Failure here must never cost a cycle: the panel is telemetry for
                    # the report, and the trading path below does not read it.
                    # Increment 2: re-rank the full universe at most once per
                    # ROUND_TABLE_RANK_REFRESH_MINUTES (0 = every cycle, byte-identical). The panel
                    # store retains the last ranking between refreshes, so the funnel keeps reading it.
                    from core.engine.rank_funnel import should_refresh_rank

                    _rank_now = time.monotonic()
                    if should_refresh_rank(
                        getattr(self, "_last_rank_refresh", None),
                        _rank_now,
                        get_config().ROUND_TABLE_RANK_REFRESH_MINUTES,
                    ):
                        try:
                            await self._rank_panel_producer.update_lstm_rankings(
                                symbols_to_process,
                                snapshots,
                                self.current_market_data,
                                current_time_utc,
                            )
                            # advance the timer only on SUCCESS → a failed rank retries next cycle
                            # rather than leaving the panel stale for a full interval.
                            self._last_rank_refresh = _rank_now
                        except (
                            Exception
                        ):  # noqa: BLE001 — a ranker must not break trading
                            logging.warning(
                                "Rank-panel producer failed this cycle — the report's rank "
                                "will be an honest gap; trading is unaffected.",
                                exc_info=True,
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

                # #3349: Earnings-Guard cache producer. The guard only READS the daily
                # cache; without this it stays cold and the guard is inert whatever the
                # setting says. Armed-only, never under SIM_MODE (no network in a replay),
                # single-flight, and OFF the cycle: a first-of-day fill of a large
                # universe takes minutes (EDGAR fair-access pacing) and must not hold
                # up trading. Until it lands, missing symbols read as cold ⇒ BUY proceeds.
                _eg_cfg = get_config()
                if getattr(_eg_cfg, "EARNINGS_GUARD_ENABLED", False) and not getattr(
                    _eg_cfg, "SIM_MODE", False
                ):
                    _eg_task = getattr(self, "_earnings_cache_task", None)
                    if _eg_task is None or _eg_task.done():
                        if _eg_task is not None and not _eg_task.cancelled():
                            _eg_exc = _eg_task.exception()
                            if _eg_exc is not None:
                                logger.warning(
                                    "EarningsGuard cache refresh failed (%s) — "
                                    "uncached symbols stay cold (BUYs proceed).",
                                    _eg_exc,
                                    exc_info=_eg_exc,
                                )
                        from core.engine.earnings_guard import (
                            ensure_fresh_earnings_cache,
                        )

                        self._earnings_cache_task = asyncio.create_task(
                            asyncio.to_thread(
                                ensure_fresh_earnings_cache,
                                list(symbols_to_process),
                                current_time_utc,
                            )
                        )

                # --- #3361: regime-beyond-VIX signal — refreshed at most once per day, and
                # ONLY while the throttle is armed (dark default ⇒ no fetch, byte-identical).
                # Never under SIM_MODE (a replay must not overwrite the live cache). Runs in
                # a thread; any failure is a WARNING and the order path simply fails open.
                try:
                    _rcfg = get_config()
                    if getattr(_rcfg, "REGIME_THROTTLE_ENABLED", False) and not getattr(
                        _rcfg, "SIM_MODE", False
                    ):
                        from core.engine.regime_signal import (
                            ensure_fresh_state,
                            provider_fetch,
                        )

                        _rnow = engine_now(timezone.utc)
                        await asyncio.to_thread(
                            ensure_fresh_state,
                            provider_fetch(self.data_provider, _rnow),
                            _rnow.date(),
                        )
                except Exception as _regime_exc:  # noqa: BLE001
                    logging.warning(
                        "RegimeSignal refresh failed (%s) — throttle fails open.",
                        _regime_exc,
                        exc_info=True,
                    )

                # --- GAP9: ONE ComplianceGatekeeper portfolio snapshot per cycle ---
                # ARMED by default (GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=True since #1962).
                # The snapshot is built ONCE here (not per symbol) and injected into every
                # symbol's state, so all parallel evaluations share one consistent portfolio
                # view. When the flag is OFF, the runner sees an empty context = today's
                # byte-identical behaviour. build_portfolio_context never raises (fail-open to None).
                _portfolio_context = None
                if get_config().GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED:
                    _portfolio_context = await build_portfolio_context(
                        getattr(self, "api", None),
                        getattr(self, "compliance_guardian", None),
                    )

                # #2672: ONE authoritative broker-position snapshot per cycle — shared by the
                # rank funnel below AND the position-context channels of every _symbol_state.
                # Deliberately OUTSIDE any feature gate (Archon audit Critical 1): the old
                # funnel-local fetch sat inside `if _top_k > 0` and zeroing that GPU tuning
                # flag would have silently starved every consumer of position truth.
                (
                    _positions_by_symbol,
                    _position_confirmed,
                ) = await _fetch_position_snapshot(getattr(self, "api", None))
                # #3604: full-exit witness for the re-entry lockout (SELL vote / manual).
                try:
                    self._note_position_snapshot(
                        _positions_by_symbol, _position_confirmed
                    )
                except Exception:  # noqa: BLE001 - observation only
                    logging.warning(
                        "[ReentryLockout] snapshot note failed.", exc_info=True
                    )

                # Round-table-v2 rank-cache funnel: cap the deep-eval set to [holdings ∪ top-K
                # LSTM-ranked] so the ~500-symbol round-table graph does not saturate the GPU every
                # cycle. ROUND_TABLE_TOP_K_EVAL<=0 → no-op → byte-identical (default is 30 = ACTIVE,
                # since #2781; earlier revisions claimed "<=0 (default)" and then "20" — both went
                # stale, #2672 / #3261). It falls back to
                # the FULL universe on ANY doubt — holdings unconfirmed (fetch failed / no api), or a
                # cold / thin / STALE panel — so a held position is never dropped from the exit eval and
                # the engine never keeps trading a shrunken set on an outdated ranking.
                _top_k = int(get_config().ROUND_TABLE_TOP_K_EVAL or 0)
                if _top_k > 0:
                    try:
                        from core.engine.rank_funnel import apply_rank_cache_funnel
                        from core.report.lstm_panel_store import (
                            active_cross_section,
                            get_store,
                        )

                        # Holdings from the shared #2672 snapshot: funnel ONLY with a
                        # confirmed book; any doubt → None → skip (full universe).
                        _held = (
                            set(_positions_by_symbol) if _position_confirmed else None
                        )

                        # Freshness: refuse a stale panel (producer stopped) → full universe + WARN.
                        _store = get_store()
                        _latest = _store.latest_snapshot_date()
                        _max_age = int(
                            get_config().ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS or 0
                        )
                        # Freshness + read AS OF the ENGINE clock (engine_now → sim time under
                        # SIM_MODE, else datetime.now — byte-identical in live). Must match the
                        # clock the producer stamps the snapshot with (current_time_utc); using
                        # the wall clock made the panel look ~years stale in SIM, so the funnel
                        # silently fell back to the full universe and could not be evaluated.
                        _stale = (
                            _latest is None
                            or (current_time_utc.date() - _latest).days > _max_age
                        )

                        if _held is not None and not _stale:
                            # #2680: the ONE ranking seam (was an unconditional
                            # cross_section_ma — flag-gated now, so the cloud stays
                            # byte-identical and the whole pipeline shares one basis).
                            _xsec = active_cross_section(_store, current_time_utc)
                            _before = len(symbols_order)
                            symbols_order = apply_rank_cache_funnel(
                                symbols_order, _held, _xsec, _top_k
                            )
                            if len(symbols_order) < _before:
                                logging.info(
                                    "[RankFunnel] deep-eval %d/%d (top-%d ∪ %d held); %d deferred.",
                                    len(symbols_order),
                                    _before,
                                    _top_k,
                                    len(_held),
                                    _before - len(symbols_order),
                                )
                        elif _stale and _latest is not None:
                            logging.warning(
                                "[RankFunnel] LSTM panel stale (latest %s > %dd) — full universe; "
                                "is the rank producer running?",
                                _latest,
                                _max_age,
                            )
                    except (
                        Exception
                    ) as _e:  # noqa: BLE001 — the funnel must never kill the cycle
                        logging.warning(
                            "[RankFunnel] skipped this cycle (%s) — full universe.", _e
                        )

                # Snapshots → SymbolEvalState-Dicts (nur Skalare, kein DataFrame)
                graph_states = []
                for symbol in symbols_order:
                    if self._shutdown_event.is_set():
                        break

                    # C4: skip a symbol already awaiting human approval — avoids a wasted
                    # analysis for a signal the queue would dedup. Dormant unless HITL_ENABLED.
                    if await self._hitl_symbol_pending(symbol):
                        continue

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

                    ohlc, price, quote_ts = _extract_ohlc_from_snapshot(snapshot_obj)
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
                    _now_utc = engine_now(timezone.utc)
                    if is_stale_quote(
                        quote_ts, _now_utc, get_config().MAX_QUOTE_AGE_SECONDS
                    ):
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
                                get_config().MAX_QUOTE_AGE_SECONDS,
                            )
                            self._log_strategy_thought(
                                f"⏳ {symbol}: Kursbild zu alt ({_age}) — Enthaltung (stale_quote)"
                            )
                            self._skipped_symbols.add(symbol)
                        continue

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
                    _symbol_state = {
                        "symbol": symbol,
                        "ohlc": ohlc,
                        "market_data_keys": [],
                        "current_time": current_time_utc.isoformat(),
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
                        "_portfolio_context": _portfolio_context,
                        # #2672: flag-gated position context from the shared per-cycle
                        # broker snapshot (OFF → all five channels None = byte-identical;
                        # unconfirmed book → None fields + confirmed=False, fail-closed).
                        **_position_context_state_keys(
                            _positions_by_symbol, _position_confirmed, symbol
                        ),
                        # #3038: flag-gated implied-volatility channels (OFF →
                        # all three None, no network I/O = byte-identical).
                        **_implied_vol_state_keys(symbol, price),
                        **_risk_reversal_state_keys(symbol, price),
                        # #3275: flag-gated composite-quality channels (OFF → all
                        # three None, no work = byte-identical). Feed-derived, so it
                        # also works under SIM (PIT read AS OF current_time_utc).
                        **_quality_state_keys(symbol, price, current_time_utc),
                    }
                    # RTR-1 (#1948, dormant default OFF): point-in-time news channel.
                    # The producer is flag-FIRST (returns None without network I/O
                    # while NEWS_SENTIMENT_NLP_ENABLED is off) → the key is only
                    # added when the feature is armed; the dormant state dict stays
                    # byte-identical to today. [] (flag on, fetch failed/empty) is
                    # passed through so NewsSentimentAgent abstains transparently.
                    if produce_news_headlines is not None:
                        try:
                            _news_headlines = await produce_news_headlines(
                                symbol, current_time_utc
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
                    graph_states.append(_symbol_state)

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
                    # Epic 3375: is_lstm Fallback-Pfad abgerissen (LSTMDynamic ist nur noch Ranker/Stimme).
                    # Non-LSTM: LangGraph-Dispatch parallel via asyncio.gather
                    # Layer 2: Per-Symbol timeout (MiFID II Art. 17)
                    _graph = (
                        build_symbol_eval_graph() if build_symbol_eval_graph else None
                    )
                    results = []  # default empty for CycleWatchdog
                    if _graph is not None:
                        # OBS-4 (#2637): one trading.cycle root per fan-out; every
                        # symbol evaluates inside a trading.symbol_eval child so the
                        # round-table model.inference/risk spans join one trade trace.
                        _cycle_span = None
                        _cycle_ctx = None
                        if _otel_trace is not None:
                            try:
                                _cycle_span = _obs_tracer.start_span("trading.cycle")
                                _cycle_span.set_attribute(
                                    "cycle.symbol_count", len(graph_states)
                                )
                                _cycle_ctx = _otel_trace.set_span_in_context(
                                    _cycle_span
                                )
                            except Exception:
                                logging.debug(
                                    "Telemetry span error: failed to start cycle span",
                                    exc_info=True,
                                )
                                _cycle_span = _cycle_ctx = None

                        logging.warning(
                            "🚀 TRADING LOOP: EXECUTION GRAPH FOR %d SYMBOLS STARTED!",
                            len(graph_states),
                        )
                        if get_config().FULL_UNIVERSE_TRADING_ENABLED:
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
                                    int(get_config().FULL_UNIVERSE_EVAL_CONCURRENCY),
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
                            _cycle_deadline = (
                                time.monotonic() + current_time_budget()[2]
                            )

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
                                    return await _run_symbol_eval(
                                        _obs_tracer,
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
                                            timeout=_symbol_eval_timeout(),
                                        ),
                                    )

                            results = await asyncio.gather(
                                *[_bounded_eval(state) for state in graph_states],
                                return_exceptions=True,
                            )
                        else:
                            results = await asyncio.gather(
                                *[
                                    _run_symbol_eval(
                                        _obs_tracer,
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
                                            timeout=_symbol_eval_timeout(),
                                        ),
                                    )
                                    for state in graph_states
                                ],
                                return_exceptions=True,
                            )
                        if _cycle_span is not None:
                            try:
                                _cycle_span.end()
                            except Exception:
                                logging.debug(
                                    "Telemetry span error: failed to end cycle span",
                                    exc_info=True,
                                )
                    else:
                        # Epic 3375 (#3399): Wenn der Graph nicht geladen werden kann,
                        # System enthält sich. Kein Fallback auf run_for_symbol, um Doppel-Orders zu vermeiden.
                        logging.warning(
                            "MIFID_AUDIT: Round Table Graph konnte nicht geladen werden. System enthält sich."
                        )
                        results = [
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
                            for s in graph_states
                        ]

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
                                _symbol_eval_timeout(),
                            )
                        elif isinstance(res, Exception):
                            logging.error("Error in graph dispatch idx %s: %s", i, res)
                        elif isinstance(res, dict) and isinstance(
                            res.get("signal"), SignalEvent
                        ):
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
                        if (
                            isinstance(res, dict)
                            and res.get("consensus_ranking") is not None
                        ):
                            _cons_sym = (
                                graph_states[i]["symbol"]
                                if i < len(graph_states)
                                else None
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
                                _cons_pm.set_live_consensus(
                                    _cons_sym, res["consensus_ranking"]
                                )

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
                    "timestamp": CompositionRoot.get_instance().clock_port.time(),
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
                    # PR B (fail-safe): pure-observation high-latency counter.
                    _bump_loop_counter(self, "_high_latency_cycles")

                # CycleWatchdog: Track whether the cycle completed any evaluations
                # HOLD/VETO = healthy (system working). Empty = timeout/crash.
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    if graph_states:
                        _completed_evals = sum(
                            1 for r in results if isinstance(r, dict) and "signal" in r
                        )
                        if _completed_evals == 0:
                            self._cycle_watchdog.record_empty_cycle(len(graph_states))
                        else:
                            self._cycle_watchdog.record_successful_cycle()

                # PR B (fail-safe): monotone trading-cycle liveness counter, bumped
                # once per completed cycle. Pure observation — never gates flow.
                _bump_loop_counter(self, "_cycles_completed")

            except APIError as e:
                logging.error("Alpaca API Error live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(20)
            except BrokerConnectionError as e:
                logging.error("Broker connection lost in live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(30)
            except Exception as e:
                logging.error("Unexpected error live loop: %s", e, exc_info=True)
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

            # #2548 sim driver: under SIM_MODE advance the virtual clock one cadence step and
            # terminate at the window's end (flat-out — no real sleep). Byte-identical when off.
            if getattr(get_config(), "SIM_MODE", False):
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
                await asyncio.sleep(0)
            else:
                # INC-6: modest cadence when the market is closed (research still ran this
                # cycle, but there is nothing time-critical to react to) — 300s vs the
                # normal 60s live cadence.
                await asyncio.sleep(300 if cycle_market_closed else 60)

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
