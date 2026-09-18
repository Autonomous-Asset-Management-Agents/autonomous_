"""ADR-OBS-01 follow-up: a cheap, cached, fail-safe count of PENDING four-eyes policy changes.

Exposed via ``governance.pending_policy_change`` on the always-200 ``/engine-diagnostics``
endpoint. A "pending" change is a loosening request that is still awaiting its distinct
second-admin approval — i.e. NOT yet applied (``PendingPolicyChange.applied is False``).
``applied=True`` is the only terminal state on the model, so an applied/approved change is
excluded from the count.

Hot-path invariants:
  * NO heavy per-request DB work. The ``SELECT COUNT(*)`` runs at most ONCE per TTL window
    (``_TTL_SECONDS``); every call in between returns the cached value with zero DB I/O.
  * FAIL-SAFE. ANY error (DB down, missing table, no session) → ``None``. This function must
    never raise and never block the endpoint's always-200 contract.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import sqlalchemy as sa

from core.database.models import PendingPolicyChange
from core.database.session import AsyncSessionLocal

# TTL window (seconds): the COUNT is recomputed at most once per window; between windows the
# cached value is returned with no DB I/O — this is what keeps the always-200 endpoint cheap.
_TTL_SECONDS = 45.0

# Cache: (count, monotonic_ts). ``None`` ts means "cold — never computed".
_cache: Tuple[Optional[int], Optional[float]] = (None, None)


def _reset_cache_for_tests() -> None:
    """Cold-start the TTL cache (test-only helper)."""
    global _cache
    _cache = (None, None)


async def _run_count_query() -> Optional[int]:
    """Async ``SELECT COUNT(*)`` of PENDING (``applied is False``) policy changes.

    Reuses the SAME async-session acquisition as the four-eyes propose/approve path
    (``AsyncSessionLocal`` from ``core.database.session``). Kept as a separate coroutine so
    the TTL cache can gate exactly one invocation per window.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(PendingPolicyChange)
            .where(PendingPolicyChange.applied.is_(False))
        )
        return int(result.scalar_one())


async def get_pending_policy_change_count() -> Optional[int]:
    """Return the cached count of PENDING four-eyes policy changes, refreshing at most once
    per TTL window. Fail-safe: any exception → ``None`` (never raises, never blocks).

    Within the TTL window the cached value is returned with NO DB I/O; only the first call
    after the window elapses runs the ``SELECT COUNT(*)``.
    """
    global _cache
    try:
        count, ts = _cache
        now = time.monotonic()
        if ts is not None and (now - ts) < _TTL_SECONDS:
            return count  # served from cache — no DB work on the hot path
        fresh = await _run_count_query()
        _cache = (fresh, time.monotonic())
        return fresh
    except Exception:  # noqa: BLE001 — deliberate: fail-safe, never break the 200 path
        return None
