# tests/unit/test_activities_pagination.py
# TDD (DESK-1 arc step 2): /activities pages backwards through get_orders until
# the oldest order, so the fill history is complete regardless of order count
# (vs /recent-trades' single 500-order window). Pages are mocked — no live keys.

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide, OrderStatus
from fastapi.testclient import TestClient

from core.engine.api_routes import app

BASE = datetime(2026, 2, 20, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _order(i, prefix="o", status=OrderStatus.FILLED):
    t = BASE + timedelta(minutes=i)
    return SimpleNamespace(
        id=f"{prefix}-{i}",
        symbol="AMZN",
        side=OrderSide.BUY,
        qty="1",
        filled_qty="1",
        filled_avg_price="100.0",
        status=status,
        submitted_at=t,
        filled_at=t,
    )


def _page(n, prefix="o", status=OrderStatus.FILLED, start=0):
    return [_order(start + i, prefix, status) for i in range(n)]


def _call(client, pages, max_orders=None):
    with patch("core.engine.api_routes.engine") as mock_engine:
        mock_api = MagicMock()
        mock_engine.api = mock_api
        mock_api.get_orders = MagicMock(side_effect=pages)
        with patch.dict("os.environ", {"ENGINE_API_KEY": "k", "REQUIRE_SIG": "false"}):
            url = "/activities"
            if max_orders is not None:
                url += f"?max_orders={max_orders}"
            return client.get(url, headers={"x-engine-key": "k"}), mock_api


def test_paginates_past_the_500_window_and_stops_on_short_page(client):
    resp, api = _call(client, [_page(500, "a"), _page(30, "b", start=500)])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["count"] == 530  # more than the single-call 500 cap
    assert body["truncated"] is False
    assert api.get_orders.call_count == 2  # it actually paged a second time


def test_dedups_orders_repeated_at_the_page_boundary(client):
    page1 = _page(500, "a")
    page2 = [page1[-1]] + _page(29, "b", start=1000)  # 1 duplicate + 29 new
    resp, _ = _call(client, [page1, page2])
    body = resp.json()
    ids = [t["id"] for t in body["trades"]]
    assert len(ids) == len(set(ids))  # no duplicates leaked
    assert body["count"] == 529


def test_only_filled_orders_are_returned(client):
    page = _page(3, "f") + _page(2, "c", status=OrderStatus.CANCELED, start=100)
    resp, _ = _call(client, [page])
    body = resp.json()
    assert body["count"] == 3
    assert all(t["side"] == "buy" for t in body["trades"])


def test_truncated_flag_when_page_cap_is_hit(client):
    # max_orders=500 → one page allowed; a full page means history may continue.
    resp, api = _call(client, [_page(500, "a")], max_orders=500)
    body = resp.json()
    assert body["truncated"] is True
    assert body["count"] == 500
    assert api.get_orders.call_count == 1
