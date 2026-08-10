# core/disclaimer.py
# GTM-1 #1801 — the BaFin risk-disclaimer gate on live-arming (enforcement + the real #1804 wording).
#
# Before the desktop arms LIVE trading the operator MUST have accepted the *current* BaFin
# risk-disclaimer. This module ships the ENFORCEMENT + VERSIONING and the legal-approved wording
# (#1804, human-authored, entity-corrected to the registered UG — see DISCLAIMER_TEXT).
#
# ┌─ REUSE, don't duplicate ────────────────────────────────────────────────────────────────────┐
# │ The acceptance is REUSED from the existing first-run record                                  │
# │   <AAA_USER_DATA_DIR>/eula_acceptance.json                                                    │
# │ written by the desktop wizard (desktop/electron/eula.cjs) and sealed onto the tamper-evident │
# │ WORM chain by core/eula_seal.py. We do NOT invent a second acceptance file.                  │
# │ The disclaimer is versioned INDEPENDENTLY via a dedicated `disclaimer_version` field in that │
# │ same record, so a disclaimer-only re-acceptance (bumping REQUIRED_DISCLAIMER_VERSION) does   │
# │ not require churning the whole EULA document version.                                        │
# └──────────────────────────────────────────────────────────────────────────────────────────────┘
#
# LOCAL-only, fail-closed — the same posture as the #1800 entitlement gate:
#   * DEPLOYMENT_MODE == LOCAL  -> enforce (own-account desktop operator).
#   * DEPLOYMENT_MODE != LOCAL  -> NO-OP. Cloud/Enterprise BYOC is a regulated fund that handles
#                                  its own compliance; behaviour stays byte-identical to before.
#   * missing / unreadable file / missing-or-old version -> NOT accepted (raise). An unreadable
#     file is NEVER swallowed into a bypass.
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── BaFin risk-disclaimer (legal-approved wording, #1804) ──────────────────────────────────────
# The REAL BaFin Execution-Only risk-disclaimer, delivered by Legal (#1804) and dropped in verbatim.
# NB: the lawyer's text named the provider "AAAgents GmbH" — a factual entity error. The registered
# Rechtsträger is the UG below (cross-checked against the website Impressum / NOTICE: HRB 54409, AG
# Mainz), so the entity name was corrected to the full registered firm per the org owner's
# instruction. Bumping REQUIRED_DISCLAIMER_VERSION FORCES every operator to re-accept before they
# can arm live again; the desktop shows THIS exact text and writes the matching `disclaimer_version`
# into eula_acceptance.json. Keep DISCLAIMER_TEXT byte-identical with the desktop copy (SHA-256 tie).
REQUIRED_DISCLAIMER_VERSION = "1-bafin-execution-only-2026-en-v2"

DISCLAIMER_TEXT = "RISK WARNING: Trading financial instruments and crypto assets is highly speculative and carries the risk of a total loss of the invested capital. The use of automated trading functions involves additional system-related risks such as network failures or slippage. Past performance is not a reliable indicator of future results. Autonomous Asset Management Agents UG (haftungsbeschränkt) does not provide investment advice or any other regulated financial services. The software acts strictly as a local technical interface (Execution-Only) without custody of client funds (No Custody). The final investment decision is made exclusively by you."

_GATE_FILE = "eula_acceptance.json"
# The dedicated field carrying the accepted disclaimer version inside eula_acceptance.json.
_DISCLAIMER_VERSION_FIELD = "disclaimer_version"


class DisclaimerNotAcceptedError(RuntimeError):
    """Raised (fail-closed) when the current BaFin risk-disclaimer has not been accepted."""


def disclaimer_text_sha256() -> str:
    """SHA-256 of the current (placeholder) disclaimer text — ties an acceptance to what was shown.

    Encoded UTF-8, matching the desktop's ``crypto.createHash('sha256').update(text,'utf8')`` so a
    JS-side record and this Python hash are byte-identical once #1804 provides the real text.
    """
    return hashlib.sha256(DISCLAIMER_TEXT.encode("utf-8")).hexdigest()


def _is_local() -> bool:
    # Same LOCAL detection as core/entitlement (single, consistent convention across gates).
    return os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL"


def _user_data_dir() -> str:
    return os.environ.get("AAA_USER_DATA_DIR", "").strip()


def _version_accepted(recorded: str) -> bool:
    """Is a recorded disclaimer version at least the required one?

    Comparison rule (documented, intentionally simple for the placeholder): the version is a
    monotonically-increasing string ordered by plain lexical ``>=`` comparison. The placeholder is
    ``"0-placeholder"``; the first real drop should sort strictly AFTER it (e.g. ``"1-bafin-2026"``,
    since ``"1" > "0"``). An empty/missing recorded version never satisfies the gate. When #1804
    introduces the first real text, revisit this if a richer scheme (e.g. semver) is ever needed —
    for now a leading integer generation prefix keeps ``>=`` correct.
    """
    return bool(recorded) and recorded >= REQUIRED_DISCLAIMER_VERSION


def _read_accepted_version() -> str | None:
    """Read the accepted disclaimer version from ``<AAA_USER_DATA_DIR>/eula_acceptance.json``.

    Returns the recorded disclaimer-version string, or ``None`` when the file is missing,
    unreadable, or carries no ``disclaimer_version`` (all treated as NOT accepted — fail-closed).
    Never raises: a read problem must resolve to "not accepted", never bubble up as a bypass.
    """
    udd = _user_data_dir()
    if not udd:
        return None
    gate = Path(udd) / _GATE_FILE
    if not gate.is_file():
        return None
    try:
        data = json.loads(gate.read_text(encoding="utf-8"))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — unreadable/corrupt -> NOT accepted (fail-closed)
        logger.warning("[Disclaimer] acceptance file unreadable (%s): %s", gate, exc)
        return None
    if not isinstance(data, dict):
        return None
    version = data.get(_DISCLAIMER_VERSION_FIELD)
    if isinstance(version, str) and version:
        return version
    return None


def assert_disclaimer_accepted() -> None:
    """Fail closed unless the current BaFin risk-disclaimer has been accepted (LOCAL only).

    Enforced ONLY under ``DEPLOYMENT_MODE=LOCAL`` (the own-account desktop). On cloud/enterprise it
    is a no-op — a regulated BYOC fund handles its own compliance and stays byte-identical.

    Accepted iff ``<AAA_USER_DATA_DIR>/eula_acceptance.json`` exists AND its recorded
    ``disclaimer_version`` is ``>=`` :data:`REQUIRED_DISCLAIMER_VERSION`. Anything else — missing
    file, unreadable file, missing field, or an older version — raises
    :class:`DisclaimerNotAcceptedError` (re-acceptance required after a major disclaimer update).
    """
    if not _is_local():
        return

    recorded = _read_accepted_version()
    if recorded is None:
        raise DisclaimerNotAcceptedError(
            "BaFin risk-disclaimer not accepted: no acceptance on record. "
            "Accept the current risk-disclaimer in the desktop before arming live trading."
        )
    if not _version_accepted(recorded):
        raise DisclaimerNotAcceptedError(
            "BaFin risk-disclaimer re-acceptance required after update: recorded version "
            f"{recorded!r} < required {REQUIRED_DISCLAIMER_VERSION!r}. "
            "Re-accept the current risk-disclaimer in the desktop before arming live trading."
        )
    logger.info(
        "[Disclaimer] risk-disclaimer accepted (version %s >= required %s) — live-arming allowed.",
        recorded,
        REQUIRED_DISCLAIMER_VERSION,
    )
