# core/engine/entry_time_reconcile.py
# #1994 — durable (restart-surviving) entry-time via Alpaca fill reconcile.
#
# On desktop (REDIS_URL empty) the anti-churn state (PortfolioManager._trade_history)
# is lost on restart, so days_held=0 and the smart_exit min-hold gate fails open.
# The broker is the source of truth: on startup we reconstruct each held position's
# entry-time from Alpaca's filled-order history. Edition-neutral (no Redis, no new
# schema/config) — BORA-minimal. Called once after restore_pm_state_from_redis().
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First. Read-only vs the broker.

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, List, Optional


def _to_naive_local(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise an Alpaca timestamp to offset-naive LOCAL time.

    Alpaca ``filled_at`` is offset-aware (UTC); PortfolioManager computes elapsed
    time with offset-naive ``datetime.now()`` (portfolio_manager.py:189,557).
    Subtracting the two would raise ``TypeError`` (AUD-2039-1 §2), so convert to
    local and strip tzinfo before storing.
    """
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _fill_side(o: Any) -> str:
    """Lowercase side value, tolerant of alpaca-py enum (OrderSide) or plain string."""
    side = getattr(o, "side", "")
    return str(getattr(side, "value", side)).lower()


def _entry_time_from_fills(fills: List[Any], position_qty: float) -> Optional[datetime]:
    """Entry-time of the CURRENTLY open position, reconstructed from filled orders.

    Aggregate BUY fills **newest-first** (descending ``filled_at``) until the current
    position quantity is reached; the OLDEST ``filled_at`` in that accumulated subset
    is the entry-time. Newest-first is load-bearing (AUD-2039-1 §1): ascending order
    would pick an old, already-closed BUY (position opened → flat → reopened) and let
    the min-hold gate be bypassed. All timestamps are normalised to offset-naive local
    (§2). Fallback (§3): if the history is exhausted before the target qty is reached,
    return the oldest BUY found; if there is no BUY at all, return None (which lets the
    existing resolve_hold_hours fail-open handle it).
    """
    buys = []
    for o in fills:
        if _fill_side(o) != "buy":
            continue
        ts = _to_naive_local(
            getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
        )
        try:
            qty = float(getattr(o, "filled_qty", None) or getattr(o, "qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if ts is None or qty <= 0:
            continue
        buys.append((ts, qty))

    if not buys:
        return None

    buys.sort(key=lambda x: x[0], reverse=True)  # newest first (§1)
    target = abs(float(position_qty or 0))
    accumulated = 0.0
    entry: Optional[datetime] = None
    for ts, qty in buys:
        entry = ts  # oldest seen so far in the accumulated subset
        accumulated += qty
        if target > 0 and accumulated >= target:
            return entry
    # History exhausted before reaching target qty → oldest BUY found (§3).
    return entry


# ADR (AUD-2042-1 FINDING-02): cap backward pagination at 20 pages.
#   20 pages × 500 (get_orders page_size) = 10 000 orders scanned — a deliberately
#   generous upper bound. A low-turnover min-hold bot rarely needs more than 1 page to
#   cover its held qty; the cap exists only to bound the startup loop against the broker
#   so a pathological/corrupt history can never spin unbounded. Read-only reconcile:
#   raising it costs startup latency only, never capital. Revisit if hold-times shorten
#   or position count grows such that 10k orders no longer covers the oldest open BUY.
_MAX_RECONCILE_PAGES = 20


async def _collect_fills(client: Any, held: dict, page_size: int = 500) -> dict:
    """Page ``get_orders`` backward (newest→oldest) collecting fills for the held
    symbols until each symbol's cumulative BUY qty covers its position (or the history
    / page-cap is exhausted). Pagination (AUD-2039-1 §3) covers positions older than a
    single 500-order window — without it, an old position's entry-time would be
    UNDER-estimated (min-hold bypass)."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    by_symbol: dict = {}
    buy_qty = dict.fromkeys(held, 0.0)
    until = None
    for _ in range(_MAX_RECONCILE_PAGES):
        req = GetOrdersRequest(
            status=QueryOrderStatus.ALL, limit=page_size, until=until
        )
        page = await asyncio.to_thread(client.get_orders, req)
        if not page:
            break
        for o in page:
            sym = str(getattr(o, "symbol", ""))
            if sym not in held:
                continue
            by_symbol.setdefault(sym, []).append(o)
            if _fill_side(o) == "buy":
                try:
                    buy_qty[sym] += float(
                        getattr(o, "filled_qty", None) or getattr(o, "qty", 0) or 0
                    )
                except (TypeError, ValueError):
                    pass
        if len(page) < page_size:
            break  # last page — history exhausted
        if all(buy_qty[s] >= abs(float(held[s])) for s in held):
            break  # every held position covered
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
    return by_symbol


async def reconcile_entry_time_from_alpaca(pm: Any) -> None:
    """Populate ``pm._trade_history`` entry-time from Alpaca for held positions that
    have no durable entry-time yet (e.g. desktop restart, no Redis).

    Source of truth = broker → restart-surviving by construction; edition-neutral.
    ``get_orders`` is paginated (``_collect_fills``) only as far back as needed to cover
    each held position; positions already-restored (Redis) are skipped. Fully fail-safe:
    any broker error leaves the state untouched (the existing resolve_hold_hours
    fail-open still applies).
    """
    client = getattr(pm, "client", None)
    if client is None:
        return
    try:
        positions = await asyncio.to_thread(client.get_all_positions)
    except Exception as e:  # noqa: BLE001 — reconcile must never break startup
        logging.warning(
            "[EntryTimeReconcile] get_all_positions failed — %s", e, exc_info=True
        )
        return

    held: dict = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) if hasattr(p, "symbol") else None
        if not sym or sym in pm._trade_history:
            continue  # already durable (restored from Redis) — leave it
        try:
            qty = float(getattr(p, "qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty:
            held[sym] = qty
    if not held:
        return

    try:
        by_symbol = await _collect_fills(client, held)
    except Exception as e:  # noqa: BLE001
        logging.warning("[EntryTimeReconcile] get_orders failed — %s", e, exc_info=True)
        return

    for sym, qty in held.items():
        entry = _entry_time_from_fills(by_symbol.get(sym, []), qty)
        if entry is not None:
            pm._trade_history[sym] = [entry]
            logging.info(
                "[EntryTimeReconcile] %s entry-time reconciled: %s", sym, entry
            )
        else:
            logging.warning(
                "[EntryTimeReconcile] %s: no BUY fill found — entry-time unknown "
                "(resolve_hold_hours fail-open applies)",
                sym,
            )
