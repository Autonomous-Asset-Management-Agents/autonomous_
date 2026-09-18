"""#3272 — RTR-0 attribution horizon defaults to the holding period, not 5 bars.  # noqa: E501

Structural mis-measurement fixed here (RCA 2026-09-08): the direction agents are held  # noqa: E501
~20 trading days (SMART_EXIT_MIN_HOLD_DAYS), so a 5-bar forward IC scores slow signals  # noqa: E501
(12-1M momentum, LSTM rank, options skew) on the wrong window. The default must match  # noqa: E501
the holding horizon. HAC/Newey-West already lags by the horizon, so the longer window's  # noqa: E501
overlap is handled — only the default was wrong.
"""

from __future__ import annotations

# fmt: off
from scripts.rtr0_attribution_benchmark import IC_HORIZON_DEFAULT_BARS, build_parser  # isort: skip # noqa: E501
# fmt: on


def test_default_horizon_matches_holding_period_not_five():
    # ~ SMART_EXIT_MIN_HOLD_DAYS (20-21 trading days), never the old 5-bar default.  # noqa: E501
    assert IC_HORIZON_DEFAULT_BARS == 21
    assert IC_HORIZON_DEFAULT_BARS >= 20


def test_cli_ic_horizon_default_is_holding_period():
    ns = build_parser().parse_args(
        ["--data-dir", "x", "--start", "2025-01-01", "--end", "2025-02-01"]
    )
    assert ns.ic_horizon == IC_HORIZON_DEFAULT_BARS == 21


def test_ic_horizon_still_overridable():
    ns = build_parser().parse_args(
        [
            "--data-dir",
            "x",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-01",
            "--ic-horizon",
            "5",
        ]
    )
    assert ns.ic_horizon == 5
