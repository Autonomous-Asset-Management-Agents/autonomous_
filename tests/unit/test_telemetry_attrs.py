"""OBS-3 (#2636): pure span-attribute builders for the money spans.

The enrichment logic lives in pure functions so it is testable without mocking the live
order path; the call sites (order_executor / lstm_strategy) apply the returned dict via
span.set_attribute. Every value must be an OTel-safe primitive (str/bool/int/float) and the
builders must never raise (telemetry must never break the trading path).
"""

from decimal import Decimal
from types import SimpleNamespace

from core.telemetry_attrs import inference_span_attributes, order_span_attributes


def test_order_span_attributes_full():
    req = SimpleNamespace(
        qty="10",
        side=SimpleNamespace(value="sell"),  # enum-like (OrderSide)
        time_in_force=SimpleNamespace(value="day"),  # enum-like (TimeInForce)
    )
    a = order_span_attributes(
        "AAPL", "SELL", req=req, order=SimpleNamespace(id="ord_123"), paper_trading=True
    )
    assert a["aaa.trade.symbol"] == "AAPL"
    assert a["aaa.trade.action"] == "SELL"
    assert a["aaa.order.qty"] == "10"
    assert a["aaa.order.side"] == "sell"  # enum unwrapped to .value
    assert a["aaa.order.tif"] == "day"
    assert "aaa.order.type" in a
    assert a["aaa.order.broker_id"] == "ord_123"
    assert a["aaa.trade.paper"] is True
    # every value OTel-safe
    assert all(isinstance(v, (str, bool, int, float)) for v in a.values())


def test_order_span_attributes_minimal():
    assert order_span_attributes("MSFT", "BUY") == {
        "aaa.trade.symbol": "MSFT",
        "aaa.trade.action": "BUY",
    }


def test_order_span_attributes_never_raises_on_garbage_req():
    a = order_span_attributes("X", "BUY", req=object(), order=object())
    assert a["aaa.trade.symbol"] == "X"  # still returns the basics, no exception


def test_order_span_attributes_coerces_nonprimitive_to_str():
    a = order_span_attributes("X", "BUY", req=SimpleNamespace(qty=Decimal("3.5")))
    assert a["aaa.order.qty"] == "3.5" and isinstance(a["aaa.order.qty"], str)


def test_inference_span_attributes_full():
    a = inference_span_attributes(
        "AAPL", market_data={"vix": 13.25}, model_name="LSTMDynamic", prediction=0.42
    )
    assert a["symbol"] == "AAPL"
    assert a["market.vix"] == 13.25
    assert a["aaa.model.name"] == "LSTMDynamic"
    assert a["aaa.model.raw_output"] == 0.42


def test_inference_span_attributes_defaults_and_safe():
    assert inference_span_attributes("MSFT") == {"symbol": "MSFT"}
    a = inference_span_attributes("Z", market_data={})  # no vix key -> default
    assert a["market.vix"] == 20.0
