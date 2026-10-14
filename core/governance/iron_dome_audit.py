"""ADR-SEC-06 (#1597): WORM audit for admin Iron Dome policy changes.

**Primary** — the EU AI Act Art-14 hash chain (the **same** recorder the HITL Autonomy Policy
uses, ``log_policy_event``): a change is recorded BEFORE the policy is mutated with
``strict=True`` so a failed write re-raises and the mutation is **refused** (never unaudited).

**Secondary** — a queryable ``iron_dome_policy_audit`` mirror row (one per change). The mirror
is **best-effort**: a failure is logged, not raised, because the Art-14 chain is already the
authoritative tamper-evident trail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.hitl_gate import log_policy_event

logger = logging.getLogger(__name__)


async def record_iron_dome_policy_change(
    old_policy: Optional[dict],
    new_policy: dict,
    actor: str = "iron_dome_admin",
) -> None:
    """Record an admin change: Art-14 hash chain (strict) + queryable mirror (best-effort)."""
    await log_policy_event(old_policy or {}, new_policy, actor, strict=True)
    await _write_audit_mirror(old_policy or {}, new_policy, actor)


def _build_audit_row(old_policy: dict, new_policy: dict, actor: str):
    """Build the ``IronDomePolicyAudit`` mirror row (pure — no DB)."""
    from core.database.models import IronDomePolicyAudit

    return IronDomePolicyAudit(
        id=uuid.uuid4().hex,
        event_time=datetime.now(timezone.utc),
        actor=actor,
        old_policy=old_policy,
        new_policy=new_policy,
    )


async def _write_audit_mirror(old_policy: dict, new_policy: dict, actor: str) -> None:
    """Persist the queryable mirror row. Best-effort: a failure is logged, not raised —
    the Art-14 hash chain is the authoritative tamper-evident audit."""
    try:
        from core.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            session.add(_build_audit_row(old_policy, new_policy, actor))
            await session.commit()
    except (
        Exception
    ) as exc:  # pragma: no cover - defensive; Art-14 trail already written
        logger.warning(
            "Iron Dome audit mirror write failed (non-fatal; Art-14 trail intact)",
            exc_info=True,
        )
