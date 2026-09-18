"""#2548 M7-extension — engine_now(): wall-clock normally, the sim-clock time under SIM_MODE.

Byte-identical to datetime.now(tz) when SIM_MODE is off (the trading path is untouched); under
SIM_MODE it returns the virtual sim day so the analysis runs as-of that day, not today.
"""

from datetime import datetime, timezone


class _Cfg:
    def __init__(self, sim):
        self.SIM_MODE = sim


def test_engine_now_is_wallclock_when_sim_off(monkeypatch):
    from core.sim import clock

    monkeypatch.setattr(clock, "get_config", lambda: _Cfg(False))
    n = clock.engine_now(timezone.utc)
    assert abs((datetime.now(timezone.utc) - n).total_seconds()) < 5
    assert n.tzinfo is not None


def test_engine_now_returns_sim_day_under_sim_mode(monkeypatch):
    import pytz

    from core.sim import clock

    sc = clock.SimClock(
        sim_date="2026-07-15", sim_window="09:30-16:00 ET", sim_cadence="1h"
    )
    monkeypatch.setattr(clock, "get_config", lambda: _Cfg(True))
    monkeypatch.setattr(clock, "get_sim_clock", lambda: sc)

    # the calendar-day check pattern: engine_now(ny_tz).date()
    ny = pytz.timezone("America/New_York")
    assert clock.engine_now(ny).date().isoformat() == "2026-07-15"
    # tz-naive request still gets a value on the sim day
    assert clock.engine_now().date().isoformat() == "2026-07-15"

    # advancing the sim clock moves engine_now with it
    sc.advance()
    assert clock.engine_now(ny) > sc.start_time
