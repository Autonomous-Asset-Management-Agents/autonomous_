"""#4033 — Der Engine-Verkauf scheitert nicht mehr am eigenen liegenden Broker-Stop.

Plan: docs/4033-*/implementation_plan.md (PR #4045).

Gemessen am 28.09.2026 (installierte App 0.5.2, Broker-Stops an): FCX und NEM SELL scheiterten mit
40310000 ``insufficient qty available`` — der eigene GTC-Stop band 276 bzw. 12 Stueck. Seit #3990
liegt bei jeder Position ein Stop ueber alle ganzen Stuecke; damit scheitert jeder Engine-Verkauf
ganzer Stuecke. Jetzt gibt die Absendestelle vor dem Verkauf die eigenen Stops frei — durchs Tor.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from tests.unit.test_broker_stops_3976 import FakeBroker, _zyklus

pytestmark = pytest.mark.vc4  # Stufen-Marker (#3396): wie test_broker_stops_3976

TAG = date(2026, 9, 28)


def _gebunden_fehler(symbol_qty, frei, gebunden):
    """Alpacas Ablehnung aus dem Audit vom 28.09. (403/40310000)."""
    from alpaca.common.exceptions import APIError

    antwort = SimpleNamespace(status_code=403)
    return APIError(
        json.dumps(
            {
                "available": str(frei),
                "code": 40310000,
                "existing_qty": str(symbol_qty),
                "held_for_orders": str(gebunden),
                "message": "insufficient qty available for order",
            }
        ),
        SimpleNamespace(response=antwort, request=None),
    )


def _nicht_stornierbar():
    from alpaca.common.exceptions import APIError

    antwort = SimpleNamespace(status_code=422)
    return APIError(
        json.dumps({"code": 42210000, "message": "order is not cancelable"}),
        SimpleNamespace(response=antwort, request=None),
    )


class VerkaufsBroker(FakeBroker):
    """Das Broker-Modell aus #3976, erweitert um Verkaeufe und ausgeloeste Stops."""

    def __init__(self, bestaende, **kw):
        super().__init__(bestaende, **kw)
        self.verkauft: list = []
        self.stornos: list = []
        self.ausgeloest: set = (
            set()
        )  # Symbole, deren Stop beim Storno schon gefuellt ist

    def fremde_order(self, symbol, qty):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = SimpleNamespace(
            id=oid,
            symbol=symbol,
            qty=float(qty),
            stop_price=None,
            time_in_force="day",
            client_order_id="manual-order-1",
            status="new",
        )
        return oid

    def cancel_order_by_id(self, oid):
        self.stornos.append(oid)
        order = self.orders[oid]
        if order.symbol in self.ausgeloest and order.status == "new":
            # Der Stop hat ausgeloest, bevor der Storno ankam.
            order.status = "filled"
            q, avg = self.bestaende[order.symbol]
            self.bestaende[order.symbol] = (round(q - order.qty, 9), avg)
            raise _nicht_stornierbar()
        super().cancel_order_by_id(oid)

    def submit_order(self, req=None, **kw):
        if getattr(req, "stop_price", None) is not None:
            return super().submit_order(req, **kw)
        symbol, qty = req.symbol, float(req.qty)
        pos = next((p for p in self.get_all_positions() if p.symbol == symbol), None)
        gehalten = float(pos.qty) if pos else 0.0
        frei = float(pos.qty_available) if pos else 0.0
        if qty > frei + 1e-9:
            raise _gebunden_fehler(gehalten, frei, round(gehalten - frei, 9))
        q, avg = self.bestaende[symbol]
        rest = round(q - qty, 9)
        if rest > 0:
            self.bestaende[symbol] = (rest, avg)
        else:
            del self.bestaende[symbol]
        self.n += 1
        self.verkauft.append((symbol, qty))
        return SimpleNamespace(id=f"o{self.n}", symbol=symbol, qty=qty, status="filled")


def _mit_stop(bestaende):
    broker = VerkaufsBroker(bestaende)
    _zyklus(broker, TAG, None, pct=3.0)
    return broker


@pytest.fixture
def datensaetze(monkeypatch):
    from core.engine import order_executor as oe

    gesammelt = []
    monkeypatch.setattr(oe, "_record_gateway_decision", gesammelt.append)
    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    return gesammelt


def _frei(broker, symbol, menge, frist_s=0.2):
    from core.engine.broker_stop_pflege import gib_stuecke_frei

    return asyncio.run(gib_stuecke_frei(broker, symbol, menge, frist_s=frist_s))


# --------------------------------------------------------------------------- Freigabe
class TestGibStueckeFrei:
    def test_own_stop_is_cancelled_and_the_full_position_is_free(self, datensaetze):
        """Gherkin 1: 276,83 FCX, eigener GTC-Stop ueber 276."""
        broker = _mit_stop({"FCX": (276.828459, 72.04)})
        assert broker.gedeckt("FCX") == pytest.approx(276.828459)

        freigabe = _frei(broker, "FCX", 276.828459)

        assert freigabe.menge == pytest.approx(276.828459)
        assert freigabe.grund is None
        assert broker.gedeckt("FCX") == 0
        assert len(broker.stornos) == 2  # GTC ueber 276 und Tages-Stop ueber 0,83

    def test_triggered_stop_limits_the_sale_to_the_holding(self, datensaetze):
        """Gherkin 2: Der Stop ueber 276 hat beim Storno schon ausgeloest."""
        broker = VerkaufsBroker({"FCX": (276.828459, 72.04)})
        broker.bestaende["FCX"] = (276.0, 72.04)
        _zyklus(broker, TAG, None, pct=3.0)  # nur der GTC ueber 276
        broker.bestaende["FCX"] = (276.828459, 72.04)  # Bruchstueck danach gekauft
        broker.ausgeloest.add("FCX")

        freigabe = _frei(broker, "FCX", 276.828459)

        assert freigabe.menge == pytest.approx(0.828459)
        assert freigabe.menge <= broker.bestaende["FCX"][0]

    def test_nothing_left_after_the_stop_fired_means_no_sale(self, datensaetze):
        broker = VerkaufsBroker({"NEM": (12.0, 50.0)})
        _zyklus(broker, TAG, None, pct=3.0)
        broker.ausgeloest.add("NEM")

        freigabe = _frei(broker, "NEM", 12.0)

        assert freigabe.menge == 0
        assert freigabe.grund

    def test_unconfirmed_cancel_frees_nothing(self, datensaetze):
        """Gherkin 3: Der Broker bestaetigt den Storno nicht in der Frist."""
        broker = _mit_stop({"NEM": (12.909594, 50.0)})
        broker.storno_bestaetigt = False

        freigabe = _frei(broker, "NEM", 12.909594, frist_s=0.1)

        assert freigabe.menge == 0
        assert "Storno" in freigabe.grund

    def test_foreign_order_is_left_alone(self, datensaetze):
        """Gherkin 5: Eine fremde Verkaufs-Order ueber 2 Stueck bleibt liegen."""
        broker = _mit_stop({"AAPL": (3.0, 100.0)})  # eigener Stop ueber 3
        broker.bestaende["AAPL"] = (5.0, 100.0)
        fremd = broker.fremde_order("AAPL", 2.0)  # von Hand, bindet 2

        freigabe = _frei(broker, "AAPL", 5.0)

        assert fremd not in broker.stornos
        assert broker.orders[fremd].status == "new"
        assert freigabe.menge == pytest.approx(3.0)

    def test_no_open_order_means_no_cancel(self, datensaetze):
        """Gherkin 6: Kein Stop liegt — verkauft wird wie heute."""
        broker = VerkaufsBroker({"X": (10.0, 100.0)})

        freigabe = _frei(broker, "X", 10.0)

        assert freigabe.menge == pytest.approx(10.0)
        assert freigabe.grund is None
        assert broker.stornos == []

    def test_unreadable_order_list_cancels_nothing(self, datensaetze):
        """Fail-closed: ohne Orderliste wird nichts blind storniert, Verkauf wie heute."""
        broker = _mit_stop({"X": (10.0, 100.0)})

        def _kaputt(*a, **k):
            raise RuntimeError("broker down")

        broker.get_orders = _kaputt
        freigabe = _frei(broker, "X", 10.0)

        assert broker.stornos == []
        assert freigabe.menge == pytest.approx(10.0)

    def test_the_cancel_goes_through_the_gate(self, datensaetze):
        """Gherkin 7: Das Tor schreibt zu jedem Storno einen Entscheidungsdatensatz."""
        broker = _mit_stop({"FCX": (276.0, 72.04)})
        datensaetze.clear()

        _frei(broker, "FCX", 276.0)

        assert len(datensaetze) == 1
        assert datensaetze[0].approved
        assert "Storno" in datensaetze[0].detail


# --------------------------------------------------------------------------- Absendestelle
def _verkaufe(broker, symbol, qty):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.engine.order_executor import OrderExecutorMixin

    req = MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY
    )
    return asyncio.run(
        OrderExecutorMixin._sende_durchs_tor(
            client=broker,
            request=req,
            symbol=symbol,
            side_enum=OrderSide.SELL,
            qty=qty,
            user_id="u1",
            decision_id="entscheidung-4033",
        )
    )


class TestSendeDurchsTor:
    def test_sale_with_own_stop_goes_out(self, datensaetze):
        """Heute rot: 40310000, ``held_for_orders`` 276."""
        broker = _mit_stop({"FCX": (276.828459, 72.04)})

        order = _verkaufe(broker, "FCX", 276.828459)

        assert order.status == "filled"
        assert broker.verkauft == [("FCX", pytest.approx(276.828459))]
        assert "FCX" not in broker.bestaende

    def test_sale_after_fired_stop_never_exceeds_the_holding(self, datensaetze):
        broker = VerkaufsBroker({"FCX": (276.0, 72.04)})
        _zyklus(broker, TAG, None, pct=3.0)
        broker.bestaende["FCX"] = (276.828459, 72.04)
        broker.ausgeloest.add("FCX")

        _verkaufe(broker, "FCX", 276.828459)

        assert broker.verkauft == [("FCX", pytest.approx(0.828459))]

    def test_unconfirmed_cancel_sends_nothing_and_names_the_reason(
        self, datensaetze, monkeypatch
    ):
        from core.engine import broker_stop_pflege

        monkeypatch.setattr(broker_stop_pflege, "STORNO_FRIST_S", 0.1)
        broker = _mit_stop({"NEM": (12.0, 50.0)})
        broker.storno_bestaetigt = False

        with pytest.raises(Exception, match="Storno"):
            _verkaufe(broker, "NEM", 12.0)
        assert broker.verkauft == []

    def test_buy_is_unchanged(self, datensaetze):
        from unittest.mock import MagicMock

        from alpaca.trading.enums import OrderSide

        from core.engine.order_executor import OrderExecutorMixin

        client = MagicMock()
        asyncio.run(
            OrderExecutorMixin._sende_durchs_tor(
                client=client,
                request=object(),
                symbol="AAPL",
                side_enum=OrderSide.BUY,
                qty=3.0,
                user_id="u1",
                decision_id="kauf-1",
            )
        )
        client.get_orders.assert_not_called()
        client.cancel_order_by_id.assert_not_called()
        client.submit_order.assert_called_once()


# --------------------------------------------------------------------------- Teilverkauf
class TestTeilverkauf:
    def test_next_maintenance_covers_the_rest(self, datensaetze):
        """Gherkin 4: 50 Stueck mit Stop ueber 50, Verkauf von 10 → Stop ueber 40."""
        broker = _mit_stop({"X": (50.0, 100.0)})

        _verkaufe(broker, "X", 10.0)
        assert broker.gedeckt("X") == 0
        _zyklus(broker, TAG, TAG, pct=3.0)

        assert broker.gedeckt("X") == pytest.approx(40.0)
