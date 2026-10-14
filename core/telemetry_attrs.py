# core/telemetry_attrs.py
# OBS-3 (#2636, INF-13 #1368) — pure builders for the "money span" attributes.
#
# The live-order and model-inference spans were near-empty (broker.submit_order.live carried
# only `trade.action`; model.inference only `symbol`+`market.vix`), so the 48 live orders and
# 62k inferences in the diagnostics export were not auditable. These pure functions build the
# enrichment dict; the call sites (order_executor / lstm_strategy) apply it via
# span.set_attribute. Kept pure so the trading path never depends on telemetry and the logic
# is unit-testable without mocking the order flow.
#
# Contract: values are OTel-safe primitives (str/bool/int/float) only; NEVER raises.
from __future__ import annotations

from typing import Any, Dict, Optional


def _primitive(v: Any) -> Any:
    """Unwrap enum-like values (OrderSide/TimeInForce → ``.value``) and coerce anything that
    is not an OTel-safe primitive to ``str``. ``None`` passes through (caller drops it).
    """
    if v is None:
        return None
    v = getattr(v, "value", v)  # OrderSide.SELL -> "sell"; plain values unchanged
    if isinstance(v, (str, bool, int, float)):
        return v
    return str(v)


def order_span_attributes(
    symbol: str,
    action: str,
    req: Any = None,
    order: Any = None,
    paper_trading: Optional[bool] = None,
) -> Dict[str, Any]:
    """Attributes for a ``broker.submit_order.live`` span (aaa.* trading namespace).

    From the order request (``req``): order type, qty/notional, side, TIF, limit price.
    From the broker result (``order``): the broker order id. Plus symbol/action/paper flag.
    Pure; never raises; only primitive values."""
    attrs: Dict[str, Any] = {}

    def _put(key: str, value: Any) -> None:
        p = _primitive(value)
        if p is not None:
            attrs[key] = p

    try:
        _put("aaa.trade.symbol", symbol)
        _put("aaa.trade.action", action)
        if req is not None:
            _put("aaa.order.type", type(req).__name__)
            _put("aaa.order.qty", getattr(req, "qty", None))
            _put("aaa.order.notional", getattr(req, "notional", None))
            _put("aaa.order.side", getattr(req, "side", None))
            _put("aaa.order.tif", getattr(req, "time_in_force", None))
            _put("aaa.order.limit_price", getattr(req, "limit_price", None))
        if order is not None:
            _put("aaa.order.broker_id", getattr(order, "id", None))
        if paper_trading is not None:
            attrs["aaa.trade.paper"] = bool(paper_trading)
    except Exception:
        pass
    return attrs


def inference_span_attributes(
    symbol: str,
    market_data: Optional[dict] = None,
    model_name: Optional[str] = None,
    prediction: Optional[float] = None,
) -> Dict[str, Any]:
    """Attributes for a ``model.inference`` span (symbol, VIX, model name, raw prediction).
    Pure; never raises; only primitive values."""
    attrs: Dict[str, Any] = {"symbol": symbol}
    try:
        if market_data is not None:
            attrs["market.vix"] = float(market_data.get("vix", 20.0))
        if model_name:
            attrs["aaa.model.name"] = str(model_name)
        if prediction is not None:
            attrs["aaa.model.raw_output"] = float(prediction)
    except Exception:
        pass
    return attrs
