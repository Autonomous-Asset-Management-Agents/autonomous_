# tests/unit/test_closed_market_mode.py
"""Pure decision functions for the market-closed report-only mode.

Design (owner, 2026-07-26): the TRADING/order cycle runs only during market hours; when the
market is closed the engine runs exactly ONE full-universe panel refresh (so the reports stay
complete 24/7, rendered on-demand from the panel) and then idles until near the next open —
the continuous round-table deep-eval + order machinery is skipped, so the GPU rests.

These are the pure, side-effect-free decision seams (mirrors core/engine/rank_funnel.py). The
loop wiring is tested at the integration level; here we pin the branching contract.
"""
from __future__ import annotations

import pytest

from core.engine.closed_market_mode import (
    closed_idle_seconds,
    should_refresh_closed_panel,
    should_run_report_only,
)


class TestShouldRunReportOnly:
    def test_market_closed_enabled_not_bypassed_enters_report_only(self):
        assert should_run_report_only(True, True, False) is True

    def test_market_open_runs_full_cycle(self):
        assert should_run_report_only(False, True, False) is False

    def test_flag_disabled_keeps_legacy_inc6_behaviour(self):
        # OFF path: byte-identical to today (INC-6 continuous analysis market-closed).
        assert should_run_report_only(True, False, False) is False

    def test_bypass_market_hours_forces_full_cycle(self):
        # BYPASS_MARKET_HOURS (test/live override) must always run the full cycle.
        assert should_run_report_only(True, True, True) is False


class TestShouldRefreshClosedPanel:
    def test_first_closed_cycle_refreshes_once(self):
        assert should_refresh_closed_panel(already_refreshed=False) is True

    def test_subsequent_closed_cycles_skip(self):
        assert should_refresh_closed_panel(already_refreshed=True) is False


class TestClosedIdleSeconds:
    def test_unknown_next_open_uses_cap(self):
        assert closed_idle_seconds(None, cap=1800.0, floor=60.0) == 1800.0

    def test_far_open_is_capped_for_responsiveness(self):
        # Hours to open → still wake within `cap` to re-check clock / shutdown / config.
        assert closed_idle_seconds(50_000.0, cap=1800.0, floor=60.0) == 1800.0

    def test_near_open_sleeps_toward_open(self):
        assert closed_idle_seconds(300.0, cap=1800.0, floor=60.0) == 300.0

    def test_below_floor_clamped_up(self):
        assert closed_idle_seconds(5.0, cap=1800.0, floor=60.0) == 60.0

    def test_negative_clamped_to_floor(self):
        # Clock says open is in the past (race) → floor, never a busy-spin.
        assert closed_idle_seconds(-100.0, cap=1800.0, floor=60.0) == 60.0
