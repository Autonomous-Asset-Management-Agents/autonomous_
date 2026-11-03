# tests/unit/test_opening_noise_guard_sim_clock.py
"""OpeningNoiseGuard minutes-since-open — math + fail-open contract (#sim-clock).

The call site (build_portfolio_context) now derives its `now` from the ENGINE clock
(engine_now → sim time under SIM_MODE, else datetime.now — byte-identical in live), NOT
wall-clock time.time(). With the wall clock, an offline sim vetoed every BUY based on the
REAL time-of-day of the run (the #3284 ablation w1 zeroed at consensus score 0.795). The
integration seam is verified empirically by the sim re-run; this pins the pure minute math
+ the strict fail-open contract that keeps a flaky clock from ever vetoing a BUY.
"""

from datetime import datetime

import pytz

from core.engine.portfolio_context import _minutes_since_market_open

_NY = pytz.timezone("America/New_York")


class _Clock:
    def __init__(self, is_open):
        self.is_open = is_open


def _epoch(hh, mm):
    return _NY.localize(datetime(2024, 5, 1, hh, mm)).timestamp()


def test_minutes_from_given_now():
    c = _Clock(True)
    assert _minutes_since_market_open(c, _epoch(9, 30)) == 0.0
    assert _minutes_since_market_open(c, _epoch(9, 45)) == 15.0
    assert _minutes_since_market_open(c, _epoch(10, 15)) == 45.0


def test_fail_open_contract():
    # market closed / missing / non-bool is_open / before open ⇒ None (guard never fires)
    assert _minutes_since_market_open(_Clock(False), _epoch(9, 45)) is None
    assert _minutes_since_market_open(None, _epoch(9, 45)) is None
    assert _minutes_since_market_open(_Clock("yes"), _epoch(9, 45)) is None
    assert _minutes_since_market_open(_Clock(True), _epoch(9, 0)) is None
