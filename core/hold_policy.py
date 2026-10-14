"""Holding-period rules shared by the strategies (#3632).

These pure functions used to live in ``core/smart_exit.py``. With the Smart Exit price
rule set retired (one exit authority: ``core/intelligent_exit.py``), they keep their
behaviour and move here because they are about the AGE, the HIGH-WATER MARK and the
RANK of a position - not about a price-driven exit decision:

* ``resolve_hold_hours`` - holding age with the #1952 fail-open default.
* ``derive_position_hwm`` - honest position high-water mark (RTR-5/B2).
* ``rank_drop_exit`` - the rebalance rule of the LSTM ranking strategy (#1952 min-hold,
  #2711 hard gate): a name that fell out of the top-N is rotated only after the
  min-hold window. It never looks at the price; stops are the intelligent exit's job.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "SMART_EXIT_MIN_HOLD_DAYS": 5.0,
    "SMART_EXIT_EXIT_RANK_HYSTERESIS": 3.0,
    "ROTATION_MIN_HOLD_HARD_GATE": True,
}


def _setting(name: str):
    """Runtime setting; the shipped value only when the config is unreadable (WARNING)."""
    try:
        import config

        return getattr(config.get_config(), name)
    except Exception:  # noqa: BLE001 - config unavailable in isolated unit tests
        logger.warning(
            "hold_policy: %s unreadable from config -> shipped default %s",
            name,
            _DEFAULTS[name],
            exc_info=True,
        )
        return _DEFAULTS[name]


def resolve_hold_hours(
    entry_time: Optional[datetime],
    now: datetime,
    min_hold_days: Optional[float] = None,
) -> float:
    """Holding age in hours, with the #1952 fail-open default.

    ``entry_time`` is None when the position was NOT opened in this process (e.g.
    reconciled after a restart and no durable entry time available). Returning ~0 h
    would silently freeze every min-hold gate, so an unknown age reads as "just past
    the min-hold window". None-safe; never raises.
    """
    if entry_time is None:
        days = (
            float(_setting("SMART_EXIT_MIN_HOLD_DAYS") or 0.0)
            if min_hold_days is None
            else float(min_hold_days)
        )
        return (days + 1.0) * 24.0
    return (now - entry_time).total_seconds() / 3600.0


def derive_position_hwm(
    entry_price: float,
    session_hwm: Optional[float] = None,
    bar_highs: Optional[list] = None,
    current_price: Optional[float] = None,
) -> float:
    """RTR-5/B2 (#2401, V-2) - honest position high-water mark from the available
    sources: ``max(entry_price, session_hwm, bar_highs..., current_price)``.

    Restart-proof by construction: after a restart the in-memory HWM maps are empty;
    with only ``session_hwm`` the result can be TOO CONSERVATIVE (a later exit), never
    a fabricated peak. None-safe; malformed values are skipped.
    """
    candidates = []
    for value in (entry_price, session_hwm, current_price):
        try:
            if value is not None:
                candidates.append(float(value))
        except (TypeError, ValueError):
            continue
    for high in bar_highs or ():
        try:
            if high is not None:
                candidates.append(float(high))
        except (TypeError, ValueError):
            continue
    return max(candidates) if candidates else 0.0


@dataclass(frozen=True)
class RankExit:
    sell: bool
    reason: str


def rank_drop_exit(
    in_top_n: bool,
    lstm_rank: Optional[int],
    hours_held: float,
    top_n_size: int = 10,
    min_hold_days: Optional[float] = None,
    exit_rank_hysteresis: Optional[float] = None,
    min_hold_hard_gate: Optional[bool] = None,
) -> RankExit:
    """Rebalance rule of the ranking strategy (formerly smart_exit rule #1).

    A name that dropped out of the top-N is rotated once it has been held at least
    ``min_hold_days`` (#1952: the LSTM signal is a ~5-day-horizon signal; selling on
    the day it slips churned the book). Inside the window a rank collapse past
    ``top_n_size * exit_rank_hysteresis`` exits early ONLY when the #2711 hard gate is
    off (legacy OR). A None rank (abstained / not ranked) never sells. Prices are not
    looked at here - every price exit is the intelligent exit's decision.
    """
    if in_top_n or lstm_rank is None or lstm_rank <= top_n_size:
        return RankExit(False, "in ranking")
    days = (
        float(_setting("SMART_EXIT_MIN_HOLD_DAYS"))
        if min_hold_days is None
        else float(min_hold_days)
    )
    hyst = (
        float(_setting("SMART_EXIT_EXIT_RANK_HYSTERESIS"))
        if exit_rank_hysteresis is None
        else float(exit_rank_hysteresis)
    )
    hard_gate = (
        bool(_setting("ROTATION_MIN_HOLD_HARD_GATE"))
        if min_hold_hard_gate is None
        else bool(min_hold_hard_gate)
    )
    min_hold_ok = hours_held / 24.0 >= days
    rank_collapsed = lstm_rank > top_n_size * hyst
    if min_hold_ok or (rank_collapsed and not hard_gate):
        trigger = "rebalancing" if min_hold_ok else "rank-collapse rebalancing"
        return RankExit(
            True, f"Dropped from LSTM top {top_n_size} (rank {lstm_rank}) - {trigger}"
        )
    return RankExit(False, "inside min-hold window")
