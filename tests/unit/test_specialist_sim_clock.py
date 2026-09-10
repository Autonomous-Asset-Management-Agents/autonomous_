"""Sim-Clock seam for the Stock Specialist (Sim-Day #2548 / SpecialistAlpha activation).

The specialist read its data windows / ``as_of`` from wall-clock ``datetime.now()``. In a
Sim-Day run (``SIM_MODE``) that means it gathers TODAY's data over a historical sim day =
look-ahead leakage. It must route its "now" through the existing ``core.sim.clock.engine_now``
seam (SimClock under SIM_MODE, wall-clock otherwise) so a backtest reads AS OF the sim day.
"""

from datetime import datetime, timezone


def test_specialist_now_utc_delegates_to_engine_clock(monkeypatch):
    import core.sim.clock as simclock
    import core.stock_specialist as ss

    # SIM off -> wall clock (byte-identical to today).
    monkeypatch.setattr(
        simclock, "get_config", lambda: type("C", (), {"SIM_MODE": False})()
    )
    now = ss._now_utc()
    assert now.tzinfo is not None
    assert abs((now - datetime.now(timezone.utc)).total_seconds()) < 5

    # SIM on -> the SimClock's virtual time (leakage-safe: as of the sim day, not today).
    sim_t = datetime(2020, 1, 2, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        simclock, "get_config", lambda: type("C", (), {"SIM_MODE": True})()
    )
    monkeypatch.setattr(
        simclock, "get_sim_clock", lambda: type("K", (), {"current_time": sim_t})()
    )
    assert ss._now_utc() == sim_t
