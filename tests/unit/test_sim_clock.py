"""#2548 M7 — SimClock must replay a REAL chosen day/window, tz-aware, at the configured cadence.

The prototype clock was a dummy: `datetime.now().replace(hour=9, minute=30)` — always "today
09:30", tz-naive, hardcoded +1min advance. That can't replay a past day and TypeErrors against
alpaca's tz-aware bar timestamps.
"""

from datetime import datetime, timezone


def _clock(date, window="09:30-10:30 ET", cadence="15m"):
    from core.sim.clock import SimClock

    return SimClock(sim_date=date, sim_window=window, sim_cadence=cadence)


def test_clock_starts_at_chosen_date_and_window_tz_aware():
    c = _clock("2026-07-15")
    assert c.current_time.tzinfo is not None  # tz-aware
    assert (
        c.current_time.year == 2026
        and c.current_time.month == 7
        and c.current_time.day == 15
    )
    assert c.current_time.hour == 9 and c.current_time.minute == 30
    assert c.end_time.hour == 10 and c.end_time.minute == 30
    assert c.is_open is True


def test_advance_uses_cadence_and_closes_at_window_end():
    c = _clock("2026-07-15", cadence="15m")
    c.advance()
    assert c.current_time.hour == 9 and c.current_time.minute == 45
    # advance past 10:30 → closed
    for _ in range(10):
        c.advance()
    assert c.current_time > c.end_time
    assert c.is_open is False


def test_naive_datetimes_compare_cleanly_against_aware():
    # a tz-aware clock time must be comparable to alpaca-style aware timestamps without TypeError
    c = _clock("2026-07-15")
    aware = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert (c.current_time < aware) in (True, False)  # no TypeError


def test_default_date_when_unset_is_a_past_weekday():
    c = _clock("", window="09:30-16:00 ET", cadence="1h")
    # empty SIM_DATE → a concrete past weekday (Mon-Fri), never a weekend / the future
    assert c.current_time.weekday() < 5
    assert c.current_time <= datetime.now(c.current_time.tzinfo)
