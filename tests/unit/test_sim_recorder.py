"""#2548 — the outcome recorder + runner order-merge (blocked orders + reasons, not just fills)."""

from core.sim.recorder import SimRecorder, get_recorder
from core.sim.runner import _build_orders


def test_recorder_is_noop_until_armed():
    r = SimRecorder()
    r.record("AAPL", "executed")  # not armed → dropped
    assert r.events == []
    r.reset()  # arm
    r.record("AAPL", "blocked:risk", "size 0")
    assert r.events == [
        {"symbol": "AAPL", "outcome": "blocked:risk", "reason": "size 0"}
    ]


def test_reset_clears_prior_events():
    r = get_recorder()
    r.reset()
    r.record("X", "executed")
    r.reset()
    assert r.events == []


class _Order:
    def __init__(self, symbol, side, qty, price):
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.filled_avg_price = price


def test_build_orders_captures_blocks_and_enriches_fills():
    events = [
        {"symbol": "AAPL", "outcome": "executed", "reason": ""},
        {
            "symbol": "MSFT",
            "outcome": "blocked:risk",
            "reason": "position size resolved to 0",
        },
        {
            "symbol": "NVDA",
            "outcome": "blocked:daily_limit",
            "reason": "Daily trade limit reached.",
        },
    ]
    broker_orders = [_Order("AAPL", "BUY", 10, 100.0)]
    orders = _build_orders(events, broker_orders)

    by_sym = {o["symbol"]: o for o in orders}
    # executed → filled, enriched with side/qty/price from the broker
    assert by_sym["AAPL"]["status"] == "filled"
    assert by_sym["AAPL"]["side"] == "BUY"
    assert by_sym["AAPL"]["qty"] == 10.0
    assert by_sym["AAPL"]["fill_price"] == 100.0
    # blocked → blocked with the reason code + detail
    assert by_sym["MSFT"]["status"] == "blocked"
    assert "blocked:risk" in by_sym["MSFT"]["blocked_reason"]
    assert "size resolved to 0" in by_sym["MSFT"]["blocked_reason"]
    assert by_sym["NVDA"]["status"] == "blocked"
    assert "daily_limit" in by_sym["NVDA"]["blocked_reason"]
