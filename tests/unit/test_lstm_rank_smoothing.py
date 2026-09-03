# tests/unit/test_lstm_rank_smoothing.py
# #2680 "Patient AI" — smoothed LSTM cross-section as the ONE ranking seam.
#
# WHY (live evidence 2026-08-05, paper 0.3.14): IVZ was bought 14:00 UTC while
# point-in-time top-10 and rotated out 17:09 UTC at point-in-time rank 242/501 —
# the rank moved ~230 places in three hours. The cross-section is recomputed every
# cycle with NO smoothing, so a one-day spike both buys and (three hours later)
# sells a name.
#
# The original #2680 revision smoothed ONLY the rank funnel
# (trading_loop.py:1547) — the funnel merely picks WHICH symbols get deep-evaluated
# and always contains the holdings, so the two seams that actually DECIDE stayed on
# the raw point-in-time rank:
#   * agents.py (LSTMSignalAgent._vote_on_rank) — the BUY vote
#   * trading_loop.py (_maybe_rotate_and_trim)  — the SELL trigger
# This suite pins the smoothing at ALL consumers through one seam
# (`active_cross_section`) plus the hardening the raw mean lacked.
#
# Contract:
#   * `LstmPanelStore.cross_section_smoothed(as_of, window_days, min_coverage)`
#     - the last N SNAPSHOT DATES <= as_of, selected BY DATE (never insertion order)
#     - MEDIAN per symbol (one outlier day must not carry the score)
#     - coverage filter: a symbol seen in fewer than min(min_coverage, n_dates)
#       of those dates is NOT ranked (a single high day must not top the book)
#     - cold start: fewer dates than the window -> average over what exists
#   * `active_cross_section(store, as_of)` — flag seam read at CALL time:
#     LSTM_RANK_SMOOTHING_ENABLED=false -> cross_section_at, exactly the pre-#2680
#     behaviour (the rollback lever). ARMED by default (owner decision 2026-08-05):
#     the point-in-time basis IS the measured churn, so off would be the broken default.
#   * config parity (BORA): flag + window + min-coverage in BOTH editions.

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]


def _store_with(snapshots):
    """Fresh store; `snapshots` = {date: {symbol: score}} recorded in the given order."""
    from core.report.lstm_panel_store import LstmPanelStore

    store = LstmPanelStore()
    for d, scores in snapshots:
        store.record_cycle(d, sorted(scores.items(), key=lambda kv: -kv[1]))
    return store


def _arm(monkeypatch, enabled, window=10, min_coverage=5):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            LSTM_RANK_SMOOTHING_ENABLED=enabled,
            LSTM_RANK_SMOOTHING_WINDOW_DAYS=window,
            LSTM_RANK_SMOOTHING_MIN_COVERAGE=min_coverage,
        ),
    )


# ---------------------------------------------------------------------------
# 1. cross_section_smoothed — median, date order, coverage, cold start
# ---------------------------------------------------------------------------


def test_median_not_mean_ignores_a_single_spike_day():
    """The IVZ mechanism: a name that spikes on ONE day must not out-rank a
    steady name. The arithmetic mean lets the spike win; the median does not."""
    from core.report.lstm_panel_store import LstmPanelStore

    days = [date(2026, 7, 20 + i) for i in range(5)]
    snaps = []
    for i, d in enumerate(days):
        # SPIKE: 0.1 on four days, 9.0 on the last -> mean 1.88, median 0.1
        # STEADY: 0.5 every day -> mean == median == 0.5
        snaps.append((d, {"SPIKE": 9.0 if i == 4 else 0.1, "STEADY": 0.5}))
    store = _store_with(snaps)

    xsec = store.cross_section_smoothed(days[-1], window_days=5, min_coverage=1)
    assert (
        xsec["STEADY"] > xsec["SPIKE"]
    ), f"median must keep the one-day spike below the steady name, got {xsec}"
    assert xsec["SPIKE"] == pytest.approx(0.1)
    assert xsec["STEADY"] == pytest.approx(0.5)
    assert isinstance(store, LstmPanelStore)


def test_window_selects_by_date_not_insertion_order():
    """Snapshots written out of order (backfill) must still yield the newest
    dates — `reversed(dict)` would return the write order instead."""
    early, mid, late = date(2026, 7, 1), date(2026, 7, 15), date(2026, 7, 30)
    # write LATE first, then EARLY, then MID (insertion order != date order)
    store = _store_with([(late, {"A": 3.0}), (early, {"A": 1.0}), (mid, {"A": 2.0})])

    # window of 1 must pick the LATEST date (3.0), not the first written
    xsec = store.cross_section_smoothed(late, window_days=1, min_coverage=1)
    assert xsec["A"] == pytest.approx(3.0)


def test_window_excludes_dates_after_as_of():
    """PIT honesty: a snapshot dated after `as_of` must never enter the window."""
    d1, d2 = date(2026, 7, 10), date(2026, 7, 11)
    store = _store_with([(d1, {"A": 1.0}), (d2, {"A": 5.0})])

    xsec = store.cross_section_smoothed(d1, window_days=5, min_coverage=1)
    assert xsec["A"] == pytest.approx(1.0), "future snapshot leaked into the window"


def test_coverage_filter_drops_a_one_day_wonder():
    """A symbol present in only 1 of 10 window dates must NOT be ranked — else a
    freshly covered name tops the book on a single sample."""
    days = [date(2026, 7, 1 + i) for i in range(10)]
    snaps = []
    for i, d in enumerate(days):
        scores = {"OLD": 0.5}
        if i == 9:
            scores["NEW"] = 9.9  # appears exactly once
        snaps.append((d, scores))
    store = _store_with(snaps)

    xsec = store.cross_section_smoothed(days[-1], window_days=10, min_coverage=5)
    assert "OLD" in xsec
    assert "NEW" not in xsec, "one-day-wonder must be excluded, not ranked top"


def test_cold_start_averages_over_available_history():
    """Fewer snapshot dates than the window -> use what exists (never block);
    the coverage floor scales down with the available history."""
    days = [date(2026, 7, 1), date(2026, 7, 2)]
    store = _store_with([(days[0], {"A": 1.0}), (days[1], {"A": 3.0})])

    xsec = store.cross_section_smoothed(days[-1], window_days=10, min_coverage=5)
    assert xsec["A"] == pytest.approx(2.0), "cold start must smooth over what exists"


def test_empty_store_returns_empty_dict():
    from core.report.lstm_panel_store import LstmPanelStore

    assert LstmPanelStore().cross_section_smoothed(date(2026, 7, 1)) == {}


# ---------------------------------------------------------------------------
# 2. active_cross_section — the ONE flag seam, resolved at call time
# ---------------------------------------------------------------------------


def _two_day_store():
    d1, d2 = date(2026, 7, 20), date(2026, 7, 21)
    return (
        _store_with([(d1, {"A": 1.0, "B": 2.0}), (d2, {"A": 9.0, "B": 2.0})]),
        d2,
    )


def test_seam_flag_off_is_point_in_time(monkeypatch):
    """The ROLLBACK lever: `=false` -> exactly `cross_section_at`, i.e. the
    pre-#2680 behaviour, so the armed default can be backed out by config alone."""
    from core.report.lstm_panel_store import active_cross_section

    store, as_of = _two_day_store()
    _arm(monkeypatch, enabled=False)

    assert active_cross_section(store, as_of) == store.cross_section_at(as_of)
    assert active_cross_section(store, as_of)["A"] == pytest.approx(9.0)


def test_seam_flag_on_is_smoothed(monkeypatch):
    """ON -> the smoothed table: A's spike day no longer dominates."""
    from core.report.lstm_panel_store import active_cross_section

    store, as_of = _two_day_store()
    _arm(monkeypatch, enabled=True, window=10, min_coverage=1)

    xsec = active_cross_section(store, as_of)
    assert xsec["A"] == pytest.approx(5.0)  # median of (1.0, 9.0)
    assert xsec["B"] == pytest.approx(2.0)


def test_seam_duck_typed_store_without_smoothing_degrades_loudly(monkeypatch, caplog):
    """A store variant that has no `cross_section_smoothed` (duck-typed readers
    exist — see the module docstring's EngineLstmPanelReader) must DEGRADE to the
    point-in-time table with a WARNING, never raise: the rotation lever wraps this
    call in a broad try/except, so an AttributeError would silently kill exits."""
    import logging as _logging

    from core.report.lstm_panel_store import active_cross_section

    _arm(monkeypatch, enabled=True)
    duck = SimpleNamespace(cross_section_at=lambda _as_of: {"AAPL": 1.0})

    with caplog.at_level(_logging.WARNING):
        xsec = active_cross_section(duck, date(2026, 7, 21))

    assert xsec == {"AAPL": 1.0}
    assert any(
        rec.levelno >= _logging.WARNING and "cross_section_smoothed" in rec.getMessage()
        for rec in caplog.records
    ), "the degradation must be visible at WARNING (§5.6)"


def test_seam_broken_config_fails_safe_to_point_in_time(monkeypatch):
    import config
    from core.report.lstm_panel_store import active_cross_section

    def _boom():
        raise ValueError("broken config")

    monkeypatch.setattr(config, "get_config", _boom)
    store, as_of = _two_day_store()
    assert active_cross_section(store, as_of) == store.cross_section_at(as_of)


# ---------------------------------------------------------------------------
# 3. All FOUR consumers go through the seam (the #2680 gap: only the funnel did)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path,why",
    [
        (
            "core/round_table/agents.py",
            "the LSTMSignalAgent BUY vote must read the seam, not cross_section_at "
            "(otherwise a spike still buys)",
        ),
        (
            "core/engine/trading_loop.py",
            "the rotation SELL trigger + the funnel must read the seam "
            "(otherwise a spike collapse still sells — the IVZ case)",
        ),
        (
            "core/engine/api_routes.py",
            "the report card must read the seam so the display can never show a "
            "different standing than the board voted on",
        ),
    ],
)
def test_consumers_use_the_seam(module_path, why):
    src = (_AI_BOT / module_path).read_text(encoding="utf-8")
    assert "active_cross_section" in src, f"{module_path}: {why}"


def test_rotation_trigger_no_longer_reads_point_in_time_directly():
    """The specific IVZ seam: `_maybe_rotate_and_trim` must not call
    `cross_section_at` any more."""
    src = (_AI_BOT / "core/engine/trading_loop.py").read_text(encoding="utf-8")
    assert (
        "store.cross_section_at(now)" not in src
    ), "the rotation lever still reads the raw point-in-time cross-section"


# ---------------------------------------------------------------------------
# 4. Config parity — BORA
# ---------------------------------------------------------------------------

_FLAGS = {
    # ARMED by default (owner decision 2026-08-05) — the point-in-time basis IS the
    # measured churn (IVZ), so shipping it off would ship the known-broken default.
    # The flag stays a flag: `=false` restores the pre-#2680 behaviour exactly.
    "LSTM_RANK_SMOOTHING_ENABLED": True,
    "LSTM_RANK_SMOOTHING_WINDOW_DAYS": 10,
    "LSTM_RANK_SMOOTHING_MIN_COVERAGE": 5,
}


@pytest.mark.parametrize("name,expected", sorted(_FLAGS.items()))
def test_enterprise_config_defines_flag(name, expected):
    import config

    cfg = config.get_config()
    assert hasattr(cfg, name), f"config.py must define {name}"
    assert getattr(cfg, name) == expected


@pytest.mark.parametrize("name,expected", sorted(_FLAGS.items()))
def test_oss_config_defines_flag(name, expected):
    spec = importlib.util.spec_from_file_location(
        "config_oss_smoothing_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, name), f"config.oss.py must define {name} (BORA parity)"
    assert getattr(module, name) == expected
