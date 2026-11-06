"""#3819 (ARC-E6 G-1a) — Charakterisierung der Signal-Übergabe vor dem Umbau.

Plan: ``docs/3819-*/implementation_plan.md`` §5 Schritt 1.

Diese Tests nageln fest, was ``_process_signal_event`` auf dem Desktop-/[Global]-Pfad
(keine Mandanten) **heute** tut: welche Order das Tor an den Broker gibt, welche
Ausführungs-Ergebnisse entstehen, welche Auslassungen auf der Audit-Kette landen, was der
Compliance-Wächter verbucht und was der PortfolioManager nachträgt. Sie sind **grün gegen
den Code vor dem Umbau** — das ist ihr Sinn. Nach dem Umbau müssen sie es unverändert
bleiben; jede Abweichung ist eine Verhaltensänderung.

Beobachtet wird an Nähten, die vom Ort eines Namens unabhängig sind — am Broker-Double
(was das Tor wirklich absendet), am Ergebnis-Recorder, am Schreiber der Audit-Kette, am
Compliance-Wächter und am PortfolioManager. Kein Patch auf einen Namen, der mit dem Umbau
das Modul wechselt, ausser denen der angemeldeten Modulbezug-Abbildung.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

import config as _config
import core.engine.order_executor as oe
from core.exceptions import TradingHaltedError
from core.kill_switch import kill_switch as _kill_switch

pytestmark = [pytest.mark.unit, pytest.mark.vc3]


# ── Aufbau ────────────────────────────────────────────────────────────────────


def _kontext(**felder):
    werte = {
        "lstm_prediction": 0.0,
        "rl_stabilized_action": 0,
        "risk_approved": True,
        "portfolio_approved": True,
        "intelligence_approved": True,
        "client_order_id": "coid-3819",
        "decision_id": "dec-3819",
        "current_price": 150.0,
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
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def _signal(action, qty, **kontext):
    return SimpleNamespace(
        action=action,
        symbol="AAPL",
        suggested_quantity=qty,
        decision_context=_kontext(**kontext),
        is_simulation=False,
        triggered_by_stop=False,
        portfolio_reason="",
    )


def _broker(**kw):
    api = MagicMock()
    api.submit_order.return_value = MagicMock(id="ord-3819")
    api.get_account.return_value = SimpleNamespace(
        cash=kw.get("cash", 100_000.0), equity=kw.get("equity", 100_000.0)
    )
    if "position" in kw:
        api.get_open_position.return_value = SimpleNamespace(qty=kw["position"])
    return api


def _engine(api, *, guardian=None, sizer=None, pm=None):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.api = api
    engine.compliance_guardian = guardian
    engine._log_strategy_thought = MagicMock()
    engine.cloud_logger = MagicMock()
    engine.live_universe = []
    engine.active_uid = None
    engine.get_active_tenant_clients = AsyncMock(return_value=[])
    engine._market_closed_blocks_order = AsyncMock(return_value=False)
    engine._hitl_holds_order = AsyncMock(return_value=False)
    if sizer is not None:
        engine.live_risk_manager = MagicMock()
        if isinstance(sizer, Exception):
            engine.live_risk_manager.calculate_position_size.side_effect = sizer
        else:
            engine.live_risk_manager.calculate_position_size.return_value = sizer
    if pm is not None:
        engine.active_strategy = SimpleNamespace(portfolio_manager=pm)
    return engine


def _waechter(order_ok=True, trade_ok=True):
    guardian = MagicMock()
    guardian.check_order.return_value = order_ok
    guardian.check_trade.return_value = trade_ok
    guardian._daily_limit_alert_sent = False
    guardian.max_daily_trades = 10
    return guardian


def _pm():
    pm = MagicMock()
    pm.score_opportunity.return_value = MagicMock()
    pm.should_open_new_position.return_value = (True, "ok", None)
    pm.can_sell_position.return_value = (True, "ok")
    pm._position_scores = {}
    pm._last_refresh_ok = True
    return pm


def _fahre(engine, event, *, schatten=False, halt=None, material=0.0, extra=()):
    """Ein Lauf durch die Übergabe; liefert die Beobachtung."""
    ergebnisse: list[str] = []
    cfg = _config.get_config()
    with (
        patch(
            "core.round_table.execution_outcomes.record_execution_outcome",
            side_effect=lambda symbol, code, *a, **k: ergebnisse.append(code),
        ),
        patch("core.hitl_gate.log_execution_event", new=AsyncMock()) as kette,
        patch.object(_kill_switch, "check_halt", side_effect=halt) as check_halt,
        patch.object(_config, "SHADOW_MODE", schatten, create=True),
        patch.object(cfg, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", material, create=True),
        patch("core.notifier.send_slack_alert", MagicMock()),
        patch("core.engine.order_executor.RedisClient") as redis,
        patch("core.engine.order_executor.restore_pm_state_from_redis", AsyncMock()),
        patch(
            "core.engine.order_executor.persist_pm_state_to_redis", AsyncMock()
        ) as persist,
    ):
        redis.get_redis = AsyncMock(return_value=None)
        for p in extra:
            p.start()
        try:
            asyncio.run(engine._process_signal_event(event))
        finally:
            for p in reversed(extra):
                p.stop()
    ctx = event.decision_context
    return {
        "orders": [
            (r.symbol, r.side.value, float(r.qty), r.client_order_id)
            for r in (c.args[0] for c in engine.api.submit_order.call_args_list)
        ],
        "ergebnisse": ergebnisse,
        "kette": [c.args[0].reason for c in kette.call_args_list],
        "halt_pruefungen": check_halt.call_count,
        "kontext": (str(ctx.alpaca_order_id), ctx.action_executed, ctx.is_simulation),
        "persistiert": [c.args[1] for c in persist.await_args_list],
    }


def _verbucht(guardian):
    """Was der Wächter als Trade verbucht hat — ohne den Zeitstempel."""
    return [
        {k: v for k, v in c.args[0].items() if k != "timestamp"}
        for c in guardian.record_trade.call_args_list
    ]


# ── Bemessung (BUY) ───────────────────────────────────────────────────────────


def test_kauf_wird_bemessen_und_durchs_tor_gesendet():
    api = _broker()
    guardian = _waechter()
    engine = _engine(api, guardian=guardian, sizer=4.0)

    beob = _fahre(engine, _signal("BUY", 0.0))

    assert beob == {
        "orders": [("AAPL", "buy", 4.0, "coid-3819")],
        "ergebnisse": ["executed"],
        "kette": [],
        "halt_pruefungen": 2,
        "kontext": ("ord-3819", True, False),
        "persistiert": [],
    }
    assert _verbucht(guardian) == [
        {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 4.0,
            "price": 150.0,
            "strategy_id": "RLStrategy",
            "user_id": "global",
            "held_qty": 0.0,
            "exit_kind": None,
        }
    ]
    groesse = engine.live_risk_manager.calculate_position_size.call_args.kwargs
    assert groesse["current_price"] == 150.0
    assert groesse["account_cash"] == 100_000.0
    assert groesse["atr"] == 2.0
    assert groesse["market_data"] == {"vix": 20.0}


def test_ein_vorschlag_wird_auf_die_bemessung_gedeckelt():
    engine = _engine(_broker(), sizer=4.0)

    beob = _fahre(engine, _signal("BUY", 10.0))

    assert beob["orders"] == [("AAPL", "buy", 4.0, "coid-3819")]


def test_ein_vorschlag_unter_der_bemessung_bleibt():
    engine = _engine(_broker(), sizer=4.0)

    beob = _fahre(engine, _signal("BUY", 3.0))

    assert beob["orders"] == [("AAPL", "buy", 3.0, "coid-3819")]


def test_ohne_risikomanager_gilt_der_vorschlag():
    engine = _engine(_broker())

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["orders"] == [("AAPL", "buy", 2.0, "coid-3819")]


def test_fehler_in_der_bemessung_ergibt_menge_null():
    engine = _engine(_broker(), sizer=RuntimeError("sizer kaputt"))

    beob = _fahre(engine, _signal("BUY", 0.0))

    assert beob == {
        "orders": [],
        "ergebnisse": ["blocked:risk"],
        "kette": [
            "SKIPPED: sizing_zero -- position size resolved to 0 "
            "(risk limits / exposure cap / available cash)"
        ],
        "halt_pruefungen": 0,
        "kontext": ("", False, False),
        "persistiert": [],
    }


def test_ein_unwesentlicher_einstieg_wird_ausgelassen():
    engine = _engine(_broker(equity=100_000.0), sizer=0.01)

    beob = _fahre(engine, _signal("BUY", 0.0), material=0.5)

    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:risk"]
    assert len(beob["kette"]) == 2
    assert beob["kette"][0].startswith("SKIPPED: skipped:immaterial_entry -- ")
    assert beob["kette"][1].startswith("SKIPPED: sizing_zero -- ")


# ── Bemessung (SELL ohne Menge: Positionsabruf) ───────────────────────────────


def test_verkauf_ohne_menge_holt_die_position():
    api = _broker(position="3")
    guardian = _waechter()
    engine = _engine(api, guardian=guardian)

    beob = _fahre(engine, _signal("SELL", 0.0))

    assert beob["orders"] == [("AAPL", "sell", 3.0, "coid-3819")]
    assert beob["ergebnisse"] == ["executed"]
    # held_qty ist der unabhängige Zeuge aus dem Positionsabruf (#2553).
    assert _verbucht(guardian)[0]["held_qty"] == 3.0
    assert api.get_open_position.call_count == 1


def test_verkauf_einer_flachen_position_ist_geschlossen():
    api = _broker()
    fehler = Exception("position does not exist")
    fehler.status_code = 404
    api.get_open_position.side_effect = fehler
    engine = _engine(api)

    beob = _fahre(engine, _signal("SELL", 0.0))

    assert beob == {
        "orders": [],
        "ergebnisse": ["closed", "blocked:risk"],
        "kette": [
            "SKIPPED: sizing_zero -- no sellable quantity (flat or fetch failed)"
        ],
        "halt_pruefungen": 0,
        "kontext": ("", False, False),
        "persistiert": [],
    }


def test_ein_fehlgeschlagener_positionsabruf_laesst_den_verkauf_fallen():
    api = _broker()
    api.get_open_position.side_effect = RuntimeError("timeout")
    engine = _engine(api)

    beob = _fahre(engine, _signal("SELL", 0.0))

    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["error", "blocked:risk"]


def test_verkauf_mit_menge_holt_den_bestand_fuer_den_waechter():
    api = _broker(position="5")
    guardian = _waechter()
    engine = _engine(api, guardian=guardian)

    beob = _fahre(engine, _signal("SELL", 2.0))

    assert beob["orders"] == [("AAPL", "sell", 2.0, "coid-3819")]
    assert _verbucht(guardian)[0]["held_qty"] == 5.0
    assert _verbucht(guardian)[0]["quantity"] == 2.0


# ── Compliance ────────────────────────────────────────────────────────────────


def test_der_orderwert_deckel_blockt():
    api = _broker()
    guardian = _waechter(order_ok=False)
    engine = _engine(api, guardian=guardian)

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:order_value"]
    assert beob["kontext"] == ("", False, False)
    guardian.check_trade.assert_not_called()
    guardian.record_trade.assert_not_called()


def test_das_tagesbudget_blockt_und_meldet_einmal():
    api = _broker()
    guardian = _waechter(trade_ok=False)
    engine = _engine(api, guardian=guardian)

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["blocked:daily_limit"]
    assert guardian._daily_limit_alert_sent is True
    guardian.record_trade.assert_not_called()


def test_ohne_waechter_wird_nichts_verbucht_und_trotzdem_gesendet():
    engine = _engine(_broker())

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["orders"] == [("AAPL", "buy", 2.0, "coid-3819")]
    assert beob["ergebnisse"] == ["executed"]


# ── Absenden ──────────────────────────────────────────────────────────────────


def test_im_schattenmodus_erreicht_nichts_den_broker():
    api = _broker()
    engine = _engine(api)

    beob = _fahre(engine, _signal("BUY", 2.0), schatten=True)

    assert beob["orders"] == []
    assert beob["ergebnisse"] == ["executed"]
    order_id, ausgefuehrt, simuliert = beob["kontext"]
    assert order_id.startswith("shadow_") and order_id.endswith("_AAPL")
    assert (ausgefuehrt, simuliert) == (True, True)
    # Schatten: nur das erste Halt-Tor, keine Wiederholung an der Absendegrenze.
    assert beob["halt_pruefungen"] == 1


def test_ein_halt_blockt_den_einstieg():
    api = _broker()
    engine = _engine(api)

    beob = _fahre(engine, _signal("BUY", 2.0), halt=TradingHaltedError("halt"))

    assert beob["orders"] == []
    assert beob["kontext"][0].startswith("failed: ")
    assert beob["kette"] == ["SKIPPED: execution_error -- halt"]


def test_ein_halt_an_der_absendegrenze_blockt_ebenso():
    api = _broker()
    engine = _engine(api)

    beob = _fahre(
        engine, _signal("BUY", 2.0), halt=[None, TradingHaltedError("spaeter Halt")]
    )

    assert beob["orders"] == []
    assert beob["halt_pruefungen"] == 2
    assert beob["kette"] == ["SKIPPED: execution_error -- spaeter Halt"]


def test_ein_schutz_exit_passiert_den_halt():
    api = _broker()
    engine = _engine(api)

    beob = _fahre(
        engine,
        _signal("SELL", 2.0, triggered_by_stop=True, stop_type="stop_loss"),
        halt=TradingHaltedError("halt"),
    )

    assert beob["orders"] == [("AAPL", "sell", 2.0, "coid-3819")]
    assert beob["halt_pruefungen"] == 0
    assert beob["kontext"] == ("ord-3819", True, False)


def test_eine_deterministische_ablehnung_gibt_den_slot_zurueck():
    api = _broker()
    http = MagicMock()
    http.response.status_code = 422
    api.submit_order.side_effect = APIError(
        error='{"code": 42210000, "message": "rejected"}', http_error=http
    )
    guardian = _waechter()
    engine = _engine(api, guardian=guardian)

    beob = _fahre(engine, _signal("BUY", 2.0))

    guardian.refund_trade.assert_called_once_with()
    assert beob["kontext"][0].startswith("failed: ")
    assert beob["ergebnisse"] == []
    assert len(beob["kette"]) == 1
    assert beob["kette"][0].startswith("SKIPPED: execution_error -- ")


def test_eine_mehrdeutige_ablehnung_behaelt_den_slot():
    api = _broker()
    http = MagicMock()
    http.response.status_code = 503
    api.submit_order.side_effect = APIError(
        error='{"code": 50300000, "message": "unavailable"}', http_error=http
    )
    guardian = _waechter()
    engine = _engine(api, guardian=guardian)

    _fahre(engine, _signal("BUY", 2.0))

    guardian.refund_trade.assert_not_called()


def test_die_telemetrie_der_absendung():
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    engine = _engine(_broker())

    _fahre(
        engine,
        _signal("BUY", 2.0),
        extra=[patch("core.engine.order_executor.tracer", tracer)],
    )

    tracer.start_as_current_span.assert_called_once_with("broker.submit_order.live")
    gesetzt = dict(c.args for c in span.set_attribute.call_args_list)
    assert gesetzt["trade.action"] == "BUY"
    assert "halt_blocked" not in gesetzt
    from core.telemetry_attrs import order_span_attributes

    erwartet = order_span_attributes(
        "AAPL", "BUY", SimpleNamespace(qty=2.0, side=MagicMock(value="buy"))
    )
    assert set(erwartet) <= set(gesetzt)


def test_die_telemetrie_markiert_den_halt_an_der_absendegrenze():
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    engine = _engine(_broker())

    _fahre(
        engine,
        _signal("BUY", 2.0),
        halt=[None, TradingHaltedError("spaeter Halt")],
        extra=[patch("core.engine.order_executor.tracer", tracer)],
    )

    span.set_attribute.assert_any_call("halt_blocked", True)


# ── Nachbuchen ────────────────────────────────────────────────────────────────


def test_ein_kauf_wird_beim_portfoliomanager_nachgebucht():
    pm = _pm()
    engine = _engine(_broker(), pm=pm)

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["persistiert"] == ["AAPL"]
    assert beob["orders"] == [("AAPL", "buy", 2.0, "coid-3819")]
    pm.record_trade.assert_called_once_with("AAPL", "buy")
    pm.update_position_conviction.assert_called_once_with("AAPL", 0.8)
    pm.clear_sell_signals_after_sale.assert_not_called()


def test_ein_verkauf_raeumt_die_verkaufssignale():
    pm = _pm()
    engine = _engine(_broker(), pm=pm)

    beob = _fahre(engine, _signal("SELL", 2.0))

    assert beob["orders"] == [("AAPL", "sell", 2.0, "coid-3819")]
    pm.record_trade.assert_called_once_with("AAPL", "sell")
    pm.clear_sell_signals_after_sale.assert_called_once_with("AAPL")
    pm.update_position_conviction.assert_not_called()


def test_ein_fehler_beim_nachbuchen_bricht_nichts_ab():
    pm = _pm()
    pm.record_trade.side_effect = RuntimeError("pm kaputt")
    engine = _engine(_broker(), pm=pm)

    beob = _fahre(engine, _signal("BUY", 2.0))

    assert beob["orders"] == [("AAPL", "buy", 2.0, "coid-3819")]
    assert beob["ergebnisse"] == ["executed"]
    assert beob["kontext"] == ("ord-3819", True, False)


# ── Zugangsdaten (secrets) ────────────────────────────────────────────────────


@pytest.mark.skipif(oe.user_alpaca_secrets is None, reason="user_secrets fehlt")
def test_ein_zugeordneter_nutzer_handelt_ueber_sein_konto():
    api = _broker()
    eigenes = _broker()
    engine = _engine(api)
    engine.active_uid = "uid-3819"
    creds = SimpleNamespace(api_key="k", secret_key="s")

    with patch(
        "core.engine.order_executor.create_trading_client", return_value=eigenes
    ) as fabrik:
        beob = _fahre(
            engine,
            _signal("BUY", 2.0),
            extra=[
                patch("core.engine.order_executor.USER_SECRETS_AVAILABLE", True),
                patch.object(
                    oe.user_alpaca_secrets,
                    "get_user_alpaca_credentials",
                    return_value=creds,
                ),
                patch.object(_config, "PAPER_TRADING", True, create=True),
            ],
        )

    fabrik.assert_called_once_with(api_key="k", secret_key="s", paper=True)
    assert beob["orders"] == []  # nicht über das globale Konto
    assert eigenes.submit_order.call_count == 1
    assert beob["ergebnisse"] == ["executed"]


@pytest.mark.skipif(oe.user_alpaca_secrets is None, reason="user_secrets fehlt")
def test_ohne_zuordnung_handelt_das_globale_konto():
    api = _broker()
    engine = _engine(api)
    engine.active_uid = "uid-3819"

    beob = _fahre(
        engine,
        _signal("BUY", 2.0),
        extra=[
            patch("core.engine.order_executor.USER_SECRETS_AVAILABLE", True),
            patch.object(
                oe.user_alpaca_secrets,
                "get_user_alpaca_credentials",
                side_effect=oe.UserAlpacaCredentialsNotFoundError("keine"),
            ),
        ],
    )

    assert beob["orders"] == [("AAPL", "buy", 2.0, "coid-3819")]
