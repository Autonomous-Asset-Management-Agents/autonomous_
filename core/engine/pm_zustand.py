# core/engine/pm_zustand.py
# #4232 (ARC-E6 H-1c) — PM-Zustand (Anti-Churn-Persistenz), wörtlich aus order_executor.py
"""PM-Zustand — Anti-Churn-Sperre über Redis sichern und wiederherstellen (#4232, H-1c).

Wörtlich aus ``order_executor.py`` umgezogen, Schnitt-Entscheidung #4183
(``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1c). Der Kern
re-exportiert beide Namen; Leser und Patches auf ``order_executor.<name>`` treffen
dasselbe Objekt (Entscheidung §3, Weg b).

Dieses Modul importiert den Kern nicht. ``datetime`` wird hier gepatcht (Weg a,
``tests/helpers/stubs.py::FixedClock``), weil sein Leser ``restore_pm_state_from_redis``
mit umgezogen ist.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

# ── Anti-Churn Redis Persistence (Rev 4 — BORA approved 2026-06-13) ────────
# Module-level functions for direct testability (no OrderExecutor fixture needed).
# Persist/restore _trade_history and _consecutive_sell_signals across process restarts.


async def restore_pm_state_from_redis(
    pm: Any,
    r: Any,
    pm_restored: set,
) -> None:
    """Restore anti-churn state from Redis after process restart.

    Only runs once per PM lifecycle (pm_restored tracks user_ids). Uses
    pm.client.get_all_positions() as source of truth for which symbols to restore
    (NF-1: no pm:tracked_symbols index → no Read-Modify-Write race condition).

    Args:
        pm:          PortfolioManager instance (has .user_id, .client, ._trade_history).
        r:           Redis/LocalStateClient instance (already fetched — no second get_redis()).
        pm_restored: Set[str] held by executor — user_ids already restored this session.
    """
    user_id = pm.user_id
    if user_id in pm_restored:
        return  # Already restored this session — no-op

    # NB-2: Validate r BEFORE setting flag — allows retry if Redis temporarily unavailable
    if r is None or not hasattr(r, "get"):
        return

    try:
        # NB-3: asyncio.to_thread() — consistent with reconciliation.py:122, no event-loop block
        open_positions = await asyncio.to_thread(pm.client.get_all_positions)
    except Exception as e:
        logging.warning("[PM-Restore] get_all_positions failed for %s — %s", user_id, e)
        return

    # NC-1: Filter None symbols (defensive coding for malformed broker responses)
    symbols = [
        sym
        for p in open_positions
        if p is not None
        for sym in [p.symbol if hasattr(p, "symbol") else p.get("symbol")]
        if sym is not None
    ]

    # Flag set here — after r validated, whether or not positions exist
    pm_restored.add(user_id)

    if not symbols:
        return

    restored_count = 0
    for sym in symbols:
        try:
            raw_history = await r.get(f"pm:trade_history:{user_id}:{sym}")
            if raw_history:
                times = [datetime.fromisoformat(t) for t in json.loads(raw_history)]
                pm._trade_history[sym] = times
                restored_count += 1

            raw_sells = await r.get(f"pm:sell_signals:{user_id}:{sym}")
            if raw_sells:
                pm._consecutive_sell_signals[sym] = int(raw_sells)
        except (
            Exception
        ) as e:  # DC-1: broad catch — includes ConnectionError, TimeoutError
            logging.warning("[PM-Restore] Error restoring %s:%s — %s", user_id, sym, e)

    if restored_count > 0:
        logging.info(
            "[PM-Restore] Anti-churn state restored for %d/%d symbols (user=%s)",
            restored_count,
            len(symbols),
            user_id,
        )


async def persist_pm_state_to_redis(
    pm: Any,
    symbol: str,
    r: Any,
) -> None:
    """Persist trade_history + sell_signals for ONE symbol to Redis/LocalStateClient.

    Two atomic set() calls — no Read-Modify-Write, no race condition (NF-1 fix).
    r is passed in from the caller — no redundant get_redis() call (F-4 fix).

    Key schema (TTL 26h — survives overnight restart, daily flush prevents stale data):
      pm:trade_history:{user_id}:{symbol}  → JSON list of ISO timestamps
      pm:sell_signals:{user_id}:{symbol}   → str(int) consecutive sell count
    """
    if r is None or not hasattr(r, "set"):
        return

    user_id = pm.user_id
    TTL_MS = 26 * 60 * 60 * 1000  # ADR: 26h — slightly over one trading day

    try:
        history = pm._trade_history.get(symbol, [])
        await r.set(
            f"pm:trade_history:{user_id}:{symbol}",
            json.dumps([t.isoformat() for t in history]),
            px=TTL_MS,
        )
        sells = pm._consecutive_sell_signals.get(symbol, 0)
        await r.set(
            f"pm:sell_signals:{user_id}:{symbol}",
            str(sells),
            px=TTL_MS,
        )
    except Exception as e:
        logging.warning("[PM-Persist] Write failed for %s:%s — %s", user_id, symbol, e)
