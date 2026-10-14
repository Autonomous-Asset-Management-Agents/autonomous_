"""#4239 (H-1j) — die Absendungs-Schritte des Desktop-Zweigs wohnen in ``signal_desktop_absendung.py``.

Plan: ``docs/4239-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1j.

Vier Blöcke des freigegebenen Zweigs von ``_process_signal_event`` ziehen als Schritte nach
``SignalDesktopAbsendungMixin``: Auftrag mit Kill-Switch-Tor, Kaufkraft-Vorprüfung der
Verdrängung, Verdrängungs-SELL und die Null-Menge-Meldung (ADR-OBS-01). Der Kaufkraft-Schritt
liefert ``bool`` (Abbruch-Ergebnis, Muster G-1b). ``_schritt_absenden`` und
``_schritt_nachbuchen`` bleiben direkte Aufrufe des Dirigenten. Was die Schritte *im Ablauf*
bewirken, halten Geld-Gate und ``test_3819_uebergabe_charakterisierung.py`` fest; hier steht
je Schritt jedes Ergebnis und der Abbruch durch den Dirigenten.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.architecture import regeln
from tests.helpers.schlaf import schlaf_nur_im_modul

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "signal_desktop_absendung.py"
KERN = PAKET / "core" / "engine" / "order_executor.py"
VIER = (
    "_schritt_desktop_auftrag",
    "_schritt_desktop_kaufkraft",
    "_schritt_desktop_verdraengung_sell",
    "_schritt_desktop_null_menge",
)


# ── Heimat und Aufbau ─────────────────────────────────────────────────────────


def test_vier_schritte_wohnen_im_absendungs_modul():
    from core.engine.order_executor import OrderExecutorMixin
    from core.engine.signal_desktop_absendung import SignalDesktopAbsendungMixin

    for name in VIER:
        assert name in SignalDesktopAbsendungMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_botengine_loest_die_schritte_auf():
    from core.engine.base import BotEngine
    from core.engine.signal_desktop_absendung import SignalDesktopAbsendungMixin

    for name in VIER:
        assert (
            inspect.getattr_static(BotEngine, name)
            is vars(SignalDesktopAbsendungMixin)[name]
        ), name


def test_modul_unter_den_schwellen():
    groessen = regeln.lade_vertrag()["groessen"]
    zeilen = len(MODUL.read_text(encoding="utf-8").splitlines())
    assert zeilen <= groessen["datei_schwelle"]
    gemessen = {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(PAKET, "core/engine")
        if b.datei == "core/engine/signal_desktop_absendung.py"
    }
    for name in VIER:
        n = gemessen[f"SignalDesktopAbsendungMixin.{name}"]
        assert n <= groessen["funktion_schwelle"], f"{name}: {n} Zeilen"


def test_kern_importiert_das_modul_nicht():
    baum = ast.parse(KERN.read_text(encoding="utf-8"))
    importiert = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            importiert.add(knoten.module or "")
            importiert.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.Import):
            importiert.update(a.name for a in knoten.names)
    for verboten in ("signal_desktop_absendung", "ausfuehrung"):
        assert not any(verboten in name for name in importiert), verboten


def test_kein_schritt_ruft_einen_schritt():
    """Option A: der Dirigent ruft jeden Schritt direkt (``_uebergabe_quelle`` liest flach)."""
    from core.engine.signal_desktop_absendung import SignalDesktopAbsendungMixin

    for name in VIER:
        baum = ast.parse(
            textwrap.dedent(inspect.getsource(vars(SignalDesktopAbsendungMixin)[name]))
        )
        gerufen = [
            n.func.attr
            for n in ast.walk(baum)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("_schritt_")
        ]
        assert not gerufen, f"{name} ruft {gerufen}"


def test_jeder_kern_name_existiert():
    """Plan §3: Kaufkraft- und SELL-Schritt fangen jede Ausnahme selbst (fail-open). Ein
    falsch geschriebener Kern-Name wäre dort nur eine WARNING — deshalb hier per ``ast``.
    """
    from core.engine import order_executor

    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    namen = {
        n.attr
        for n in ast.walk(baum)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "order_executor"
    }
    assert namen, "das Modul liest keinen Kern-Namen"
    fehlend = sorted(n for n in namen if n not in vars(order_executor))
    assert not fehlend, fehlend


# ── Je Schritt ────────────────────────────────────────────────────────────────


def _engine(**felder):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.cloud_logger = MagicMock()
    for k, v in felder.items():
        setattr(engine, k, v)
    return engine


def _kontext(**felder):
    werte = {
        "current_price": 150.0,
        "decision_id": "dec-4239",
        "lstm_prediction": 0.7,
        "forecast_vol": None,
        "skew_percentile": None,
        "vote_coverage": None,
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def _broker(bp=1000.0, menge=2.0, preis=50.0):
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(multiplier="1", buying_power=bp)
    client.get_open_position.return_value = SimpleNamespace(
        qty=menge, current_price=preis
    )
    return client


def _st(action="BUY", qty=2.0, **felder):
    from core.engine.order_executor import _Uebergabe

    werte = {
        "event": SimpleNamespace(is_simulation=False, triggered_by_stop=False),
        "symbol": "AAPL",
        "action": action,
        "context": _kontext(),
        "should_log": True,
        "qty": qty,
        "curr": 150.0,
        "uid": None,
        "trade_client": _broker(),
        "fb_pm": None,
        "fb_redis": None,
        "held_broker_qty": 0.0,
        "sizing_trace": {},
        "pc_symbol_to_close": None,
        "pc_pm": None,
        "fallback_displacement_sell_order_id": None,
        "fallback_displacement_sell_qty": 0.0,
        "fb_deferred_close_symbol": None,
        "fb_deferred_close_qty": 0.0,
    }
    werte.update(felder)
    return _Uebergabe(**werte)


def _lauf(coro):
    return asyncio.run(coro)


def _tor_klasse():
    """Das Tor wird an der Klasse des Kerns ersetzt (Muster ``test_h1g_absendung_abgang.py``)."""
    from core.engine.order_executor import OrderExecutorMixin

    return OrderExecutorMixin


def _konfig(buy_first=True, schatten=False):
    return SimpleNamespace(DISPLACEMENT_BUY_FIRST=buy_first, SHADOW_MODE=schatten)


# Auftrag und Kill-Switch-Tor


def test_auftrag_baut_request_mit_abgeleiteter_coid():
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import _derived_coid

    st = _st()
    with patch("core.engine.order_executor.kill_switch") as ks:
        _lauf(_engine()._schritt_desktop_auftrag(st))

    ks.check_halt.assert_called_once_with("global")
    assert st.side_enum is OrderSide.BUY
    assert (st.req.symbol, st.req.qty, st.req.side) == ("AAPL", 2.0, OrderSide.BUY)
    assert st.req.client_order_id == _derived_coid(st.context)
    assert st.schutz_exit is False


def test_auftrag_uebernimmt_eine_gesetzte_coid():
    st = _st(context=_kontext(client_order_id="coid-4239"))
    with patch("core.engine.order_executor.kill_switch"):
        _lauf(_engine()._schritt_desktop_auftrag(st))
    assert st.req.client_order_id == "coid-4239"


def test_halt_wirft_und_erfasst_blocked_kill_switch(monkeypatch):
    import config

    # #2113: die Erfassung ist flag-gesteuert; das Flag wird wie in
    # test_execution_outcome_durable.py gesetzt, nicht ``_capture_enabled`` gepatcht.
    monkeypatch.setattr(
        config.get_config(), "DECISION_CAPTURE_ENABLED", True, raising=False
    )
    st = _st(uid="u-1")
    with (
        patch("core.engine.order_executor.kill_switch") as ks,
        patch("core.engine.order_executor._rec_outcome") as rec,
    ):
        ks.check_halt.side_effect = RuntimeError("halt")
        with pytest.raises(RuntimeError, match="halt"):
            _lauf(_engine()._schritt_desktop_auftrag(st))

    ks.check_halt.assert_called_once_with("u-1")
    rec.assert_called_once_with(
        "AAPL",
        "blocked:kill_switch",
        "kill-switch halt — order blocked",
        decision_id="dec-4239",
    )


def test_schutz_exit_passiert_den_halt():
    from alpaca.trading.enums import OrderSide

    st = _st(action="SELL", context=_kontext(triggered_by_stop=True))
    with patch("core.engine.order_executor.kill_switch") as ks:
        ks.check_halt.side_effect = RuntimeError("halt")
        _lauf(_engine()._schritt_desktop_auftrag(st))

    ks.check_halt.assert_not_called()
    assert st.schutz_exit is True
    assert st.side_enum is OrderSide.SELL


# Kaufkraft-Vorprüfung


def test_kaufkraft_ohne_ziel_liefert_true():
    st = _st()
    assert _lauf(_engine()._schritt_desktop_kaufkraft(st)) is True
    assert st.pc_oq == 0.0
    st.trade_client.get_account.assert_not_called()


def test_kaufkraft_zu_knapp_liefert_false_mit_audit_und_publish():
    from datetime import datetime

    from core.composition.root import CompositionRoot

    redis = MagicMock()
    st = _st(pc_symbol_to_close="MSFT", trade_client=_broker(bp=100.0), fb_redis=redis)
    uhr = SimpleNamespace(now=lambda tz: datetime(2026, 10, 7, tzinfo=tz))
    with (
        patch("core.engine.order_executor.config", _konfig()),
        patch.object(
            CompositionRoot,
            "get_instance",
            return_value=SimpleNamespace(clock_port=uhr),
        ),
        patch("core.engine.order_executor._safe_publish", new=AsyncMock()) as publish,
        patch("core.engine.order_executor._rec_outcome") as rec,
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        assert _lauf(_engine()._schritt_desktop_kaufkraft(st)) is False

    assert publish.await_args.args[:2] == (redis, "explainability:global")
    assert '"type": "trade_rejected"' in publish.await_args.args[2]
    assert "2026-10-07T00:00:00+00:00" in publish.await_args.args[2]
    rec.assert_called_once_with(
        "AAPL", "blocked:precheck", "Insufficient settled buying power."
    )
    audit.assert_awaited_once_with(
        "AAPL",
        "BUY",
        "insufficient_buying_power",
        "required=300.00 available=100.00",
        order_value=300.0,
    )


def test_kaufkraft_reicht_liefert_true_und_schreibt_menge():
    st = _st(pc_symbol_to_close="MSFT")
    with (
        patch("core.engine.order_executor.config", _konfig()),
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        assert _lauf(_engine()._schritt_desktop_kaufkraft(st)) is True

    assert (st.pc_oq, st.fb_buy_first) == (2.0, True)
    st.trade_client.get_open_position.assert_called_once_with("MSFT")
    audit.assert_not_awaited()


def test_kaufkraft_fehler_liefert_true_ohne_menge(caplog):
    client = _broker()
    client.get_account.side_effect = RuntimeError("konto weg")
    st = _st(pc_symbol_to_close="MSFT", trade_client=client)
    with caplog.at_level(logging.WARNING):
        assert _lauf(_engine()._schritt_desktop_kaufkraft(st)) is True

    assert st.pc_oq == 0.0
    assert any(
        "[Global] Failed to displace MSFT" in r.getMessage() and r.exc_info
        for r in caplog.records
    )


# Verdrängungs-SELL


def _sell_st(**felder):
    werte = {"pc_symbol_to_close": "MSFT", "pc_oq": 2.0, "fb_buy_first": False}
    werte.update(felder)
    return _st(**werte)


def test_sell_buy_first_schiebt_auf():
    st = _sell_st(fb_buy_first=True)
    with patch.object(
        _tor_klasse(),
        "_sende_durchs_tor",
        new=AsyncMock(),
    ) as tor:
        _lauf(_engine()._schritt_desktop_verdraengung_sell(st))

    tor.assert_not_awaited()
    assert (st.fb_deferred_close_symbol, st.fb_deferred_close_qty) == ("MSFT", 2.0)
    assert st.fallback_displacement_sell_order_id is None


def test_sell_sofort_geht_durchs_tor_mit_halt_davor():
    pm = MagicMock()
    guardian = MagicMock()
    reihenfolge = []
    st = _sell_st(pc_pm=pm)
    with (
        patch("core.engine.order_executor.config", _konfig(buy_first=False)),
        patch("core.engine.order_executor.kill_switch") as ks,
        patch.object(
            _tor_klasse(),
            "_sende_durchs_tor",
            new=AsyncMock(return_value=SimpleNamespace(id="sell-1")),
        ) as tor,
        patch("core.engine.verdraengung.register_displacement_leg") as leg,
        patch(
            "core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()
        ) as persist,
        schlaf_nur_im_modul("core.engine.order_executor") as schlaf,
    ):
        ks.check_halt.side_effect = lambda *_: reihenfolge.append("halt")
        tor.side_effect = lambda **_: (
            reihenfolge.append("tor") or SimpleNamespace(id="sell-1")
        )
        _lauf(
            _engine(compliance_guardian=guardian)._schritt_desktop_verdraengung_sell(st)
        )

    assert reihenfolge == ["halt", "tor"]
    ks.check_halt.assert_called_once_with("global")
    kw = tor.await_args.kwargs
    assert (kw["symbol"], kw["qty"], kw["intent_kind"]) == ("MSFT", 2.0, "displacement")
    assert kw["client"] is st.trade_client
    assert (
        st.fallback_displacement_sell_order_id,
        st.fallback_displacement_sell_qty,
    ) == (
        "sell-1",
        2.0,
    )
    assert leg.call_args.args[0] is guardian
    pm.record_trade.assert_called_once_with("MSFT", "sell")
    persist.assert_awaited_once_with(pm, "MSFT", None)
    schlaf.assert_awaited_once_with(0.5)


def test_sell_schatten_geht_an_den_proxy():
    from core.engine.order_executor import DryRunOrderProxy

    st = _sell_st()
    with (
        patch(
            "core.engine.order_executor.config", _konfig(buy_first=False, schatten=True)
        ),
        patch("core.engine.order_executor.kill_switch") as ks,
        patch.object(
            _tor_klasse(),
            "_sende_durchs_tor",
            new=AsyncMock(),
        ) as tor,
        schlaf_nur_im_modul("core.engine.order_executor"),
    ):
        _lauf(_engine()._schritt_desktop_verdraengung_sell(st))

    ks.check_halt.assert_not_called()
    assert isinstance(tor.await_args.kwargs["client"], DryRunOrderProxy)
    assert st.fallback_displacement_sell_order_id is None


def test_sell_ohne_menge_tut_nichts():
    st = _sell_st(pc_oq=0.0)
    with patch.object(
        _tor_klasse(),
        "_sende_durchs_tor",
        new=AsyncMock(),
    ) as tor:
        _lauf(_engine()._schritt_desktop_verdraengung_sell(st))
    tor.assert_not_awaited()
    assert st.fb_deferred_close_symbol is None


def test_sell_fehler_wird_gewarnt(caplog):
    st = _sell_st()
    with (
        patch("core.engine.order_executor.config", _konfig(buy_first=False)),
        patch("core.engine.order_executor.kill_switch"),
        patch.object(
            _tor_klasse(),
            "_sende_durchs_tor",
            new=AsyncMock(side_effect=RuntimeError("tor zu")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        _lauf(_engine()._schritt_desktop_verdraengung_sell(st))

    assert any(
        "[Global] Failed to displace MSFT" in r.getMessage() and r.exc_info
        for r in caplog.records
    )
    assert st.fallback_displacement_sell_order_id is None


# Null-Menge


def test_null_menge_meldet_zero_reason(caplog):
    st = _st(qty=0.0, sizing_trace={"zero_reason": "cash floor"})
    with (
        patch("core.engine.order_executor._rec_outcome") as rec,
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
        caplog.at_level(logging.WARNING),
    ):
        _lauf(_engine()._schritt_desktop_null_menge(st))

    rec.assert_called_once_with(
        "AAPL", "blocked:risk", "position size resolved to 0 (risk/cash)"
    )
    audit.assert_awaited_once_with(
        "AAPL", "BUY", "sizing_zero", "position size resolved to 0 — cash floor"
    )
    assert any(
        "dropped: position size resolved to 0" in r.getMessage() for r in caplog.records
    )


def test_null_menge_sell_nennt_keine_verkaufbare_menge():
    st = _st(action="SELL", qty=0.0)
    with (
        patch("core.engine.order_executor._rec_outcome"),
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        _lauf(_engine()._schritt_desktop_null_menge(st))
    audit.assert_awaited_once_with(
        "AAPL", "SELL", "sizing_zero", "no sellable quantity (flat or fetch failed)"
    )


# ── Abbruch und Übergabe durch den Dirigenten ─────────────────────────────────


def _dirigent_lauf(broker, buy_first=True):
    """Ein freigegebener Desktop-BUY mit Verdrängungsziel MSFT durch den ganzen Dirigenten."""
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "ok", "MSFT")
    engine = _engine(
        api=broker,
        active_uid=None,
        active_strategy=SimpleNamespace(portfolio_manager=pm),
        compliance_guardian=MagicMock(),
        get_active_tenant_clients=AsyncMock(return_value=[]),
        _market_closed_blocks_order=AsyncMock(return_value=False),
        _hitl_holds_order=AsyncMock(return_value=False),
        _schritt_compliance=AsyncMock(return_value=(True, 150.0)),
        _schritt_absenden=AsyncMock(return_value=SimpleNamespace(id="buy-1")),
        _schritt_nachbuchen=AsyncMock(),
    )
    event = SimpleNamespace(
        symbol="AAPL",
        action="BUY",
        suggested_quantity=2.0,
        decision_context=_kontext(
            rl_stabilized_action=0,
            risk_approved=True,
            alpaca_order_id="",
        ),
        is_simulation=False,
        triggered_by_stop=False,
        portfolio_reason="",
    )
    with (
        patch("core.engine.order_executor.config", _konfig(buy_first=buy_first)),
        patch("core.engine.order_executor.kill_switch"),
        patch("core.engine.order_executor.RedisClient") as redis_client,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor._earnings_guard_veto", return_value=None),
        patch("core.engine.order_executor._rec_outcome"),
        patch("core.engine.order_executor._capture_outcome"),
        patch.object(
            _tor_klasse(),
            "_sende_durchs_tor",
            new=AsyncMock(),
        ) as tor,
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        redis_client.get_redis = AsyncMock(return_value=None)
        _lauf(engine._process_signal_event(event))
    return engine, tor, audit


def test_abbruch_kaufkraft_ueberspringt_absendung_und_abschluss():
    engine, tor, audit = _dirigent_lauf(_broker(bp=100.0))

    engine._schritt_absenden.assert_not_awaited()
    engine._schritt_nachbuchen.assert_not_awaited()
    tor.assert_not_awaited()
    engine.cloud_logger.log_decision.assert_not_called()
    assert audit.await_args.args[2] == "insufficient_buying_power"


def test_absenden_erhaelt_die_felder_aus_dem_zustand():
    engine, tor, audit = _dirigent_lauf(_broker(bp=1000.0))

    tor.assert_not_awaited()
    engine._schritt_absenden.assert_awaited_once()
    kw = engine._schritt_absenden.await_args.kwargs
    assert (kw["_fb_deferred_close_symbol"], kw["_fb_deferred_close_qty"]) == (
        "MSFT",
        2.0,
    )
    assert kw["_schutz_exit"] is False
    assert kw["_fallback_displacement_sell_order_id"] is None
    assert (kw["req"].symbol, kw["qty"], kw["_pc_symbol_to_close"]) == (
        "AAPL",
        2.0,
        "MSFT",
    )
    engine._schritt_nachbuchen.assert_awaited_once()
    audit.assert_not_awaited()


def test_fehler_in_der_vorpruefung_blockiert_keinen_kauf():
    broker = _broker()
    broker.get_account.side_effect = RuntimeError("konto weg")
    engine, tor, _ = _dirigent_lauf(broker)

    tor.assert_not_awaited()
    engine._schritt_absenden.assert_awaited_once()
    kw = engine._schritt_absenden.await_args.kwargs
    assert kw["_fb_deferred_close_symbol"] is None


def test_fehler_im_verdraengungs_sell_blockiert_keinen_kauf():
    broker = _broker()
    engine, tor, _ = _dirigent_lauf(broker, buy_first=False)
    # Ohne Fehler: der SELL ging sofort durchs Tor, danach der Kauf.
    tor.assert_awaited_once()
    engine._schritt_absenden.assert_awaited_once()

    broker2 = _broker()
    with patch(
        "core.engine.verdraengung.register_displacement_leg",
        side_effect=RuntimeError("compliance weg"),
    ):
        engine2, tor2, _ = _dirigent_lauf(broker2, buy_first=False)
    tor2.assert_awaited_once()
    engine2._schritt_absenden.assert_awaited_once()
