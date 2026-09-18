"""#2548 UX (#82/#83) — SimResult serialisation, summary, and the --compare diff."""

from core.sim.result import SimResult, compare


def _r(**kw):
    base = {
        "sim_date": "2026-07-15",
        "window": "09:30-10:30 ET",
        "cadence": "15m",
        "symbols": ["AAPL", "MSFT"],
        "starting_equity": 100000.0,
        "ending_equity": 100500.0,
        "n_cycles": 4,
    }
    base.update(kw)
    return SimResult(**base)


def test_derived_pnl_and_counts():
    r = _r(
        orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 10,
                "status": "filled",
                "fill_price": 100.0,
            },
            {
                "symbol": "MSFT",
                "side": "BUY",
                "qty": 5,
                "status": "blocked",
                "blocked_reason": "blocked:risk",
            },
        ]
    )
    assert r.pnl == 500.0
    assert round(r.pnl_pct, 2) == 0.5
    assert r.n_filled == 1
    assert r.n_blocked == 1


def test_json_roundtrip(tmp_path):
    r = _r(
        orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 10,
                "status": "filled",
                "fill_price": 100.0,
            }
        ],
        end_positions={"AAPL": 10},
    )
    p = tmp_path / "sim_result.json"
    r.to_json(str(p))
    back = SimResult.from_json(str(p))
    assert back.sim_date == r.sim_date
    assert back.orders == r.orders
    assert back.end_positions == {"AAPL": 10}
    assert back.pnl == r.pnl


def test_summary_is_human_readable():
    r = _r(
        orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 10,
                "status": "filled",
                "fill_price": 100.0,
            }
        ]
    )
    s = r.summary()
    assert "Sim-Day 2026-07-15" in s
    assert "AAPL" in s
    assert "P&L" in s


def test_compare_shows_order_and_pnl_delta():
    prev = _r(
        orders=[{"symbol": "AAPL", "side": "BUY", "qty": 10, "status": "filled"}],
        ending_equity=100000.0,
    )
    # order-logic change: now it also buys MSFT and P&L differs
    curr = _r(
        orders=[
            {"symbol": "AAPL", "side": "BUY", "qty": 10, "status": "filled"},
            {"symbol": "MSFT", "side": "BUY", "qty": 5, "status": "filled"},
        ],
        ending_equity=100800.0,
    )
    diff = compare(prev, curr)
    assert "1 new order" in diff
    assert "MSFT" in diff
    assert "P&L" in diff
