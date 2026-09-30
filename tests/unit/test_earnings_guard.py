# tests/unit/test_earnings_guard.py
"""#3349 Earnings-Proximity-Guard — pure rule, cadence estimate, cache, fail modes.

The guard blocks NEW BUYs in a window around a symbol's earnings report. These pin:
POST (exact 8-K date) and PRE (cadence estimate) windows, the two fail modes
(fetch failure ⇒ fail-CLOSED; no reports / cold cache ⇒ fail-OPEN), and the
cache reader. ``now`` is always passed in (the caller uses the ENGINE clock).
"""

import json
from datetime import date

from core.engine.earnings_guard import (
    DATA_MISSING,
    earnings_block_reason,
    earnings_guard_block,
    load_cached_report_dates,
    next_earnings_estimate,
)

REPORT = date(2026, 7, 28)  # CNC 8-K Item 2.02 (verified)


def test_post_window_blocks_day_after_report():
    assert earnings_block_reason([REPORT], date(2026, 7, 29), post_days=2, pre_days=0)
    # exactly at the edge (+2) still blocks; one past (+3) is clear
    assert earnings_block_reason([REPORT], date(2026, 7, 30), post_days=2, pre_days=0)
    assert (
        earnings_block_reason([REPORT], date(2026, 7, 31), post_days=2, pre_days=0)
        is None
    )


def test_post_window_off_when_post_days_zero():
    assert (
        earnings_block_reason([REPORT], date(2026, 7, 29), post_days=0, pre_days=0)
        is None
    )


def test_pre_window_blocks_before_estimated_report():
    est = date(2026, 8, 20)
    assert earnings_block_reason(
        [], date(2026, 8, 17), 0, pre_days=3, next_estimate=est
    )
    assert (
        earnings_block_reason([], date(2026, 8, 16), 0, pre_days=3, next_estimate=est)
        is None
    )


def test_fetch_failure_fails_closed():
    assert (
        earnings_block_reason([], date(2026, 7, 29), 2, 0, fetch_failed=True)
        == DATA_MISSING
    )


def test_no_reports_fails_open():
    # successful lookup, no reports (e.g. ETF) ⇒ no veto
    assert earnings_block_reason([], date(2026, 7, 29), post_days=2, pre_days=0) is None


def test_next_earnings_estimate_median_cadence():
    ds = [date(2026, 1, 28), date(2026, 4, 29), date(2026, 7, 28)]  # ~91d gaps
    est = next_earnings_estimate(ds)
    assert est is not None and est > ds[-1]
    assert next_earnings_estimate([date(2026, 7, 28)]) is None  # <2 ⇒ None


def test_guard_block_cold_cache_is_fail_open():
    # provider reports a missing/cold entry ⇒ no veto (never a market-wide halt)
    assert (
        earnings_guard_block("XYZ", date(2026, 7, 29), 2, 0, lambda s: (None, False))
        is None
    )


def test_guard_block_fetch_error_fail_closed():
    out = earnings_guard_block("XYZ", date(2026, 7, 29), 2, 0, lambda s: (None, True))
    assert out is not None and out[0] == "blocked:earnings_data_missing"


def test_guard_block_window_hit_tags_earnings():
    out = earnings_guard_block(
        "CNC", date(2026, 7, 29), 2, 0, lambda s: ([REPORT], False)
    )
    assert out is not None and out[0] == "blocked:earnings"


def test_load_cached_report_dates(tmp_path):
    p = tmp_path / "earnings_dates.json"
    p.write_text(
        json.dumps(
            {
                "CNC": {"dates": ["2026-07-28", "2026-04-29"], "error": False},
                "DOWN": {"error": True},
            }
        ),
        encoding="utf-8",
    )
    dates, failed = load_cached_report_dates("CNC", str(p))
    assert failed is False and dates == [date(2026, 7, 28), date(2026, 4, 29)]
    assert load_cached_report_dates("DOWN", str(p)) == (None, True)  # fail-closed
    assert load_cached_report_dates("MISSING", str(p)) == (None, False)  # cold
    assert load_cached_report_dates("CNC", str(tmp_path / "nope.json")) == (None, False)
