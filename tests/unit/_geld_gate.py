"""#3392 Teil B — Geld-Gate: feste Order-Absichten durch den ECHTEN Tenant-Pfad.

Plan: ``docs/3392-codeowners-ruleset-verhaltens-gate/implementation_plan.md`` §9 und §10.

Gemessen wird die Menge, die den Broker erreicht (``client.submit_order``), nicht die
Ausgabe des Sizers. Echt laufen: ``RiskManager`` (Sizer), ``ComplianceGuardian``
(``check_order``/``check_trade``), ``_execute_tenant_order`` bis zur Absendung durchs Tor.
Ersetzt sind nur die Aussenwelt (Broker-Client, Redis, PortfolioManager-Zulassung) und
die Zeit-/Zustandsquellen, die ein Test festhalten muss.

Aufruf zum Neuschreiben der Referenz (nur mit Begruendung im PR, der Diff IST das Geld):

    python tests/unit/_geld_gate.py --schreibe
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

REFERENZ = _AI_BOT / "tests" / "fixtures" / "geld_gate_referenz_3392.json"

# Die Deckel, die das Gate kennt. Jeder muss in mindestens einem Szenario der bindende
# sein (Abdeckungs-Abgleich). Sizer-Deckel heissen wie ihr ``binding_limit``-Wert.
SIZER_DECKEL = (
    "position_cap",
    "cash",
    "total_exposure_cap",
    "kelly",
    "max_loss_per_trade",
    "compliance_order_value",
)
NACH_SIZER = ("regime_drossel", "freigegebene_obergrenze")
KEIN_DECKEL = "zielgewicht"  # nichts hat geklemmt: das Zielgewicht setzt die Menge
DECKEL = SIZER_DECKEL + NACH_SIZER + (KEIN_DECKEL,)


@dataclass(frozen=True)
class Szenario:
    name: str
    bindet: str  # welcher Deckel binden SOLL (Abdeckung; das Gate misst, was bindet)
    equity: float = 100_000.0
    cash: float = 100_000.0
    preis: float = 100.0
    atr: float = 1.0
    vix: float = 20.0
    konviktion: float = 0.8
    gehalten: int = 0  # Zahl gehaltener Positionen (freie Slots)
    bestand_marktwert: float = 0.0  # Summe market_value beim Broker (Exposure)
    quelle: str = "ai"
    freigegeben: float = 0.0  # suggested_quantity bei quelle="human_approved"
    regime_faktor: float = 1.0
    forecast_vol: float | None = None  # #3619: Vol-Prognose je Titel (None = keine)
    config: dict = field(default_factory=dict)  # Attribute des Moduls ``config``
    laufzeit: dict = field(
        default_factory=dict
    )  # Attribute von ``config.get_config()``


# Die eingecheckte Szenario-Tabelle. Eine Zeile je Deckel (mindestens), plus die Faelle
# "nichts klemmt" und "Boden verwirft". Werte so gewaehlt, dass der genannte Deckel bindet.
SZENARIEN: tuple[Szenario, ...] = (
    # Konto 50k, Cash reichlich, ATR klein: nichts klemmt, das Zielgewicht setzt die Menge.
    Szenario(
        "zielgewicht_setzt_die_menge",
        KEIN_DECKEL,
        equity=50_000.0,
        cash=200_000.0,
        atr=0.5,
    ),
    # Konviktions-Modus: Konfidenz "high" (x1.5) hebt ueber MAX_POSITION_PERCENT.
    Szenario(
        "positionsdeckel",
        "position_cap",
        equity=20_000.0,
        cash=1_000_000.0,
        atr=0.2,
        config={"CLEAN_WEIGHT_SIZING": "off"},
    ),
    Szenario("cash_knapp", "cash", equity=50_000.0, cash=2_000.0, atr=0.5),
    Szenario(
        "gesamt_exposure",
        "total_exposure_cap",
        equity=50_000.0,
        cash=200_000.0,
        atr=0.5,
        bestand_marktwert=45_000.0,
        gehalten=5,
    ),
    Szenario(
        "kelly",
        "kelly",
        equity=50_000.0,
        cash=200_000.0,
        atr=0.5,
        config={"KELLY_FRACTION_CAP": 0.5},
    ),
    # 1,5 % von 50k = 750 USD Verlust je Trade; ATR 10 x 3 = 30 USD je Stueck -> 25 Stueck.
    Szenario(
        "max_verlust", "max_loss_per_trade", equity=50_000.0, cash=200_000.0, atr=10.0
    ),
    Szenario(
        "order_deckel",
        "compliance_order_value",
        equity=400_000.0,
        cash=400_000.0,
        atr=0.5,
    ),
    Szenario(
        "regime_drossel",
        "regime_drossel",
        equity=50_000.0,
        cash=200_000.0,
        atr=0.5,
        regime_faktor=0.5,
    ),
    Szenario(
        "freigegebene_obergrenze",
        "freigegebene_obergrenze",
        equity=50_000.0,
        cash=200_000.0,
        atr=0.5,
        quelle="human_approved",
        freigegeben=3.0,
    ),
    # Boden: Cash unter einem Dollar -> keine Order.
    Szenario(
        "boden_verwirft", "null:insufficient_cash", equity=50_000.0, cash=0.5, atr=0.5
    ),
)


def _uhr():
    """Die Uhr ist seit der Ports-Umstellung Pflicht im ``RiskManager`` (ClockPort).

    Im Harness eine feste Uhr: Die Bemessung liest sie nur fuer Zeitfenster; ein fester
    Wert haelt die Messung reproduzierbar.
    """
    uhr = MagicMock()
    uhr.now.return_value = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    return uhr


def _client(sz: Szenario, gesendet: list) -> MagicMock:
    from alpaca.trading.enums import OrderStatus

    client = MagicMock()
    client.get_account.return_value = MagicMock(
        cash=sz.cash, equity=sz.equity, buying_power=sz.cash
    )
    positionen = (
        [MagicMock(market_value=sz.bestand_marktwert, symbol="HELD")]
        if sz.bestand_marktwert
        else []
    )
    client.get_all_positions.return_value = positionen

    def _submit(req):
        gesendet.append(req)
        return MagicMock(id=f"order-{len(gesendet)}")

    client.submit_order.side_effect = _submit
    client.get_order_by_id.return_value = MagicMock(
        status=OrderStatus.FILLED, filled_qty=0, filled_avg_price=sz.preis
    )
    return client


def _ereignis(sz: Szenario):
    from core.events import SignalEvent

    ctx = MagicMock()
    ctx.current_price = sz.preis
    ctx.conviction_score = sz.konviktion
    ctx.atr_14d = sz.atr
    ctx.vix_level = sz.vix
    ctx.lstm_prediction = 0.5
    ctx.forecast_vol = sz.forecast_vol
    ctx.skew_percentile = None
    ctx.vote_coverage = None
    ctx.client_order_id = f"gate-{sz.name}"
    ctx.decision_id = f"gate-{sz.name}"
    ctx.alpaca_order_id = None
    # Ausdrueckliche Werte statt Attrappen-Attributen: Der Entscheidungs-Mitschnitt
    # (#2840) schreibt diese Felder in die Datenbank, und SQLAlchemy nimmt kein
    # MagicMock fuer eine Boolean-Spalte ("Not a boolean value"). Review #3630
    # FINDING-01: Der Fehler stand als ERROR im Log, ohne einen Test rot zu faerben —
    # also genau die Art stiller Fehlschlag, die dieses Gate anderswo bekaempft.
    ctx.symbol = "GATE"
    ctx.action = "BUY"
    ctx.session_id = "geld-gate"
    ctx.gatekeeper_approved = True
    ctx.gatekeeper_reason = "harness"
    ctx.round_table_votes = None
    ctx.consensus_score = 0.0
    ctx.market_regime = "neutral"
    ctx.model_version_id = None
    ctx.cycle_open = None
    ctx.cycle_high = None
    ctx.cycle_low = None
    ctx.cycle_close = None
    ctx.symbol_to_close = ""
    ctx.portfolio_reason = ""
    ctx.action_executed = False
    ctx.is_simulation = False
    event = MagicMock(spec=SignalEvent)
    event.action = "BUY"
    event.symbol = "GATE"
    event.suggested_quantity = sz.freigegeben
    event.decision_context = ctx
    event.is_simulation = False
    return event


def fahre(sz: Szenario) -> dict:
    """Ein Szenario durch den Tenant-Pfad. Liefert das Messergebnis als dict."""
    import config
    from core import kill_switch as ks_mod
    from core.compliance import ComplianceGuardian
    from core.engine import order_executor as oe
    from core.risk_manager import RiskManager

    gesendet: list = []
    client = _client(sz, gesendet)
    beobachtet: dict = {}

    rm = RiskManager(client=client, total_capital=sz.equity, clock=_uhr())
    echte_bemessung = rm.calculate_position_size

    def _bemessung(*args, **kwargs):
        menge = echte_bemessung(*args, **kwargs)
        beobachtet["sizer_menge"] = float(menge)
        beobachtet["spur"] = dict(kwargs.get("sizing_trace") or {})
        return menge

    rm.calculate_position_size = _bemessung

    pm = MagicMock()
    pm._position_scores = {f"H{i}": 1.0 for i in range(sz.gehalten)}
    pm._last_refresh_ok = True
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = (True, "OK", None)

    executor = oe.OrderExecutorMixin.__new__(oe.OrderExecutorMixin)
    executor.api = client
    executor.compliance_guardian = ComplianceGuardian()
    executor.cloud_logger = MagicMock()
    executor.live_universe = []
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

    redis = MagicMock(
        publish=AsyncMock(),
        lock=MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        ),
    )

    echte_drossel = oe._regime_throttled_size

    def _drossel(symbol, action, size, context):
        # Die ECHTE Drossel; nur ihre Datenquelle ist unten festgelegt.
        ergebnis = echte_drossel(symbol, action, size, context)
        beobachtet["nach_drossel"] = float(ergebnis or 0.0)
        return ergebnis

    lesung = (
        {"factor": sz.regime_faktor, "score": 0.0, "threshold": 0.0, "asof": "fest"}
        if sz.regime_faktor < 1.0
        else None
    )

    with ExitStack() as st:
        for k, v in sz.config.items():
            st.enter_context(patch.object(config, k, v, create=True))
        for k, v in sz.laufzeit.items():
            st.enter_context(patch.object(config.get_config(), k, v, create=True))
        st.enter_context(patch.object(config, "SHADOW_MODE", False, create=True))
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
        st.enter_context(patch.object(oe, "_record_gateway_decision", lambda d: None))
        st.enter_context(patch.object(oe, "_regime_throttled_size", _drossel))
        st.enter_context(
            patch(
                "core.engine.regime_signal.active_throttle_reading",
                lambda *a, **k: lesung,
            )
        )
        mr = st.enter_context(patch.object(oe, "RedisClient"))
        mr.get_redis = AsyncMock(return_value=redis)
        st.enter_context(patch.object(oe, "restore_pm_state_from_redis", AsyncMock()))
        st.enter_context(
            patch(
                "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
                AsyncMock(),
            )
        )
        asyncio.run(
            executor._execute_tenant_order(
                {"user_id": "gate", "client": client, "equity": sz.equity},
                _ereignis(sz),
                source=sz.quelle,
            )
        )

    menge = float(gesendet[0].qty) if gesendet else 0.0
    return {
        "menge": round(menge, 6),
        "notional": round(menge * sz.preis, 2),
        "deckel": zuordnen(beobachtet, menge),
        "zielgewicht": beobachtet.get("spur", {}).get("sizing_target_weight"),
    }


def zuordnen(beobachtet: dict, gesendet: float) -> str:
    """Welcher Deckel hat die abgesendete Menge zuletzt gesetzt?

    Nach dem Sizer per Differenz (Sizer-Menge gegen abgesendete Menge); im Sizer aus der
    Spur (``binding_limit``, zuletzt bindender), sonst das Zielgewicht.
    """
    sizer = beobachtet.get("sizer_menge", 0.0)
    nach_drossel = beobachtet.get("nach_drossel", sizer)
    spur = beobachtet.get("spur", {})
    if gesendet and round(gesendet, 6) < round(nach_drossel, 6):
        return "freigegebene_obergrenze"
    if gesendet and round(nach_drossel, 6) < round(sizer, 6):
        return "regime_drossel"
    if spur.get("zero_reason"):
        return f"null:{spur['zero_reason']}"
    return spur.get("binding_limit", KEIN_DECKEL)


def fahre_desktop(sz: Szenario, vorgabe: float) -> dict:
    """Der Desktop-/[Global]-Pfad (``_process_signal_event`` ohne aktive Tenants) mit
    ``suggested_quantity = vorgabe``. Liefert Sizer-Aufrufe und abgesendete Menge.

    Plan §9.8 / #3528: Ist eine Menge vorgegeben, laeuft der Sizer dort nicht. Diese
    Funktion haelt das heutige Verhalten sichtbar fest; sie bewertet es nicht.
    """
    import config
    from core import kill_switch as ks_mod
    from core.compliance import ComplianceGuardian
    from core.engine import order_executor as oe
    from core.risk_manager import RiskManager

    gesendet: list = []
    client = _client(sz, gesendet)
    rm = RiskManager(client=client, total_capital=sz.equity, clock=_uhr())
    aufrufe: list = []
    echte_bemessung = rm.calculate_position_size

    def _bemessung(*args, **kwargs):
        aufrufe.append(1)
        return echte_bemessung(*args, **kwargs)

    rm.calculate_position_size = _bemessung

    executor = oe.OrderExecutorMixin.__new__(oe.OrderExecutorMixin)
    executor.api = client
    executor.compliance_guardian = ComplianceGuardian()
    executor.cloud_logger = MagicMock()
    executor.live_universe = []
    executor.live_risk_manager = rm
    executor.active_uid = None
    executor.active_strategy = None
    executor.get_active_tenant_clients = AsyncMock(return_value=[])
    executor._market_closed_blocks_order = AsyncMock(return_value=False)

    ereignis = _ereignis(sz)
    ereignis.suggested_quantity = vorgabe

    with ExitStack() as st:
        st.enter_context(patch.object(config, "SHADOW_MODE", False, create=True))
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
        st.enter_context(patch.object(oe, "_record_gateway_decision", lambda d: None))
        st.enter_context(patch.object(oe, "USER_SECRETS_AVAILABLE", False))
        mr = st.enter_context(patch.object(oe, "RedisClient"))
        mr.get_redis = AsyncMock(return_value=None)
        asyncio.run(executor._process_signal_event(ereignis))

    menge = float(gesendet[0].qty) if gesendet else 0.0
    return {"sizer_aufrufe": len(aufrufe), "menge": menge, "notional": menge * sz.preis}


# ---------------------------------------------------------------------------
# Befunde gegen die Referenz
# ---------------------------------------------------------------------------

WIRKUNGSLOS = "WIRKUNGSLOS"
GELDWIRKUNG = "GELDWIRKUNG"
DECKEL_WECHSEL = "DECKEL-WECHSEL"
NEU = "NEU"


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def befunde(referenz: dict, ist: dict) -> list[tuple[str, str, str]]:
    """(Art, Szenario, Text) je Abweichung. Leere Liste = das Geld ist unveraendert.

    Reihenfolge der Pruefung: erst das Geld (Notional), dann der Deckel, dann die
    Eingangsgroesse. WIRKUNGSLOS ist der Vorfall: die Eingangsgroesse hat sich bewegt,
    das Geld nicht, weil ein Deckel weiter hinten unveraendert bindet.
    """
    aus = []
    for name, jetzt in ist.items():
        alt = referenz.get(name)
        if alt is None:
            aus.append((NEU, name, "Szenario ohne Referenz — Referenz neu schreiben."))
            continue
        if jetzt["notional"] != alt["notional"]:
            aus.append(
                (
                    GELDWIRKUNG,
                    name,
                    f"Notional {alt['notional']} -> {jetzt['notional']} USD "
                    f"(Deckel {alt['deckel']} -> {jetzt['deckel']}).",
                )
            )
        elif jetzt["deckel"] != alt["deckel"]:
            aus.append(
                (
                    DECKEL_WECHSEL,
                    name,
                    f"Deckel {alt['deckel']} -> {jetzt['deckel']}, Notional gleich "
                    f"{jetzt['notional']} USD.",
                )
            )
        elif jetzt["zielgewicht"] != alt["zielgewicht"]:
            aus.append(
                (
                    WIRKUNGSLOS,
                    name,
                    f"Zielgewicht {alt['zielgewicht']} -> {jetzt['zielgewicht']}, "
                    f"Notional unveraendert {jetzt['notional']} USD — "
                    f"bindender Deckel: {jetzt['deckel']}.",
                )
            )
    return aus


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
        for name, wert in gemessen.items():
            print(name, wert)
