# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/engine/portfolio_context.py
# GAP9 — build the per-cycle portfolio snapshot the ComplianceGatekeeper ("Iron Dome")
# needs. Without it the gatekeeper runs on an empty dict and approves everything (see
# core/round_table/gatekeeper.py docstring: the caller injects portfolio_context).
#
# Contract: ONE snapshot per trading cycle, built in the trading loop and injected into
# every symbol's SymbolEvalState so all parallel evaluations share one consistent view
# (core/engine/trading_loop.py). The gatekeeper itself stays sync/dict-only — all broker
# I/O happens HERE, off the gatekeeper's hot path.
#
# Fail-OPEN by design: any error (no API, fetch failure, equity<=0) returns None. The
# runner then falls back to an empty context = exactly today's behaviour. Strict fail-CLOSED
# is a separate, opt-in runner flag (GATEKEEPER_REQUIRE_CONTEXT) — never silent (§5.6).
#
# Scope (v1): single-account engine view (self.api). Multi-tenant per-client context is an
# explicit Non-Goal. The symbol->sector map has no production source on main yet, so the
# sector-concentration check stays inactive but VISIBLE (empty map + WARNING).

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Bounded so a slow/hung broker call can never stall the whole cycle. Matches the
# snapshot-fetch timeout pattern in trading_loop.py.
_API_TIMEOUT_SEC = 10.0

# Mirrors core/compliance.py ComplianceGuardian.max_daily_trades default — used only when
# no guardian is wired (e.g. simulation), so the daily-limit check stays sane.
_DEFAULT_MAX_DAILY_TRADES = 50


def _fetch_account_and_positions(api):
    """Blocking broker reads, run off the event loop via asyncio.to_thread."""
    return api.get_account(), api.get_all_positions()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def build_portfolio_context(api, compliance_guardian) -> Optional[Dict[str, Any]]:
    """Build the 7-key portfolio_context the ComplianceGatekeeper expects.

    Returns the snapshot dict, or ``None`` on any failure (fail-open: the runner then
    behaves exactly as today). Never raises.
    """
    if api is None:
        logger.warning(
            "portfolio_context: no trading API — gatekeeper runs WITHOUT portfolio context "
            "(concentration / PDT / daily-limit checks inert this cycle)."
        )
        return None

    try:
        account, positions = await asyncio.wait_for(
            asyncio.to_thread(_fetch_account_and_positions, api),
            timeout=_API_TIMEOUT_SEC,
        )
    except (
        Exception
    ) as exc:  # broad on purpose: NEVER let a broker hiccup break the cycle
        logger.warning(
            "portfolio_context: account/positions fetch failed (%s) — gatekeeper runs "
            "WITHOUT portfolio context.",
            exc,
        )
        return None

    equity = _coerce_float(getattr(account, "equity", 0.0))
    if math.isnan(equity) or math.isinf(equity) or equity <= 0:
        # NaN check is explicit: float('nan') <= 0 evaluates to False in Python,
        # so the plain <=0 guard silently passes NaN and poisons the snapshot (P1-3).
        logger.warning(
            "portfolio_context: invalid equity (%.2f) — gatekeeper runs WITHOUT portfolio "
            "context (cannot compute weights).",
            equity,
        )
        return None

    # --- Symbol concentration weights (market_value / equity) ---
    symbol_weights: Dict[str, float] = {}
    for pos in positions or []:
        symbol = getattr(pos, "symbol", None)
        if not symbol:
            continue
        market_value = _coerce_float(getattr(pos, "market_value", 0.0))
        if math.isnan(market_value) or math.isinf(market_value):
            logger.warning(
                "portfolio_context: NaN/Inf market_value for %s — position skipped.",
                symbol,
            )
            continue
        # ADR-RISK-01: concentration weight = absolute exposure, direction-agnostic.
        # abs(market_value) ensures shorts contribute their full risk to the Iron Dome
        # (P0 fix: max(0,…) made every short invisible → 0% concentration, see #1159).
        # Known limitation: a BUY that would COVER an existing short (reducing exposure)
        # is treated identically to a BUY that opens a new long (increasing exposure) —
        # the gatekeeper may conservatively block the covering trade. Acceptable for
        # paper-trading v1 (over-restrictive ≠ unsafe). Direction-aware splitting into
        # long_weights / short_weights is tracked as a follow-up brick (gatekeeper.py).
        symbol_weights[symbol] = min(1.0, abs(market_value) / equity)

    # --- PDT: alpaca-py TradeAccount.daytrade_count = rolling 5 trading days (exact PDT) ---
    day_trades = max(0, _coerce_int(getattr(account, "daytrade_count", 0)))

    # --- Daily-trade counters from the ComplianceGuardian (single source of truth) ---
    current_daily_trades = 0
    max_daily_trades = _DEFAULT_MAX_DAILY_TRADES
    if compliance_guardian is not None:
        current_daily_trades = max(
            0, _coerce_int(getattr(compliance_guardian, "daily_trades", 0))
        )
        max_daily_trades = _coerce_int(
            getattr(compliance_guardian, "max_daily_trades", _DEFAULT_MAX_DAILY_TRADES),
            _DEFAULT_MAX_DAILY_TRADES,
        )

    # --- Sector concentration: no production symbol->sector source on main (Non-Goal). ---
    # Keep the keys present (gatekeeper reads them) but empty, and make the gap VISIBLE so
    # the inactive sector check is never mistaken for "passing".
    logger.warning(
        "portfolio_context: no production symbol->sector map — sector-concentration check "
        "is INACTIVE this cycle (empty sector_weights). Tracked as a follow-up brick."
    )

    return {
        "day_trades_last_5d": day_trades,
        "max_daily_trades": max_daily_trades,
        "current_daily_trades": current_daily_trades,
        "symbol_weights": symbol_weights,
        "sector_weights": {},
        "symbol_sector_map": {},
        # v1: the only lock source is the transient Redis order-lock at execution time
        # (order_executor.py), not portfolio state — so this stays False = today's behaviour.
        "position_locked": False,
    }
