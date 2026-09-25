# tests/unit/test_sim_halt_ends_run.py
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class _Engine:
    def __init__(self):
        self._sim_complete = threading.Event()
        self._shutdown_event = threading.Event()
        self.strategy_running = threading.Event()
        self._sim_cycles = 0

    def start_live_strategy(self):
        self.strategy_running.set()

    def halt(self):
        self._shutdown_event.set()
        self.strategy_running.clear()

    def flackern(self):
        self._shutdown_event.set()
        self._shutdown_event.clear()


def _warte_deterministic(engine, budget_s: float, events=None):
    from core.sim.runner import _sim_wait_reason

    events = events or {}
    t0 = 1000.0
    current_time = t0

    with patch("core.sim.runner.time.time", side_effect=lambda: current_time):
        while True:
            offset = current_time - t0

            if offset in events:
                events[offset]()

            grund = _sim_wait_reason(engine, t0, budget_s)
            if grund is not None:
                return grund, offset

            current_time += 0.5

            if current_time - t0 > budget_s + 5.0:
                raise RuntimeError("Endless loop in _warte_deterministic")


def test_anhalten_beendet_den_lauf_sofort():
    e = _Engine()
    e.start_live_strategy()
    e._sim_cycles = 1

    events = {0.5: e.halt}

    grund, dauer = _warte_deterministic(e, budget_s=5.0, events=events)

    assert grund == "halted"
    assert dauer < 2.0


def test_regulaeres_fensterende_bleibt_unveraendert():
    e = _Engine()
    e.start_live_strategy()

    events = {0.5: e._sim_complete.set}

    grund, dauer = _warte_deterministic(e, budget_s=5.0, events=events)

    assert grund == "complete"
    assert dauer < 2.0


def test_zeitgrenze_bleibt_erhalten():
    e = _Engine()
    e.start_live_strategy()

    grund, dauer = _warte_deterministic(e, budget_s=1.0)

    assert grund == "timeout"
    assert dauer >= 1.0


def test_fertig_schlaegt_anhalten():
    e = _Engine()
    e._sim_complete.set()
    e._shutdown_event.set()
    e._sim_cycles = 1

    from core.sim.runner import _sim_wait_reason

    with patch("core.sim.runner.time.time", return_value=1000.0):
        assert _sim_wait_reason(e, 1000.0, 5.0) == "complete"


def test_ergebnis_traegt_die_halt_markierung():
    from core.sim.result import SimResult

    r = SimResult(
        sim_date="2026-06-23",
        window="09:30-10:30 ET",
        cadence="15m",
        symbols=[],
        orders=[],
        end_positions={},
        starting_equity=100000.0,
        ending_equity=92290.80,
        n_cycles=115,
        wall_time_s=3771.0,
        halted=True,
        halt_reason="Portfolio stop loss (7.7%)",
    )
    assert r.halted is True
    assert "stop loss" in r.halt_reason.lower()
    assert r.timed_out is False


def test_startup_flackern_beendet_den_lauf_nicht():
    e = _Engine()
    e.start_live_strategy()
    e._sim_cycles = 0

    def start_flackern():
        e._shutdown_event.set()

    def end_flackern():
        e._shutdown_event.clear()

    events = {
        0.5: start_flackern,
        1.0: end_flackern,
        2.0: e._sim_complete.set,
    }

    grund, dauer = _warte_deterministic(e, budget_s=8.0, events=events)

    assert grund == "complete"


def test_halt_ohne_gespielten_zyklus_zaehlt_nicht():
    e = _Engine()
    e.start_live_strategy()
    e._sim_cycles = 0
    e.halt()

    from core.sim.runner import _sim_wait_reason

    with patch("core.sim.runner.time.time", return_value=1000.0):
        assert _sim_wait_reason(e, 1000.0, 5.0) is None


def test_echter_halt_nach_zyklen_greift_weiterhin():
    e = _Engine()
    e.start_live_strategy()
    e._sim_cycles = 12345
    e.halt()

    grund, dauer = _warte_deterministic(e, budget_s=8.0)
    assert grund == "halted"
    assert dauer < 3.0


def test_halt_seen_concurrency():
    from core.sim.runner import _HALT_SEEN, _sim_wait_reason

    _HALT_SEEN.clear()

    engines = [_Engine() for _ in range(20)]
    for e in engines:
        e.start_live_strategy()
        e._sim_cycles = 100
        e.halt()

    def worker(engine):
        with patch("core.sim.runner.time.time", return_value=1000.0):
            return _sim_wait_reason(engine, 1000.0, 5.0)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker, engines))

    assert all(r is None for r in results)
    assert len(_HALT_SEEN) == 20
