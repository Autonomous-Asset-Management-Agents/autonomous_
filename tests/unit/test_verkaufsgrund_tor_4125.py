"""#4125 Teil 3a (Plan §13c, §13f): der Grund laeuft am ``OrderIntent`` durchs Tor in die Ablage.

- ``_sende_durchs_tor`` nimmt den Grund als optionales Schluesselwort und haengt ihn an den Intent.
- Die Tor-Senke legt bei einem freigegebenen Verkauf zusaetzlich ``sell_reason`` ab — mit derselben
  ``decision_id`` wie ``gateway_decision``.
- Klasse: der mitgegebene Grund, sonst aus ``intent_kind``, sonst ``unknown`` mit WARNING.
- Kein ``sell_reason`` bei Kauf, Ablehnung, Systemfehler und beim Platzieren eines Broker-Stops.
"""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alpaca.trading.enums import OrderSide

from core.contracts import OrderIntent
from core.contracts.verkaufsgrund import Verkaufsgrund
from core.gateway import fabrik
from core.ports import audit_ablage_port
from core.ports.audit_ablage_port import AuditAblagePort

pytestmark = pytest.mark.vc3

_ZEIT = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
_LOSS_CUT = Verkaufsgrund(
    klasse="loss_cut",
    werte={"verlust_pct": -6.5, "stufe": -6.0, "haltedauer_h": 30.0},
    zeitpunkt=_ZEIT,
)


class _Port(AuditAblagePort):
    def __init__(self, wirft_bei=None):
        self.abgelegt = []
        self._wirft_bei = wirft_bei

    def lege_ab(self, art, daten):
        if art == self._wirft_bei:
            raise RuntimeError("Ablage kaputt")
        self.abgelegt.append((art, dict(daten)))

    def von(self, art):
        return [d for a, d in self.abgelegt if a == art]


@pytest.fixture
def port():
    fabrik.reset_ablage_zaehler()
    p = _Port()
    audit_ablage_port.setze_ablage(p)
    yield p
    audit_ablage_port.setze_ablage(None)
    fabrik.reset_ablage_zaehler()


def _intent(**ueber):
    basis = {
        "decision_id": "dec-1",
        "symbol": "AAPL",
        "side": "sell",
        "qty": 2.0,
        "intent_kind": "trim",
        "halted": False,
    }
    basis.update(ueber)
    return OrderIntent(**basis)


def _tor(halted=False, broker=None):
    broker = broker or MagicMock()
    return fabrik.gateway_for(broker, is_halted=lambda user_id=None: halted), broker


# -- Vertrag: der Grund am OrderIntent ---------------------------------------


def test_order_intent_traegt_den_grund_optional():
    assert _intent().verkaufsgrund is None
    assert _intent(verkaufsgrund=_LOSS_CUT).verkaufsgrund is _LOSS_CUT


# -- Tor-Senke: sell_reason ----------------------------------------------------


def test_freigegebener_verkauf_legt_sell_reason_mit_derselben_decision_id_ab(port):
    tor, _ = _tor()
    tor.submit(_intent(verkaufsgrund=_LOSS_CUT), request={})

    (entscheidung,) = port.von("gateway_decision")
    (grund,) = port.von("sell_reason")
    assert grund["decision_id"] == entscheidung["decision_id"] == "dec-1"
    assert grund["klasse"] == "loss_cut"
    assert grund["verlust_pct"] == -6.5
    assert grund["stufe"] == -6.0
    assert grund["haltedauer_h"] == 30.0
    assert grund["zeitpunkt"] == _ZEIT.isoformat()
    assert grund["symbol"] == "AAPL"
    assert grund["qty"] == 2.0


@pytest.mark.parametrize(
    "kind", ["displacement", "panic", "breaker", "strategy_switch"]
)
def test_klasse_aus_intent_kind(port, kind):
    tor, _ = _tor()
    tor.submit(_intent(intent_kind=kind), request={})

    (grund,) = port.von("sell_reason")
    assert grund["klasse"] == kind


def test_ohne_grund_unknown_mit_warning(port, caplog):
    tor, _ = _tor()
    with caplog.at_level(logging.WARNING):
        tor.submit(_intent(intent_kind="trim"), request={})

    (grund,) = port.von("sell_reason")
    assert grund["klasse"] == "unknown"
    assert any("Verkaufsgrund fehlt" in r.getMessage() for r in caplog.records)


def test_kein_sell_reason_bei_kauf(port):
    tor, _ = _tor()
    tor.submit(_intent(side="buy", intent_kind="entry"), request={})
    assert port.von("sell_reason") == []


def test_kein_sell_reason_bei_ablehnung(port):
    tor, _ = _tor(halted=True)
    tor.submit(_intent(verkaufsgrund=_LOSS_CUT), request={})
    assert port.von("gateway_decision")
    assert port.von("sell_reason") == []


def test_kein_sell_reason_bei_systemfehler(port):
    broker = MagicMock()
    broker.submit_order.side_effect = RuntimeError("down")
    tor, _ = _tor(broker=broker)
    with pytest.raises(RuntimeError):
        tor.submit(_intent(verkaufsgrund=_LOSS_CUT), request={})
    assert port.von("sell_reason") == []


def test_kein_sell_reason_beim_platzieren_eines_broker_stops(port):
    tor, _ = _tor()
    tor.submit(
        _intent(intent_kind="stop", is_protective_exit=True, stop_price=95.0),
        request={},
    )
    assert port.von("gateway_decision")
    assert port.von("sell_reason") == []


def test_schutz_exit_geht_hinaus_auch_wenn_sell_reason_wirft():
    audit_ablage_port.setze_ablage(_Port(wirft_bei="sell_reason"))
    try:
        tor, broker = _tor(halted=True)
        entscheidung = tor.submit(
            _intent(
                intent_kind="stop", is_protective_exit=True, verkaufsgrund=_LOSS_CUT
            ),
            request={},
        )
    finally:
        audit_ablage_port.setze_ablage(None)
    assert entscheidung.approved is True
    broker.submit_order.assert_called_once()


def test_storno_legt_keinen_sell_reason_ab(port):
    tor, _ = _tor()
    tor.storniere("stop-1", symbol="AAPL", decision_id="dec-9", grund="Verkauf")
    assert port.von("sell_reason") == []


# -- _sende_durchs_tor nimmt den Grund als Schluesselwort ---------------------


def _sende(monkeypatch, **abweichend):
    from core.engine import order_executor as oe
    from core.engine.order_executor import OrderExecutorMixin

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    gesehen = []
    monkeypatch.setattr(
        oe,
        "_record_gateway_decision",
        lambda d, **kw: gesehen.append(kw.get("intent")),
    )
    argumente = {
        "client": MagicMock(),
        "request": object(),
        "symbol": "AAPL",
        "side_enum": OrderSide.SELL,
        "qty": 3.0,
        "user_id": "u1",
        "decision_id": "entscheidung-9",
    }
    argumente.update(abweichend)
    asyncio.run(OrderExecutorMixin._sende_durchs_tor(**argumente))
    return gesehen


def test_sende_durchs_tor_haengt_den_grund_an_den_intent(monkeypatch):
    (intent,) = _sende(monkeypatch, verkaufsgrund=_LOSS_CUT)
    assert intent.verkaufsgrund is _LOSS_CUT


def test_sende_durchs_tor_ohne_grund_bleibt_gueltig(monkeypatch):
    (intent,) = _sende(monkeypatch)
    assert intent.verkaufsgrund is None


def test_failsafe_reicht_den_grund_an_beide_versuche(monkeypatch):
    from alpaca.common.exceptions import APIError

    from core.engine.order_executor import OrderExecutorMixin

    gesehen = []

    def _side_effect(**kw):
        gesehen.append(kw.get("verkaufsgrund"))
        if len(gesehen) == 1:
            raise APIError("abgelehnt")
        return SimpleNamespace(id="o-2")

    mock_tor = AsyncMock(side_effect=_side_effect)
    monkeypatch.setattr(OrderExecutorMixin, "_sende_durchs_tor", mock_tor)
    asyncio.run(
        OrderExecutorMixin._submit_with_market_failsafe(
            None,
            client=MagicMock(),
            primary_req=SimpleNamespace(client_order_id="exit-0-dec-1"),
            symbol="AAPL",
            qty=2.0,
            side_enum=OrderSide.SELL,
            user_id="u1",
            is_exit=True,
            decision_id="dec-1",
            verkaufsgrund=_LOSS_CUT,
        )
    )
    assert gesehen == [_LOSS_CUT, _LOSS_CUT]


# -- consensus_sell am Signal des Round Tables (Weg 13) ----------------------


def _stimme():
    return SimpleNamespace(
        agent_name="TestAgent", score=0.3, weight=1.0, reasoning="t", vetoed=False
    )


def _zustand():
    return {"symbol": "AAPL", "ohlc": {"close": 100.0}, "ml": None}


def test_konsens_sell_traegt_konsens_und_schwelle(monkeypatch):
    from core.round_table import signal_bau

    monkeypatch.setattr(signal_bau, "SIGNAL_SELL_THRESHOLD", 0.45)
    event = signal_bau._score_to_signal(_zustand(), 0.30, [_stimme()])
    assert event.action == "SELL"
    assert event.verkaufsgrund.klasse == "consensus_sell"
    assert event.verkaufsgrund.werte["konsens"] == pytest.approx(0.30)
    assert event.verkaufsgrund.werte["schwelle"] == pytest.approx(0.45)


def test_konsens_kauf_traegt_keinen_grund(monkeypatch):
    from core.round_table import signal_bau

    monkeypatch.setattr(signal_bau, "SIGNAL_BUY_THRESHOLD", 0.70)
    event = signal_bau._score_to_signal(_zustand(), 0.90, [_stimme()])
    assert event.action == "BUY"
    assert event.verkaufsgrund is None


# -- Absende-Wege reichen den Grund aus dem Signal -----------------------------


def test_verkauf_auf_dem_absendeweg_traegt_den_grund():
    from core.engine.order_aufbau import verkaufsgrund_der_absendung

    event = SimpleNamespace(verkaufsgrund=_LOSS_CUT, decision_context=None)
    assert verkaufsgrund_der_absendung("SELL", event) is _LOSS_CUT


def test_kauf_hat_keinen_grund_auf_dem_absendeweg():
    from core.engine.order_aufbau import verkaufsgrund_der_absendung

    event = SimpleNamespace(verkaufsgrund=_LOSS_CUT, decision_context=None)
    assert verkaufsgrund_der_absendung("BUY", event) is None


def test_desktop_absende_argumente_tragen_den_grund():
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    st = SimpleNamespace(
        **dict.fromkeys(
            (
                "symbol context qty req side_enum trade_client uid schutz_exit fb_pm "
                "fb_redis pc_pm pc_symbol_to_close fallback_displacement_sell_order_id "
                "fallback_displacement_sell_qty fb_deferred_close_symbol "
                "fb_deferred_close_qty"
            ).split()
        ),
        action="SELL",
        event=SimpleNamespace(verkaufsgrund=_LOSS_CUT, decision_context=None),
    )
    assert SignalUebergabeMixin._absende_argumente(st)["verkaufsgrund"] is _LOSS_CUT


def test_folgeversuch_derselben_entscheidung_legt_keinen_zweiten_grund_ab(port):
    """Rest-Market-Order nach einem nicht gefuellten Limit-Exit: dieselbe decision_id."""
    tor, _ = _tor()
    tor.submit(_intent(decision_id="dec-rest", verkaufsgrund=_LOSS_CUT), request={})
    tor.submit(_intent(decision_id="dec-rest", verkaufsgrund=_LOSS_CUT), request={})

    assert len(port.von("gateway_decision")) == 2
    assert len(port.von("sell_reason")) == 1


def test_ungueltiger_grund_verhindert_nie_eine_order(monkeypatch):
    """Ein Mock oder Fremdobjekt als Grund: Die Order geht ohne Grund hinaus, nie gar nicht."""
    (intent,) = _sende(monkeypatch, verkaufsgrund=MagicMock())
    assert intent.verkaufsgrund is None


def test_kontext_mit_fremdobjekt_als_grund_faellt_auf_den_weg_zurueck():
    from core.engine.order_aufbau import verkaufsgrund_aus_kontext

    event = SimpleNamespace(
        verkaufsgrund=MagicMock(),
        decision_context=SimpleNamespace(
            triggered_by_stop=False, portfolio_reason="rotation: dropped to rank 14/40"
        ),
    )
    assert verkaufsgrund_aus_kontext(event).klasse == "rotation"
