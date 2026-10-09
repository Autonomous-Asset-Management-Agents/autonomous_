"""#3819 (ARC-E6 G-1a) — die Signal-Übergabe als Folge benannter Schritte.

Plan: ``docs/3819-*/implementation_plan.md`` §5 Schritte 2 und 3.

* **Größen** werden direkt gegen ``regeln.pruefe_groessen`` geprüft, nicht über
  ``test_groessen_gegen_den_code``: dort steht die Regel auf ``warnen``
  (``vertrag.toml``) und meldet nur eine Warnung. Ein Tor, das nicht rot werden kann,
  ist keins.
* **Je bewegtem Schritt ein Test** auf die neue Einheit — ihre Eingaben und das, was sie
  dem Dirigenten zurückgibt. Was sie *im Ablauf* bewirken, hält
  ``test_3819_uebergabe_charakterisierung.py`` fest.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _config
from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_AI_BOT = Path(__file__).resolve().parents[2]
_EXECUTOR = "core/engine/order_executor.py"
_SCHRITTE = "core/engine/signal_uebergabe.py"
#: #4238 (H-1i): die Entscheid-Schritte des Desktop-Zweigs.
_ENTSCHEID = "core/engine/signal_desktop_entscheid.py"
#: #4239 (H-1j): die Absendungs-Schritte des Desktop-Zweigs.
_DESKTOP_ABSENDUNG = "core/engine/signal_desktop_absendung.py"

#: Die Schritte in der Reihenfolge, in der der Dirigent sie aufruft.
_REIHENFOLGE = [
    #: #4240 (H-1k): Protokollpflicht und Markt-Tor vor dem grossen ``try``.
    "_schritt_protokollpflicht",
    "_schritt_markt_offen",
    "_schritt_secrets",
    "_schritt_desktop_pm_laden",
    "_schritt_verkaufsmenge",
    "_schritt_bemessung",
    "_schritt_desktop_earnings",
    "_schritt_desktop_verdraengung",
    "_schritt_desktop_verkaufstor",
    "_schritt_compliance",
    "_schritt_desktop_auftrag",
    "_schritt_desktop_kaufkraft",
    "_schritt_desktop_verdraengung_sell",
    "_schritt_absenden",
    "_schritt_nachbuchen",
    "_schritt_desktop_null_menge",
    #: #4240 (H-1k): Mandanten-Verteilung und Rumpf des aeusseren Fehlerfaengers.
    "_schritt_verteilung",
    "_schritt_uebergabe_fehler",
]


# ── Größen (Plan §5 Schritt 2) ────────────────────────────────────────────────


def test_die_groessen_der_uebergabe_stimmen_mit_dem_vertrag():
    """Datei- und Funktionszahl sind gemessen eingetragen — in beide Richtungen.

    Geprüft wird nur, was dieser Umbau bewegt: die Dateizahl des Executors, der Dirigent
    und das Schrittmodul. Die übrigen Funktionen des Executors sind Gegenstand eigener
    Sub-Issues (``_execute_tenant_order``: G-1b, #3821).
    """
    meldungen = regeln.pruefe_groessen(_AI_BOT, regeln.lade_vertrag())
    eigene = [
        m
        for m in meldungen
        if m.startswith(f"Groessen: {_EXECUTOR} ")
        or _SCHRITTE in m
        or _ENTSCHEID in m
        or _DESKTOP_ABSENDUNG in m
        or "_process_signal_event" in m
        or "SignalUebergabeMixin" in m
    ]
    assert not eigene, "\n".join(eigene)


def test_jeder_schritt_liegt_unter_der_funktionsschwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["funktion_schwelle"]
    gemessen = {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(_AI_BOT, "core/engine")
        if b.datei in (_SCHRITTE, _ENTSCHEID, _DESKTOP_ABSENDUNG)
    }
    schritte = {
        name.split(".")[-1]: n
        for name, n in gemessen.items()
        if name.startswith(
            (
                "SignalUebergabeMixin._schritt_",
                "SignalDesktopEntscheidMixin._schritt_",
                "SignalDesktopAbsendungMixin._schritt_",
            )
        )
    }
    assert sorted(schritte) == sorted(_REIHENFOLGE)
    zu_lang = {name: n for name, n in schritte.items() if n > schwelle}
    assert not zu_lang, f"Schritte über {schwelle} Zeilen: {zu_lang}"


def test_das_schrittmodul_bleibt_unter_der_dateischwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["datei_schwelle"]
    zeilen = len((_AI_BOT / _SCHRITTE).read_text(encoding="utf-8").splitlines())
    assert zeilen <= schwelle


# ── Aufbau ────────────────────────────────────────────────────────────────────


def test_die_komponierte_klasse_traegt_die_schritte():
    from core.engine.base import BotEngine
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    assert SignalUebergabeMixin in BotEngine.__mro__
    for name in _REIHENFOLGE:
        assert callable(getattr(BotEngine, name)), name


def test_der_dirigent_ruft_die_schritte_in_der_reihenfolge_der_uebergabe():
    import inspect

    from core.engine.base import BotEngine
    from tests.unit._uebergabe_quelle import _schritte

    dirigent = inspect.getsource(BotEngine._process_signal_event)
    assert _schritte(dirigent) == _REIHENFOLGE


def test_das_schrittmodul_importiert_den_executor_nicht_umgekehrt():
    """Die Richtung ist erzwungen (Plan §1): die Schritte rufen
    ``OrderExecutorMixin._sende_durchs_tor`` namentlich. Umgekehrt entstünde ein Zyklus.
    """
    executor = (_AI_BOT / _EXECUTOR).read_text(encoding="utf-8")
    assert "signal_uebergabe" not in executor


# ── Je Schritt (Plan §5 Schritt 3) ────────────────────────────────────────────


def _engine(**felder):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    for k, v in felder.items():
        setattr(engine, k, v)
    return engine


def _kontext(**felder):
    werte = {
        "current_price": 150.0,
        "client_order_id": "coid-3819",
        "decision_id": "dec-3819",
        "conviction_score": 0.8,
        "atr_14d": 2.0,
        "vix_level": 20.0,
        "forecast_vol": None,
        "skew_percentile": None,
        "vote_coverage": None,
        "triggered_by_stop": False,
        "stop_type": "",
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def _lauf(coro):
    return asyncio.run(coro)


def test_secrets_ohne_nutzermodul_behaelt_den_mitgegebenen_zugang():
    engine = _engine()
    with patch("core.engine.order_executor.USER_SECRETS_AVAILABLE", False):
        assert (
            engine._schritt_secrets(symbol="AAPL", uid="u", resolved_client=None)
            is None
        )


def test_secrets_liefert_den_zugang_des_nutzers():
    import core.engine.order_executor as oe

    if oe.user_alpaca_secrets is None:
        pytest.skip("user_secrets fehlt")
    engine = _engine()
    eigenes = object()
    with (
        patch("core.engine.order_executor.USER_SECRETS_AVAILABLE", True),
        patch.object(
            oe.user_alpaca_secrets,
            "get_user_alpaca_credentials",
            return_value=SimpleNamespace(api_key="k", secret_key="s"),
        ),
        patch("core.engine.order_executor.create_trading_client", return_value=eigenes),
    ):
        zugang = engine._schritt_secrets(symbol="AAPL", uid="u", resolved_client=None)
    assert zugang is eigenes


def test_verkaufsmenge_liefert_menge_und_bestand():
    client = MagicMock()
    client.get_open_position.return_value = SimpleNamespace(qty="-3")
    qty, bestand = _lauf(
        _engine()._schritt_verkaufsmenge(
            symbol="AAPL", held_broker_qty=0.0, trade_client=client
        )
    )
    assert (qty, bestand) == (-3.0, 3.0)


def test_verkaufsmenge_bei_fehler_null_und_bestand_unberuehrt():
    client = MagicMock()
    client.get_open_position.side_effect = RuntimeError("timeout")
    qty, bestand = _lauf(
        _engine()._schritt_verkaufsmenge(
            symbol="AAPL", held_broker_qty=7.0, trade_client=client
        )
    )
    assert (qty, bestand) == (0.0, 7.0)


def _bemessung(engine, client, **kw):
    werte = {
        "symbol": "AAPL",
        "action": "BUY",
        "context": _kontext(),
        "curr": 0.0,
        "_original_qty": 0.0,
        "_sizing_trace": {},
        "trade_client": client,
        "_fb_pm": None,
    }
    werte.update(kw)
    cfg = _config.get_config()
    with patch.object(cfg, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", 0.0, create=True):
        return _lauf(engine._schritt_bemessung(**werte))


def test_bemessung_liefert_menge_pm_und_preis():
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(cash=1000.0, equity=1000.0)
    rm = MagicMock()
    rm.calculate_position_size.return_value = 4.0
    pm = SimpleNamespace(_position_scores={}, _last_refresh_ok=True)
    engine = _engine(
        live_risk_manager=rm, active_strategy=SimpleNamespace(portfolio_manager=pm)
    )

    qty, fb_pm, curr = _bemessung(engine, client)

    assert (qty, curr) == (4.0, 150.0)
    assert fb_pm is pm


def test_bemessung_deckelt_einen_vorschlag():
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(cash=1000.0, equity=1000.0)
    rm = MagicMock()
    rm.calculate_position_size.return_value = 4.0
    engine = _engine(live_risk_manager=rm)

    qty, _, _ = _bemessung(engine, client, _original_qty=9.0)

    assert qty == 4.0


def test_bemessung_bei_fehler_null_und_pm_unberuehrt():
    client = MagicMock()
    client.get_account.side_effect = RuntimeError("kein Konto")
    engine = _engine(live_risk_manager=MagicMock())
    alt = object()

    qty, fb_pm, curr = _bemessung(engine, client, _fb_pm=alt)

    assert qty == 0.0
    assert fb_pm is alt
    assert curr == 150.0  # die erste Zeile lief noch


def _compliance(engine, **kw):
    werte = {
        "symbol": "AAPL",
        "action": "BUY",
        "context": _kontext(),
        "event": SimpleNamespace(triggered_by_stop=False, portfolio_reason=""),
        "qty": 2.0,
        "curr": 0.0,
        "held_broker_qty": 0.0,
        "trade_client": MagicMock(),
        "uid": None,
        "approved": True,
    }
    werte.update(kw)
    return _lauf(engine._schritt_compliance(**werte))


def test_compliance_ohne_waechter_aendert_nichts():
    engine = _engine(compliance_guardian=None)
    assert _compliance(engine, curr=99.0) == (True, 99.0)


def test_compliance_freigabe_verbucht_den_trade():
    guardian = MagicMock()
    guardian.check_order.return_value = True
    guardian.check_trade.return_value = True
    engine = _engine(compliance_guardian=guardian)

    assert _compliance(engine) == (True, 150.0)
    guardian.record_trade.assert_called_once()


def test_compliance_sperre_liefert_nicht_freigegeben():
    guardian = MagicMock()
    guardian.check_order.return_value = False
    engine = _engine(compliance_guardian=guardian)

    assert _compliance(engine) == (False, 150.0)
    guardian.record_trade.assert_not_called()


def _absenden(engine, **kw):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    req = MarketOrderRequest(
        symbol="AAPL",
        qty=2.0,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id="coid-3819",
    )
    werte = {
        "symbol": "AAPL",
        "action": "BUY",
        "context": _kontext(),
        "qty": 2.0,
        "req": req,
        "side_enum": OrderSide.BUY,
        "trade_client": MagicMock(),
        "uid": None,
        "_schutz_exit": False,
        "_fb_pm": None,
        "_fb_redis": None,
        "_pc_pm": None,
        "_pc_symbol_to_close": None,
        "_fallback_displacement_sell_order_id": None,
        "_fallback_displacement_sell_qty": 0.0,
        "_fb_deferred_close_symbol": None,
        "_fb_deferred_close_qty": 0.0,
    }
    werte.update(kw)
    return _lauf(engine._schritt_absenden(**werte)), werte


def test_absenden_liefert_die_order_des_tors():
    from core.kill_switch import kill_switch

    engine = _engine(compliance_guardian=None)
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="ord-1")
    with (
        patch.object(_config, "SHADOW_MODE", False, create=True),
        patch.object(kill_switch, "check_halt"),
    ):
        order, werte = _absenden(engine, trade_client=client)

    assert order.id == "ord-1"
    assert werte["context"].alpaca_order_id == "ord-1"
    assert werte["context"].action_executed is True
    assert client.submit_order.call_args.args[0].qty == 2.0


def test_absenden_im_schatten_liefert_eine_trockenorder():
    engine = _engine(compliance_guardian=None)
    client = MagicMock()
    with patch.object(_config, "SHADOW_MODE", True, create=True):
        order, werte = _absenden(engine, trade_client=client)

    assert str(order.id).startswith("shadow_")
    assert werte["context"].is_simulation is True
    client.submit_order.assert_not_called()


def test_nachbuchen_traegt_den_kauf_ein():
    pm = MagicMock()
    with patch(
        "core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()
    ) as persist:
        _lauf(
            _engine()._schritt_nachbuchen(
                symbol="AAPL", action="BUY", context=_kontext(), pm=pm, _fb_redis=None
            )
        )
    pm.record_trade.assert_called_once_with("AAPL", "buy")
    pm.update_position_conviction.assert_called_once_with("AAPL", 0.8)
    persist.assert_awaited_once_with(pm, "AAPL", None)


def test_nachbuchen_ohne_pm_tut_nichts():
    _lauf(
        _engine()._schritt_nachbuchen(
            symbol="AAPL", action="BUY", context=_kontext(), pm=None, _fb_redis=None
        )
    )
