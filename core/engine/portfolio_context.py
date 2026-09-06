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
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set, Tuple

import pytz

logger = logging.getLogger(__name__)

# Bounded so a slow/hung broker call can never stall the whole cycle. Matches the
# snapshot-fetch timeout pattern in trading_loop.py.
_API_TIMEOUT_SEC = 10.0

# #2037: US/Eastern is the FINRA trading-day reference for "today's fills".
_MARKET_TZ = pytz.timezone("America/New_York")


def _minutes_since_market_open(clock, now) -> Optional[float]:
    """Minutes since today's 09:30 America/New_York regular session open, or ``None`` when
    the market is closed / the clock is missing / anything goes wrong. Intraday buy-pacing
    (OpeningNoiseGuard) fail-open: ``None`` ⇒ the guard does not fire. Regular session only
    (half-days are treated as regular — over-restrictive ≠ unsafe for an opening-noise gate).
    """
    try:
        # Strict: is_open must be a real bool True (a non-bool / truthy test-double clock
        # must NOT drive a time-dependent opening veto — fail-open).
        if clock is None or getattr(clock, "is_open", False) is not True:
            return None
        now_ny = datetime.fromtimestamp(float(now), tz=_MARKET_TZ)
        open_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed = (now_ny - open_ny).total_seconds() / 60.0
        return elapsed if elapsed >= 0 else None
    except Exception:  # noqa: BLE001 — any failure ⇒ fail-open (no veto)
        return None


# Generous submitted_at lower bound for the fills query. Membership is decided by
# filled_at (US/Eastern), NEVER by this bound — it only limits how far back get_orders
# scans. 4 days amply covers cross-midnight + weekend gaps for this low-turnover day-bot;
# an order submitted > 4 days before it fills today (a long-standing GTC) is the only
# residual, mitigated by the opt-in default-OFF flag + Alpaca's own PDT enforcement.
_FILL_LOOKBACK_DAYS = 4
_MAX_FILL_PAGES = 20  # bound the pagination loop (held-symbols-filtered → few orders)

# Mirrors core/compliance.py ComplianceGuardian.max_daily_trades default — used only when
# no guardian is wired (e.g. simulation), so the daily-limit check stays sane.
_DEFAULT_MAX_DAILY_TRADES = 50


def _fetch_account_and_positions(api):
    """Blocking broker reads, run off the event loop via asyncio.to_thread."""
    return api.get_account(), api.get_all_positions()


def _fetch_today_fills(api, held_symbols) -> Set[str]:
    """Blocking broker read: the subset of ``held_symbols`` with a fill on TODAY's
    US/Eastern trading date (#2037 PDT cross-day SELL-Ausnahme).

    P0-safety (AUD-2050 review-verified):
    - ``status=ALL`` — a still-open ``partially_filled`` order's today-fill must count;
      ``CLOSED`` would drop it (partially_filled is an OPEN status).
    - Membership is decided by ``filled_at`` in US/Eastern, NEVER by the ``after`` param
      (``after`` filters ``submitted_at`` — an order submitted before open but FILLED today
      must count). ``after`` is only a generous scan bound; ``filled_at`` decides.
    Filtered to ``held_symbols`` (few) and paginated backward via ``until``.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    held = [s for s in held_symbols if s]
    if not held:
        return set()
    today_et = datetime.now(_MARKET_TZ).date()
    after = datetime.now(_MARKET_TZ) - timedelta(days=_FILL_LOOKBACK_DAYS)
    traded: Set[str] = set()
    until = None
    for _ in range(_MAX_FILL_PAGES):
        req = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            symbols=held,
            after=after,
            limit=500,
            until=until,
        )
        page = api.get_orders(req)
        if not page:
            break
        for o in page:
            filled_at = getattr(o, "filled_at", None)
            if filled_at is None:
                continue
            try:
                if filled_at.astimezone(_MARKET_TZ).date() == today_et:
                    sym = getattr(o, "symbol", None)
                    if sym:
                        traded.add(sym)
            except (ValueError, AttributeError, OverflowError):
                continue
        if len(traded) >= len(held) or len(page) < 500:
            break
        oldest = min(
            (
                getattr(o, "submitted_at", None)
                for o in page
                if getattr(o, "submitted_at", None) is not None
            ),
            default=None,
        )
        if oldest is None:
            break
        until = oldest
    return traded


_MAX_COUNT_PAGES = 20  # bound the unfiltered pagination scan depth


def _fetch_today_trade_counts(api) -> Tuple[Dict[str, int], bool]:
    """Blocking broker read: per-symbol COUNT of orders filled on TODAY's US/Eastern
    trading date, across the WHOLE universe (#2153 Part A — anti-churn entry cap).

    Deliberately NOT ``_fetch_today_fills``: that helper is a membership set over HELD
    symbols and is wrong for counting on two counts —
      1. it early-exits once every held symbol has ≥1 fill → a symbol filled 7× would
         count as 1 and the cap would never trip;
      2. it filters by ``symbols=held`` → a bought-then-sold (now-unheld) symbol is
         invisible and could be re-bought forever.
    So this omits the ``symbols`` filter (ALL symbols) and breaks only on the last
    page / date bound, NEVER on symbol coverage. ``status=ALL`` so a still-open
    ``partially_filled`` today-fill counts (mirrors #2037's filled_at-decides rule).
    ``filled_at`` is timezone-aware → compared in US/Eastern; a ``None`` filled_at is
    skipped (never a naive-compare crash).

    Returns ``(counts, valid)``. ``valid`` is ``False`` when the broker query raised —
    a fail-closed marker so the gatekeeper vetoes BUYs rather than trust a silent 0.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    counts: Dict[str, int] = {}
    try:
        today_et = datetime.now(_MARKET_TZ).date()
        after = datetime.now(_MARKET_TZ) - timedelta(days=_FILL_LOOKBACK_DAYS)
        until = None
        for _ in range(_MAX_COUNT_PAGES):
            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=after,
                limit=500,
                until=until,
            )
            page = api.get_orders(req)
            if not page:
                break
            for o in page:
                filled_at = getattr(o, "filled_at", None)
                if filled_at is None:
                    continue
                try:
                    if filled_at.astimezone(_MARKET_TZ).date() == today_et:
                        sym = getattr(o, "symbol", None)
                        if sym:
                            counts[sym] = counts.get(sym, 0) + 1
                except (ValueError, AttributeError, OverflowError):
                    continue
            if len(page) < 500:
                break
            oldest = min(
                (
                    getattr(o, "submitted_at", None)
                    for o in page
                    if getattr(o, "submitted_at", None) is not None
                ),
                default=None,
            )
            if oldest is None:
                break
            until = oldest
    except Exception as exc:  # noqa: BLE001 — any broker error ⇒ fail-closed marker
        logger.warning(
            "portfolio_context: today trade-count query failed (%s) — fail-closed "
            "(gatekeeper will veto BUYs this cycle).",
            exc,
        )
        return {}, False
    return counts, True


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
    held_symbols: Set[str] = set()  # #2037: the universe for the traded_today map
    for pos in positions or []:
        symbol = getattr(pos, "symbol", None)
        if not symbol:
            continue
        held_symbols.add(symbol)
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

    # --- #2037: per-symbol "traded today?" for the PDT cross-day SELL exemption ---
    # OPT-IN (GATEKEEPER_PDT_CROSSDAY_EXEMPTION, default OFF) → no broker call + empty map ⇒
    # gatekeeper fail-safe (block) ⇒ byte-identical today's behaviour. FINDING-01 (AUD-2050):
    # the sync broker call runs OFF the event loop via asyncio.to_thread with its own timeout.
    # Defensive: a fills-query failure degrades to an EMPTY map (NOT None) so the whole context
    # is still built and the gatekeeper blocks — never an under-block.
    traded_today: Dict[str, bool] = {}
    from config import get_config

    if get_config().GATEKEEPER_PDT_CROSSDAY_EXEMPTION and held_symbols:
        try:
            fills = await asyncio.wait_for(
                asyncio.to_thread(_fetch_today_fills, api, held_symbols),
                timeout=_API_TIMEOUT_SEC,
            )
            traded_today = {sym: sym in fills for sym in held_symbols}
        except Exception as exc:  # noqa: BLE001 — never let this fail the whole context
            logger.warning(
                "portfolio_context: today-fills query failed (%s) — traded_today empty; "
                "PDT cross-day exemption fails safe to BLOCK this cycle.",
                exc,
            )
            traded_today = {}

    # --- #2153 Part A: per-symbol today-trade COUNT for the anti-churn entry cap ---
    # OPT-IN GATEKEEPER_SYMBOL_CHURN_LIMIT (default ON — operator D1). Off ⇒ no broker
    # call + inert guard (byte-identical to today). On ⇒ the count is broker-sourced
    # (restart-proof, correct on both editions). A query failure fails CLOSED:
    # symbol_trades_today_valid=False ⇒ the gatekeeper vetoes ALL BUYs this cycle.
    cfg = get_config()
    symbol_churn_guard_active = bool(
        getattr(cfg, "GATEKEEPER_SYMBOL_CHURN_LIMIT", False)
    )
    symbol_trades_today: Dict[str, int] = {}
    symbol_trades_today_valid = False
    if symbol_churn_guard_active:
        try:
            symbol_trades_today, symbol_trades_today_valid = await asyncio.wait_for(
                asyncio.to_thread(_fetch_today_trade_counts, api),
                timeout=_API_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001 — timeout/unexpected ⇒ fail-closed
            logger.warning(
                "portfolio_context: churn count wait failed (%s) — fail-closed "
                "(gatekeeper will veto BUYs this cycle).",
                exc,
            )
            symbol_trades_today, symbol_trades_today_valid = {}, False
    max_trades_per_symbol = int(getattr(cfg, "MAX_TRADES_PER_SYMBOL_PER_DAY", 5))

    # --- Intraday buy-pacing (clean re-home of #2546) — BUY-only guard fields ---
    # Config is read HERE (like every other gate's flags); the gatekeeper stays dict-only.
    # Every field is fail-open: on any error it stays None ⇒ the sub-guard is inert.
    intraday_timing_active = bool(getattr(cfg, "INTRADAY_TIMING_ENABLED", False))
    minutes_since_market_open: Optional[float] = None
    last_buy_elapsed_minutes: Optional[float] = None
    buys_last_hour: Optional[int] = None
    if intraday_timing_active:
        _now = time.time()
        try:  # defensive clock read (off-thread + timeout + fail-open, Archon WARNING-2)
            clock = await asyncio.wait_for(
                asyncio.to_thread(api.get_clock), timeout=_API_TIMEOUT_SEC
            )
            minutes_since_market_open = _minutes_since_market_open(clock, _now)
        except Exception as exc:  # noqa: BLE001 — a clock hiccup must NEVER veto a BUY
            logger.warning(
                "portfolio_context: get_clock failed (%s) — OpeningNoiseGuard inert "
                "this cycle (fail-open).",
                exc,
            )
        try:  # buy-history from the DEDICATED buffer (never the 60 s wash buffer).
            # Coerce to int/float: a guardian that returns a non-numeric value (a bad
            # wiring / a test double) must NEVER fabricate a pacing veto — the fail-open
            # contract of this builder (int(MagicMock) raises → None → guard inert).
            _bh = compliance_guardian.buys_last_hour(_now)
            buys_last_hour = int(_bh) if _bh is not None else None
            _lb = compliance_guardian.minutes_since_last_buy(_now)
            last_buy_elapsed_minutes = float(_lb) if _lb is not None else None
        except Exception as exc:  # noqa: BLE001 — non-numeric / error ⇒ fail-open
            logger.warning(
                "portfolio_context: buy-history read failed (%s) — BuyPacingGuard inert "
                "(fail-open).",
                exc,
            )
            buys_last_hour = None
            last_buy_elapsed_minutes = None

    return {
        # ADR-PDT-01: equity was already computed above and simply never handed on, so
        # the gatekeeper could not apply FINRA's $25k floor no matter how correct its
        # logic was. Read-only telemetry for the gate; nothing else consumes it.
        "equity": equity,
        "day_trades_last_5d": day_trades,
        "max_daily_trades": max_daily_trades,
        "current_daily_trades": current_daily_trades,
        "symbol_weights": symbol_weights,
        "sector_weights": {},
        "symbol_sector_map": {},
        # v1: the only lock source is the transient Redis order-lock at execution time
        # (order_executor.py), not portfolio state — so this stays False = today's behaviour.
        "position_locked": False,
        # #2037: {symbol: traded_today?} — only populated when the opt-in exemption flag is
        # on; empty otherwise ⇒ gatekeeper .get(sym, True) fail-safe ⇒ PDT block unchanged.
        "traded_today": traded_today,
        # #2153 Part A: {symbol: count} of today's fills + validity + guard state + cap.
        # Empty/inactive unless GATEKEEPER_SYMBOL_CHURN_LIMIT is on (default ON).
        "symbol_trades_today": symbol_trades_today,
        "symbol_trades_today_valid": symbol_trades_today_valid,
        "symbol_churn_guard_active": symbol_churn_guard_active,
        "max_trades_per_symbol": max_trades_per_symbol,
        # Intraday buy-pacing (clean re-home of #2546): the gatekeeper's BUY-only
        # OpeningNoise / BuyPacing guard reads these. Measured fields are None ⇒ fail-open.
        "intraday_timing_active": intraday_timing_active,
        "no_buy_opening_minutes": int(getattr(cfg, "NO_BUY_OPENING_MINUTES", 30)),
        "global_buy_cooldown_minutes": int(
            getattr(cfg, "GLOBAL_BUY_COOLDOWN_MINUTES", 30)
        ),
        "max_buys_per_hour": int(getattr(cfg, "MAX_BUYS_PER_HOUR", 2)),
        "minutes_since_market_open": minutes_since_market_open,
        "last_buy_elapsed_minutes": last_buy_elapsed_minutes,
        "buys_last_hour": buys_last_hour,
    }
