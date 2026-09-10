"""ADR-SEC-06 (#1598 §5): four-eyes (segregation of duties) for loosening Iron Dome limits.

Loosening a risk limit (widening it toward the immutable floor) requires TWO distinct admins —
the initiator plus one approver. Tightening needs only one (safety-positive). Four-eyes applies
only in the enterprise edition; the OSS / desktop edition (``DEPLOYMENT_MODE=LOCAL``) is a single
operator, so it is disabled there.
"""

from __future__ import annotations

import os
from typing import List

# Adjustable limits where a HIGHER value is less restrictive (looser).
_HIGHER_IS_LOOSER = (
    "max_daily_trades",
    "max_order_value",
    "portfolio_stop_loss_pct",
    "daily_drawdown_pct",
)
# ...and where a LOWER value is less restrictive.
_LOWER_IS_LOOSER = ("wash_trade_window_seconds",)


def four_eyes_required() -> bool:
    """True only in the enterprise / multi-tenant edition. The OSS / desktop edition
    (``DEPLOYMENT_MODE=LOCAL``) is single-operator, so four-eyes is disabled there."""
    return os.environ.get("DEPLOYMENT_MODE", "").upper() != "LOCAL"


def is_loosening(old_policy: dict, new_policy: dict) -> bool:
    """True if ``new_policy`` widens ANY control vs ``old_policy`` (moves toward the floor).

    Both sides are first resolved to their EFFECTIVE values via ``load_policy`` (fills omitted
    fields with the strict default + clamps to the floor). This closes the key-omission bypass
    (#1635 P1): omitting a field that the current policy set TIGHTER than the default would
    otherwise widen it back to the default without being flagged as loosening.
    """
    from dataclasses import asdict

    from core.governance.iron_dome_policy import load_policy

    old = asdict(load_policy(old_policy))
    new = asdict(load_policy(new_policy))
    for field in _HIGHER_IS_LOOSER:
        if new[field] > old[field]:
            return True
    for field in _LOWER_IS_LOOSER:
        if new[field] < old[field]:
            return True
    return False


def add_approval(approvals: List[str], approver: str, initiator: str) -> List[str]:
    """Return the approver list with ``approver`` added iff it is a DISTINCT admin — not the
    initiator (segregation of duties) and not already present."""
    result = list(approvals or [])
    if approver != initiator and approver not in result:
        result.append(approver)
    return result


def is_ready_to_apply(approvals: List[str], cooloff_until, now) -> bool:
    """Ready when at least one distinct approver has signed off (initiator + 1 = two admins)
    AND the cool-off has elapsed."""
    return len(approvals or []) >= 1 and now >= cooloff_until
