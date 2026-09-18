# flake8: noqa
# tests/unit/test_spy_rebase_gapday_3071.py
# rc3 validation finding (#3071 follow-up): a performance-baseline on a NON-trading day
# (2026-08-01 = Saturday) made _reconstruct_spy_points fall back to spy_df.iloc[0] — the
# OLDEST close of the fetched window (2026-01-08, 689.44) — so the S&P card showed +11.10%
# where the real since-Aug-1 figure is +1.08% (verified against the engine's own SPY cache).
# The rebase base for a gap-day start must be the first close ON/AFTER the start date.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.engine import api_routes


def _spy_df():
    # Window reaching back far before the curve (the real failure shape): an old
    # January close (689.44) at iloc[0], then the relevant August days.
    idx = pd.to_datetime(
        ["2026-01-08", "2026-07-31", "2026-08-03", "2026-08-04", "2026-08-26"]
    )
    return pd.DataFrame({"close": [689.44, 746.79, 757.72, 771.11, 765.94]}, index=idx)


def _points():
    # Curve starting on Saturday 2026-08-01 (gap day — no SPY close on that date).
    return [
        {"date": "2026-08-01", "equity": 222.95},
        {"date": "2026-08-03", "equity": 223.10},
        {"date": "2026-08-26", "equity": 6143.60},
    ]


def _run(points):
    eng = MagicMock()
    eng.data_provider.get_data.return_value = _spy_df()
    with patch.object(api_routes, "engine", eng):
        return api_routes._reconstruct_spy_points(points, points[0]["equity"])


def test_gap_day_start_rebases_to_first_close_on_or_after_the_start():
    spy_points, spy_first_close = _run(_points())
    # NOT the window's oldest close (689.44 → the +11.10% bug); the first trading day
    # on/after 2026-08-01 is 2026-08-03 (757.72).
    assert spy_first_close == pytest.approx(757.72)
    # first→last of the rebased curve = the real since-baseline S&P return (~+1.08%)
    ret = (spy_points[-1]["equity"] / spy_points[0]["equity"] - 1) * 100
    assert ret == pytest.approx(1.08, abs=0.1)


def test_trading_day_start_is_unchanged():
    pts = [
        {"date": "2026-08-03", "equity": 223.10},
        {"date": "2026-08-26", "equity": 6143.60},
    ]
    spy_points, spy_first_close = _run(pts)
    assert spy_first_close == pytest.approx(757.72)  # exact-date hit, as before


def test_start_after_all_spy_data_fails_soft():
    pts = [
        {"date": "2027-01-01", "equity": 100.0},
        {"date": "2027-01-02", "equity": 101.0},
    ]
    spy_points, spy_first_close = _run(pts)
    # no close on/after the start → nearest BEFORE (last known), never the window's oldest
    assert spy_first_close == pytest.approx(765.94)
