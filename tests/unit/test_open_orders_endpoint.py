# tests/unit/test_open_orders_endpoint.py
# TDD (K / #2137): /open-orders returns the operator's OPEN/working orders (display-only), mirroring
# /activities' tenant resolution + fail-soft posture. Alpaca is mocked — no live keys.

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType
from fastapi.testclient import TestClient

from core.engine.api_routes import _order_to_open_order, app

BASE = datetime(2026, 7, 15, 16, 39, 0, tzinfo=timezone.utc)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _open_order(
    oid="o-1",
    symbol="AAPL",
    side=OrderSide.BUY,
    qty="0.2057",
    otype=OrderType.LIMIT,
    limit="320.00",
    stop=None,
    status=OrderStatus.NEW,
):
    return SimpleNamespace(
        id=oid,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=otype,
        limit_price=limit,
        stop_price=stop,
        status=status,
        submitted_at=BASE,
    )


def _call(client, orders_or_exc):
    with patch("core.engine.api_routes.engine") as mock_engine:
        mock_api = MagicMock()
        mock_engine.api = mock_api
        if isinstance(orders_or_exc, Exception):
            mock_api.get_orders = MagicMock(side_effect=orders_or_exc)
        else:
            mock_api.get_orders = MagicMock(return_value=orders_or_exc)
        with patch.dict("os.environ", {"ENGINE_API_KEY": "k", "REQUIRE_SIG": "false"}):
            return client.get("/open-orders", headers={"x-engine-key": "k"})


def test_open_order_mapping_is_field_complete_and_none_safe():
    d = _order_to_open_order(_open_order())
    assert d["symbol"] == "AAPL"
    assert d["side"] == "buy"
    assert d["qty"] == pytest.approx(
        0.2057
    )  # fractional precision preserved (fmtQty on the console)
    assert d["type"] == "limit"
    assert d["limit_price"] == pytest.approx(320.0)
    assert d["stop_price"] is None  # None-safe (no stop on this order)
    assert d["status"] == "new"
    assert d["submitted_at"]


def test_open_orders_endpoint_returns_working_orders(client):
    resp = _call(
        client,
        [
            _open_order("o-1"),
            _open_order(
                "o-2",
                symbol="MSFT",
                side=OrderSide.SELL,
                otype=OrderType.MARKET,
                limit=None,
            ),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["count"] == 2
    syms = {o["symbol"] for o in body["orders"]}
    assert syms == {"AAPL", "MSFT"}
    # a market order has no limit price → None-safe
    msft = next(o for o in body["orders"] if o["symbol"] == "MSFT")
    assert msft["type"] == "market"
    assert msft["limit_price"] is None


def test_open_orders_is_fail_soft_on_broker_error(client):
    resp = _call(client, RuntimeError("broker down"))
    assert resp.status_code == 200  # never 500 — display store must not blank the page
    body = resp.json()
    assert body["status"] == "error"
    assert body["orders"] == []
    assert body["count"] == 0


# --- HITL cancel (#2137): the operator can always pull a working order (EU AI Act Art. 14) ---
def _call_cancel(client, order_id="o-1", raise_exc=None):
    """Returns (response, broker_api_mock, audit_mock). The WORM audit call is stubbed here so the
    behavioural cancel tests stay hermetic (no real hash-chain I/O); a dedicated test below exercises
    the real chain write end to end."""
    with patch("core.engine.api_routes.engine") as mock_engine, patch(
        "core.engine.api_routes.hitl_gate.log_manual_cancel_event", new=AsyncMock()
    ) as audit_mock:
        mock_api = MagicMock()
        mock_engine.api = mock_api
        if raise_exc is not None:
            mock_api.cancel_order_by_id = MagicMock(side_effect=raise_exc)
        else:
            mock_api.cancel_order_by_id = MagicMock(return_value=None)
        with patch.dict("os.environ", {"ENGINE_API_KEY": "k", "REQUIRE_SIG": "false"}):
            body = {} if order_id is None else {"order_id": order_id}
            return (
                client.post("/cancel-order", json=body, headers={"x-engine-key": "k"}),
                mock_api,
                audit_mock,
            )


def test_cancel_order_calls_the_broker_and_succeeds(client):
    resp, api, audit = _call_cancel(client, "o-1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["order_id"] == "o-1"
    api.cancel_order_by_id.assert_called_once_with("o-1")
    # the manual intervention is recorded on the WORM audit chain (Art. 14)
    audit.assert_awaited_once_with(order_id="o-1", actor="operator")


def test_cancel_order_missing_id_is_rejected(client):
    resp, api, audit = _call_cancel(client, order_id=None)
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] == "missing_order_id"
    api.cancel_order_by_id.assert_not_called()
    audit.assert_not_awaited()  # nothing cancelled → nothing to audit


def test_cancel_order_is_fail_soft_on_broker_error(client):
    resp, _, audit = _call_cancel(client, "o-1", raise_exc=RuntimeError("broker down"))
    assert resp.status_code == 200  # never 500
    assert resp.json()["status"] == "error"
    audit.assert_not_awaited()  # the cancel never happened → do not fabricate an audit record


def test_manual_cancel_event_serialises_with_its_discriminator():
    from core.round_table.senate_log import ManualCancelEvent, _hitl_event_to_dict

    d = _hitl_event_to_dict(
        ManualCancelEvent(
            timestamp="2026-07-16T00:00:00Z", actor="operator", order_id="o-9"
        )
    )
    assert d == {
        "event_type": "manual_order_cancel",
        "timestamp": "2026-07-16T00:00:00Z",
        "actor": "operator",
        "order_id": "o-9",
    }


def test_cancel_order_writes_manual_cancel_to_the_worm_audit_chain(client, tmp_path):
    # End-to-end compliance evidence: a successful HITL cancel must land an immutable
    # `manual_order_cancel` record on the tamper-evident Art-14 hash chain (not just a log line).
    import core.hitl_gate as hg
    import core.round_table.runner as runner

    old_senate = getattr(runner, "_senate", None)
    runner._senate = (
        None  # force the hitl_gate fallback logger against the tmp SENATE_LOG_DIR
    )
    hg._fallback_audit_logger = None
    try:
        with patch("core.engine.api_routes.engine") as mock_engine, patch.dict(
            "os.environ",
            {
                "ENGINE_API_KEY": "k",
                "REQUIRE_SIG": "false",
                "SENATE_LOG_DIR": str(tmp_path),
            },
        ):
            mock_engine.api = MagicMock()
            mock_engine.api.cancel_order_by_id = MagicMock(return_value=None)
            resp = client.post(
                "/cancel-order",
                json={"order_id": "abc-123"},
                headers={"x-engine-key": "k"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "success"

        entries = []
        for f in sorted(tmp_path.glob("audit_log_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        cancels = [e for e in entries if e.get("event_type") == "manual_order_cancel"]
        assert len(cancels) == 1, entries
        assert cancels[0]["order_id"] == "abc-123"
        assert cancels[0]["actor"] == "operator"
        assert cancels[0]["timestamp"]  # UTC stamp present
    finally:
        runner._senate = old_senate
        hg._fallback_audit_logger = None


def test_open_orders_sorts_safely_when_submitted_at_is_missing(client):
    # P1 (Archon): a None / absent submitted_at must NOT crash the sort. In Python 3, comparing a
    # datetime against the old "" fallback raises TypeError → the endpoint would fail-soft to empty.
    o_ok = _open_order("o-1")
    o_no_ts = _open_order("o-2", symbol="MSFT")
    o_no_ts.submitted_at = None
    resp = _call(client, [o_ok, o_no_ts])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["count"] == 2  # both returned, no TypeError


def test_open_orders_and_cancel_are_operator_private_not_on_public_gateway():
    # Security (Archon P1): open orders + cancel are operator-private — the desktop calls the engine
    # directly. They must NEVER appear in serve_public_api.py (whose ALLOWED_*_PATHS register PUBLIC
    # proxy routes), unlike /health etc. Assert against the gateway SOURCE (importing it boots the
    # whole proxy app) so the private boundary is locked against drift.
    import os

    gateway = os.path.join(os.path.dirname(__file__), "..", "..", "serve_public_api.py")
    with open(gateway, encoding="utf-8") as f:
        src = f.read()
    assert (
        "/open-orders" not in src
    ), "open orders must stay operator-private (not on the public gateway)"
    assert (
        "/cancel-order" not in src
    ), "cancel must stay operator-private (not on the public gateway)"
