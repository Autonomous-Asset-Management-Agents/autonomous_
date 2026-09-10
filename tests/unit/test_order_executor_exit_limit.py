"""#2558 — Exit-selective marketable-limit execution (TDD Red→Green).

Design: Option A (owner-approved), plan docs/specs/exit-marketable-limit-execution.

Gives EXITS (SELLs) a marketable-limit path — crossing the spread for less slippage
than a plain market order — behind a NEW exit-selective flag ``USE_LIMIT_EXITS``,
WITHOUT touching the buy side, and WITHOUT duplicating the existing limit price math.
An exit MUST always fill: on broker rejection / exception the exit falls back to a
plain market order so the position always closes (fail-safe).

Seams under test (kept small + pure so Red→Green is fast, no 800-line method drive):
  * build_broker_order_request      — limit-vs-market routing + price heuristic
  * OrderExecutorMixin._submit_with_market_failsafe — rejection → market fallback
  * exit_failsafe_remaining_qty     — confirmed-unfilled remainder (non-fill guard)
"""

import uuid
from unittest.mock import MagicMock

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from core.engine.order_executor import (
    OrderExecutorMixin,
    build_broker_order_request,
    exit_failsafe_remaining_qty,
)

BUFFER = 0.001
PRICE = 150.0


def _build(side, *, use_limit_orders=False, use_limit_exits=False, price=PRICE):
    return build_broker_order_request(
        symbol="AAPL",
        qty=3.0,
        side_enum=side,
        client_order_id="cid-1",
        current_price=price,
        use_limit_orders=use_limit_orders,
        use_limit_exits=use_limit_exits,
        spread_buffer=BUFFER,
    )


# ---------------------------------------------------------------------------
# Routing: USE_LIMIT_EXITS=True → SELL is a marketable limit, BUY is unaffected
# ---------------------------------------------------------------------------
class TestExitSelectiveRouting:
    def test_sell_uses_marketable_limit_when_use_limit_exits_on(self):
        req = _build(OrderSide.SELL, use_limit_exits=True)
        assert isinstance(req, LimitOrderRequest)
        # marketable SELL crosses DOWN through the spread (below mid) to fill promptly.
        assert req.limit_price == round(PRICE * (1.0 - BUFFER), 2)
        assert req.limit_price < PRICE

    def test_buy_unaffected_by_use_limit_exits(self):
        # Global buy-side flag OFF + only the exit flag ON → a BUY stays a market order.
        req = _build(OrderSide.BUY, use_limit_orders=False, use_limit_exits=True)
        assert isinstance(req, MarketOrderRequest)

    def test_use_limit_exits_independent_of_global_flag(self):
        # Exit routing must NOT depend on USE_LIMIT_ORDERS being set.
        sell = _build(OrderSide.SELL, use_limit_orders=False, use_limit_exits=True)
        assert isinstance(sell, LimitOrderRequest)


# ---------------------------------------------------------------------------
# USE_LIMIT_EXITS=False → byte-identical to today (market orders both sides)
# ---------------------------------------------------------------------------
class TestFlagOffIsByteIdenticalToToday:
    def test_sell_is_market_when_all_flags_off(self):
        req = _build(OrderSide.SELL, use_limit_orders=False, use_limit_exits=False)
        assert isinstance(req, MarketOrderRequest)

    def test_buy_is_market_when_all_flags_off(self):
        req = _build(OrderSide.BUY, use_limit_orders=False, use_limit_exits=False)
        assert isinstance(req, MarketOrderRequest)

    def test_global_flag_still_governs_buys(self):
        # The pre-existing global behaviour is preserved: USE_LIMIT_ORDERS makes a
        # BUY a limit that crosses UP through the spread.
        req = _build(OrderSide.BUY, use_limit_orders=True)
        assert isinstance(req, LimitOrderRequest)
        assert req.limit_price == round(PRICE * (1.0 + BUFFER), 2)

    def test_no_price_falls_back_to_market(self):
        req = _build(OrderSide.SELL, use_limit_exits=True, price=None)
        assert isinstance(req, MarketOrderRequest)


# ---------------------------------------------------------------------------
# Fail-safe: a rejected / erroring marketable-limit EXIT falls back to MARKET
# ---------------------------------------------------------------------------
def _executor():
    ex = OrderExecutorMixin.__new__(OrderExecutorMixin)
    return ex


@pytest.mark.anyio
async def test_rejected_limit_exit_falls_back_to_market_order():
    ex = _executor()
    limit_req = LimitOrderRequest(
        symbol="AAPL",
        qty=3.0,
        side=OrderSide.SELL,
        limit_price=149.85,
        time_in_force=TimeInForce.DAY,
        client_order_id=str(uuid.uuid4()),
    )
    submitted = []
    filled_order = MagicMock(id="mkt-1")

    def _submit(req):
        submitted.append(req)
        if isinstance(req, LimitOrderRequest):
            raise APIError("rejected")
        return filled_order

    client = MagicMock()
    client.submit_order = MagicMock(side_effect=_submit)

    order = await ex._submit_with_market_failsafe(
        client=client,
        primary_req=limit_req,
        symbol="AAPL",
        qty=3.0,
        side_enum=OrderSide.SELL,
        user_id="u1",
        is_exit=True,
    )
    # position still closes: a MARKET order was submitted after the rejection.
    assert order is filled_order
    assert isinstance(submitted[0], LimitOrderRequest)
    assert isinstance(submitted[-1], MarketOrderRequest)
    assert submitted[-1].side == OrderSide.SELL
    assert float(submitted[-1].qty) == 3.0


@pytest.mark.anyio
async def test_non_exit_rejection_is_reraised_not_swallowed():
    # A BUY (or any non-exit) keeps the existing behaviour: the APIError propagates
    # to the caller's recovery/refund handler — no silent market fallback.
    ex = _executor()
    buy_req = LimitOrderRequest(
        symbol="AAPL",
        qty=1.0,
        side=OrderSide.BUY,
        limit_price=150.15,
        time_in_force=TimeInForce.DAY,
        client_order_id=str(uuid.uuid4()),
    )
    client = MagicMock()
    client.submit_order = MagicMock(side_effect=APIError("rejected"))

    with pytest.raises(APIError):
        await ex._submit_with_market_failsafe(
            client=client,
            primary_req=buy_req,
            symbol="AAPL",
            qty=1.0,
            side_enum=OrderSide.BUY,
            user_id="u1",
            is_exit=False,
        )
    assert client.submit_order.call_count == 1  # no fallback attempt


@pytest.mark.anyio
async def test_happy_path_submits_primary_only():
    ex = _executor()
    limit_req = LimitOrderRequest(
        symbol="AAPL",
        qty=2.0,
        side=OrderSide.SELL,
        limit_price=149.85,
        time_in_force=TimeInForce.DAY,
        client_order_id=str(uuid.uuid4()),
    )
    ok = MagicMock(id="lim-1")
    client = MagicMock()
    client.submit_order = MagicMock(return_value=ok)

    order = await ex._submit_with_market_failsafe(
        client=client,
        primary_req=limit_req,
        symbol="AAPL",
        qty=2.0,
        side_enum=OrderSide.SELL,
        user_id="u1",
        is_exit=True,
    )
    assert order is ok
    assert client.submit_order.call_count == 1


# ---------------------------------------------------------------------------
# Non-fill guard: only re-submit the CONFIRMED-unfilled remainder (no oversell)
# ---------------------------------------------------------------------------
class TestExitFailsafeRemainingQty:
    def test_confirmed_zero_fill_returns_full_qty(self):
        live = MagicMock(filled_qty="0")
        assert (
            exit_failsafe_remaining_qty(
                is_limit_exit=True, order_qty=5.0, live_order=live
            )
            == 5.0
        )

    def test_partial_fill_returns_remainder(self):
        live = MagicMock(filled_qty="2")
        assert (
            exit_failsafe_remaining_qty(
                is_limit_exit=True, order_qty=5.0, live_order=live
            )
            == 3.0
        )

    def test_full_fill_returns_none(self):
        live = MagicMock(filled_qty="5")
        assert (
            exit_failsafe_remaining_qty(
                is_limit_exit=True, order_qty=5.0, live_order=live
            )
            is None
        )

    def test_unknown_state_returns_none_fail_closed(self):
        # never polled → cannot prove 0 fills → do NOT market-sell (oversell risk).
        assert (
            exit_failsafe_remaining_qty(
                is_limit_exit=True, order_qty=5.0, live_order=None
            )
            is None
        )

    def test_non_exit_returns_none(self):
        live = MagicMock(filled_qty="0")
        assert (
            exit_failsafe_remaining_qty(
                is_limit_exit=False, order_qty=5.0, live_order=live
            )
            is None
        )
