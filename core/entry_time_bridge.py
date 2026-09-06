# core/entry_time_bridge.py
# #2965 — the missing bridge between the durable entry-time (#1994 reconcile
# writes PortfolioManager._trade_history) and the strategies' own in-memory
# _entry_time maps, which only the legacy buy paths ever fill. Without it a
# foreign/executor/restart position has no known age, the #1952 fail-open marks
# it past the min-hold window, and rotation rule #1 sells it immediately
# (proven live: HWM bought 14:23:54, sold the same minute, 2026-08-19).
#
# Deliberately a LEAF module (stdlib + lazy config import only) so both
# core/strategies/* and core/engine/* can import it without a cycle.
# Flag-dark: ENTRY_TIME_RECONCILE_PER_CYCLE default False => bridged_entry_time
# returns exactly what the caller's own map held — byte-identical behavior.

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def per_cycle_enabled() -> bool:
    """The #2965 feature flag (both editions, default False). Fail-safe OFF."""
    try:
        import config

        return bool(getattr(config, "ENTRY_TIME_RECONCILE_PER_CYCLE", False))
    except Exception:  # noqa: BLE001 — a config fault must never arm the feature
        return False


def durable_entry_time(pm: Any, symbol: str) -> Optional[datetime]:
    """The reconciled entry-time from ``pm._trade_history`` — or None.

    Read-only and fail-safe: any shape surprise (no PM, empty history, non-
    datetime entry) degrades to None so the caller's existing default applies.
    """
    try:
        history = getattr(pm, "_trade_history", None) or {}
        entries = history.get(symbol) or []
        ts = entries[0] if entries else None
        return ts if isinstance(ts, datetime) else None
    except Exception:  # noqa: BLE001 — a bridge fault must never break a cycle
        return None


def bridged_entry_time(strategy: Any, symbol: str) -> Optional[datetime]:
    """The strategy's own in-memory entry-time, else (flag ON) the durable one.

    Resolution order:
      1. ``strategy._entry_time[symbol]`` — the legacy buy path's own record
         always wins (unchanged semantics).
      2. Flag ON: ``strategy.portfolio_manager._trade_history`` — the #1994/
         #2046 reconciled broker truth (covers executor buys, foreign fills,
         restarts). Strategies without a portfolio_manager (LSTMDynamicStrategy)
         degrade to None.
      3. None — the caller keeps its pre-existing default (#1952 fail-open in
         lstm_strategy, the conservative current_time in rl_execution).
    """
    try:
        own = getattr(strategy, "_entry_time", {}).get(symbol)
    except Exception:  # noqa: BLE001
        own = None
    if own is not None:
        return own
    if not per_cycle_enabled():
        return None
    entry = durable_entry_time(getattr(strategy, "portfolio_manager", None), symbol)
    if entry is not None:
        logger.debug(
            "[EntryTimeBridge] %s: using durable reconciled entry-time %s",
            symbol,
            entry,
        )
    return entry
