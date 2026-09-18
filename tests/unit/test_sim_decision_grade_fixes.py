# tests/unit/test_sim_decision_grade_fixes.py — regression guards for the #2630 sim decision-grade
# fixes. Corpus-free + deterministic (bind the unbound method to a lightweight fake self).
from types import SimpleNamespace

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from core.sim.broker import VirtualLiveBroker
from core.sim.exceptions import SimAPIError, flat_position_error, rejected_order_error


# --- #4: SimAPIError must be a REAL APIError so live isinstance(e, APIError) checks pass ----------
def test_sim_apierror_is_real_apierror_subclass():
    e = flat_position_error()
    assert isinstance(e, SimAPIError)
    assert isinstance(
        e, APIError
    )  # the load-bearing fix: rl_signal.py:688 isinstance check
    assert (
        e.code == 40410000
    )  # flat-position contract; live path also accepts .status_code == 404


def test_rejected_order_error_carries_status_code():
    e = rejected_order_error(403)
    assert isinstance(e, APIError)
    assert e.status_code == 403
    assert e.code == 40010001


# --- #A: get_orders must honour the request status filter (ALL/CLOSED include terminal orders) ----
def _fake_broker_orders():
    return SimpleNamespace(
        orders=[
            SimpleNamespace(status="filled"),
            SimpleNamespace(status="accepted"),
            SimpleNamespace(status="canceled"),
        ]
    )


def test_get_orders_all_includes_terminal():
    fake = _fake_broker_orders()
    out = VirtualLiveBroker.get_orders(
        fake, GetOrdersRequest(status=QueryOrderStatus.ALL)
    )
    assert (
        len(out) == 3
    )  # entry_time_reconcile pages status=ALL for the filled BUY history


def test_get_orders_closed_only_terminal():
    fake = _fake_broker_orders()
    out = VirtualLiveBroker.get_orders(
        fake, GetOrdersRequest(status=QueryOrderStatus.CLOSED)
    )
    assert {o.status for o in out} == {"filled", "canceled"}


def test_get_orders_default_excludes_terminal():
    fake = _fake_broker_orders()
    out = VirtualLiveBroker.get_orders(
        fake
    )  # default (cancel/poll path) → only open orders
    assert [o.status for o in out] == ["accepted"]


# --- #2632 last-mile: seed the LSTM rank panel so the strict-ML gate can pass ---------------------
def _panel_fake_broker(n_symbols=35, n_bars=3):
    """A corpus-shaped provider: n_symbols each with n_bars daily closes strictly before the
    sim day, distinct per symbol so the cross-section is dispersed (not a collapsed model).
    """
    import pandas as pd

    idx = pd.to_datetime(["2026-07-25", "2026-07-26", "2026-07-27"][:n_bars])
    syms = [f"S{i}" for i in range(n_symbols)]
    frames = {
        s: pd.DataFrame(
            {"close": [100.0 + i, 101.0 + i, 102.0 + i * 1.5][:n_bars]}, index=idx
        )
        for i, s in enumerate(syms)
    }
    provider = SimpleNamespace(symbols=syms, frames=frames)
    return SimpleNamespace(data_client=SimpleNamespace(_provider=provider)), syms


def test_seed_lstm_panel_populates_dispersed_cross_section():
    """The empty panel is the strict-ML-gate blocker (LSTMSignalAgent abstains → gate hard-blocks
    every symbol → 0 orders). The seed must produce a panel that clears BOTH agent guards:
    n >= _MIN_PANEL_SYMBOLS (30) AND >= 2 distinct scores."""
    from datetime import datetime, timezone

    from core.report.lstm_panel_store import get_store
    from core.sim.runner import _seed_lstm_panel

    get_store().reset()
    try:
        broker, syms = _panel_fake_broker(n_symbols=35, n_bars=3)
        # #2693: the panel is keyed on the clock's CURRENT day (a multi-day run re-seeds on
        # every rollover), so the stub carries both — as the real SimClock always does.
        _t = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
        clock = SimpleNamespace(start_time=_t, current_time=_t)

        n = _seed_lstm_panel(broker, clock)

        assert n == 35
        xsec = get_store().cross_section_at(clock.start_time.date())
        assert (
            len(xsec) >= 30
        )  # >= _MIN_PANEL_SYMBOLS → LSTM agent does not abstain on n
        assert (
            len(set(xsec.values())) >= 2
        )  # dispersed → not read as a collapsed-model "sell all"
        assert set(xsec) == {s.upper() for s in syms}
    finally:
        get_store().reset()


def test_seed_lstm_panel_respects_no_look_ahead():
    """Only bars STRICTLY BEFORE the sim day feed the score — the sim-day bar itself must never
    leak into the ranking (same no-look-ahead contract as _prior_close / the snapshot fix).
    """
    from datetime import datetime, timezone

    import pandas as pd

    from core.report.lstm_panel_store import get_store
    from core.sim.runner import _seed_lstm_panel

    get_store().reset()
    try:
        # A same-day bar with a wild close — it must NOT change the seeded score.
        idx = pd.to_datetime(["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])
        frames = {
            f"S{i}": pd.DataFrame(
                {"close": [100.0 + i, 101.0 + i, 102.0 + i, 9_999.0]}, index=idx
            )
            for i in range(31)
        }
        provider = SimpleNamespace(symbols=list(frames), frames=frames)
        broker = SimpleNamespace(data_client=SimpleNamespace(_provider=provider))
        # #2693: the panel is keyed on the clock's CURRENT day (a multi-day run re-seeds on
        # every rollover), so the stub carries both — as the real SimClock always does.
        _t = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
        clock = SimpleNamespace(start_time=_t, current_time=_t)

        _seed_lstm_panel(broker, clock)
        xsec = get_store().cross_section_at(clock.start_time.date())
        # score = 07-29/07-28 - 1 for S0 = 102/101 - 1 ≈ 0.0099; the 9,999 same-day bar is excluded.
        assert xsec["S0"] == (102.0 / 101.0 - 1.0)
    finally:
        get_store().reset()


def test_result_summary_survives_none_order_fields():
    """A blocked/skipped SELL record carries an explicit None side/qty (the signal never
    became a broker order). ``SimResult.summary()`` must render it, not crash with
    ``TypeError: unsupported format string passed to NoneType.__format__``. (Surfaced once the
    LSTM-panel seed let approved decisions actually reach the executor.)"""
    from core.sim.result import SimResult

    r = SimResult(
        sim_date="2026-08-03",
        window="09:30-10:30",
        cadence="15m",
        symbols=["ADBE"],
        orders=[
            {
                "symbol": "ADBE",
                "side": None,
                "qty": None,
                "status": "blocked",
                "blocked_reason": "blocked:risk — position size resolved to 0",
            },
            {"symbol": "AAPL", "side": "sell", "qty": 0, "status": "closed"},
        ],
        end_positions={},
        starting_equity=100_000.0,
        ending_equity=100_000.0,
        n_cycles=5,
        wall_time_s=45.5,
    )
    out = r.summary()  # must not raise
    assert "ADBE" in out and "AAPL" in out
    assert "blocked:risk" in out
