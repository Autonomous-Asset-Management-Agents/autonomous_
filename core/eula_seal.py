# core/eula_seal.py
# GTM-1 T3 (#1466): seal the desktop first-run EULA + Risk-Disclosure acceptance onto the
# tamper-evident SHA-256 WORM hash chain, on the first engine boot after the operator accepted.
#
# The desktop first-run wizard writes the deliberate acceptance to
# `<AAA_USER_DATA_DIR>/eula_acceptance.json` (it gates the app before boot). The ENGINE owns the
# WORM write (BORA — same pattern as LIVE-1 T4 `/api/live/enable`; no JS-side crypto), sealing it
# onto the same chain as the HITL / live-enablement audits, then stamps `sealed_at` for idempotency.
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.round_table.senate_log import EulaAcceptanceEvent, LocalJSONAuditLogger

logger = logging.getLogger(__name__)

_GATE_FILE = "eula_acceptance.json"


def _user_data_dir() -> str:
    return os.environ.get("AAA_USER_DATA_DIR", "").strip()


async def seal_eula_acceptance() -> bool:
    """Seal a pending first-run EULA acceptance onto the WORM chain (idempotent, best-effort).

    Reads ``<AAA_USER_DATA_DIR>/eula_acceptance.json``; on the first boot it appends a string-only
    ``eula_acceptance`` event to the SHA-256 hash chain (via ``LocalJSONAuditLogger``, which keys on
    ``SENATE_LOG_DIR`` exactly like the live engine), then stamps ``sealed_at`` so later boots skip
    it. Returns True iff a NEW acceptance was sealed. Never raises — audit must not crash the boot.
    """
    udd = _user_data_dir()
    if not udd:
        return False
    gate = Path(udd) / _GATE_FILE
    if not gate.is_file():
        return False
    try:
        data = json.loads(gate.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[EULA] gate-file unreadable (%s): %s", gate, exc)
        return False
    if data.get("sealed_at"):
        return False  # already on the chain

    event = EulaAcceptanceEvent(
        timestamp=str(data.get("acceptedAt", "")),
        actor=str(data.get("actor", "operator")),
        document=str(data.get("document", "eula")),
        version=str(data.get("version", "")),
        text_sha256=str(data.get("text_sha256", "")),
        app_version=str(data.get("app_version", "")),
    )
    try:
        # The engine restarts its in-memory chain each boot, so a fresh logger is consistent with
        # the existing per-process behaviour; the entry is hash-sealed on its own content.
        await LocalJSONAuditLogger().log_hitl_event(event)
    except Exception as exc:
        logger.warning("[EULA] failed to seal acceptance onto the WORM chain: %s", exc)
        return False

    try:
        from datetime import datetime, timezone

        data["sealed_at"] = datetime.now(timezone.utc).isoformat()
        gate.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (
        Exception
    ) as exc:  # sealed on-chain already; the stamp is only the skip-marker
        logger.warning("[EULA] sealed on-chain but could not stamp sealed_at: %s", exc)
    logger.info(
        "[EULA] first-run acceptance sealed onto the WORM chain (version %s)",
        event.version,
    )
    return True
