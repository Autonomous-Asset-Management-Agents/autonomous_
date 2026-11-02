"""#4825 — Der Engine-Nachkauf scheitert nicht mehr am eigenen liegenden Broker-Stop.

Plan: docs/4825-*/implementation_plan.md.

Gemessen 13.–26.10.2026 (installierte App 1.1.0, Paper-Konto, ``mifid_decision_log``): 57 Kaeufe
gehaltener Titel (AMGN 32, WSM 12, META 8, …) scheiterten mit 40310000 ``potential wash trade
detected`` / ``opposite side market/stop order exists`` — der eigene GTC-Stop lag auf dem Symbol.
Jetzt gibt die Absendestelle vor dem Kauf die eigenen Stops frei — durchs Tor, wie beim Verkauf
(#4033). Hat ein Stop dabei ausgeloest, wird nicht gekauft (kein Rueckkauf am Stop-Out, #3604).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.unit.test_broker_stops_3976 import _zyklus
from tests.unit.test_verkauf_trotz_stop_4033 import TAG, VerkaufsBroker

pytestmark = pytest.mark.vc4  # Stufen-Marker (#3396): wie test_verkauf_trotz_stop_4033


def _wash_trade_fehler(existing_order_id):
    """Alpacas Ablehnung aus dem Audit vom 13.–26.10. (403/40310000)."""
    from alpaca.common.exceptions import APIError

    antwort = SimpleNamespace(status_code=403)
    return APIError(
        json.dumps(
            {
                "code": 40310000,
                "existing_order_id": existing_order_id,
                "message": "potential wash trade detected. use complex orders",
                "reject_reason": "opposite side market/stop order exists",
            }
        ),
        SimpleNamespace(response=antwort, request=None),
    )


class KaufBroker(VerkaufsBroker):
    """Das Broker-Modell aus #4033, erweitert um Kaeufe und teilweise ausgeloeste Stops.

    Ein Kauf scheitert, solange auf dem Symbol eine Verkaufs-Order offen ist — eigene wie
    fremde (Alpacas Wash-Trade-Schutz).
    """

    def __init__(self, bestaende, **kw):
        super().__init__(bestaende, **kw)
        self.gekauft: list = []
        self.teilweise: dict = (
            {}
        )  # Symbol -> Stueck, die der Stop vor dem Storno fuellte
        self.kauf_scheitert = False

    def cancel_order_by_id(self, oid):
        order = self.orders[oid]
        if order.symbol in self.teilweise and order.status == "new":
            self.stornos.append(oid)
            gefuellt = self.teilweise[order.symbol]
            q, avg = self.bestaende[order.symbol]
            self.bestaende[order.symbol] = (round(q - gefuellt, 9), avg)
            order.status = "canceled"
            order.filled_qty = str(gefuellt)
            return
        super().cancel_order_by_id(oid)

    def submit_order(self, req=None, **kw):
        if str(getattr(req.side, "value", req.side)).lower() != "buy":
            return super().submit_order(req, **kw)
        symbol, qty = req.symbol, float(req.qty)
        gegen = [o for o in self.offene() if o.symbol == symbol]
        if gegen:
            raise _wash_trade_fehler(gegen[0].id)
        if self.kauf_scheitert:
            raise RuntimeError("Broker lehnt den Kauf ab (anderer Grund)")
        q, avg = self.bestaende.get(symbol, (0.0, 0.0))
        self.bestaende[symbol] = (round(q + qty, 9), avg or 100.0)
        self.n += 1
        self.gekauft.append((symbol, qty))
        return SimpleNamespace(id=f"o{self.n}", symbol=symbol, qty=qty, status="filled")


def _mit_stop(bestaende):
    broker = KaufBroker(bestaende)
    _zyklus(broker, TAG, None, pct=3.0)
    return broker


@pytest.fixture
def halt(monkeypatch):
    """Der Halt-Zustand der Engine, je Test umschaltbar (``halt["an"] = True``)."""
    from core.engine import order_executor as oe

    zustand = {"an": False}
    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: zustand["an"]
    )
    return zustand


@pytest.fixture
def datensaetze(monkeypatch, halt):
    from core.engine import order_executor as oe

    gesammelt = []
    monkeypatch.setattr(
        oe, "_record_gateway_decision", lambda d, **_: gesammelt.append(d)
    )
    return gesammelt


def _kauf_request(symbol, qty):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    return MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY
    )


def _kaufe(broker, symbol, qty):
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import OrderExecutorMixin

    return asyncio.run(
        OrderExecutorMixin._sende_durchs_tor(
            client=broker,
            request=_kauf_request(symbol, qty),
            symbol=symbol,
            side_enum=OrderSide.BUY,
            qty=qty,
            user_id="u1",
            decision_id="entscheidung-4825",
        )
    )


def _intent(symbol, qty, halted=False):
    from core.contracts import OrderIntent

    return OrderIntent(
        decision_id="entscheidung-4825",
        symbol=symbol,
        side="buy",
        qty=float(qty),
        intent_kind="entry",
        is_protective_exit=False,
        halted=halted,
    )


def _frei(broker, symbol, qty, halted=False, frist_s=0.2, monkeypatch=None):
    from core.engine import broker_stop_pflege

    if monkeypatch is not None:
        monkeypatch.setattr(broker_stop_pflege, "STORNO_FRIST_S", frist_s)
    req = _kauf_request(symbol, qty)
    return asyncio.run(
        broker_stop_pflege.gib_kauf_frei(broker, _intent(symbol, qty, halted), req)
    )


# --------------------------------------------------------------------------- Absendestelle
class TestSendeDurchsTorKauf:
    def test_topup_with_own_stop_goes_out(self, datensaetze):
        """Gherkin 1: 14,22 AMGN, eigener GTC-Stop ueber 14. Heute rot: 40310000."""
        broker = _mit_stop({"AMGN": (14.22, 300.0)})
        assert broker.gedeckt("AMGN") == pytest.approx(14.22)

        order = _kaufe(broker, "AMGN", 9.9)

        assert order.status == "filled"
        assert broker.gekauft == [("AMGN", pytest.approx(9.9))]
        assert broker.bestaende["AMGN"][0] == pytest.approx(24.12)
        assert broker.gedeckt("AMGN") == 0

    def test_first_buy_without_order_cancels_nothing(self, datensaetze):
        """Gherkin 4: Erstkauf — keine Order auf dem Symbol, gekauft wird wie heute."""
        broker = KaufBroker({})

        _kaufe(broker, "AMGN", 9.9)

        assert broker.stornos == []
        assert broker.gekauft == [("AMGN", pytest.approx(9.9))]

    def test_halted_buy_cancels_no_stop(self, datensaetze, halt):
        """Gherkin 6: Halt — das Tor lehnt den Kauf ab, kein Stop faellt."""
        from core.exceptions import TradingHaltedError

        broker = _mit_stop({"AMGN": (14.22, 300.0)})
        halt["an"] = True

        with pytest.raises(TradingHaltedError):
            _kaufe(broker, "AMGN", 9.9)

        assert broker.stornos == []
        assert broker.gedeckt("AMGN") == pytest.approx(14.22)
        assert broker.gekauft == []

    def test_fired_stop_sends_no_buy(self, datensaetze):
        """Gherkin 2 an der Absendestelle: kein Rueckkauf am Stop-Out (#3604)."""
        from core.engine.broker_stop_pflege import KaufNichtFrei

        broker = _mit_stop({"AMGN": (14.0, 300.0)})
        broker.ausgeloest.add("AMGN")

        with pytest.raises(KaufNichtFrei, match="ausgeloest"):
            _kaufe(broker, "AMGN", 9.9)
        assert broker.gekauft == []


# --------------------------------------------------------------------------- Freigabe
class TestGibKaufFrei:
    def test_own_stops_are_cancelled_and_the_buy_is_unchanged(self, datensaetze):
        broker = _mit_stop({"AMGN": (14.22, 300.0)})
        intent, req = _intent("AMGN", 9.9), _kauf_request("AMGN", 9.9)
        from core.engine.broker_stop_pflege import gib_kauf_frei

        neu_intent, neu_req = asyncio.run(gib_kauf_frei(broker, intent, req))

        assert (neu_intent, neu_req) == (intent, req)
        assert len(broker.stornos) == 2  # GTC ueber 14 und Tages-Stop ueber 0,22
        assert broker.gedeckt("AMGN") == 0

    def test_fully_fired_stop_means_no_buy(self, datensaetze):
        """Gherkin 2: Der Stop hat vor dem Storno ganz ausgeloest (Status ``filled``)."""
        from core.engine.broker_stop_pflege import KaufNichtFrei, StueckeNichtFrei

        broker = _mit_stop({"AMGN": (14.0, 300.0)})
        broker.ausgeloest.add("AMGN")

        with pytest.raises(KaufNichtFrei, match="ausgeloest") as fehler:
            _frei(broker, "AMGN", 9.9)
        assert isinstance(fehler.value, StueckeNichtFrei)
        assert "AMGN BUY nicht abgesetzt" in str(fehler.value)

    def test_partially_fired_stop_means_no_buy(self, datensaetze):
        """Gherkin 2: ``canceled`` mit ``filled_qty > 0`` — teilweise ausgeloest."""
        from core.engine.broker_stop_pflege import KaufNichtFrei

        broker = _mit_stop({"AMGN": (14.0, 300.0)})
        broker.teilweise["AMGN"] = 3.0

        with pytest.raises(KaufNichtFrei, match="ausgeloest"):
            _frei(broker, "AMGN", 9.9)

    def test_unconfirmed_cancel_means_no_buy(self, datensaetze, monkeypatch):
        """Gherkin 3: Der Broker bestaetigt den Storno nicht in der Frist."""
        from core.engine.broker_stop_pflege import KaufNichtFrei

        broker = _mit_stop({"AMGN": (14.22, 300.0)})
        broker.storno_bestaetigt = False

        with pytest.raises(KaufNichtFrei, match="Storno"):
            _frei(broker, "AMGN", 9.9, frist_s=0.1, monkeypatch=monkeypatch)

    def test_no_open_order_means_no_cancel(self, datensaetze):
        """Gherkin 4: Erstkauf."""
        broker = KaufBroker({})

        _frei(broker, "AMGN", 9.9)

        assert broker.stornos == []

    def test_foreign_order_is_left_alone(self, datensaetze):
        """Gherkin 5: Eine fremde Verkaufs-Order bleibt liegen."""
        broker = _mit_stop({"AMGN": (3.0, 300.0)})  # eigener Stop ueber 3
        broker.bestaende["AMGN"] = (5.0, 300.0)
        fremd = broker.fremde_order("AMGN", 2.0)

        _frei(broker, "AMGN", 9.9)

        assert fremd not in broker.stornos
        assert broker.orders[fremd].status == "new"
        assert len(broker.stornos) == 1

    def test_halted_cancels_nothing(self, datensaetze):
        """Gherkin 6: Halt — das Tor lehnt den Kauf ohnehin ab."""
        broker = _mit_stop({"AMGN": (14.22, 300.0)})

        _frei(broker, "AMGN", 9.9, halted=True)

        assert broker.stornos == []
        assert broker.gedeckt("AMGN") == pytest.approx(14.22)

    def test_shadow_mode_cancels_nothing(self, datensaetze):
        from core.engine.broker_stop_pflege import gib_kauf_frei
        from core.engine.order_executor import DryRunOrderProxy

        proxy = DryRunOrderProxy("u1")
        req = _kauf_request("AMGN", 9.9)
        intent = _intent("AMGN", 9.9)

        assert asyncio.run(gib_kauf_frei(proxy, intent, req)) == (intent, req)

    def test_unreadable_order_list_cancels_nothing(self, datensaetze):
        """Fail-closed: ohne Orderliste wird nichts blind storniert."""
        broker = _mit_stop({"AMGN": (14.22, 300.0)})

        def _kaputt(*a, **k):
            raise RuntimeError("broker down")

        broker.get_orders = _kaputt
        _frei(broker, "AMGN", 9.9)

        assert broker.stornos == []

    def test_the_cancel_goes_through_the_gate(self, datensaetze):
        """Jeder Storno erzeugt einen Entscheidungsdatensatz des Tors."""
        broker = _mit_stop({"AMGN": (14.0, 300.0)})
        datensaetze.clear()

        _frei(broker, "AMGN", 9.9)

        assert len(datensaetze) == 1
        assert datensaetze[0].approved
        assert "Storno" in datensaetze[0].detail
        assert "Kauf" in datensaetze[0].detail


# --------------------------------------------------------------------------- Danach: Pflege
class TestNachkaufDannPflege:
    def test_next_maintenance_covers_the_whole_position(self, datensaetze):
        """Gherkin 1, zweiter Teil: Nach dem Nachkauf liegt ein Stop ueber 24 Stueck."""
        broker = _mit_stop({"AMGN": (14.22, 300.0)})

        _kaufe(broker, "AMGN", 9.9)
        _zyklus(broker, TAG, TAG, pct=3.0)

        assert broker.gedeckt("AMGN") == pytest.approx(24.12)
        assert 24.0 in [o.qty for o in broker.offene() if o.symbol == "AMGN"]

    def test_failed_buy_gets_the_old_stop_back(self, datensaetze):
        """Soll 4: Scheitert der Kauf, legt die Pflege den Stop fuer den alten Bestand neu."""
        broker = _mit_stop({"AMGN": (14.22, 300.0)})
        broker.kauf_scheitert = True

        with pytest.raises(RuntimeError, match="anderer Grund"):
            _kaufe(broker, "AMGN", 9.9)
        assert broker.gedeckt("AMGN") == 0
        _zyklus(broker, TAG, TAG, pct=3.0)

        assert broker.gedeckt("AMGN") == pytest.approx(14.22)
