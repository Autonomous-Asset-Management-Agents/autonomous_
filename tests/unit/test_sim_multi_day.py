# tests/unit/test_sim_multi_day.py
# #2693 (sub-issue of #2632) — multi-day replay.
#
# WHY THIS EXISTS: a one-day sim cannot answer hold-vs-rotate, and that is not a gap in the
# implementation but in the arithmetic. On a day with one bar per symbol, equity is
# `cash + Σ qty·price`, so selling at the mark and holding at the mark are worth the SAME —
# an exit only moves value between the two summands. Measured (PR #2692): old vs new rotation
# parameters came out at Δ 0.00 (+153.23 both arms) although the arms provably behaved
# differently (one extra exit, five more blocked entries). The consequence of an exit lands on
# LATER days, which a one-day window does not contain.
#
# The corpus already supports this: 286 daily bars per symbol (2025-06-13 … 2026-08-04, 504
# symbols), and both the provider (`replay.get_data`, inclusive PIT slice) and the data client
# (everything derived from `clock.current_time`) are date-driven. Only SimClock and run_sim
# assume a single day.
#
# Backwards compatibility is a hard requirement: `--days 1` (the default) must behave exactly as
# before, because every existing sim test and the #2690/#2692 acceptance runs depend on it.

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest


def _clock(sim_date="2026-08-04", window="09:30-16:00 ET", cadence="1h"):
    from core.sim.clock import SimClock

    return SimClock(sim_date=sim_date, sim_window=window, sim_cadence=cadence)


def _drain(clock, max_steps=5000):
    """Advance until the clock closes; return the ordered list of distinct dates seen."""
    seen = [clock.current_time.date()]
    steps = 0
    while clock.is_open and steps < max_steps:
        clock.advance()
        steps += 1
        if clock.is_open and clock.current_time.date() != seen[-1]:
            seen.append(clock.current_time.date())
    assert steps < max_steps, "clock did not terminate"
    return seen


# ---------------------------------------------------------------------------
# Backwards compatibility — the single day must not move
# ---------------------------------------------------------------------------


def test_single_day_is_unchanged_without_trading_days():
    """No day list set (every existing test, and --days 1) → exactly today's behaviour."""
    c = _clock()
    assert _drain(c) == [date(2026, 8, 4)]


def test_single_day_when_only_its_own_date_is_offered():
    c = _clock()
    c.set_trading_days([date(2026, 8, 4)])
    assert _drain(c) == [date(2026, 8, 4)]


def test_is_open_is_false_after_the_last_day():
    c = _clock()
    c.set_trading_days([date(2026, 8, 3), date(2026, 8, 4)])
    _drain(c)
    assert (
        not c.is_open
    ), "the loop terminates on is_open — it must go False at the very end"


# ---------------------------------------------------------------------------
# Rollover
# ---------------------------------------------------------------------------


def test_replays_every_offered_trading_day_in_order():
    c = _clock(sim_date="2026-08-03")
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    c.set_trading_days(days)
    assert _drain(c) == days


def test_rollover_restarts_the_window_rather_than_continuing_the_clock():
    """Day 2 must begin at 09:30, not at 16:00 + cadence."""
    c = _clock(sim_date="2026-08-03", cadence="1h")
    c.set_trading_days([date(2026, 8, 3), date(2026, 8, 4)])
    while c.current_time.date() == date(2026, 8, 3):
        c.advance()
    assert c.current_time.date() == date(2026, 8, 4)
    assert (c.current_time.hour, c.current_time.minute) == (9, 30)


def test_advance_reports_whether_it_rolled():
    """The driver needs the signal to do per-day bookkeeping (panel seed, equity point)."""
    c = _clock(sim_date="2026-08-03", cadence="1h")
    c.set_trading_days([date(2026, 8, 3), date(2026, 8, 4)])
    rolls = 0
    while c.is_open:
        if c.advance():
            rolls += 1
    assert rolls == 1, "exactly one rollover between two days"


def test_days_with_no_corpus_bars_are_simply_not_offered():
    """Weekends and holidays never reach the clock — the runner passes the CORPUS's dates, so
    the calendar is data-derived rather than guessed."""
    c = _clock(sim_date="2026-07-31")
    # 2026-08-01/02 is a weekend → absent from the corpus
    days = [date(2026, 7, 31), date(2026, 8, 3)]
    c.set_trading_days(days)
    assert _drain(c) == days


def test_clock_repositions_to_the_first_offered_day():
    """The caller owns the selection; the clock follows it.

    This contract replaced an earlier "SIM_DATE anchors, earlier days are dropped" design, which
    the first real multi-day run disproved: without `--date` the clock defaults to *yesterday*,
    which the corpus does not carry, so every selected day counted as "before the start", nothing
    was queued, and the run silently replayed one empty day.
    """
    c = _clock(sim_date="2026-08-05")  # a day the corpus does not have
    c.set_trading_days([date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4)])
    assert c.current_time.date() == date(2026, 7, 31)
    assert (c.current_time.hour, c.current_time.minute) == (9, 30)
    assert _drain(c) == [date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4)]


def test_empty_calendar_leaves_the_clock_single_day():
    """Fail-soft: an empty selection must not produce a zero-day run — silent, and
    indistinguishable from "nothing happened"."""
    c = _clock(sim_date="2026-08-04")
    assert c.set_trading_days([]) == 1
    assert _drain(c) == [date(2026, 8, 4)]


def test_unusable_calendar_leaves_the_clock_single_day():
    c = _clock(sim_date="2026-08-04")
    assert c.set_trading_days(["not-a-date"]) == 1
    assert _drain(c) == [date(2026, 8, 4)]


def test_calendar_is_set_before_positions_are_seeded():
    """`_seed_positions` prices the starting book off `clock.start_time`; repositioning the clock
    afterwards would price the seed on the wrong day's prior close."""
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner.run_sim)
    assert src.find("set_trading_days") < src.find(
        "_seed_positions(broker"
    ), "the calendar must be set before the starting book is priced"


def test_setting_days_twice_is_idempotent():
    c = _clock(sim_date="2026-08-03")
    days = [date(2026, 8, 3), date(2026, 8, 4)]
    c.set_trading_days(days)
    c.set_trading_days(days)
    assert _drain(c) == days


def test_day_count_is_reported():
    c = _clock(sim_date="2026-08-03")
    c.set_trading_days([date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)])
    assert c.n_days == 3
    _drain(c)
    assert c.days_completed == 3


# ---------------------------------------------------------------------------
# Trading-day selection from the corpus (the runner's side)
# ---------------------------------------------------------------------------


def _provider(dates_by_symbol):
    import pandas as pd

    frames = {
        sym: pd.DataFrame(
            {"close": [1.0] * len(ds)},
            index=pd.DatetimeIndex([pd.Timestamp(d) for d in ds]),
        )
        for sym, ds in dates_by_symbol.items()
    }
    return SimpleNamespace(frames=frames, symbols=list(frames))


def test_corpus_trading_days_are_the_union_across_symbols():
    """A symbol that IPO'd mid-corpus must not shorten the calendar."""
    from core.sim.runner import _corpus_trading_days

    p = _provider(
        {
            "AAA": ["2026-08-03", "2026-08-04", "2026-08-05"],
            "BBB": ["2026-08-04"],  # partial history
        }
    )
    assert _corpus_trading_days(p) == [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
    ]


def test_corpus_trading_days_are_sorted_and_unique():
    from core.sim.runner import _corpus_trading_days

    p = _provider({"AAA": ["2026-08-05", "2026-08-03", "2026-08-05"]})
    assert _corpus_trading_days(p) == [date(2026, 8, 3), date(2026, 8, 5)]


def test_selected_window_is_n_days_from_the_start():
    from core.sim.runner import _select_replay_days

    all_days = [date(2026, 8, d) for d in (3, 4, 5, 6, 7)]
    assert _select_replay_days(all_days, date(2026, 8, 4), 3) == [
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
    ]


def test_selection_is_capped_by_the_available_history():
    """Asking for more days than the corpus has must run what exists, not invent days."""
    from core.sim.runner import _select_replay_days

    all_days = [date(2026, 8, 3), date(2026, 8, 4)]
    assert _select_replay_days(all_days, date(2026, 8, 3), 30) == all_days


def test_empty_start_date_takes_the_LAST_n_days():
    """`--days 20` without a date is "replay the last 20 available days" — anchoring at the
    corpus end is the only reading that cannot run into a data hole."""
    from core.sim.runner import _select_replay_days

    all_days = [date(2026, 8, d) for d in (3, 4, 5, 6, 7)]
    assert _select_replay_days(all_days, None, 2) == [
        date(2026, 8, 6),
        date(2026, 8, 7),
    ]


def test_single_day_selection_matches_the_explicit_date():
    from core.sim.runner import _select_replay_days

    all_days = [date(2026, 8, d) for d in (3, 4, 5)]
    assert _select_replay_days(all_days, date(2026, 8, 4), 1) == [date(2026, 8, 4)]


# ---------------------------------------------------------------------------
# No look-ahead in the per-day panel seed
# ---------------------------------------------------------------------------


def test_panel_seed_uses_the_CURRENT_sim_day_not_the_first():
    """Seeding day 3 with day 1's cross-section would freeze the ranking and silently disable
    the very rotation the multi-day run exists to observe."""
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner._seed_lstm_panel)
    assert "current_time" in src
    assert (
        "start_time.date()" not in src
    ), "the panel date must follow the clock, not the run's first day"


def test_panel_seed_cutoff_excludes_the_seeded_day_itself():
    """PIT: day D's ranking may only use bars STRICTLY before D."""
    import pandas as pd

    from core.sim.runner import _seed_lstm_panel

    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-08-0{d}") for d in (3, 4, 5)])
    frame = pd.DataFrame({"close": [100.0, 110.0, 999.0]}, index=idx)
    provider = SimpleNamespace(frames={"AAA": frame}, symbols=["AAA"])
    broker = SimpleNamespace(data_client=SimpleNamespace(_provider=provider))
    clock = SimpleNamespace(
        start_time=datetime(2026, 8, 3, 9, 30),
        current_time=datetime(2026, 8, 5, 9, 30),
    )

    captured = {}

    class _Store:
        def record_cycle(self, d, ranked):
            captured["date"] = d
            captured["ranked"] = ranked

    import core.report.lstm_panel_store as store_mod

    real = store_mod.get_store
    store_mod.get_store = lambda: _Store()
    try:
        _seed_lstm_panel(broker, clock)
    finally:
        store_mod.get_store = real

    assert captured["date"] == date(2026, 8, 5)
    # score = prior-day return from the two closes BEFORE 2026-08-05 → 110/100 - 1, never 999
    assert captured["ranked"][0][1] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------


def test_recorder_collects_one_equity_point_per_day():
    from core.sim.recorder import SimRecorder

    r = SimRecorder()
    r.reset()
    r.record_daily_equity(date(2026, 8, 3), 100_000.0, 5_000.0, 3)
    r.record_daily_equity(date(2026, 8, 4), 101_000.0, 4_000.0, 4)
    assert [p["date"] for p in r.daily_equity] == ["2026-08-03", "2026-08-04"]
    assert r.daily_equity[1]["equity"] == pytest.approx(101_000.0)
    assert r.daily_equity[1]["n_positions"] == 4


def test_recorder_is_noop_until_armed():
    from core.sim.recorder import SimRecorder

    r = SimRecorder()
    r.record_daily_equity(date(2026, 8, 3), 1.0, 1.0, 1)
    assert r.daily_equity == []


def test_reset_clears_the_curve():
    from core.sim.recorder import SimRecorder

    r = SimRecorder()
    r.reset()
    r.record_daily_equity(date(2026, 8, 3), 1.0, 1.0, 1)
    r.reset()
    assert r.daily_equity == []


def test_same_day_recorded_twice_keeps_the_latest():
    """The final mark re-records the closing day — one point per date, not two."""
    from core.sim.recorder import SimRecorder

    r = SimRecorder()
    r.reset()
    r.record_daily_equity(date(2026, 8, 3), 100.0, 1.0, 1)
    r.record_daily_equity(date(2026, 8, 3), 105.0, 1.0, 1)
    assert len(r.daily_equity) == 1
    assert r.daily_equity[0]["equity"] == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# Derived multi-day metrics — the numbers an A/B is actually read on
# ---------------------------------------------------------------------------


def _result(curve, starting=100_000.0):
    from core.sim.result import SimResult

    return SimResult(
        sim_date="2026-08-03",
        window="09:30-16:00 ET",
        cadence="15m",
        starting_equity=starting,
        ending_equity=curve[-1][1] if curve else starting,
        daily_equity=[
            {"date": d.isoformat(), "equity": e, "cash": 0.0, "n_positions": 1}
            for d, e in curve
        ],
    )


def test_derived_reports_the_day_count():
    r = _result([(date(2026, 8, 3), 100_000.0), (date(2026, 8, 4), 101_000.0)])
    assert r.to_dict()["derived"]["n_days"] == 2


def test_derived_max_drawdown_is_measured_from_the_running_peak():
    curve = [
        (date(2026, 8, 3), 100_000.0),
        (date(2026, 8, 4), 110_000.0),  # peak
        (date(2026, 8, 5), 99_000.0),  # -10% from peak
        (date(2026, 8, 6), 104_500.0),
    ]
    dd = _result(curve).to_dict()["derived"]["max_drawdown_pct"]
    assert dd == pytest.approx(-10.0, abs=0.01)


def test_derived_drawdown_is_zero_on_a_monotone_curve():
    curve = [(date(2026, 8, 3), 100.0), (date(2026, 8, 4), 101.0)]
    assert _result(curve).to_dict()["derived"]["max_drawdown_pct"] == pytest.approx(0.0)


def test_derived_best_and_worst_day():
    curve = [
        (date(2026, 8, 3), 100.0),
        (date(2026, 8, 4), 110.0),  # +10%
        (date(2026, 8, 5), 99.0),  # -10%
    ]
    d = _result(curve).to_dict()["derived"]
    assert d["best_day_pct"] == pytest.approx(10.0, abs=0.01)
    assert d["worst_day_pct"] == pytest.approx(-10.0, abs=0.01)


def test_single_day_result_reports_no_multi_day_metrics():
    """A one-day run must not grow fields that suggest a curve it does not have."""
    from core.sim.result import SimResult

    d = SimResult(
        sim_date="2026-08-04",
        window="09:30-16:00 ET",
        cadence="15m",
        starting_equity=100_000.0,
        ending_equity=100_100.0,
    ).to_dict()
    assert d["derived"].get("n_days") in (None, 1)
    assert d["derived"].get("max_drawdown_pct") is None


def test_compare_shows_the_curve_difference():
    from core.sim.result import compare

    a = _result([(date(2026, 8, 3), 100_000.0), (date(2026, 8, 4), 101_000.0)])
    b = _result([(date(2026, 8, 3), 100_000.0), (date(2026, 8, 4), 99_000.0)])
    out = compare(a, b, encoding="utf-8")
    assert "day" in out.lower() or "tage" in out.lower()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_runner_hands_the_corpus_calendar_to_the_clock_after_boot():
    """The clock is built BEFORE the data client exists, so the day list can only be handed over
    after boot — but before anything prices off the clock's dates (see the seeding test).
    """
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner.run_sim)
    hand = src.find("set_trading_days")
    boot = src.find("_init_trading_clients()")
    assert hand != -1, "run_sim must hand the corpus calendar to the clock"
    assert (
        hand > boot
    ), "the calendar comes from the provider, which exists only after boot"


def test_sim_driver_seeds_the_panel_on_rollover():
    import inspect

    from core.engine.trading_loop import TradingLoopMixin

    src = inspect.getsource(TradingLoopMixin.live_trading_loop)
    assert "on_new_day" in src or "rolled" in src, (
        "the per-cycle sim driver must react to a day rollover — otherwise day 2+ votes on "
        "day 1's ranking"
    )


def test_config_exposes_sim_days_in_both_editions():
    """BORA parity — a sim knob missing from the OSS config diverges the editions."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("config.py", "config.oss.py"):
        assert "SIM_DAYS" in (root / name).read_text(encoding="utf-8"), name


def test_cli_offers_a_days_flag():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "sim" / "__main__.py"
    ).read_text(encoding="utf-8")
    assert "--days" in src


def test_sim_days_default_is_one():
    """Default must keep every existing run and test byte-identical."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "config.py").read_text(encoding="utf-8")
    m = re.search(
        r"SIM_DAYS[^\n]*getenv\(\s*[\"']SIM_DAYS[\"']\s*,\s*[\"'](\d+)[\"']", src
    )
    assert m and m.group(1) == "1"


def test_trading_day_helpers_tolerate_an_empty_corpus():
    from core.sim.runner import _corpus_trading_days, _select_replay_days

    assert _corpus_trading_days(SimpleNamespace(frames={}, symbols=[])) == []
    assert _select_replay_days([], date(2026, 8, 4), 5) == []


# ---------------------------------------------------------------------------
# Two multi-day traps, both found by the first 5-day run
# ---------------------------------------------------------------------------


def test_timeout_scales_with_the_day_count():
    """The 300s default was sized for ONE day. A 20-day run exceeds it, and the old code then
    wrote a result anyway — a truncated run that LOOKS complete is the worst possible outcome
    for an A/B, because the missing days are invisible in the output."""
    from core.sim.runner import _effective_timeout

    assert _effective_timeout(None, 1) == pytest.approx(300.0)
    assert _effective_timeout(None, 10) == pytest.approx(3000.0)
    # an explicit value always wins — the caller knows better
    assert _effective_timeout(60.0, 10) == pytest.approx(60.0)


def test_result_marks_a_truncated_run():
    """A run cut short by the timeout must SAY so in the result, not only in a log line."""
    from core.sim.result import SimResult

    d = SimResult(
        sim_date="2026-08-04",
        window="09:30-16:00 ET",
        cadence="1h",
        timed_out=True,
    ).to_dict()
    assert d["timed_out"] is True
    assert d["derived"]["timed_out"] is True


def test_result_is_not_truncated_by_default():
    from core.sim.result import SimResult

    d = SimResult(sim_date="x", window="y", cadence="z").to_dict()
    assert d["derived"]["timed_out"] is False


def test_compare_flags_a_truncated_arm():
    """Comparing a complete arm against a truncated one is meaningless — say it loudly."""
    from core.sim.result import SimResult, compare

    a = SimResult(sim_date="d", window="w", cadence="c")
    b = SimResult(sim_date="d", window="w", cadence="c", timed_out=True)
    assert "truncat" in compare(a, b, encoding="utf-8").lower()


def test_runner_stops_the_engine_before_returning():
    """The engine's daemon threads keep logging after run_sim returns; the interpreter then
    finalises underneath them and dies with
        Fatal Python error: _enter_buffered_busy ... at interpreter shutdown
    The 5-day run wrote a complete result and STILL exited 127, so any script checking $?
    would read a successful run as a failure."""
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner.run_sim)
    assert "stop_strategy" in src, "run_sim must shut the engine down before it returns"


def test_min_hold_spans_days_because_entry_times_advance(monkeypatch):
    """A position entered on day 1 must age: the fill stamp comes from the sim clock, so
    min-hold in DAYS becomes a real constraint instead of always-satisfied."""
    from core.sim.fill_engine import _fill_time

    t1 = _fill_time(datetime(2026, 8, 3, 15, 0))
    t2 = _fill_time(datetime(2026, 8, 5, 15, 0))
    assert (t2 - t1) >= timedelta(days=1)
