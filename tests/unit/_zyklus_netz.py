"""#4242 (H-2a) — Zyklus-Netz: volle Zyklen von ``live_trading_loop`` gegen eine Referenz.

Plan: ``docs/4242-h-2a-a-zyklus-netz/implementation_plan.md``; Entscheidung:
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md`` (H-2a, §3, §4).

Das Netz für die Zerlegung H-2b bis H-2k. Muster wie der Nahttest #3716
(``BotEngine.__new__``, gefälschter Broker, Abbruch über den Schlaf), aber **keine**
Methode von ``TradingLoopMixin`` und ``AusstiegsHebelMixin`` ist am Exemplar ersetzt.
Gefälscht ist nur die Außenwelt: Broker- und Datenclient, Redis, LLM-Prüfung, die Stores
der Zustandsschlüssel (IV, Skew, Qualität), das LSTM-Panel und die Uhr.

Gemessen wird, was den Broker erreicht (jede mutierende Client-Methode, in Reihenfolge,
mit normalisierten Argumenten), der Zustand je Symbol vor dem Round-Table-Graph, die
HWM-Persistenz, die Schlafdauer am Zyklusende (60 offen, 300 bei Fehlern/geschlossen …)
und — als Abdeckungsnachweis — welche Methoden der Handelsschleife liefen.

Patch-Ziele am Kern nur aus der Tabelle der Entscheidung §3 (``PATCH_ZIELE``); kein Patch
auf ``core.engine.trading_loop.time`` oder ``.config``. Konfiguration wird am
Einstellungsobjekt ``config.get_config()`` gesetzt — das sieht jeder Leser, egal in
welchem Modul er nach dem Umzug steht.

Aufruf zum Neuschreiben der Referenz (nur mit Begründung im PR):

    python tests/unit/_zyklus_netz.py --schreibe
"""

from __future__ import annotations

import asyncio
import enum
import inspect
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

REFERENZ = _AI_BOT / "tests" / "fixtures" / "zyklus_netz_referenz_4242.json"

#: Entscheidung §3: die Namen, die am Modulobjekt ``core.engine.trading_loop`` gepatcht
#: werden dürfen (``time`` und ``config`` ausdrücklich nicht).
ERLAUBTE_PATCH_ZIELE = frozenset(
    {
        "get_config",
        "kill_switch",
        "build_symbol_eval_graph",
        "build_portfolio_context",
        "_fetch_position_snapshot",
        "BYPASS_MARKET_HOURS",
        "save_position_hwm",
        "load_position_hwm",
        "engine_now",
        "CompositionRoot",
        "_extract_ohlc_from_snapshot",
        "plan_position_stops",
        "_symbol_eval_timeout",
        "StockSnapshotRequest",
        "asyncio",
    }
)

#: Was dieses Netz tatsächlich am Kern patcht. Der Wächter prüft es gegen die Tabelle.
PATCH_ZIELE = (
    "build_symbol_eval_graph",
    "engine_now",
    "CompositionRoot",
    "BYPASS_MARKET_HOURS",
    "save_position_hwm",
    "load_position_hwm",
)

_KERN = "core.engine.trading_loop"

#: Die feste Uhr aller Szenarien: Donnerstag, 10:30 New York — Markt offen.
JETZT = datetime(2026, 9, 24, 14, 30, tzinfo=timezone.utc)
NAECHSTE_OEFFNUNG = datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc)

#: Mutierende Broker-Methoden: alles, was beim Broker etwas anlegt, ändert oder storniert.
MUTIEREND = (
    "submit_order",
    "cancel_order_by_id",
    "cancel_orders",
    "replace_order_by_id",
    "close_position",
    "close_all_positions",
)

#: Kanäle der Zustandsschlüssel (H-2c) plus die Felder, die jeden Symbolzustand tragen.
ZUSTAND_SCHLUESSEL = (
    "symbol",
    "ohlc",
    "current_time",
    "vix",
    "regime",
    "in_position",
    "position_qty",
    "position_avg_price",
    "unrealized_pnl",
    "position_context_confirmed",
    "implied_vol",
    "implied_vol_reference",
    "implied_vol_reference_date",
    "risk_reversal",
    "risk_reversal_reference",
    "risk_reversal_reference_date",
    "quality_score",
    "quality_reference",
    "quality_reference_date",
    "news_headlines",
)

#: Methoden der Handelsschleife, deren Lauf je Szenario gezählt wird (``wraps``-Spione
#: am Klassenattribut — sie rufen durch und ersetzen nichts).
BEOBACHTET = (
    "_zyklus_vorbereiten",
    "_marktzeit_pruefen",
    "_markt_geschlossen_berichten",
    "_run_closed_report_pass",
    "_schutz_vor_konsens",
    "_update_live_account_equity",
    "_maintain_broker_stops",
    "_run_position_stop_checks",
    "_ratchet_high_water_marks",
    "_ratchet_entry_times",
    "_run_deconcentration_and_rotation_exits",
    "_rotation_ausstiege",
    "_rotation_rangverlust",
    "_rotation_ueberhang",
    "_trim_ausstiege",
    "_stopout_reentry_locked",
    "_zyklus_kontext_aufbauen",
    "_fetch_snapshots_chunked",
    "_kontext_snapshots",
    "_kontext_rang_trichter",
    "_symbole_vorbereiten",
    "_symbol_zustand",
    "_round_table_bewerten",
    "_signale_ausfuehren",
    "_zyklus_auswerten",
    "_zyklusgrenze",
    "_perform_graceful_handover",
)

# ---------------------------------------------------------------------------
# Die Welt: ein Konto mit vier Positionen, ein Universum mit fünf Titeln
# ---------------------------------------------------------------------------

#: symbol -> (Stück, Einstand, Kurs, Haltetage). AAPL fällt aus den Top-N (Rotation),
#: NVDA ist übergewichtet (Trim), AMD liegt 10 % im Minus (Positions-Stop), MSFT bleibt.
POSITIONEN = {
    "AAPL": (10, 200.0, 210.0, 12),
    "AMD": (20, 150.0, 135.0, 6),
    "MSFT": (5, 400.0, 408.0, 9),
    "NVDA": (40, 100.0, 125.0, 20),
}
UNIVERSUM = ["AAPL", "AMD", "MSFT", "NVDA", "TSLA"]
KURSE = {"AAPL": 210.0, "AMD": 135.0, "MSFT": 408.0, "NVDA": 125.0, "TSLA": 250.0}
#: LSTM-Querschnitt: TSLA vorn, AAPL ganz hinten; 40 Füllwerte halten das Panel warm.
QUERSCHNITT = {
    "TSLA": 0.95,
    "MSFT": 0.90,
    "AMD": 0.88,
    "NVDA": 0.86,
    **{f"F{i:02d}": round(0.80 - i * 0.01, 2) for i in range(40)},
    "AAPL": 0.01,
}
KONTO = 100_000.0

#: Werte der Zustands-Stores (IV, Skew, Qualität) je Symbol — fest, ohne Netz.
STORE_WERTE = {s: round(0.20 + i * 0.01, 4) for i, s in enumerate(UNIVERSUM)}
STORE_REFERENZ = (0.25, "2026-09-23")


@dataclass(frozen=True)
class Szenario:
    name: str
    markt_offen: bool = True
    uebergabe: bool = False  # Strategie-Swap steht an (Graceful Handover)
    flag: str | None = None  # Flag eines Zustandsschlüssels, eingeschaltet
    zweige: tuple[str, ...] = ()  # Methoden, die laufen MÜSSEN (Wächter)


_OFFEN_ZWEIGE = (
    "_maintain_broker_stops",
    "_run_position_stop_checks",
    "_ratchet_high_water_marks",
    "_ratchet_entry_times",
    "_run_deconcentration_and_rotation_exits",
    "_rotation_ausstiege",
    "_rotation_rangverlust",
    "_rotation_ueberhang",
    "_trim_ausstiege",
    "_update_live_account_equity",
    "_fetch_snapshots_chunked",
    "_symbol_zustand",
    "_round_table_bewerten",
    "_signale_ausfuehren",
    "_zyklus_auswerten",
    "_zyklusgrenze",
)

#: Flags der Zustandsschlüssel (H-2c). Gemessen am Code, nicht am Namen der Funktion:
#: ``_implied_vol_state_keys`` schaltet ``VIXAWARE_IMPLIED_VOL_ENABLED``
#: (core/implied_vol.py::_enabled), ``_risk_reversal_state_keys`` schaltet
#: ``UPSIDE_SKEW_AGENT_ENABLED`` (core/options_skew.py::_enabled).
ZUSTANDS_FLAGS = (
    "REGIME_CONDITIONER_ENABLED",
    "ROUND_TABLE_POSITION_CONTEXT_ENABLED",
    "VIXAWARE_IMPLIED_VOL_ENABLED",
    "QUALITY_AGENT_ENABLED",
    "UPSIDE_SKEW_AGENT_ENABLED",
)

SZENARIEN: tuple[Szenario, ...] = (
    Szenario("offener_markt_mit_position", zweige=_OFFEN_ZWEIGE),
    Szenario(
        "markt_geschlossen",
        markt_offen=False,
        zweige=(
            "_markt_geschlossen_berichten",
            "_run_closed_report_pass",
            "_fetch_snapshots_chunked",
        ),
    ),
    Szenario(
        "shutdown_an_der_zyklusgrenze",
        uebergabe=True,
        zweige=("_zyklusgrenze", "_perform_graceful_handover"),
    ),
    *(
        Szenario(f"flag_{f}", flag=f, zweige=("_symbol_zustand",))
        for f in ZUSTANDS_FLAGS
    ),
)

#: Einstellungen, die jedes Szenario festhält — unabhängig von ``.env`` und Default.
#: Was eine Datei, das Netz oder einen Hintergrund-Task anfassen würde, ist aus; was
#: einen Zweig der Schleife öffnet, ist an.
GRUND_EINSTELLUNGEN = {
    # Außenwelt: kein Abgleich, keine Lease, keine Hintergrund-Tasks, kein Netz
    "RECONCILIATION_ENABLED": False,
    "ENGINE_LEASE_ENABLED": False,
    "HITL_ENABLED": False,
    "EARNINGS_GUARD_ENABLED": False,
    "REGIME_THROTTLE_ENABLED": False,
    "LSTM_BAR_STORE_WARMUP_ENABLED": False,
    "SPECIALIST_COVERAGE_DYNAMIC": False,
    "NEWS_SENTIMENT_NLP_ENABLED": False,
    "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT": False,
    "LSTM_RANK_SMOOTHING_ENABLED": False,
    "ENTRY_TIME_RECONCILE_PER_CYCLE": False,
    "ORDER_OUTBOX_ENABLED": False,
    "SIM_MODE": False,
    "PAPER_TRADING": True,
    # Zweige der Schleife: offen
    "MARKET_CLOSED_REPORT_ONLY": True,
    "BROKER_STOPS_ENABLED": True,
    "STOP_LOSS_PCT": 7.0,
    "POSITION_EXIT_HWM_TRAILING_ENABLED": True,
    "POSITION_EXIT_HWM_PERSIST_ENABLED": True,
    "POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED": True,
    "ROTATION_EXIT_ENABLED": True,
    "DECONCENTRATION_TRIM_ENABLED": True,
    "BOOK_CAP_ENFORCEMENT_ENABLED": True,
    "ROTATION_MAX_EXITS_PER_CYCLE": 1,
    "ROTATION_MAX_EXITS_PER_SESSION": 2,
    "ROTATION_PANEL_MAX_AGE_DAYS": 3,
    "TRIM_MAX_EXITS_PER_SESSION": 3,
    "SMART_EXIT_MIN_HOLD_DAYS": 5.0,
    "SMART_EXIT_EXIT_RANK_HYSTERESIS": 3.0,
    "CONSENSUS_RETENTION_THRESHOLD": 0.0,
    "REENTRY_LOCKOUT_DAYS": 1,
    "STOP_EXIT_SLOT_HOLD_DAYS": 1,
    "GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED": True,
    "ROUND_TABLE_TOP_K_EVAL": 30,
    "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 3,
    "FULL_UNIVERSE_TRADING_ENABLED": False,
    "SNAPSHOT_FETCH_CHUNK_SIZE": 100,
    "MAX_QUOTE_AGE_SECONDS": 900.0,
    "DEFAULT_EQUITY": KONTO,
    # Zustandsschlüssel: aus, bis ein Flag-Szenario eines einschaltet
    **dict.fromkeys(ZUSTANDS_FLAGS, False),
}


# ---------------------------------------------------------------------------
# Außenwelt
# ---------------------------------------------------------------------------


def _position(symbol: str) -> SimpleNamespace:
    stueck, einstand, kurs, _ = POSITIONEN[symbol]
    return SimpleNamespace(
        symbol=symbol,
        qty=str(stueck),
        qty_available=str(stueck),
        side="long",
        avg_entry_price=str(einstand),
        current_price=str(kurs),
        market_value=str(stueck * kurs),
        cost_basis=str(stueck * einstand),
        unrealized_pl=str(stueck * (kurs - einstand)),
        unrealized_plpc=str((kurs - einstand) / einstand),
        asset_class="us_equity",
    )


def _schnappschuss(symbol: str) -> SimpleNamespace:
    kurs = KURSE[symbol]
    return SimpleNamespace(
        latest_trade=SimpleNamespace(price=kurs, size=100, timestamp=JETZT),
        daily_bar=SimpleNamespace(
            open=kurs * 0.99,
            high=kurs * 1.01,
            low=kurs * 0.98,
            close=kurs * 0.995,
            volume=1_000_000.0,
        ),
    )


def _broker(markt_offen: bool) -> MagicMock:
    """Broker-Client. Lesende Methoden antworten fest; mutierende werden mitgeschrieben."""
    from alpaca.trading.enums import OrderStatus

    client = MagicMock(name="broker")
    client.get_clock.return_value = SimpleNamespace(
        is_open=markt_offen, next_open=NAECHSTE_OEFFNUNG
    )
    client.get_account.return_value = SimpleNamespace(
        equity=str(KONTO),
        last_equity=str(KONTO),
        cash=str(KONTO / 2),
        buying_power=str(KONTO / 2),
        portfolio_value=str(KONTO),
        status="ACTIVE",
        trading_blocked=False,
        account_blocked=False,
        pattern_day_trader=False,
        daytrade_count=0,
        multiplier="1",
    )
    client.get_all_positions.side_effect = lambda *a, **k: [
        _position(s) for s in sorted(POSITIONEN)
    ]

    def _offene_position(symbol, *a, **k):
        if symbol in POSITIONEN:
            return _position(symbol)
        raise LookupError(f"position does not exist: {symbol}")

    client.get_open_position.side_effect = _offene_position

    # Mit Gedächtnis: Ein gelegter Stop liegt danach offen beim Broker, ein Storno räumt
    # ihn ab. Nur so fährt die Freigabe vor dem Verkauf (#4033) ihren Storno-Zweig.
    orders: dict = {}

    def _submit(*args, **kwargs):
        req = args[0] if args else kwargs.get("order_data")
        oid = f"order-{len(orders) + 1}"
        stop = getattr(req, "stop_price", None)
        orders[oid] = SimpleNamespace(
            id=oid,
            client_order_id=getattr(req, "client_order_id", None) or oid,
            symbol=getattr(req, "symbol", None),
            qty=str(getattr(req, "qty", None)),
            side=getattr(req, "side", None),
            stop_price=None if stop is None else str(stop),
            status=OrderStatus.NEW if stop is not None else OrderStatus.ACCEPTED,
            filled_qty="0",
            filled_avg_price=None,
        )
        return orders[oid]

    def _storno(oid, *a, **k):
        orders[str(oid)].status = OrderStatus.CANCELED

    client.submit_order.side_effect = _submit
    client.cancel_order_by_id.side_effect = _storno
    client.get_orders.side_effect = lambda *a, **k: [
        o
        for o in orders.values()
        if o.stop_price is not None and o.status == OrderStatus.NEW
    ]
    client.get_order_by_id.side_effect = lambda oid, *a, **k: orders[str(oid)]

    def _unbekannt(coid, *a, **k):
        raise LookupError(f"order not found: {coid}")

    client.get_order_by_client_id.side_effect = _unbekannt
    return client


def _datenclient() -> MagicMock:
    daten = MagicMock(name="daten")
    daten.get_stock_snapshot.side_effect = lambda req, *a, **k: {
        s: _schnappschuss(s) for s in req.symbol_or_symbols if s in KURSE
    }
    return daten


class _Panel:
    """LSTM-Panel-Store mit festem Querschnitt (Rotation, Rang-Trichter)."""

    def latest_snapshot_date(self) -> date:
        return JETZT.date()

    def cross_section_at(self, as_of) -> dict:
        return dict(QUERSCHNITT)


class _WerteStore:
    """Store der Zustandsschlüssel (IV, Skew, Qualität): fest, ohne Datei."""

    def get(self, symbol, tag):
        return STORE_WERTE.get(symbol)

    def put(self, symbol, tag, wert):
        pass

    def reference(self, tag):
        return STORE_REFERENZ


class _PortfolioManager:
    """Der PM der aktiven Strategie — Außenwelt der Ausstiegsrunde."""

    client = None
    user_id = "global"
    max_positions = 3  # vier gehalten: Überhang-Abbau hat etwas zu tun

    def __init__(self) -> None:
        self._position_scores = {
            s: SimpleNamespace(
                symbol=s,
                qty=float(stueck),
                avg_entry=einstand,
                current_price=kurs,
                market_value=stueck * kurs,
                days_held=tage,
            )
            for s, (stueck, einstand, kurs, tage) in POSITIONEN.items()
        }
        self._trade_history = {
            s: [JETZT - timedelta(days=tage)]
            for s, (_, _, _, tage) in POSITIONEN.items()
        }
        self._consecutive_sell_signals: dict = {}
        self.gebucht: list = []

    def refresh_positions(self) -> None:
        pass

    def get_rebalance_recommendations(self) -> list:
        mv = POSITIONEN["NVDA"][0] * POSITIONEN["NVDA"][2]
        return [
            {
                "action": "REDUCE",
                "symbol": "NVDA",
                "market_value": mv,
                "adjustment_value": -mv / 4,
                "drift_pct": 12.5,
            }
        ]

    def record_trade(self, symbol, side) -> None:
        self.gebucht.append([symbol, side])

    def can_sell_position(self, symbol):
        return True, "zyklus-netz"

    def update_position_conviction(self, symbol, conviction) -> None:
        pass

    def clear_sell_signals_after_sale(self, symbol) -> None:
        pass


class _Registry:
    """Agent-Registry mit einem anstehenden Strategie-Swap (Graceful Handover)."""

    def __init__(self, alt, neu) -> None:
        self._aktiv = alt
        self._pending_name = "ZyklusNetzNeu"
        self._strategies = {"ZyklusNetz": alt, "ZyklusNetzNeu": neu}

    def has_pending_swap(self) -> bool:
        return self._pending_name is not None

    def get_active(self):
        return self._aktiv

    def commit_swap(self) -> None:
        self._aktiv = self._strategies[self._pending_name]
        self._pending_name = None


def _strategie(name: str, rm, pm, protokoll: dict) -> SimpleNamespace:
    def _rangliste(symbole, schnappschuesse, marktdaten, zeit, *_a, **_kw):
        protokoll["rangliste"].append(sorted(schnappschuesse))

    def _uebernommen(positionen):
        protokoll["uebergeben"].append(sorted(p.symbol for p in positionen))

    return SimpleNamespace(
        strategy_name=name,
        symbols=list(UNIVERSUM),
        risk_manager=rm,
        portfolio_manager=pm,
        update_lstm_rankings=AsyncMock(side_effect=_rangliste),
        on_positions_received=_uebernommen,
    )


def baue_engine(sz: Szenario):
    """Engine und Außenwelt eines Szenarios. Keine Methode der Schleife wird gesetzt."""
    from core.compliance import ComplianceGuardian
    from core.engine.base import BotEngine
    from core.engine.reentry_lockout import ReentryLockout
    from core.risk_manager import RiskManager

    protokoll: dict = {"rangliste": [], "uebergeben": [], "hwm": [], "zustand": []}
    client = _broker(sz.markt_offen)

    engine = BotEngine.__new__(BotEngine)
    engine.api = client
    engine.data_api = _datenclient()
    engine.data_provider = None
    engine.strategy_running = asyncio.Event()
    engine.strategy_running.set()
    engine._shutdown_event = asyncio.Event()
    engine._skipped_symbols = set()
    engine.active_universe = list(UNIVERSUM)
    engine.live_universe = []
    engine.strategy_lock = RLock()
    engine._rank_panel_producer = None
    engine._rank_panel_consumer = None
    engine.compliance_guardian = ComplianceGuardian()
    engine.live_risk_manager = RiskManager(
        client=client, total_capital=KONTO, clock=MagicMock(return_value=None)
    )
    engine.live_risk_manager.calculate_position_size = MagicMock(return_value=7)
    engine.cloud_logger = MagicMock()
    engine.active_uid = None
    engine.main_loop = None
    engine.update_callback = None
    engine.current_market_data = {"vix": 18.5, "regime": "bull"}
    engine._cycle_latencies = []
    engine._reentry_store = ReentryLockout(None)
    engine.specialist_registry = None

    pm = _PortfolioManager()
    alt = _strategie("ZyklusNetz", engine.live_risk_manager, pm, protokoll)
    engine.active_strategy = alt
    if sz.uebergabe:
        neu = _strategie("ZyklusNetzNeu", engine.live_risk_manager, pm, protokoll)
        engine.agent_registry = _Registry(alt, neu)
    else:
        engine.agent_registry = None

    # Außenwelt der Engine selbst (BotEngine, nicht die Schleife): Redis, LLM, Modell,
    # Mandanten, Gedanken-Log.
    engine._check_redis = AsyncMock(return_value=True)
    engine._check_llm = AsyncMock(return_value=True)
    engine._check_model_files = MagicMock(return_value=True)
    engine.get_active_tenant_clients = AsyncMock(return_value=[])
    engine._market_closed_blocks_order = AsyncMock(return_value=False)

    class _Redis:
        def get(self, *a):
            return None

        def set(self, *a, **k):
            pass

    engine.get_redis = AsyncMock(return_value=_Redis())
    return engine, {"client": client, "pm": pm, "protokoll": protokoll}


# ---------------------------------------------------------------------------
# Messen
# ---------------------------------------------------------------------------


def _wert(v):
    """JSON-fester, adressfreier Wert."""
    if isinstance(v, enum.Enum):
        return _wert(v.value)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _wert(x) for k, x in sorted(v.items(), key=lambda kv: str(kv))}
    if isinstance(v, (list, tuple, set, frozenset)):
        werte = [_wert(x) for x in v]
        return (
            sorted(werte, key=json.dumps) if isinstance(v, (set, frozenset)) else werte
        )
    return type(v).__name__


_ORDER_FELDER = (
    "symbol",
    "side",
    "qty",
    "notional",
    "type",
    "order_type",
    "time_in_force",
    "stop_price",
    "limit_price",
    "order_class",
)


def _order(req) -> dict:
    """Normalisierte Order: Symbol, Seite, Menge, Typ, Stop-/Limitpreis."""
    aus = {}
    for feld in _ORDER_FELDER:
        wert = getattr(req, feld, None)
        if wert is None and isinstance(req, dict):
            wert = req.get(feld)
        if wert is not None:
            if feld in ("qty", "notional", "stop_price", "limit_price"):
                wert = float(wert)
            aus[feld] = _wert(wert)
    return aus


def _broker_aufrufe(client) -> list:
    aus = []
    for name, args, kwargs in client.mock_calls:
        if name not in MUTIEREND:
            continue
        if name == "submit_order":
            req = args[0] if args else kwargs.get("order_data")
            aus.append({"methode": name, "order": _order(req)})
        else:
            aus.append({"methode": name, "args": [str(a) for a in args]})
    return aus


def _graph(protokoll: dict):
    """Round-Table-Graph: hält den Zustand je Symbol fest; BUY nur für Ungehaltene."""
    from core.events import DecisionContext, SignalEvent

    def _bewerte(state, *args, **kwargs):
        protokoll["zustand"].append(
            {k: _wert(state[k]) for k in ZUSTAND_SCHLUESSEL if k in state}
        )
        symbol = state["symbol"]
        aktion = "HOLD" if symbol in POSITIONEN else "BUY"
        return {
            "signal": SignalEvent(
                symbol=symbol,
                action=aktion,
                decision_context=DecisionContext(
                    reasoning_summary="zyklus-netz", action=aktion
                ),
            )
        }

    graph = MagicMock(name="graph")
    graph.ainvoke = AsyncMock(side_effect=_bewerte)
    return graph


def _uhr():
    uhr = SimpleNamespace(now=lambda: JETZT, time=lambda: JETZT.timestamp())
    return SimpleNamespace(get_instance=lambda: SimpleNamespace(clock_port=uhr))


def _engine_now(tz=None):
    return JETZT.astimezone(tz) if tz is not None else JETZT.replace(tzinfo=None)


def fahre(sz: Szenario) -> dict:
    """Ein Szenario: ein voller Lauf von ``live_trading_loop`` bis zum ersten Zyklusende."""
    import config
    from core import implied_vol
    from core import kill_switch as ks_mod
    from core import options_skew, quality_score
    from core.engine import trading_loop as tl

    engine, welt = baue_engine(sz)
    protokoll = welt["protokoll"]
    schlaf: list = []

    def _schlaf_effekt(sekunden=0, *_a, **_kw):
        # Kurze Wartezeiten (Storno-Bestätigung u. a.) laufen durch; das erste lange
        # Warten ist das Zyklusende — dort wird der Lauf ordentlich beendet.
        if sekunden is not None and sekunden >= 5:
            schlaf.append(_wert(float(sekunden)))
            engine._shutdown_event.set()
        return None

    mock_schlaf = AsyncMock(side_effect=_schlaf_effekt)

    def _hwm_speichern(karte, *_a, **_kw):
        protokoll["hwm"].append(_wert(karte))

    mock_hwm_speichern = AsyncMock(side_effect=_hwm_speichern)

    gelaufen = dict.fromkeys(BEOBACHTET, 0)

    def _spion(name, echt):
        if inspect.iscoroutinefunction(echt):
            spy_mock = AsyncMock(side_effect=echt)

            class _BoundAsyncSpy:
                def __init__(self, mock):
                    self._mock = mock

                def __get__(self, obj, objtype=None):
                    if obj is None:
                        return self

                    def _call(*a, **k):
                        gelaufen[name] += 1
                        return self._mock(obj, *a, **k)

                    return _call

            return _BoundAsyncSpy(spy_mock)

        def _sync(self, *a, **k):
            gelaufen[name] += 1
            return echt(self, *a, **k)

        return _sync

    einstellungen = dict(GRUND_EINSTELLUNGEN)
    if sz.flag:
        einstellungen[sz.flag] = True

    cfg = config.get_config()
    with ExitStack() as st:
        for k, v in einstellungen.items():
            st.enter_context(patch.object(cfg, k, v, create=True))
        st.enter_context(patch.object(config, "SHADOW_MODE", False, create=True))
        # Spione am Klassenattribut der definierenden Klasse — nicht am Exemplar.
        for name in BEOBACHTET:
            klasse = next(k for k in type(engine).__mro__ if name in vars(k))
            st.enter_context(
                patch.object(klasse, name, _spion(name, vars(klasse)[name]))
            )
        st.enter_context(patch("core.engine.trading_loop.engine_now", _engine_now))
        st.enter_context(patch("core.engine.trading_loop.CompositionRoot", _uhr()))
        st.enter_context(patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", False))
        st.enter_context(
            patch(
                "core.engine.trading_loop.load_position_hwm",
                AsyncMock(return_value={}),
            )
        )
        st.enter_context(
            patch("core.engine.trading_loop.save_position_hwm", mock_hwm_speichern)
        )
        graph = _graph(protokoll)
        st.enter_context(
            patch("core.engine.trading_loop.build_symbol_eval_graph", lambda: graph)
        )
        # Außenwelt an ihren eigenen Nähten
        st.enter_context(patch("asyncio.sleep", mock_schlaf))
        st.enter_context(
            patch.object(
                type(ks_mod.kill_switch), "is_halted", lambda self, user_id=None: False
            )
        )
        st.enter_context(
            patch.object(
                type(ks_mod.kill_switch), "check_halt", lambda self, user_id=None: None
            )
        )
        st.enter_context(
            patch("core.report.lstm_panel_store.get_store", lambda: _Panel())
        )
        for modul in (implied_vol, options_skew, quality_score):
            st.enter_context(patch.object(modul, "_STORE", _WerteStore()))
        st.enter_context(
            patch.object(implied_vol, "fetch_atm30_iv", lambda *a, **k: None)
        )
        st.enter_context(
            patch.object(options_skew, "fetch_risk_reversal", lambda *a, **k: None)
        )
        st.enter_context(
            patch.object(quality_score, "_read_fundamentals", lambda *a, **k: None)
        )
        asyncio.run(asyncio.wait_for(engine.live_trading_loop(), timeout=120))

    return {
        "broker": _broker_aufrufe(welt["client"]),
        "zustand": sorted(protokoll["zustand"], key=lambda z: z["symbol"]),
        "hwm": protokoll["hwm"],
        "rangliste": protokoll["rangliste"],
        "uebergeben": protokoll["uebergeben"],
        "pm_gebucht": welt["pm"].gebucht,
        "schlaf": schlaf,
        "aktive_strategie": engine.active_strategy.strategy_name,
        "gelaufen": {k: v for k, v in gelaufen.items() if v},
    }


def messwerte(ergebnis: dict) -> int:
    """Zahl der Messwerte eines Szenarios (Leerlauf-Wächter)."""
    return sum(
        len(ergebnis[k])
        for k in ("broker", "zustand", "hwm", "rangliste", "uebergeben", "pm_gebucht")
    )


# ---------------------------------------------------------------------------
# Befunde gegen die Referenz
# ---------------------------------------------------------------------------

NEU = "NEU"
FEHLT = "FEHLT"
ABWEICHUNG = "ABWEICHUNG"


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def befunde(referenz: dict, ist: dict) -> list[tuple[str, str, str]]:
    """(Art, Szenario, Text) je Abweichung. Leere Liste = Verhalten unverändert."""
    aus = []
    for name in sorted(set(referenz) - set(ist)):
        aus.append((FEHLT, name, "Referenz ohne gemessenes Szenario."))
    for name, jetzt in ist.items():
        alt = referenz.get(name)
        if alt is None:
            aus.append((NEU, name, "Szenario ohne Referenz — Referenz neu schreiben."))
            continue
        for feld in sorted(set(alt) | set(jetzt)):
            if alt.get(feld) != jetzt.get(feld):
                aus.append(
                    (
                        ABWEICHUNG,
                        name,
                        f"{feld}: {json.dumps(alt.get(feld), sort_keys=True)} -> "
                        f"{json.dumps(jetzt.get(feld), sort_keys=True)}",
                    )
                )
    return aus


def veraendere_eine_menge(ist: dict) -> bool:
    """Gegenprobe: die Menge der ersten ``submit_order`` um ein Stück verändern."""
    for wert in ist.values():
        for aufruf in wert["broker"]:
            if aufruf["methode"] == "submit_order" and "qty" in aufruf["order"]:
                aufruf["order"]["qty"] += 1
                return True
    return False


def lade_referenz() -> dict:
    return json.loads(REFERENZ.read_text(encoding="utf-8"))


def schreibe_referenz(ist: dict) -> None:
    REFERENZ.write_text(
        json.dumps(ist, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    gemessen = messe_alle()
    if "--schreibe" in sys.argv:
        schreibe_referenz(gemessen)
        print(f"geschrieben: {REFERENZ}")
    else:
        print(json.dumps(gemessen, indent=2, sort_keys=True, ensure_ascii=False))
