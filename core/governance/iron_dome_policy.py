"""ADR-SEC-06 (#1583): the single, admin-adjustable Iron Dome policy.

Operational risk/compliance limits are adjustable at runtime within immutable
hard-floor caps. This module is the policy-store loader: it parses the raw policy
(the `config_value` JSON of the `SystemConfig` row, `config_key="iron_dome_policy"`),
clamps every value to the hard-floor caps, and **fails closed** to the tightest-safe
default when the source is missing or malformed.

ADR-SEC-05 invariant: the AI/agents have **no write path** here; only the admin
endpoint (sub-issue #1595) writes the `SystemConfig` row that this module reads.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The SystemConfig key under which the effective policy is stored (read by the engine,
# written only by the admin endpoint — #1595).
CONFIG_KEY = "iron_dome_policy"

# --- Immutable hard-floor caps (ADR-SEC-06 §2) -----------------------------------
# Each cap is a regulatory/risk constant with its Basis (where the value comes from) and
# Rationale (why this value), per CODING_POLICY.md §6. Ratified under #1599; the clamp
# enforces every cap fail-closed (no admin loosening can widen a control past it).
#
# ADR-C04 — MAX_DAILY_TRADES_CEILING (ratified #1599)
#   Basis: structural — MAX_POSITIONS (10) x MAX_TRADES_PER_SYMBOL_PER_DAY (5).
#   Rationale: the absolute number of distinct fills a day can structurally produce.
MAX_DAILY_TRADES_CEILING = 50
# ADR-C03 — WASH_TRADE_WINDOW_MIN_SECONDS (ratified #1599)
#   Basis: wash-trade detection window minimum (compliance.py).
#   Rationale: < 30 s would wrongly flag legitimate rapid correction trades as wash.
WASH_TRADE_WINDOW_MIN_SECONDS = 30
# ADR-C01 — MAX_ORDER_VALUE_CEILING (ratified #1599)
#   Basis: single-order notional ceiling. Specific MiFID II / ESMA anchor
#   (large-in-scale / extended-reporting) PENDING Compliance sign-off (#1599).
#   Rationale: 10x the 10,000 EUR per-order operating default (config.py).
MAX_ORDER_VALUE_CEILING = 100_000.0
# ADR-R07 — PORTFOLIO_STOP_LOSS_PCT_CEILING (ratified #1599)
#   Basis: ADR-R07; tightest-safe live default is 7 % (risk_manager.py).
#   Rationale: a stop-loss looser than 10 % materially weakens capital preservation.
PORTFOLIO_STOP_LOSS_PCT_CEILING = 0.10
# ADR-R01 — DAILY_DRAWDOWN_PCT_CEILING (ratified #1599)
#   Basis: ADR-R01 (internal risk policy v1.2, 2023-2024 backtests); live 17.5 %.
#   Rationale: 17.5 % absorbs ~3-sigma intraday; 20 % is the absolute protective ceiling.
DAILY_DRAWDOWN_PCT_CEILING = 0.20


@dataclass(frozen=True)
class IronDomePolicy:
    """The effective, immutable operational policy enforced by the Iron Dome."""

    max_daily_trades: int
    wash_trade_window_seconds: int
    max_order_value: float
    portfolio_stop_loss_pct: float
    daily_drawdown_pct: float


# Fail-closed default = the tightest-safe current values (verified against code).
STRICT_DEFAULT = IronDomePolicy(
    max_daily_trades=10,  # config.py#L220 (the tighter of 10 / 50)
    wash_trade_window_seconds=60,  # compliance.py#L60
    max_order_value=10_000.0,  # config.py#L217 (ADR-C01)
    portfolio_stop_loss_pct=0.07,  # risk_manager.py#L114 (ADR-R07)
    daily_drawdown_pct=0.175,  # risk_manager.py#L47 (ADR-R01)
)


def _coerce(value: Any, default: Any) -> Optional[Any]:
    """Coerce ``value`` to the type of ``default``; return None if impossible or NaN."""
    try:
        coerced = type(default)(value)
    except (TypeError, ValueError):
        return None
    # SEC-01: a NaN float is a "valid" float but silently bypasses every downstream limit
    # comparison (nan > x is always False). Reject it -> fail closed to the strict default.
    if isinstance(coerced, float) and math.isnan(coerced):
        return None
    return coerced


def _clamp_ceiling(value: Any, default: Any, ceiling: Any) -> Any:
    v = _coerce(value, default)
    return default if v is None else min(v, ceiling)


def _clamp_floor(value: Any, default: Any, floor: Any) -> Any:
    v = _coerce(value, default)
    return default if v is None else max(v, floor)


def load_policy(raw: Optional[Any]) -> IronDomePolicy:
    """Parse a stored policy into the effective :class:`IronDomePolicy`.

    Fail-closed: a missing or non-dict source returns :data:`STRICT_DEFAULT`. Each field
    is filled from ``STRICT_DEFAULT`` when absent/invalid, and every provided value is
    **clamped** to its immutable hard-floor cap (ADR-SEC-06 §2) — a submitted value can
    only ever land at or tighter than the floor, never wider.
    """
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "IronDomePolicy: malformed source (%s); failing closed to strict default.",
                type(raw).__name__,
            )
        return STRICT_DEFAULT

    return IronDomePolicy(
        max_daily_trades=_clamp_ceiling(
            raw.get("max_daily_trades"),
            STRICT_DEFAULT.max_daily_trades,
            MAX_DAILY_TRADES_CEILING,
        ),
        wash_trade_window_seconds=_clamp_floor(
            raw.get("wash_trade_window_seconds"),
            STRICT_DEFAULT.wash_trade_window_seconds,
            WASH_TRADE_WINDOW_MIN_SECONDS,
        ),
        max_order_value=_clamp_ceiling(
            raw.get("max_order_value"),
            STRICT_DEFAULT.max_order_value,
            MAX_ORDER_VALUE_CEILING,
        ),
        portfolio_stop_loss_pct=_clamp_ceiling(
            raw.get("portfolio_stop_loss_pct"),
            STRICT_DEFAULT.portfolio_stop_loss_pct,
            PORTFOLIO_STOP_LOSS_PCT_CEILING,
        ),
        daily_drawdown_pct=_clamp_ceiling(
            raw.get("daily_drawdown_pct"),
            STRICT_DEFAULT.daily_drawdown_pct,
            DAILY_DRAWDOWN_PCT_CEILING,
        ),
    )


def apply_policy(policy_value: Optional[Any], targets) -> None:
    """Apply the effective policy to each target exposing ``reload_policy`` (null-safe).

    Used at boot (#1619) to push the persisted SystemConfig policy into the freshly-created
    guardians, and reusable on the change path. A None / reload_policy-less target is skipped,
    never raised — boot must not break on a not-yet-initialised guardian.
    """
    for target in targets:
        if target is not None and hasattr(target, "reload_policy"):
            target.reload_policy(policy_value)
