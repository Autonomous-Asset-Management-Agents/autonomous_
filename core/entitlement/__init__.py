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

__all__ = ["resolve_entitlement", "activate_license", "Entitlement", "Tier"]

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


def activate_license(raw_token: str) -> Optional[Tier]:
    """GTM-2 (#1809) T4 — offline activation of a pasted license key.

    Verifies ``raw_token`` with the SAME shared verifier ``resolve_entitlement`` uses
    (crypto.verify_token — Ed25519 signature + unexpired + known tier) and, ONLY on a
    pass, persists it as ``<AAA_USER_DATA_DIR>/license.json`` — the exact file the next
    ``resolve_entitlement`` reads, so the tier flips without a second crypto path.

    Fully offline: no network, no cloud, no login (BaFin-safe — the company is never in
    the trading loop and holds no remote kill-switch). Fail-closed — returns None and
    NEVER writes a license file when: the deployment is not LOCAL (cloud/dev is always
    PROFESSIONAL and has no license concept), the token is invalid/expired/untrusted, or
    there is no user-data dir / the write fails. Returns the resolved Tier on success.
    """
    if not _is_local():
        return None

    tier = verify_token(raw_token)
    if tier is None:
        logger.warning(
            "[Entitlement] activation rejected — token invalid/expired/untrusted."
        )
        return None

    udd = os.getenv("AAA_USER_DATA_DIR", "").strip()
    if not udd:
        logger.warning(
            "[Entitlement] activation cannot persist — AAA_USER_DATA_DIR unset."
        )
        return None

    path = Path(udd) / _LICENSE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"token": raw_token}), encoding="utf-8")
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a persist failure must not crash; fail-closed
        logger.warning("[Entitlement] activation could not write %s: %s", path, exc)
        return None

    logger.info("[Entitlement] license activated offline → %s.", tier.value)
    return tier
