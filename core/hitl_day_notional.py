"""core/hitl_day_notional.py — per-NY-trading-day autonomous-notional counter (PR-0a-ii-4a).

The HITL threshold gate (EU AI Act Art. 14) routes a real-money order to human approval
once the day's **autonomous** notional would exceed ``HITL_MAX_VALUE_PER_DAY``. That running
total MUST survive a process restart: a process-local counter would reset to zero on every
crash / cold-start / deploy, re-opening the full daily budget — an Art-14 bypass (the P2/N3
finding). So the total is **Redis-persisted**, keyed by NY trading date.

Backend = ``RedisClient.get_redis()`` → real Redis (cloud), the in-memory ``LocalStateClient``
(desktop), or ``None`` (every method no-ops). **Dormant** until the threshold gate
(PR-0a-ii-4b) wires it.

Keys: ``hitl:day_notional:{YYYY-MM-DD}`` (NY date) → cumulative autonomous notional (float),
with a 48h ms TTL as belt-and-suspenders; the date-keyed scheme already self-isolates days,
and ``rollover`` deletes *yesterday's* key on an NY-date change (N3 — it never zeroes today's
key, which a naive reset firing on first boot would, re-introducing the restart bypass).
"""

from __future__ import annotations

import logging

from core.redis_client import RedisClient

logger = logging.getLogger(__name__)

_PREFIX = "hitl:day_notional:"
# 48h TTL (ms): well past one trading day; rollover is the primary cleanup, this is a backstop.
_TTL_MS = 48 * 60 * 60 * 1000


def _key(ny_date: str) -> str:
    return f"{_PREFIX}{ny_date}"


class HitlDayNotional:
    """Redis-persisted per-NY-day autonomous-notional counter. All methods static + async."""

    @staticmethod
    async def current(ny_date: str) -> float:
        """The autonomous notional executed so far on ``ny_date`` (0.0 without Redis)."""
        redis = await RedisClient.get_redis()
        if redis is None:
            return 0.0
        value = await redis.get(_key(ny_date))
        return float(value) if value else 0.0

    @staticmethod
    async def add(ny_date: str, amount: float) -> float:
        """Atomically add ``amount`` to ``ny_date``'s autonomous total; return the new total.

        Uses ``incrbyfloat`` (atomic) then ``pexpire`` (TTL) — real Redis' ``INCRBYFLOAT``
        carries no per-op TTL, so the TTL is a separate call (same on both backends).
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return 0.0
        key = _key(ny_date)
        new_total = await redis.incrbyfloat(key, amount)
        await redis.pexpire(key, _TTL_MS)
        return float(new_total)

    @staticmethod
    async def rollover(previous_ny_date: str) -> None:
        """On an NY-date change, delete the PREVIOUS day's key (N3).

        Deleting yesterday (rather than zeroing today) means a restart whose first cycle
        sees a new NY date cannot wipe today's accumulated budget — the date-keyed scheme
        makes today's key spring up fresh on its own.
        """
        redis = await RedisClient.get_redis()
        if redis is None:
            return
        await redis.delete(_key(previous_ny_date))
        logger.info("[HITL] day-notional rollover: cleared %s", previous_ny_date)
