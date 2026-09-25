"""core/governance/portfolio_constraints.py — HITL-approved sector-cap store (Epic #2655, Weg 1).

The local, edition-neutral store for the async EOD sector-governance flow. #2654 READS the
approved caps in the PortfolioManager's rebalance pass; #2653 WRITES them — exclusively through
the four-eyes ``POST /api/admin/portfolio-constraints/{propose,approve}`` path, WORM-audited
before every mutation. This is deliberately NOT the Iron-Dome policy store (`iron_dome_policy`):
sector caps are portfolio-sizing governance, the Iron Dome's compliance hard-floors stay a
separate, untouched object (epic guardrail).

Shape on disk (``<AAA_USER_DATA_DIR>/portfolio_constraints.json``)::

    {
      "approved": {"Technology": 0.20, ...},   # relative caps ONLY (0 < cap <= 1)
      "approved_meta": {"actor": "a->b", "approved_at": "..."},
      "pending": {...}                          # the four-eyes proposal awaiting a 2nd admin
    }

Read side is FAIL-SAFE: a missing/corrupt file or any non-relative value degrades to "no
constraint" (the engine's own hard caps still apply) — never to a crash in the order path.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_FILENAME = "portfolio_constraints.json"


def _store_path() -> str:
    """<AAA_USER_DATA_DIR>/portfolio_constraints.json (desktop launcher always sets the env
    var — mirrors core.audit_paths); CWD fallback for bare dev runs."""
    base = os.environ.get("AAA_USER_DATA_DIR", "").strip() or os.getcwd()
    return os.path.join(base, _FILENAME)


def _read_store() -> Dict[str, Any]:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_store(data: Dict[str, Any]) -> None:
    """Atomic replace so a crash mid-write can never leave a corrupt store (the read side
    would fail-safe to {} — dropping ACTIVE approved caps on the floor)."""
    path = _store_path()
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", prefix=_FILENAME, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _valid_caps(raw: Any) -> Dict[str, float]:
    """Keep RELATIVE caps only (0 < cap <= 1) — absolute $ values or junk are dropped
    (Archon audit: stale absolute numbers applied hours later would mis-trim)."""
    caps: Dict[str, float] = {}
    if not isinstance(raw, dict):
        return caps
    for sector, cap in raw.items():
        if isinstance(cap, bool) or not isinstance(cap, (int, float)):
            continue
        if 0 < float(cap) <= 1:
            caps[str(sector)] = float(cap)
    return caps


def load_approved_constraints() -> Dict[str, float]:
    """The ACTIVE approved sector caps: {sector: relative_cap}. Fail-safe {}."""
    return _valid_caps(_read_store().get("approved"))


def save_pending_proposal(proposal: Dict[str, Any]) -> None:
    """#2653 propose-step: stage a proposal for the DISTINCT second admin's /approve."""
    data = _read_store()
    data["pending"] = proposal
    _write_store(data)


def load_pending_proposal() -> Optional[Dict[str, Any]]:
    pending = _read_store().get("pending")
    return pending if isinstance(pending, dict) else None


def commit_approved_constraints(caps: Dict[str, float], meta: Dict[str, Any]) -> None:
    """#2653 approve-step: persist the caps as ACTIVE and clear the pending slot. The caller
    (api route) records the WORM audit BEFORE invoking this — a failed audit refuses the
    mutation, so this function stays a dumb persist."""
    data = _read_store()
    data["approved"] = _valid_caps(caps)
    data["approved_meta"] = meta
    data.pop("pending", None)
    _write_store(data)
