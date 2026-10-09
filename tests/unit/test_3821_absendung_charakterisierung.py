"""#3821 (ARC-E6 G-1b) — Charakterisierung der Absendung je Mandant vor dem Umbau.

Plan: ``docs/3821-*/implementation_plan.md`` §6 Schritt 1.

Diese Tests nageln fest, was ``_execute_tenant_order`` **heute** tut: welche Order an den
Broker geht, welche Ausführungs-Ergebnisse entstehen, welche Auslassungen auf der
Audit-Kette landen, was der Compliance-Wächter verbucht, was der PortfolioManager
nachträgt, welche Erklärungen veröffentlicht werden — und was die Funktion zurückgibt
(``None`` für eine Prüfung, die vor dem Absenden abbricht; ``False`` für einen Fehler vor
dem Broker; ``True``, sobald die Order den Broker erreicht hat). Sie sind **grün gegen
den Code vor dem Umbau** — das ist ihr Sinn. Nach dem Umbau müssen sie es unverändert
bleiben; jede Abweichung ist eine Verhaltensänderung.

Drei Tests halten Fehler fest, die heute im Code stehen (``symbol_to_close`` und
``sizing_trace`` sind auf dem SELL-Pfad nie gebunden). Sie sind **kein Wunsch**, sondern
die Beobachtung: Dieser Umbau korrigiert nichts (Plan §2 Punkt 3). Wer die Fehler
behebt, ändert diese drei Tests mit — sichtbar, in einem eigenen PR.

Beobachtet wird an Nähten, die vom Ort eines Namens unabhängig sind: am Broker-Double,
am Ergebnis-Recorder, am Schreiber der Audit-Kette, am Compliance-Wächter, am
PortfolioManager und am Redis-Kanal.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderStatus

import config as _config
import core.engine.order_executor as oe
from core.exceptions import TradingHaltedError
from core.kill_switch import kill_switch as _kill_switch
from tests.helpers.schlaf import schlaf_nur_im_modul

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_UNGEBUNDEN = (
    "cannot access local variable '{}' where it is not associated with a value"
)


# ── Aufbau ────────────────────────────────────────────────────────────────────


def _kontext(**felder):
    werte = {
        "lstm_prediction": 0.5,
        "client_order_id": "coid-3821",
        "decision_id": "dec-3821",
        "current_price": 100.0,
        "conviction_score": 0.8,
        "atr_14d": 2.0,
        "vix_level": 20.0,
        "forecast_vol": None,
        "skew_percentile": None,
        "vote_coverage": None,
        "triggered_by_stop": False,
        "stop_type": "",
        "alpaca_order_id": "",
        "action_executed": False,
        "is_simulation": False,
        "symbol_to_close": "",
        "portfolio_reason": "",
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def _signal(action, qty=0.0, **kontext):
    return SimpleNamespace(
        action=action,
        symbol="AAPL",
        suggested_quantity=qty,
        decision_context=_kontext(**kontext),
        is_simulation=False,
        triggered_by_stop=False,
        portfolio_reason="",
    )


def _api_fehler(status, code=42210000):
    http = MagicMock()
    http.response.status_code = status
    return APIError(error=json.dumps({"code": code, "message": "x"}), http_error=http)


def _broker(*, position=None, status=OrderStatus.FILLED, filled=0, **konto):
    api = MagicMock()
    ids = iter(f"ord-{i}" for i in range(1, 10))
    api.submit_order.side_effect = lambda req: MagicMock(id=next(ids))
    api.get_account.return_value = SimpleNamespace(
        cash=konto.get("cash", 100_000.0),
        buying_power=konto.get("buying_power", 100_000.0),
        multiplier=konto.get("multiplier", "1"),
    )
    if isinstance(position, Exception):
        api.get_open_position.side_effect = position
    elif position is not None:
        api.get_open_position.return_value = SimpleNamespace(qty=position)
    api.get_order_by_id.return_value = SimpleNamespace(
        status=status, filled_qty=filled, filled_avg_price=100.0, id="poll"
    )
    return api


def _waechter(order_ok=True, trade_ok=True):
    guardian = MagicMock()
    guardian.check_order.return_value = order_ok
    guardian.check_trade.return_value = trade_ok
    guardian._daily_limit_alert_sent = False
    guardian.max_daily_trades = 10
    return guardian


def _pm(*, oeffnen=(True, "ok", None), verkaufen=(True, "ok")):
    pm = MagicMock()
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = oeffnen
    pm.can_sell_position.return_value = verkaufen
    pm._position_scores = {}
    pm._last_refresh_ok = True
    return pm


def _engine(*, guardian=None, groesse=4.0, pm=None, hitl=False):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.compliance_guardian = guardian
    engine.cloud_logger = MagicMock()
    engine.live_universe = []
    rm = MagicMock()
    if callable(groesse):
        rm.calculate_position_size.side_effect = groesse
    else:
        rm.calculate_position_size.return_value = groesse
    engine._get_tenant_risk_manager = MagicMock(return_value=rm)
    engine._get_tenant_portfolio_manager = MagicMock(return_value=pm or _pm())
    engine._hitl_holds_order = AsyncMock(return_value=hitl)
    engine._recover_displacement = AsyncMock()
    return engine


def _fahre(
    engine,
    api,
    event,
    *,
    source="ai",
    schatten=False,
    halt=None,
    gesperrt=False,
    kauf_zuerst=True,
    limit_exits=False,
    frist=1.0,
    material=0.0,
    erfassung=False,
    extra=(),
):
    """Ein Lauf durch die Absendung je Mandant; liefert die Beobachtung."""
    ergebnisse: list[str] = []
    meldungen: list[tuple[str, str]] = []
    cfg = _config.get_config()

    def _publish(kanal, nachricht):
        daten = json.loads(nachricht)
        meldungen.append((daten["type"], daten["title"]))

    sperre = MagicMock(
        acquire=AsyncMock(return_value=not gesperrt), release=AsyncMock()
    )
    redis = MagicMock(publish=AsyncMock(side_effect=_publish))
    redis.lock.return_value = sperre
    stack = ExitStack()
    stack.enter_context(
        patch(
            "core.round_table.execution_outcomes.record_execution_outcome",
            side_effect=lambda symbol, code, *a, **k: ergebnisse.append(code),
        )
    )
    kette = stack.enter_context(
        patch("core.hitl_gate.log_execution_event", new=AsyncMock())
    )
    check_halt = stack.enter_context(
        patch.object(_kill_switch, "check_halt", side_effect=halt)
    )
    stack.enter_context(patch.object(_kill_switch, "is_halted", return_value=False))
    stack.enter_context(patch.object(_config, "SHADOW_MODE", schatten, create=True))
    stack.enter_context(
        patch.object(_config, "DISPLACEMENT_BUY_FIRST", kauf_zuerst, create=True)
    )
    stack.enter_context(patch.object(_config, "USE_LIMIT_ORDERS", False, create=True))
    stack.enter_context(
        patch.object(_config, "USE_LIMIT_EXITS", limit_exits, create=True)
    )
    stack.enter_context(
        patch.object(_config, "ORDER_FILL_TIMEOUT_SECONDS", frist, create=True)
    )
    stack.enter_context(
        patch.object(_config, "ORDER_FILL_POLL_SECONDS", 1.0, create=True)
    )
    stack.enter_context(
        patch.object(cfg, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", material, create=True)
    )
    stack.enter_context(
        patch.object(cfg, "DECISION_CAPTURE_ENABLED", erfassung, create=True)
    )
    stack.enter_context(patch.object(oe, "_record_gateway_decision", lambda d: None))
    stack.enter_context(schlaf_nur_im_modul("core.engine.order_executor", AsyncMock()))
    stack.enter_context(patch("core.notifier.send_slack_alert", MagicMock()))
    redis_cls = stack.enter_context(patch("core.engine.order_executor.RedisClient"))
    stack.enter_context(
        patch("core.engine.order_executor.restore_pm_state_from_redis", AsyncMock())
    )
    stack.enter_context(
        patch(
            "core.engine.entry_time_reconcile.reconcile_entry_time_from_alpaca",
            AsyncMock(),
        )
    )
    persist = stack.enter_context(
        patch("core.engine.order_executor.persist_pm_state_to_redis", AsyncMock())
    )
    with stack:
        redis_cls.get_redis = AsyncMock(return_value=redis)
        for p in extra:
            p.start()
        try:
            rueckgabe = asyncio.run(
                engine._execute_tenant_order(
                    {"user_id": "u-3821", "client": api, "equity": 100_000.0},
                    event,
                    source=source,
                )
            )
        finally:
            for p in reversed(extra):
                p.stop()
    ctx = event.decision_context
    return {
        "rueckgabe": rueckgabe,
        "orders": [
            (r.symbol, r.side.value, float(r.qty))
            for r in (c.args[0] for c in api.submit_order.call_args_list)
        ],
        "ergebnisse": ergebnisse,
        "kette": [
            getattr(c.args[0], "reason", None) or c.args[0].branch
            for c in kette.call_args_list
        ],
        "halt_pruefungen": check_halt.call_count,
        "kontext": (str(ctx.alpaca_order_id), ctx.action_executed, ctx.is_simulation),
        "persistiert": [c.args[1] for c in persist.await_args_list],
        "meldungen": meldungen,
        "storniert": [c.args[0] for c in api.cancel_order_by_id.call_args_list],
        "sperre_frei": sperre.release.await_count,
    }


def _verbucht(guardian):
    """Was der Wächter als Trade verbucht hat — ohne den Zeitstempel."""
    return [
        {k: v for k, v in c.args[0].items() if k != "timestamp"}
        for c in guardian.record_trade.call_args_list
    ]


# ── Sperre und Vorbereitung ───────────────────────────────────────────────────


def test_eine_gehaltene_sperre_blockiert_ohne_spur():
    api = _broker()
    beob = _fahre(_engine(), api, _signal("BUY"), gesperrt=True)

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["kette"] == []
    assert beob["sperre_frei"] == 0


def test_ohne_preis_wird_ausgelassen():
    beob = _fahre(_engine(), _broker(), _signal("BUY", current_price=0.0))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["kette"] == [
        "SKIPPED: other -- missing current_price in DecisionContext"
    ]
    assert beob["sperre_frei"] == 1


# ── Kauf ──────────────────────────────────────────────────────────────────────


def test_ein_kauf_geht_durchs_tor_und_wird_nachgebucht():
    api = _broker()
    guardian = _waechter()
    pm = _pm()
    engine = _engine(guardian=guardian, pm=pm)

    beob = _fahre(engine, api, _signal("BUY"))

    assert beob == {
        "rueckgabe": True,
        "orders": [("AAPL", "buy", 4.0)],
        "ergebnisse": ["executed"],
        "kette": ["executed"],
        "halt_pruefungen": 1,
        "kontext": ("ord-1", True, False),
        "persistiert": ["AAPL"],
        "meldungen": [("trade_executed", "BUY Order Executed: AAPL")],
        "storniert": [],
        "sperre_frei": 1,
    }
    assert _verbucht(guardian) == [
        {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 4.0,
            "price": 100.0,
            "strategy_id": "RLStrategy",
            "user_id": "u-3821",
            "held_qty": 0.0,
            "exit_kind": None,
        }
    ]
    pm.record_trade.assert_called_once_with("AAPL", "buy")
    pm.update_position_conviction.assert_called_once_with("AAPL", 0.8)
    groesse = engine._get_tenant_risk_manager.return_value
    kw = groesse.calculate_position_size.call_args.kwargs
    assert (kw["current_price"], kw["account_cash"], kw["atr"]) == (
        100.0,
        100_000.0,
        2.0,
    )
    assert kw["market_data"] == {"vix": 20.0}


def test_ein_fehlender_atr_faellt_auf_fuenf_prozent_des_preises():
    engine = _engine()
    _fahre(engine, _broker(), _signal("BUY", atr_14d=0.0))

    rm = engine._get_tenant_risk_manager.return_value
    assert rm.calculate_position_size.call_args.kwargs["atr"] == 5.0


def test_menge_null_nennt_die_ursache():
    def _null(**kw):
        kw["sizing_trace"]["zero_reason"] = "cash"
        return 0.0

    beob = _fahre(_engine(groesse=_null), _broker(), _signal("BUY"))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:risk"]
    assert beob["kette"] == ["SKIPPED: sizing_zero -- size 0 — cash"]
    assert beob["meldungen"] == [("trade_rejected", "Risk Blocked: AAPL")]


def test_ein_unwesentlicher_einstieg_wird_ausgelassen():
    beob = _fahre(_engine(groesse=0.01), _broker(), _signal("BUY"), material=0.05)

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert len(beob["kette"]) == 1
    assert beob["kette"][0].startswith("SKIPPED: skipped:immaterial_entry -- ")


def test_der_earnings_waechter_laesst_aus():
    veto = patch.object(
        oe, "_earnings_guard_veto", return_value=("skipped:earnings", "naehe")
    )
    beob = _fahre(_engine(), _broker(), _signal("BUY"), extra=(veto,))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["skipped:earnings"]
    assert beob["kette"] == ["SKIPPED: skipped:earnings -- naehe"]


def test_der_portfoliomanager_lehnt_ab():
    pm = _pm(oeffnen=(False, "buch voll", None))
    event = _signal("BUY")

    beob = _fahre(_engine(pm=pm), _broker(), event)

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["kette"] == ["SKIPPED: other -- portfolio declined: buch voll"]
    assert beob["meldungen"] == [("trade_rejected", "Portfolio Blocked: AAPL")]
    assert event.decision_context.portfolio_reason == "buch voll"


def test_hitl_haelt_den_kauf():
    engine = _engine(hitl=True)
    beob = _fahre(engine, _broker(), _signal("BUY"))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert engine._hitl_holds_order.await_args.args[2:] == ("AAPL", "u-3821", 4.0)


def test_freigegebene_menge_ist_eine_obergrenze():
    guardian = _waechter()
    beob = _fahre(
        _engine(guardian=guardian),
        _broker(),
        _signal("BUY", qty=3.0),
        source="human_approved",
    )

    assert beob["orders"] == [("AAPL", "buy", 3.0)]
    guardian.record_trade.assert_not_called()


# ── Verdrängung ───────────────────────────────────────────────────────────────


def test_verdraengung_kauf_zuerst_verkauft_nach_dem_kauf():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker()
    api.get_open_position.return_value = SimpleNamespace(qty=5.0, current_price=50.0)
    event = _signal("BUY")

    beob = _fahre(_engine(guardian=_waechter(), pm=pm), api, event)

    assert beob["rueckgabe"] is True
    assert beob["orders"] == [("AAPL", "buy", 4.0), ("OLD", "sell", 5.0)]
    assert event.decision_context.symbol_to_close == "OLD"


def test_verdraengung_verkauf_zuerst():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker()
    api.get_open_position.return_value = SimpleNamespace(qty=5.0, current_price=50.0)

    beob = _fahre(_engine(pm=pm), api, _signal("BUY"), kauf_zuerst=False)

    assert beob["rueckgabe"] is True
    assert beob["orders"] == [("OLD", "sell", 5.0), ("AAPL", "buy", 4.0)]
    assert beob["halt_pruefungen"] == 2
    assert beob["persistiert"] == ["OLD", "AAPL"]


def test_verdraengung_ohne_kaufkraft_wird_ausgelassen():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker(buying_power=10.0)
    api.get_open_position.return_value = SimpleNamespace(qty=5.0, current_price=50.0)

    beob = _fahre(_engine(pm=pm), api, _signal("BUY"))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["kette"][0].startswith("SKIPPED: insufficient_buying_power -- ")
    assert beob["meldungen"] == [("trade_rejected", "Precheck Blocked: AAPL")]


def test_eine_gescheiterte_verdraengung_wird_ausgelassen():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker(position=RuntimeError("weg"))

    beob = _fahre(_engine(pm=pm), api, _signal("BUY"))

    assert beob["rueckgabe"] is None
    assert beob["kette"] == [
        "SKIPPED: execution_error -- displacement swap of OLD failed: weg"
    ]


def test_eine_abgelehnte_kauforder_holt_die_verdraengung_zurueck():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker()
    api.get_open_position.return_value = SimpleNamespace(qty=5.0, current_price=50.0)
    fehler = _api_fehler(422)
    gesendet = []

    def _submit(req):
        gesendet.append(req)
        if len(gesendet) == 2:
            raise fehler
        return MagicMock(id="ord-sell")

    api.submit_order.side_effect = _submit
    guardian = _waechter()
    engine = _engine(guardian=guardian, pm=pm)

    beob = _fahre(engine, api, _signal("BUY"), kauf_zuerst=False)

    assert beob["rueckgabe"] is False
    kw = engine._recover_displacement.await_args.kwargs
    assert (kw["symbol_to_close"], kw["displacement_sell_order_id"]) == (
        "OLD",
        "ord-sell",
    )
    assert kw["displacement_sell_qty"] == 5.0
    guardian.refund_trade.assert_called_once_with()


# ── Compliance ────────────────────────────────────────────────────────────────


def test_der_orderwert_blockiert():
    beob = _fahre(
        _engine(guardian=_waechter(order_ok=False)), _broker(), _signal("BUY")
    )

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:order_value"]
    assert beob["meldungen"] == [("trade_rejected", "Compliance Blocked: AAPL")]


def test_das_tageslimit_blockiert():
    guardian = _waechter(trade_ok=False)
    beob = _fahre(_engine(guardian=guardian), _broker(), _signal("BUY"))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:daily_limit"]
    assert guardian._daily_limit_alert_sent is True
    guardian.record_trade.assert_not_called()


def test_der_staubboden_laesst_einen_ausstieg_aus():
    boden = patch.object(oe, "_dust_floor_skips_exit", return_value=True)
    guardian = _waechter()
    beob = _fahre(
        _engine(guardian=guardian),
        _broker(position=3.0),
        _signal("SELL"),
        extra=(boden,),
    )

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    guardian.check_trade.assert_not_called()


# ── Halt, Schatten, Absendung ─────────────────────────────────────────────────


def test_ein_halt_bricht_vor_dem_broker_ab():
    beob = _fahre(
        _engine(),
        _broker(),
        _signal("BUY"),
        halt=TradingHaltedError("halt"),
        erfassung=True,
    )

    assert beob["rueckgabe"] is False
    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:kill_switch"]
    assert beob["kette"] == ["SKIPPED: execution_error -- halt"]
    assert beob["meldungen"] == [("trade_rejected", "API Error: AAPL")]


def test_ein_schutz_exit_geht_trotz_halt():
    beob = _fahre(
        _engine(),
        _broker(position=3.0),
        _signal("SELL", triggered_by_stop=True, stop_type="hard"),
        halt=TradingHaltedError("halt"),
    )

    assert beob["rueckgabe"] is True
    assert beob["orders"] == [("AAPL", "sell", 3.0)]
    assert beob["halt_pruefungen"] == 0


def test_schattenmodus_sendet_nicht_an_den_broker():
    pm = _pm(oeffnen=(True, "tausch", "OLD"))
    api = _broker()
    api.get_open_position.return_value = SimpleNamespace(qty=5.0, current_price=50.0)

    beob = _fahre(_engine(pm=pm), api, _signal("BUY"), schatten=True)

    assert beob["rueckgabe"] is True
    assert beob["orders"] == []
    assert beob["kontext"][1:] == (True, True)
    assert beob["kontext"][0] != ""
    assert beob["ergebnisse"] == []
    assert beob["meldungen"] == [("trade_executed", "BUY Order Executed: AAPL")]


def test_eine_deterministische_ablehnung_gibt_den_slot_zurueck():
    api = _broker()
    api.submit_order.side_effect = _api_fehler(422)
    guardian = _waechter()

    beob = _fahre(_engine(guardian=guardian), api, _signal("BUY"))

    assert beob["rueckgabe"] is False
    guardian.refund_trade.assert_called_once_with()
    assert len(beob["kette"]) == 1
    assert beob["kette"][0].startswith("SKIPPED: execution_error -- ")
    assert beob["meldungen"] == [("trade_rejected", "API Error: AAPL")]


def test_eine_nicht_gefuellte_order_wird_storniert():
    api = _broker(status=OrderStatus.NEW, filled=0)
    guardian = _waechter()
    pm = _pm()

    beob = _fahre(_engine(guardian=guardian, pm=pm), api, _signal("BUY"))

    assert beob["rueckgabe"] is True
    assert beob["storniert"] == ["ord-1"]
    guardian.refund_trade.assert_called_once_with()
    pm.record_trade.assert_not_called()
    assert beob["meldungen"] == []


def test_eine_teilgefuellte_order_behaelt_den_slot():
    api = _broker(status=OrderStatus.NEW, filled=1)
    guardian = _waechter()

    beob = _fahre(_engine(guardian=guardian), api, _signal("BUY"))

    assert beob["rueckgabe"] is True
    guardian.refund_trade.assert_not_called()


# ── Verkauf ───────────────────────────────────────────────────────────────────


def test_anti_churn_haelt_den_verkauf():
    pm = _pm(verkaufen=(False, "zu frueh"))
    beob = _fahre(_engine(pm=pm), _broker(position=3.0), _signal("SELL"))

    assert beob["rueckgabe"] is None
    assert beob["orders"] == []
    pm.record_sell_signal.assert_called_once_with("AAPL")
    assert beob["persistiert"] == ["AAPL"]
    assert beob["kette"] == ["SKIPPED: other -- anti-churn: zu frueh"]
    assert beob["meldungen"] == [("trade_rejected", "Anti-Churn Blocked: AAPL")]


def test_ohne_position_ist_der_verkauf_erledigt():
    fehler = RuntimeError("404")
    fehler.status_code = 404
    beob = _fahre(_engine(), _broker(position=fehler), _signal("SELL"))

    assert beob["rueckgabe"] is None
    assert beob["ergebnisse"] == ["closed"]
    assert beob["kette"] == ["SKIPPED: other -- no open position (already flat/closed)"]


def test_ein_fehler_beim_positionsabruf_bricht_ab():
    beob = _fahre(_engine(), _broker(position=RuntimeError("boom")), _signal("SELL"))

    assert beob["rueckgabe"] is None
    assert beob["ergebnisse"] == ["error"]
    assert beob["kette"] == ["SKIPPED: execution_error -- position fetch failed: boom"]


def test_ein_verkauf_verkauft_den_gehaltenen_bestand():
    guardian = _waechter()
    pm = _pm()
    beob = _fahre(
        _engine(guardian=guardian, pm=pm), _broker(position=3.0), _signal("SELL")
    )

    assert beob["rueckgabe"] is True
    assert beob["orders"] == [("AAPL", "sell", 3.0)]
    assert _verbucht(guardian)[0]["held_qty"] == 3.0
    pm.record_trade.assert_called_once_with("AAPL", "sell")
    pm.clear_sell_signals_after_sale.assert_called_once_with("AAPL")
    assert beob["meldungen"] == [("trade_executed", "SELL Order Executed: AAPL")]


def test_buchungsfehler_nach_verkauf_bestaetigt_404_und_raeumt():
    pm = _pm()
    pm.record_trade.side_effect = [RuntimeError("buch"), None]
    api = _broker()
    api.get_open_position.side_effect = [
        SimpleNamespace(qty=3.0),
        _api_fehler(404, 40410000),
    ]

    beob = _fahre(_engine(pm=pm), api, _signal("SELL"))

    assert beob["rueckgabe"] is True
    assert pm.record_trade.call_count == 2
    assert beob["meldungen"] == [
        ("state_inconsistency", "Portfolio State Error: AAPL"),
        ("trade_executed", "SELL Order Executed: AAPL"),
    ]


def test_buchungsfehler_nach_verkauf_mit_anderem_fehler_meldet_kritisch():
    pm = _pm()
    pm.record_trade.side_effect = RuntimeError("buch")
    api = _broker()
    api.get_open_position.side_effect = [SimpleNamespace(qty=3.0), _api_fehler(429)]

    beob = _fahre(_engine(pm=pm), api, _signal("SELL"))

    assert beob["rueckgabe"] is True
    assert beob["kette"] == ["executed"]
    assert beob["meldungen"] == [("state_inconsistency", "API Error: AAPL")]


def test_buchungsfehler_nach_kauf_bleibt_eine_warnung():
    pm = _pm()
    pm.record_trade.side_effect = RuntimeError("buch")

    beob = _fahre(_engine(pm=pm), _broker(), _signal("BUY"))

    assert beob["rueckgabe"] is True
    assert beob["meldungen"] == [("trade_executed", "BUY Order Executed: AAPL")]


# ── Heute im Code: Namen, die auf dem SELL-Pfad nie gebunden sind ─────────────


def test_heute_verkauf_ohne_bestand_endet_im_namensfehler():
    """``sizing_trace`` wird nur im Kauf-Zweig gebunden (Befund, nicht korrigiert)."""
    beob = _fahre(_engine(), _broker(position=0.0), _signal("SELL"))

    assert beob["rueckgabe"] is False
    assert beob["orders"] == []
    assert beob["kette"] == [
        "SKIPPED: execution_error -- " + _UNGEBUNDEN.format("sizing_trace")
    ]


def test_heute_abgelehnter_verkauf_gibt_den_slot_nicht_zurueck():
    """``symbol_to_close`` wird nur im Kauf-Zweig gebunden (Befund, nicht korrigiert)."""
    api = _broker(position=3.0)
    api.submit_order.side_effect = _api_fehler(422)
    guardian = _waechter()

    beob = _fahre(_engine(guardian=guardian), api, _signal("SELL"))

    assert beob["rueckgabe"] is False
    guardian.refund_trade.assert_not_called()
    assert beob["kette"] == [
        "SKIPPED: execution_error -- " + _UNGEBUNDEN.format("symbol_to_close")
    ]


def test_heute_nicht_gefuellter_verkauf_endet_im_namensfehler():
    """Storno und Markt-Rückfall laufen noch, dann bricht derselbe Name ab."""
    api = _broker(position=3.0, status=OrderStatus.CANCELED, filled=0)
    guardian = _waechter()

    beob = _fahre(_engine(guardian=guardian), api, _signal("SELL"), limit_exits=True)

    assert beob["rueckgabe"] is False
    assert beob["storniert"] == ["ord-1"]
    assert [o[:2] for o in beob["orders"]] == [("AAPL", "sell"), ("AAPL", "sell")]
    guardian.refund_trade.assert_not_called()
    assert beob["kette"] == [
        "SKIPPED: execution_error -- " + _UNGEBUNDEN.format("symbol_to_close")
    ]
