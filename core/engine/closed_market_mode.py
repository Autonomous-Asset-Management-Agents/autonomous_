"""Market-closed report-only mode — trade only during market hours, keep reports live 24/7.

Owner design (2026-07-26): INC-6 today runs the FULL round-table cycle even when the market is
closed (analysis continuous, only order *submission* blocked downstream) — that continuous
full-universe torch inference is the market-closed GPU load, and it produces "decisions" that
can never become orders.

This mode changes that: when the market is closed, the loop runs exactly ONE full-universe LSTM
rank/panel refresh (shortly after close) so the on-demand reports stay COMPLETE 24/7, then idles
until near the next open. The continuous per-symbol round-table deep-eval and the whole
order/trade machinery (execution, risk-exits, position management, HITL drain) are skipped — the
GPU rests. Trading resumes automatically at the next open.

These are the pure, side-effect-free decision seams (mirrors ``core/engine/rank_funnel.py``); the
loop reads them and owns the I/O (clock, snapshot fetch, panel producer, sleep). The mode is
flag-gated (``MARKET_CLOSED_REPORT_ONLY``) and env-reversible to the legacy INC-6 behaviour.
"""

from __future__ import annotations


def should_run_report_only(
    market_closed: bool, report_only_enabled: bool, bypass_market_hours: bool
) -> bool:
    """Whether THIS cycle should enter report-only mode (skip trading/orders, at most one panel
    pass) instead of the full trading cycle.

    True only when the market is closed AND the mode is enabled AND market hours are not being
    bypassed. Market-open, mode-disabled (legacy INC-6), or ``BYPASS_MARKET_HOURS`` (the
    test/live override) → False → the full cycle runs exactly as today.
    """
    return bool(market_closed and report_only_enabled and not bypass_market_hours)


def should_refresh_closed_panel(already_refreshed: bool) -> bool:
    """Within report-only mode, run the single full-universe panel refresh only once per closed
    period — on the first closed cycle. ``already_refreshed`` is reset to False by the loop on the
    next market-open cycle, arming the next close.
    """
    return not already_refreshed


def closed_idle_seconds(
    seconds_to_next_open: "float | None", cap: float = 300.0, floor: float = 60.0
) -> float:
    """Bounded idle between market-closed clock re-checks.

    After the one report/panel pass the loop does NO analysis on subsequent closed wakes (the
    ``_closed_panel_refreshed`` flag skips it) — so the "idle until near open" is behavioural,
    not a single long sleep. We therefore wake at a modest ``cap`` cadence (default 300s, the
    same heartbeat as legacy INC-6): the loop stays responsive to the shutdown event/config and
    resumes trading within ``cap`` of the open, at ~zero GPU cost per wake. Sleep toward the next
    open but never longer than ``cap`` nor shorter than ``floor`` (never a busy-spin even if the
    clock reports the open in the past). Unknown next open → ``cap``.
    """
    if seconds_to_next_open is None:
        return cap
    return max(floor, min(cap, float(seconds_to_next_open)))
