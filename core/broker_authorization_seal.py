# core/broker_authorization_seal.py
# #5135 (GTM-1): seal the operator's answers to the Alpaca authorization prompt onto the
# tamper-evident SHA-256 WORM chain, on the next engine boot (pattern: core/eula_seal.py).
#
# The desktop shell writes ONE file per answer to `<AAA_USER_DATA_DIR>/broker_authorizations/`
# (desktop/electron/broker-authorization.cjs). The ENGINE owns the WORM write (BORA — no JS-side
# crypto beyond the text hash) and stamps `sealed_at` per file for idempotency.
from __future__ import annotations

import json
import logging
import os
import re
from datetime import timezone
from pathlib import Path

from core.round_table.senate_log import BrokerAuthorizationEvent, LocalJSONAuditLogger
from core.sim.clock import engine_now

logger = logging.getLogger(__name__)

_ANSWER_DIR = "broker_authorizations"
_ANSWERS = frozenset({"allow", "deny"})
# Mask produced by the shell: "••••" + the last (at most) four characters. Anything else could be
# a clear-text key and never reaches the chain.
_MASK = re.compile(r"^••••.{0,4}$")


def _user_data_dir() -> str:
    return os.environ.get("AAA_USER_DATA_DIR", "").strip()


async def _seal_one(path: Path, logger_: LocalJSONAuditLogger) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[BROKER-AUTH] answer file unreadable (%s): %s", path, exc)
        return False
    if not isinstance(data, dict) or data.get("sealed_at"):
        return False
    answer = str(data.get("answer", ""))
    if answer not in _ANSWERS:
        logger.warning(
            "[BROKER-AUTH] answer %r in %s is neither allow nor deny — not sealed",
            answer,
            path.name,
        )
        return False
    key_masked = str(data.get("key_masked", ""))
    if not _MASK.match(key_masked):
        logger.warning(
            "[BROKER-AUTH] key_masked in %s does not match the mask — sealed as ''",
            path.name,
        )
        key_masked = ""

    event = BrokerAuthorizationEvent(
        timestamp=str(data.get("answeredAt", "")),
        actor=str(data.get("actor", "operator")),
        version=str(data.get("version", "")),
        text_sha256=str(data.get("text_sha256", "")),
        answer=answer,
        slot=str(data.get("slot", "")),
        key_masked=key_masked,
    )
    try:
        await logger_.log_hitl_event(event)
    except Exception as exc:
        logger.warning(
            "[BROKER-AUTH] failed to seal %s onto the WORM chain: %s", path.name, exc
        )
        return False

    try:
        data["sealed_at"] = engine_now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (
        Exception
    ) as exc:  # sealed on-chain already; the stamp is only the skip-marker
        logger.warning(
            "[BROKER-AUTH] sealed %s but could not stamp sealed_at: %s", path.name, exc
        )
    return True


async def seal_broker_authorizations() -> int:
    """Seal every pending Alpaca authorization answer onto the WORM chain (idempotent, best-effort).

    Files are processed in file-name order; the name starts with an ISO timestamp, so the order is
    chronological. Returns the number of NEWLY sealed answers. Never raises.
    """
    try:
        udd = _user_data_dir()
        if not udd:
            return 0
        folder = Path(udd) / _ANSWER_DIR
        if not folder.is_dir():
            return 0
        audit = LocalJSONAuditLogger()
        sealed = 0
        for path in sorted(folder.glob("*.json")):
            if await _seal_one(path, audit):
                sealed += 1
        if sealed:
            logger.info(
                "[BROKER-AUTH] %d authorization answer(s) sealed onto the WORM chain",
                sealed,
            )
        return sealed
    except Exception as exc:
        logger.warning("[BROKER-AUTH] seal step failed: %s", exc)
        return 0
