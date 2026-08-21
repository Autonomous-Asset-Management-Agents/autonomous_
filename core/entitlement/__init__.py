# core/entitlement/__init__.py
# GTM-1 (#1800) — signed Tier-Entitlement layer, public surface.
#
# resolve_entitlement() is THE single entry point every gate calls. It is desktop-scoped:
#   * DEPLOYMENT_MODE != LOCAL  -> FULL entitlement (PROFESSIONAL-equivalent, all 9 agents,
#                                  live allowed, XAI on). Cloud/Dev/CI stay byte-identical.
#   * DEPLOYMENT_MODE == LOCAL  -> read <AAA_USER_DATA_DIR>/license.json, verify the Ed25519
#                                  token, map Tier -> TIER_REGISTRY entry. Missing / invalid /
#                                  expired / unreadable -> BASIC (fail-closed).
#
# It NEVER raises: a resolution failure degrades to BASIC, it does not crash the boot.
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from core.entitlement.crypto import verify_token
from core.entitlement.tier import TIER_REGISTRY, Entitlement, Tier

__all__ = ["resolve_entitlement", "Entitlement", "Tier"]

logger = logging.getLogger(__name__)

_LICENSE_FILE = "license.json"


def _is_local() -> bool:
    return os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL"


def _read_local_token() -> Optional[str]:
    """Return the raw token string from <AAA_USER_DATA_DIR>/license.json, or None."""
    udd = os.getenv("AAA_USER_DATA_DIR", "").strip()
    if not udd:
        return None
    path = Path(udd) / _LICENSE_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — unreadable/corrupt file -> fail-closed BASIC
        logger.warning("[Entitlement] license.json unreadable (%s): %s", path, exc)
        return None
    if isinstance(data, dict):
        token = data.get("token")
        if isinstance(token, str) and token:
            return token
    logger.warning("[Entitlement] license.json present but has no 'token' string.")
    return None


def resolve_entitlement() -> Entitlement:
    """Resolve the active :class:`Entitlement` (single entry point for all gates).

    Non-LOCAL deployments always get the full PROFESSIONAL-equivalent bundle so cloud,
    dev, and CI behaviour is unchanged. LOCAL desktops are gated by the signed license;
    anything missing/invalid/expired fails closed to BASIC.
    """
    if not _is_local():
        return TIER_REGISTRY[Tier.PROFESSIONAL]

    token = _read_local_token()
    if token is None:
        return TIER_REGISTRY[Tier.BASIC]

    tier = verify_token(token)
    if tier is None:
        logger.warning(
            "[Entitlement] license token invalid/expired — falling back to BASIC (fail-closed)."
        )
        return TIER_REGISTRY[Tier.BASIC]

    return TIER_REGISTRY[tier]
