"""#2548 H4-H6 — VirtualLiveBroker must duck-type the alpaca TradingClient surface the LIVE path
calls, so a real cycle runs without AttributeError and without silently suppressing BUYs/SELLs.
"""

import inspect

import pytest


def _broker():
    from core.sim.broker import VirtualLiveBroker

    return VirtualLiveBroker()


def test_class_name_and_attr_do_not_trip_sim_sniff():
    # strategies/base.py:140-143: 'Simulation' in type(client).__name__ or hasattr(client,'simulation_data')
    # would flip the engine into the sim/backtest path and silently drop the live guards.
    b = _broker()
    assert "Simulation" not in type(b).__name__
    assert not hasattr(b, "simulation_data")


def test_submit_order_first_param_is_order_data():
    from core.sim.broker import VirtualLiveBroker

    params = list(inspect.signature(VirtualLiveBroker.submit_order).parameters)
    assert (
        params[1] == "order_data"
    )  # [0] is self — the signature sniff picks the request path


def test_get_account_exposes_full_surface():
    acc = _broker().get_account()
    for attr in (
        "equity",
        "cash",
        "buying_power",
        "daytrading_buying_power",
        "pattern_day_trader",
        "multiplier",
        "last_equity",
        "status",
        "currency",
    ):
        assert hasattr(
            acc, attr
        ), f"get_account missing .{attr} (BP/PDT gate would throw → BUY skipped)"
    assert acc.status == "ACTIVE"
    assert acc.currency == "USD"
    assert acc.multiplier == "1"


def test_get_open_position_raises_apierror_when_flat():
    from core.sim.exceptions import SimAPIError

    b = _broker()
    assert b.get_all_positions() == []  # empty list, never None
    with pytest.raises(SimAPIError) as ei:
        b.get_open_position("AAPL")
    assert ei.value.code == 40410000  # the "position does not exist" contract


def test_held_position_has_h6_fields():
    from core.sim.fill_engine import DummyPosition

    b = _broker()
    b.positions["AAPL"] = DummyPosition("AAPL", 10, 100.0)
    pos = b.get_open_position("AAPL")
    for attr in (
        "symbol",
        "qty",
        "qty_available",
        "avg_entry_price",
        "current_price",
        "market_value",
        "unrealized_pl",
        "unrealized_plpc",
        "side",
    ):
        assert hasattr(pos, attr), f"position missing .{attr}"


def test_close_all_positions_dual_call_shape():
    from core.sim.fill_engine import DummyPosition

    b = _broker()
    b.positions["AAPL"] = DummyPosition("AAPL", 1, 10.0)
    b.close_all_positions(cancel_orders=True)  # risk_manager shape
    assert b.get_all_positions() == []
    b.positions["MSFT"] = DummyPosition("MSFT", 1, 10.0)
    b.close_all_positions(
        object()
    )  # monitor_loop positional ClosePositionRequest shape
    assert b.get_all_positions() == []


def test_order_and_clock_methods_exist():
    b = _broker()
    assert b.get_orders() == []
    b.cancel_orders()  # no crash on empty book
    clock = b.get_clock()
    assert hasattr(clock, "is_open")
    assert clock.next_open.strftime(
        "%H:%M"
    )  # tz-safe datetime (trading_loop calls .strftime)
