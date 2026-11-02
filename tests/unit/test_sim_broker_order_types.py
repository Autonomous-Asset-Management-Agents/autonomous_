"""Sim-Broker: ruhende Stop-Orders, Ablehnung, Teilausfuehrung (#3377, ARC-E1.1).

Warum diese Tests zuerst gruen sein muessen: Der Sim-Broker ist das Messinstrument
fuer die Kettenabnahme CH-3. Ein Instrument, das selbst ungeprueft ist, taugt nicht
als Beweis. Der Plan (docs/3377-e2e-ch2-ch3-sim-broker/implementation_plan.md, §7)
verlangt deshalb zwei Schichten: erst diese Tests gruen, dann CH-2/CH-3 rot.

Die drei Faehigkeiten sind Test-Vorrichtungen, keine Produktschalter — sie haengen am
Broker-Objekt und sind ohne Zutun aus. Der letzte Test haelt genau das fest.
"""

from types import SimpleNamespace

import pytest
from alpaca.trading.enums import OrderSide

try:
    from core.sim.broker import VirtualLiveBroker
    from core.sim.fill_engine import DummyPosition
except (
    ImportError
):  # pragma: no cover - Pfad-Fallback wie in tests/unit/test_sim_components.py
    from ai_trading_bot.core.sim.broker import VirtualLiveBroker
    from ai_trading_bot.core.sim.fill_engine import DummyPosition


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Vorrichtung: feste Preise statt Korpus — der Test steuert den Kurs, nicht die Platte
# ---------------------------------------------------------------------------


class _FakeDataClient:
    """Liefert genau einen Balken je Symbol, mit dem Preis, den der Test setzt."""

    def __init__(self, prices):
        self.prices = prices

    def get_stock_bars(self, request):
        symbol = request.symbol_or_symbols
        price = self.prices[symbol]
        return SimpleNamespace(data={symbol: [SimpleNamespace(close=price)]})


def _order(symbol, qty, side, *, stop_price=None, status="accepted"):
    return SimpleNamespace(
        symbol=symbol,
        qty=qty,
        side=side,
        status=status,
        stop_price=stop_price,
        filled_qty=None,
        filled_avg_price=None,
        filled_at=None,
    )


@pytest.fixture
def broker_at():
    """Broker mit steuerbarem Preis; gibt (broker, set_price) zurueck."""

    def _make(symbol, price):
        broker = VirtualLiveBroker()
        client = _FakeDataClient({symbol: price})
        broker.fill_engine.data_client = client
        return broker, (lambda new_price: client.prices.__setitem__(symbol, new_price))

    return _make


# ---------------------------------------------------------------------------
# 1. Ruhende Stop-Order
# ---------------------------------------------------------------------------


def test_stop_sell_rests_until_price_crosses(broker_at):
    """Ein Schutz-Stop liegt beim Broker und feuert erst, wenn der Kurs ihn erreicht."""
    broker, set_price = broker_at("AAPL", 100.0)
    broker.positions["AAPL"] = DummyPosition("AAPL", 10, 100.0)
    order = _order("AAPL", 10, OrderSide.SELL, stop_price=90.0)

    broker.fill_engine.process_orders([order])
    assert order.status == "accepted", "Stop darf ueber der Schwelle nicht ausloesen"
    assert "AAPL" in broker.positions

    set_price(89.5)
    filled = broker.fill_engine.process_orders([order])

    assert order.status == "filled"
    assert filled == [order]
    assert "AAPL" not in broker.positions


def test_stop_buy_triggers_upwards(broker_at):
    """Gegenprobe zur Richtung: ein BUY-Stop loest nach oben aus, nicht nach unten."""
    broker, set_price = broker_at("MSFT", 100.0)
    order = _order("MSFT", 1, OrderSide.BUY, stop_price=110.0)

    broker.fill_engine.process_orders([order])
    assert order.status == "accepted"

    set_price(110.5)
    broker.fill_engine.process_orders([order])
    assert order.status == "filled"


def test_order_without_stop_price_is_immediate(broker_at):
    """Ohne stop_price bleibt es bei Fill-at-submit — das bisherige Verhalten."""
    broker, _ = broker_at("AAPL", 100.0)
    order = _order("AAPL", 2, OrderSide.BUY)

    broker.fill_engine.process_orders([order])

    assert order.status == "filled"
    assert float(order.filled_qty) == 2.0


# ---------------------------------------------------------------------------
# 2. Ablehnung
# ---------------------------------------------------------------------------


def test_rejection_is_deterministic_and_leaves_the_book_untouched(broker_at):
    broker, _ = broker_at("AAPL", 100.0)
    broker.sim_reject_symbols = {"AAPL"}
    cash_before = broker.cash
    order = _order("AAPL", 3, OrderSide.BUY)

    broker.fill_engine.process_orders([order])

    assert order.status == "rejected"
    assert broker.cash == cash_before
    assert "AAPL" not in broker.positions

    # Eine abgelehnte Order wird nicht spaeter doch noch gefuellt.
    broker.fill_engine.process_orders([order])
    assert order.status == "rejected"


# ---------------------------------------------------------------------------
# 3. Teilausfuehrung
# ---------------------------------------------------------------------------


def test_partial_fill_accumulates_until_complete(broker_at):
    broker, _ = broker_at("AAPL", 100.0)
    broker.positions["AAPL"] = DummyPosition("AAPL", 10, 100.0)
    broker.sim_partial_fill_ratio = {"AAPL": 0.5}
    order = _order("AAPL", 10, OrderSide.SELL)

    filled = broker.fill_engine.process_orders([order])
    assert order.status == "partially_filled"
    assert float(order.filled_qty) == 5.0
    assert filled == [], "eine Teilausfuehrung ist noch kein erledigter Auftrag"

    broker.fill_engine.process_orders([order])
    assert float(order.filled_qty) == 7.5
    assert order.status == "partially_filled"

    broker.sim_partial_fill_ratio = {}
    filled = broker.fill_engine.process_orders([order])
    assert order.status == "filled"
    assert filled == [order]


# ---------------------------------------------------------------------------
# 4. Determinismus und Nicht-Regression
# ---------------------------------------------------------------------------


def test_same_inputs_produce_the_same_result(broker_at):
    """Zwei identische Laeufe, identisches Ergebnis — ohne Zufall im Instrument."""

    def run():
        broker, set_price = broker_at("AAPL", 100.0)
        broker.positions["AAPL"] = DummyPosition("AAPL", 10, 100.0)
        broker.sim_reject_symbols = {"TSLA"}
        stop = _order("AAPL", 10, OrderSide.SELL, stop_price=95.0)
        rejected = _order("TSLA", 1, OrderSide.BUY)
        broker.fill_engine.process_orders([stop, rejected])
        set_price(94.0)
        broker.fill_engine.process_orders([stop, rejected])
        return [(o.symbol, o.status, o.filled_qty) for o in (stop, rejected)], round(
            broker.cash, 6
        )

    assert run() == run()


def test_untouched_broker_behaves_exactly_as_before(broker_at):
    """Ohne gesetzte Vorrichtung gibt es weder Ablehnung noch Teil-Fill noch Ruhen."""
    broker, _ = broker_at("AAPL", 100.0)

    assert broker.sim_reject_symbols == set()
    assert broker.sim_partial_fill_ratio == {}

    order = _order("AAPL", 4, OrderSide.BUY)
    filled = broker.fill_engine.process_orders([order])

    assert order.status == "filled"
    assert filled == [order]
    assert float(broker.positions["AAPL"].qty) == 4.0
