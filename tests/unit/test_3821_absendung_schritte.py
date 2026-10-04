"""#3821 (ARC-E6 G-1b) — die Absendung je Mandant als Folge benannter Schritte.

Plan: ``docs/3821-*/implementation_plan.md`` §6 Schritte 2 und 3.

* **Größen** werden direkt gegen ``regeln.pruefe_groessen`` geprüft, nicht über
  ``test_groessen_gegen_den_code``: dort steht die Regel auf ``warnen`` und meldet nur
  eine Warnung (Plan, Nachbesserung 2).
* **Je Schritt ein Test** auf die neue Einheit — ihre Eingaben, der Zustand, den sie dem
  Dirigenten hinterlässt, und ob sie die Absendung beendet. Was sie *im Ablauf* bewirken,
  hält ``test_3821_absendung_charakterisierung.py`` fest.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, OrderStatus

import config as _config
import core.engine.order_executor as oe
from core.kill_switch import kill_switch as _kill_switch
from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_EXECUTOR = "core/engine/order_executor.py"
_NACHLAUF = "core/engine/absendung_nachlauf.py"
#: Die Schritte nach dem Broker, die im Nachlaufmodul liegen (Dateizahl des Executors).
_IM_NACHLAUF = {
    "_schritt_mandant_endzustand",
    "_schritt_mandant_verkauf_nachbuchen",
    "_schritt_mandant_kauf_nachbuchen",
    "_schritt_mandant_melden",
    "_schritt_mandant_fehler",
}
_DIRIGENT = "_execute_tenant_order"

#: Die Schritte in der Reihenfolge, in der der Dirigent sie aufruft.
_REIHENFOLGE = [
    "_schritt_mandant_vorbereiten",
    "_schritt_mandant_bestand",
    "_schritt_mandant_bemessung",
    "_schritt_mandant_portfolio",
    "_schritt_mandant_verdraengung",
    "_schritt_mandant_sofort_verkaufen",
    "_schritt_mandant_menge",
    "_schritt_mandant_compliance",
    "_schritt_mandant_auftrag",
    "_schritt_mandant_schatten",
    "_schritt_mandant_senden",
    "_schritt_mandant_fuellung",
    "_schritt_mandant_storno",
    "_schritt_mandant_endzustand",
    "_schritt_mandant_verkauf_nachbuchen",
    "_schritt_mandant_kauf_nachbuchen",
    "_schritt_mandant_melden",
    "_schritt_mandant_fehler",
]


# ── Größen (Plan §6 Schritt 2) ────────────────────────────────────────────────


def test_die_absendung_liegt_unter_der_funktionsschwelle():
    """Dirigent und Schritte melden nichts — weder zu lang noch einen veralteten Eintrag."""
    meldungen = regeln.pruefe_groessen(oe_wurzel(), regeln.lade_vertrag())
    eigene = [
        m
        for m in meldungen
        if _DIRIGENT in m
        or "_schritt_mandant_" in m
        or m.startswith(f"Groessen: {_EXECUTOR} ")
        or _NACHLAUF in m
    ]
    assert not eigene, "\n".join(eigene)


def test_das_nachlaufmodul_bleibt_unter_der_dateischwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["datei_schwelle"]
    zeilen = len((oe_wurzel() / _NACHLAUF).read_text(encoding="utf-8").splitlines())
    assert zeilen <= schwelle


def test_jeder_schritt_liegt_unter_der_funktionsschwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["funktion_schwelle"]
    gemessen = {
        b.was.split(".")[-1]: int(b.zusatz)
        for b in regeln.funktions_groessen(oe_wurzel(), "core/engine")
        if b.datei in (_EXECUTOR, _NACHLAUF)
    }
    schritte = {n: z for n, z in gemessen.items() if n.startswith("_schritt_mandant_")}
    assert sorted(schritte) == sorted(_REIHENFOLGE)
    zu_lang = {n: z for n, z in schritte.items() if z > schwelle}
    assert not zu_lang, f"Schritte über {schwelle} Zeilen: {zu_lang}"
    assert gemessen[_DIRIGENT] <= schwelle


def oe_wurzel():
    from pathlib import Path

    return Path(oe.__file__).resolve().parents[2]


# ── Aufbau ────────────────────────────────────────────────────────────────────


def test_der_dirigent_ruft_die_schritte_in_der_reihenfolge_der_absendung():
    from core.engine.base import BotEngine
    from tests.unit._uebergabe_quelle import _schritte

    dirigent = inspect.getsource(BotEngine._execute_tenant_order)
    assert _schritte(dirigent) == _REIHENFOLGE


def test_jeder_schritt_kommt_aus_seinem_mixin():
    """Alle Mixins landen in ``BotEngine``; ein gleicher Name überschriebe still."""
    from core.engine.absendung_nachlauf import AbsendungNachlaufMixin
    from core.engine.base import BotEngine
    from core.engine.order_executor import OrderExecutorMixin

    assert AbsendungNachlaufMixin in BotEngine.__mro__
    for name in _REIHENFOLGE:
        heimat = AbsendungNachlaufMixin if name in _IM_NACHLAUF else OrderExecutorMixin
        assert name in vars(heimat), name
        assert inspect.getattr_static(BotEngine, name) is vars(heimat)[name], name


def test_der_executor_importiert_das_nachlaufmodul_nicht():
    """Die Richtung ist erzwungen: der Nachlauf liest ``order_executor.<name>``."""
    executor = (oe_wurzel() / _EXECUTOR).read_text(encoding="utf-8")
    assert "absendung_nachlauf" not in executor


# ── Je Schritt (Plan §6 Schritt 3) ────────────────────────────────────────────


def _kontext(**felder):
    werte = {
        "current_price": 100.0,
        "client_order_id": "coid-3821",
        "decision_id": "dec-3821",
        "conviction_score": 0.8,
        "atr_14d": 2.0,
        "vix_level": 20.0,
        "forecast_vol": None,
        "skew_percentile": None,
        "vote_coverage": None,
        "lstm_prediction": 0.5,
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


def _zustand(action="BUY", **felder):
    ctx = felder.pop("context", None) or _kontext()
    event = SimpleNamespace(
        action=action,
        symbol="AAPL",
        suggested_quantity=felder.pop("suggested_quantity", 0.0),
        decision_context=ctx,
        triggered_by_stop=False,
        portfolio_reason="",
    )
    werte = {
        "user_id": "u-3821",
        "client": MagicMock(),
        "equity": 100_000.0,
        "action": action,
        "symbol": "AAPL",
        "context": ctx,
        "event": event,
        "source": "ai",
        "redis_client": None,
        "order_submitted": False,
        "submit_attempted": False,
        "displacement_sell_order_id": None,
        "deferred_close_symbol": None,
        "deferred_close_qty": 0.0,
        "displacement_sell_qty": 0.0,
        "sofort_verkaufen_qty": None,
        "live_order": None,
        "curr": 100.0,
        "pm": MagicMock(),
        "forecast_vol": None,
        "rr_percentile": None,
        "coverage": None,
    }
    werte.update(felder)
    return oe._Absendung(**werte)


def _engine(**felder):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.compliance_guardian = None
    engine.cloud_logger = MagicMock()
    for k, v in felder.items():
        setattr(engine, k, v)
    return engine


def _lauf(coro):
    with (
        patch("core.engine.order_executor.RedisClient") as redis_cls,
        patch("core.engine.order_executor._audit_skipped_signal", AsyncMock()) as audit,
        patch("core.engine.order_executor._rec_outcome") as rec,
        patch("core.engine.order_executor.persist_pm_state_to_redis", AsyncMock()),
    ):
        redis = MagicMock(publish=AsyncMock())
        redis_cls.get_redis = AsyncMock(return_value=redis)
        ergebnis = asyncio.run(coro())
    return ergebnis, audit, rec, redis


def _titel(redis):
    return [json.loads(c.args[1])["title"] for c in redis.publish.await_args_list]


def test_vorbereiten_bricht_ohne_preis_ab():
    engine = _engine(_get_tenant_risk_manager=MagicMock())
    st = _zustand(context=_kontext(current_price=0.0))
    weiter, audit, _, _ = _lauf(lambda: engine._schritt_mandant_vorbereiten(st))
    assert weiter is False
    assert audit.await_args.args[3] == "missing current_price in DecisionContext"


def test_vorbereiten_haelt_risiko_und_portfoliomanager_fest():
    rm, pm = MagicMock(), MagicMock()
    engine = _engine(
        _get_tenant_risk_manager=MagicMock(return_value=rm),
        _get_tenant_portfolio_manager=MagicMock(return_value=pm),
        _entry_time_reconciled={"u-3821"},
    )
    st = _zustand()
    with patch("core.engine.order_executor.restore_pm_state_from_redis", AsyncMock()):
        weiter, *_ = _lauf(lambda: engine._schritt_mandant_vorbereiten(st))
    assert weiter is True
    assert (st.rm, st.pm, st.curr) == (rm, pm, 100.0)


def test_bestand_haelt_den_gehaltenen_bestand_fest():
    st = _zustand("SELL", pm=MagicMock(can_sell_position=lambda s: (True, "")))
    st.client.get_open_position.return_value = SimpleNamespace(qty=-3.0)
    weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_bestand(st))
    assert weiter is True
    assert (st.size, st.held_broker_qty) == (-3.0, 3.0)
    assert not hasattr(st, "sizing_trace")


def test_bestand_bricht_bei_anti_churn_ab():
    st = _zustand("SELL", pm=MagicMock(can_sell_position=lambda s: (False, "kurz")))
    weiter, audit, _, _ = _lauf(lambda: _engine()._schritt_mandant_bestand(st))
    assert weiter is False
    st.pm.record_sell_signal.assert_called_once_with("AAPL")
    assert audit.await_args.args[3] == "anti-churn: kurz"


def test_bemessung_haelt_menge_spur_und_konto_fest():
    rm = MagicMock()
    rm.calculate_position_size.return_value = 4.0
    st = _zustand(rm=rm, pm=MagicMock(_position_scores={}, _last_refresh_ok=True))
    st.client.get_account.return_value = SimpleNamespace(cash=500.0)
    with patch.object(_config.get_config(), "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", 0.0):
        weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_bemessung(st))
    assert weiter is True
    assert st.size == 4.0
    assert st.sizing_trace == {}
    assert st.account.cash == 500.0
    assert rm.calculate_position_size.call_args.kwargs["account_cash"] == 500.0


def test_portfolio_haelt_den_zu_verdraengenden_titel_fest():
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "tausch", "OLD")
    st = _zustand(pm=pm, size=4.0)
    engine = _engine(_hitl_holds_order=AsyncMock(return_value=False))
    weiter, *_ = _lauf(lambda: engine._schritt_mandant_portfolio(st))
    assert weiter is True
    assert st.symbol_to_close == "OLD"
    assert st.context.symbol_to_close == "OLD"
    assert st.context.portfolio_reason == "tausch"


def test_portfolio_bricht_ab_wenn_hitl_haelt():
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "ok", None)
    st = _zustand(pm=pm, size=4.0)
    engine = _engine(_hitl_holds_order=AsyncMock(return_value=True))
    weiter, *_ = _lauf(lambda: engine._schritt_mandant_portfolio(st))
    assert weiter is False


def test_verdraengung_ohne_titel_tut_nichts():
    st = _zustand(symbol_to_close=None, size=4.0, account=SimpleNamespace())
    weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_verdraengung(st))
    assert weiter is True
    st.client.get_open_position.assert_not_called()


def test_verdraengung_kauf_zuerst_merkt_den_verkauf_vor():
    st = _zustand(
        symbol_to_close="OLD",
        size=4.0,
        account=SimpleNamespace(buying_power=100_000.0, multiplier="1"),
    )
    st.client.get_open_position.return_value = SimpleNamespace(
        qty=5.0, current_price=50.0
    )
    with patch.object(_config, "DISPLACEMENT_BUY_FIRST", True, create=True):
        weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_verdraengung(st))
    assert weiter is True
    assert (st.deferred_close_symbol, st.deferred_close_qty) == ("OLD", 5.0)
    st.client.submit_order.assert_not_called()


def test_verdraengung_ohne_kauf_zuerst_merkt_den_sofortverkauf_vor():
    st = _zustand(
        symbol_to_close="OLD",
        size=4.0,
        account=SimpleNamespace(buying_power=100_000.0, multiplier="1"),
    )
    st.client.get_open_position.return_value = SimpleNamespace(
        qty=5.0, current_price=50.0
    )
    with patch.object(_config, "DISPLACEMENT_BUY_FIRST", False, create=True):
        weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_verdraengung(st))
    assert weiter is True
    assert (st.sofort_verkaufen_qty, st.deferred_close_symbol) == (5.0, None)


def test_sofort_verkaufen_ohne_vormerkung_tut_nichts():
    st = _zustand(symbol_to_close="OLD")
    weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_sofort_verkaufen(st))
    assert weiter is True
    assert st.displacement_sell_order_id is None


def test_sofort_verkaufen_verkauft_durchs_tor():
    pm = MagicMock()
    st = _zustand(symbol_to_close="OLD", sofort_verkaufen_qty=5.0, pm=pm)
    with (
        patch.object(_kill_switch, "check_halt") as halt,
        patch.object(
            oe.OrderExecutorMixin,
            "_sende_durchs_tor",
            AsyncMock(return_value=MagicMock(id="sell-1")),
        ) as tor,
        patch("core.engine.order_executor.asyncio.sleep", new=AsyncMock()),
    ):
        weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_sofort_verkaufen(st))
    assert weiter is True
    halt.assert_called_once_with("u-3821")
    assert tor.await_args.kwargs["intent_kind"] == "displacement"
    assert (st.displacement_sell_order_id, st.displacement_sell_qty) == ("sell-1", 5.0)
    pm.record_trade.assert_called_once_with("OLD", "sell")


def test_sofort_verkaufen_scheitert_wie_die_verdraengung():
    st = _zustand(symbol_to_close="OLD", sofort_verkaufen_qty=5.0)
    with patch.object(_kill_switch, "check_halt", side_effect=RuntimeError("halt")):
        weiter, audit, _, _ = _lauf(
            lambda: _engine()._schritt_mandant_sofort_verkaufen(st)
        )
    assert weiter is False
    assert audit.await_args.args[3] == "displacement swap of OLD failed: halt"


def test_menge_deckelt_auf_die_freigabe():
    st = _zustand(size=10.0, sizing_trace={}, suggested_quantity=3.0)
    st.source = "human_approved"
    weiter, *_ = _lauf(lambda: _engine()._schritt_mandant_menge(st))
    assert weiter is True
    assert st.size == 3.0


def test_menge_null_bricht_mit_ursache_ab():
    st = _zustand(size=0.0, sizing_trace={"zero_reason": "cash"})
    weiter, audit, rec, _ = _lauf(lambda: _engine()._schritt_mandant_menge(st))
    assert weiter is False
    assert rec.call_args.args == ("AAPL", "blocked:risk", "size 0 — cash")


def test_menge_null_ohne_spur_wirft_wie_die_lokale_variable():
    """Auf dem SELL-Pfad ist ``sizing_trace`` nie gebunden — heute ein Namensfehler."""
    st = _zustand("SELL", size=0.0)
    with pytest.raises(UnboundLocalError, match="'sizing_trace'"):
        _lauf(lambda: _engine()._schritt_mandant_menge(st))


def test_compliance_bricht_am_orderwert_ab():
    guardian = MagicMock()
    guardian.check_order.return_value = False
    st = _zustand(size=4.0)
    engine = _engine(compliance_guardian=guardian)
    weiter, _, rec, _ = _lauf(lambda: engine._schritt_mandant_compliance(st))
    assert weiter is False
    assert rec.call_args.args[1] == "blocked:order_value"


def test_compliance_verbucht_den_trade():
    guardian = MagicMock()
    st = _zustand(size=4.0)
    engine = _engine(compliance_guardian=guardian)
    weiter, *_ = _lauf(lambda: engine._schritt_mandant_compliance(st))
    assert weiter is True
    assert guardian.record_trade.call_args.args[0]["quantity"] == 4.0


def test_auftrag_baut_die_anfrage_und_prueft_den_halt():
    st = _zustand(size=4.0)
    with (
        patch.object(_config, "USE_LIMIT_ORDERS", False, create=True),
        patch.object(_config, "USE_LIMIT_EXITS", False, create=True),
        patch.object(_kill_switch, "check_halt") as halt,
    ):
        _engine()._schritt_mandant_auftrag(st)
    halt.assert_called_once_with("u-3821")
    assert st.side_enum == OrderSide.BUY
    assert st.client_order_id == "coid-3821"
    assert (st.req.symbol, float(st.req.qty)) == ("AAPL", 4.0)
    assert st.is_limit_exit is False


def test_schatten_bucht_eine_simulierte_order():
    st = _zustand(size=4.0, req=MagicMock(), side_enum=OrderSide.BUY)
    order = MagicMock(id="dry-1")
    with patch.object(
        oe.OrderExecutorMixin, "_sende_durchs_tor", AsyncMock(return_value=order)
    ):
        _lauf(lambda: _engine()._schritt_mandant_schatten(st))
    assert st.order is order
    assert (st.context.alpaca_order_id, st.context.is_simulation) == ("dry-1", True)


def test_senden_haelt_die_broker_order_fest():
    st = _zustand(size=4.0, req=MagicMock(), side_enum=OrderSide.BUY)
    st.is_limit_exit = False
    order = MagicMock(id="ord-1")
    engine = _engine(_submit_with_market_failsafe=AsyncMock(return_value=order))
    _, _, rec, _ = _lauf(lambda: engine._schritt_mandant_senden(st))
    assert st.submit_attempted is True
    assert st.order is order
    assert rec.call_args.args == ("AAPL", "executed")
    assert st.context.action_executed is True


def test_senden_gibt_bei_deterministischer_ablehnung_den_slot_zurueck():
    http = MagicMock()
    http.response.status_code = 422
    fehler = APIError(error='{"code": 1, "message": "x"}', http_error=http)
    guardian = MagicMock()
    st = _zustand(
        size=4.0, req=MagicMock(), side_enum=OrderSide.BUY, symbol_to_close=None
    )
    st.is_limit_exit = False
    engine = _engine(
        compliance_guardian=guardian,
        _submit_with_market_failsafe=AsyncMock(side_effect=fehler),
    )
    with pytest.raises(APIError):
        _lauf(lambda: engine._schritt_mandant_senden(st))
    guardian.refund_trade.assert_called_once_with()


def test_fuellung_meldet_eine_gefuellte_order():
    st = _zustand(order=MagicMock(id="ord-1"))
    st.client.get_order_by_id.return_value = SimpleNamespace(status=OrderStatus.FILLED)
    st.max_wait_seconds, st.poll_interval = 2.0, 1.0
    with patch("core.engine.order_executor.asyncio.sleep", new=AsyncMock()):
        gefuellt, *_ = _lauf(lambda: _engine()._schritt_mandant_fuellung(st))
    assert gefuellt is True
    assert st.live_order.status == OrderStatus.FILLED


def test_fuellung_meldet_eine_haengende_order():
    st = _zustand(order=MagicMock(id="ord-1"))
    st.client.get_order_by_id.return_value = SimpleNamespace(status=OrderStatus.NEW)
    st.max_wait_seconds, st.poll_interval = 2.0, 1.0
    with patch("core.engine.order_executor.asyncio.sleep", new=AsyncMock()):
        gefuellt, *_ = _lauf(lambda: _engine()._schritt_mandant_fuellung(st))
    assert gefuellt is False
    assert st.client.get_order_by_id.call_count == 2


def test_storno_storniert_und_gibt_den_slot_zurueck():
    guardian = MagicMock()
    st = _zustand(
        order=MagicMock(id="ord-1"),
        size=4.0,
        side_enum=OrderSide.BUY,
        symbol_to_close=None,
        client_order_id="coid",
        live_order=SimpleNamespace(filled_qty=0),
    )
    st.is_limit_exit = False
    st.max_wait_seconds = 120.0
    engine = _engine(compliance_guardian=guardian)
    _lauf(lambda: engine._schritt_mandant_storno(st))
    st.client.cancel_order_by_id.assert_called_once_with("ord-1")
    guardian.refund_trade.assert_called_once_with()


def test_endzustand_nimmt_den_beobachteten_stand():
    st = _zustand(order=MagicMock(id="ord-1"), live_order=MagicMock(), size=4.0)
    with patch.object(oe, "erfasse_order_endzustand", AsyncMock()) as erfasse:
        _lauf(lambda: _engine()._schritt_mandant_endzustand(st))
    kw = erfasse.await_args.kwargs
    assert kw["order"] is st.live_order
    assert kw["order_value"] == 400.0


def test_verkauf_nachbuchen_bucht_den_verkauf():
    pm = MagicMock()
    st = _zustand("SELL")
    engine = _engine(_get_tenant_portfolio_manager=MagicMock(return_value=pm))
    _lauf(lambda: engine._schritt_mandant_verkauf_nachbuchen(st))
    pm.record_trade.assert_called_once_with("AAPL", "sell")
    pm.clear_sell_signals_after_sale.assert_called_once_with("AAPL")


def test_kauf_nachbuchen_bucht_kauf_und_konviktion():
    pm = MagicMock()
    st = _zustand()
    engine = _engine(_get_tenant_portfolio_manager=MagicMock(return_value=pm))
    _lauf(lambda: engine._schritt_mandant_kauf_nachbuchen(st))
    pm.record_trade.assert_called_once_with("AAPL", "buy")
    pm.update_position_conviction.assert_called_once_with("AAPL", 0.8)


def test_melden_veroeffentlicht_die_ausfuehrung():
    st = _zustand(order=MagicMock(id="ord-1"), size=4.0)
    _, _, _, redis = _lauf(lambda: _engine()._schritt_mandant_melden(st))
    assert _titel(redis) == ["BUY Order Executed: AAPL"]


def test_fehler_vor_dem_broker_wird_ausgelassen():
    st = _zustand()
    _, audit, _, redis = _lauf(
        lambda: _engine()._schritt_mandant_fehler(st, RuntimeError("x"))
    )
    assert audit.await_args.args[3] == "x"
    assert json.loads(redis.publish.await_args.args[1])["type"] == "trade_rejected"


def test_fehler_nach_dem_broker_ist_eine_inkonsistenz():
    st = _zustand(order_submitted=True)
    _, audit, _, redis = _lauf(
        lambda: _engine()._schritt_mandant_fehler(st, RuntimeError("x"))
    )
    audit.assert_not_awaited()
    assert json.loads(redis.publish.await_args.args[1])["type"] == "state_inconsistency"
