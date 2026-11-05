"""Panel-Stale/Coverage-Gate — #1907 (MLR-7) Increment 2/3, plan §5.5.

WHY: the offline full-universe panel (scripts/build_full_universe_panel.py) is the
data foundation for the cross-sectional retrains (#2388). A retrain that quietly
runs on a panel whose data is weeks old, or on a window the membership table does
not cover, bakes staleness or survivorship/look-ahead bias into a trading model —
invisibly, before the first metric is computed. This gate makes that failure LOUD
and BLOCKING.

WHAT IT JUDGES:
  * Panel age — from the panel manifest's ``coverage_end`` (the recency of the DATA,
    deliberately not a build timestamp: a freshly built panel of old data is still
    old data, and the manifest stays deterministic/reproducible without wall-clock
    fields). Older than ``PANEL_MAX_AGE_DAYS`` -> block.
  * Membership coverage — when the caller supplies its window end (``as_of``), the
    same claim authority the simulation uses, ``has_point_in_time_membership``
    (#1907 Inc 1), must uphold the survivorship claim for it -> otherwise block.

POSTURE: default-safe. ``PANEL_STALE_GATE_ENABLED`` defaults to True (conservative,
blocking); a missing or unreadable manifest blocks (fail-closed — garbage must not
train). ``PANEL_STALE_GATE_ENABLED=False`` short-circuits IMMEDIATELY — no reads,
no logs, no behaviour: the BORA byte-identical off-state.

Config is read via ``config.get_config()`` only — no direct environment reads in
the finance core (CODING_POLICY §2.10). Blocks log at WARNING, never DEBUG (§5.6).

CONSUMERS: the #2388 retrain pre-stage (Inc 4) and
``scripts/build_full_universe_panel.py --check-gate``. Nothing on the live decision
path imports this module.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, Union

from config import get_config

logger = logging.getLogger(__name__)


def _as_date(value: Union[date, datetime, str, None]) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def panel_gate_check(
    manifest_path: Union[str, Path],
    as_of: Union[date, datetime, None] = None,
    today: Optional[date] = None,
) -> Tuple[bool, str]:
    """Judge whether the panel behind ``manifest_path`` may feed a training run.

    Returns ``(allowed, reason)``. ``as_of`` is the training/backtest window end —
    pass it whenever known, so membership coverage is judged too (the no-as_of
    call only judges panel age). ``today`` exists for testability and defaults to
    the wall-clock date.
    """
    cfg = get_config()
    if not cfg.PANEL_STALE_GATE_ENABLED:
        # BORA off-state: no reads, no logs, no behaviour beyond this answer.
        return True, "PANEL_STALE_GATE_ENABLED=False — gate disabled"

    max_age_days = int(cfg.PANEL_MAX_AGE_DAYS)
    today = today or date.today()
    manifest_path = Path(manifest_path)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        coverage_end = _as_date(manifest["coverage_end"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = (
            f"panel manifest {manifest_path} missing or unreadable "
            f"({type(exc).__name__}) — cannot judge panel freshness, blocking "
            f"(fail-closed)"
        )
        logger.warning("PanelStaleGate: %s", reason)
        return False, reason

    age_days = (today - coverage_end).days
    if age_days > max_age_days:
        reason = (
            f"panel data is {age_days} days old (coverage_end {coverage_end}, "
            f"today {today}) — exceeds PANEL_MAX_AGE_DAYS={max_age_days}; rebuild "
            f"via scripts/build_full_universe_panel.py before training"
        )
        logger.warning("PanelStaleGate: %s", reason)
        return False, reason

    if as_of is not None:
        # Same claim authority the simulation runner uses (#1907 Inc 1): the
        # membership table must uphold the survivorship claim for the window end.
        from core.data_provider import HistoricalDataProvider

        if not HistoricalDataProvider.has_point_in_time_membership(as_of=as_of):
            reason = (
                f"membership table does not cover as_of={_as_date(as_of)} "
                f"(has_point_in_time_membership=False) — training outside "
                f"membership coverage would reintroduce survivorship/look-ahead "
                f"bias; blocking"
            )
            logger.warning("PanelStaleGate: %s", reason)
            return False, reason

    return True, (
        f"panel fresh (data age {age_days}d <= {max_age_days}d)"
        + ("" if as_of is None else f", membership covers {_as_date(as_of)}")
    )
