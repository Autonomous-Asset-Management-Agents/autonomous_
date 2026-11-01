"""#2683 — durable LSTM rank panel: the SQLAlchemy adapter behind ``LstmPanelStore``.

WHY: ``core/report/lstm_panel_store.LstmPanelStore`` is an in-memory singleton, and its only
writer records ONE snapshot per calendar day. Since #2680 the ranking basis of every consumer
(the LSTM BUY vote, the rotation SELL trigger, the deep-eval funnel, the report card) is the
per-symbol MEDIAN over the last N snapshot DATES — so a restart, which empties the store,
collapses that median to a single sample: exactly the point-in-time value the smoothing was
written to replace. The operations engine restarts at least daily, so without this module the
smoothing is inert in practice.

DESIGN
------
* **Adapter, not a dependency.** The store never constructs this; it is injected
  (``attach_persistence``). Unit tests use a fake, and the sim never touches a database.
* **Sync API over the async DB layer.** ``record_cycle`` is synchronous and is called from
  inside the running trading-loop event loop, so ``asyncio.run`` cannot be used inline. Each
  operation therefore runs in a short-lived worker thread with its OWN event loop — the same
  bridge ``core/cloud_logger`` uses for its flush. The cost is irrelevant here: one write per
  day (~universe rows) and one read per process.
* **Idempotent write.** Delete-then-insert for the one affected date instead of a
  dialect-specific upsert, so the same code path holds on SQLite (desktop) and PostgreSQL
  (enterprise) — BORA-neutral. A later same-day cycle simply replaces that day.
* **Bounded.** ``prune_before`` drops dates the store no longer retains, keeping the table at
  roughly ``SNAPSHOT_HISTORY_LEN × universe`` rows.
* **Never raises into the caller.** The store wraps every call, but the adapter also degrades
  on its own: a missing ORM/DB layer makes it a no-op rather than an import error.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)


def _run_blocking(coro_factory):
    """Run an async DB operation to completion in a dedicated thread + event loop.

    Safe to call from inside a running event loop (which ``record_cycle`` is) and from plain
    sync code (``get_store`` hydration at import/boot). Exceptions are re-raised to the
    caller so the store can log them with its own context.
    """
    box: dict = {}

    def _worker():
        try:
            box["result"] = asyncio.run(coro_factory())
        except (
            Exception
        ) as exc:  # noqa: BLE001 — carried to, and re-raised in, the caller
            box["error"] = exc

    t = threading.Thread(target=_worker, name="LstmPanelPersistence", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


class SqlPanelPersistence:
    """``save`` / ``load`` / ``prune_before`` over the ``lstm_panel_snapshots`` table."""

    def _session_maker(self):
        # Imported lazily: a CI/unit environment without asyncpg/aiosqlite must not turn this
        # module into an import error — the store then simply stays memory-only.
        from core.database.session import AsyncSessionLocal

        if AsyncSessionLocal is None:
            raise RuntimeError("no DB session maker available")
        return AsyncSessionLocal

    # ---- write ----------------------------------------------------------
    def save(self, snapshot_date: date, rows: Sequence[Tuple[str, float]]) -> int:
        """Replace the stored snapshot for ``snapshot_date`` with ``rows``."""
        from sqlalchemy import delete, insert

        from core.database.models import LstmPanelSnapshot

        maker = self._session_maker()
        payload = []
        for sym, score in rows or ():
            try:
                payload.append(
                    {
                        "snapshot_date": snapshot_date,
                        "symbol": str(sym),
                        "score": float(score),
                    }
                )
            except (TypeError, ValueError):
                continue  # one malformed row must not void the day
        if not payload:
            return 0

        async def _op():
            async with maker() as session:
                async with session.begin():
                    await session.execute(
                        delete(LstmPanelSnapshot).where(
                            LstmPanelSnapshot.snapshot_date == snapshot_date
                        )
                    )
                    await session.execute(insert(LstmPanelSnapshot), payload)
            return len(payload)

        return int(_run_blocking(_op) or 0)

    # ---- read -----------------------------------------------------------
    def load(self, max_dates: int) -> List[Tuple[date, str, float]]:
        """The rows of the newest ``max_dates`` snapshot dates, oldest date first.

        The store re-orders by date anyway; returning them sorted keeps the contract obvious.
        The table is bounded, so one plain SELECT is cheaper than a two-step date query.
        """
        from sqlalchemy import select

        from core.database.models import LstmPanelSnapshot

        maker = self._session_maker()

        async def _op():
            async with maker() as session:
                result = await session.execute(
                    select(
                        LstmPanelSnapshot.snapshot_date,
                        LstmPanelSnapshot.symbol,
                        LstmPanelSnapshot.score,
                    )
                )
                return [tuple(r) for r in result.all()]

        rows = _run_blocking(_op) or []
        if not rows:
            return []
        keep = set(sorted({r[0] for r in rows})[-int(max_dates) :])
        return sorted((r for r in rows if r[0] in keep), key=lambda r: (r[0], r[1]))

    # ---- retention ------------------------------------------------------
    def prune_before(self, oldest_retained: date) -> int:
        """Drop snapshots older than the oldest date the in-memory store still retains."""
        from sqlalchemy import delete

        from core.database.models import LstmPanelSnapshot

        maker = self._session_maker()

        async def _op():
            async with maker() as session:
                async with session.begin():
                    res = await session.execute(
                        delete(LstmPanelSnapshot).where(
                            LstmPanelSnapshot.snapshot_date < oldest_retained
                        )
                    )
            return int(getattr(res, "rowcount", 0) or 0)

        return int(_run_blocking(_op) or 0)
