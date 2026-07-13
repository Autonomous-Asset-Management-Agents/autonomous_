# tests/unit/test_recent_trades_fill_filter.py
# TDD (DESK-1): /recent-trades must match the Alpaca OrderStatus ENUM, not str(enum).
#
# Regression: alpaca-py's OrderStatus is a bare ``(str, Enum)`` with no ``__str__``
# override, so on Python 3.11+ ``str(OrderStatus.FILLED) == "OrderStatus.FILLED"``.
# The old filter ``str(o.status).lower() == "filled"`` therefore NEVER matched ->
# 0 trades despite open positions. The same bug corrupted ``side`` (OrderSide.BUY
# -> "orderside.buy"). These tests pin the enum-aware behaviour.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide, OrderStatus
from fastapi.testclient import TestClient

from core.engine.api_routes import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _order(**kw):
    base = {
        "id": "o1",
        "symbol": "AMZN",
        "side": OrderSide.BUY,
        "qty": "10",
        "filled_qty": "10",
        "filled_avg_price": "150.0",
        "status": OrderStatus.FILLED,
        "filled_at": "2026-02-20T15:00:00Z",
        "submitted_at": "2026-02-20T14:59:00Z",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _call(client, orders):
    with patch("core.engine.api_routes.engine") as mock_engine:
        mock_api = MagicMock()
        mock_engine.api = mock_api
        mock_api.get_orders.return_value = orders
        with patch.dict("os.environ", {"ENGINE_API_KEY": "k", "REQUIRE_SIG": "false"}):
            return client.get("/recent-trades", headers={"x-engine-key": "k"})


def test_filled_enum_order_appears_with_normalized_side(client):
    """An OrderStatus.FILLED enum order must appear, with side normalized to 'buy'."""
    resp = _call(client, [_order()])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["trades"]) == 1, body
    t = body["trades"][0]
    assert t["symbol"] == "AMZN"
    assert t["side"] == "buy"  # not "orderside.buy"
    assert t["qty"] == 10.0
    assert t["price"] == 150.0


def test_non_filled_orders_excluded(client):
    """Only filled orders are returned; canceled/other enum states are dropped."""
    orders = [
        _order(id="c1", status=OrderStatus.CANCELED),
        _order(id="f1", status=OrderStatus.FILLED),
    ]
    resp = _call(client, orders)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [t["id"] for t in body["trades"]]
    assert ids == ["f1"], body


def test_raw_string_status_still_matches(client):
    """Back-compat: a plain-string status/side (non-enum) must still work."""
    resp = _call(client, [_order(status="filled", side="sell")])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["trades"]) == 1
    assert body["trades"][0]["side"] == "sell"
