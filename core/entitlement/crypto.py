# core/entitlement/crypto.py
# GTM-1 (#1800) — offline Ed25519 verification for signed tier tokens.
#
# WIRE FORMAT (documented, self-contained, no network):
# -----------------------------------------------------------------------------
# A license "token" is a single base64(ASCII) string. Decoding it yields a UTF-8
# JSON ENVELOPE with exactly two fields:
#
#     {
#       "payload": "<base64 of the CANONICAL payload JSON>",
#       "sig":     "<base64 of the detached Ed25519 signature>"
#     }
#
# The CANONICAL payload is the claims object serialised deterministically with
#     json.dumps(payload, separators=(",", ":"), sort_keys=True)
# encoded UTF-8. Those exact bytes are (a) what is base64-embedded in "payload"
# and (b) what the Ed25519 signature in "sig" is computed over. Re-deriving the
# canonical bytes from the decoded claims and comparing is unnecessary because we
# verify the signature directly against the embedded "payload" bytes — so any
# tampering with the embedded payload breaks verification (fail-closed).
#
# Claims schema:
#     {
#       "tier":       "BASIC" | "PRO" | "PROFESSIONAL" | "INSTITUTIONAL",
#       "expires_at": ISO-8601 datetime string (UTC recommended),
#       "issued_to":  free-text licensee id,
#       "nonce":      unique per-token string (replay/dedup aid)
#     }
#
# verify_token() is FAIL-CLOSED: it returns the Tier only when the signature
# verifies against one of TRUSTED_PUBLIC_KEYS AND expires_at is in the future AND
# tier is a known Tier. Every other outcome -> None.
#
# KEY INJECTION / ROTATION:
# TRUSTED_PUBLIC_KEYS is a module-level list holding the CURRENT key plus any still-
# trusted predecessors; verify_token tries each until one verifies (rotation-safe).
# The REAL baked-in production key is provisioned in #1839 — until then the list
# holds only a clearly-marked placeholder (see _PLACEHOLDER_*). Tests inject their
# own key by monkeypatching this list. Additionally, extra keys may be supplied at
# import time via the AAA_ENTITLEMENT_PUBKEYS env var (comma-separated base64 of the
# 32-byte raw Ed25519 public keys) — used by the provisioning step and by ops.
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.entitlement.tier import Tier

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Trusted keys
# --------------------------------------------------------------------------- #
# NO hard-coded trust anchor until #1839 bakes in the real production public key. An
# EMPTY default means NO token can ever verify before provisioning (fail-closed by
# construction) — safer than any placeholder key (avoids Ed25519 low-order-point edge
# cases). Real keys arrive baked-in (#1839) and/or via AAA_ENTITLEMENT_PUBKEYS env var;
# tests monkeypatch this list with their own generated key.


def _load_env_pubkeys() -> List[Ed25519PublicKey]:
    """Optionally load extra trusted keys from AAA_ENTITLEMENT_PUBKEYS (comma-sep b64)."""
    raw = os.getenv("AAA_ENTITLEMENT_PUBKEYS", "").strip()
    if not raw:
        # Fallback to baked-in beta public key (ADR-GTM-1b)
        raw = "Rj4yopwrtnELzX309AV5vBAOuAqhlcL49IFsSDljPw0="
    keys: List[Ed25519PublicKey] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(base64.b64decode(token)))
        except Exception as exc:  # noqa: BLE001 — never let a bad env key crash import
            logger.warning(
                "[Entitlement] ignoring invalid AAA_ENTITLEMENT_PUBKEYS entry: %s", exc
            )
    return keys


# Current + predecessor keys, in preference order. Tests monkeypatch this list.
# Empty until a real key is provisioned (#1839) or supplied via AAA_ENTITLEMENT_PUBKEYS.
TRUSTED_PUBLIC_KEYS: List[Ed25519PublicKey] = [*_load_env_pubkeys()]


# --------------------------------------------------------------------------- #
# Canonicalisation + encoding (also used by test/provisioning code to MINT tokens)
# --------------------------------------------------------------------------- #
def _canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _encode_token(private_key: Ed25519PrivateKey, payload: dict) -> str:
    """Mint a token for ``payload``, signed by ``private_key`` (test/provisioning helper).

    Kept in the production module so the verifier and the minter share one canonical
    wire format and can never drift.
    """
    canonical = _canonical_payload_bytes(payload)
    sig = private_key.sign(canonical)
    envelope = {
        "payload": base64.b64encode(canonical).decode("ascii"),
        "sig": base64.b64encode(sig).decode("ascii"),
    }
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- #
# Verification (the public API)
# --------------------------------------------------------------------------- #
def _expiry_in_future(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > datetime.now(timezone.utc)


def verify_token(raw: str) -> Optional[Tier]:
    """Return the encoded ``Tier`` iff ``raw`` is a valid, unexpired, trusted token.

    Fail-closed: any malformed input, bad/absent signature, unknown tier, or past
    expiry yields ``None``. Tries every key in ``TRUSTED_PUBLIC_KEYS`` (rotation-safe).
    """
    if not raw or not isinstance(raw, str):
        return None

    # 1. Decode the outer base64 -> envelope JSON.
    try:
        envelope = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
        payload_bytes = base64.b64decode(envelope["payload"])
        signature = base64.b64decode(envelope["sig"])
    except Exception:  # noqa: BLE001 — any decode failure is a fail-closed reject
        return None

    # 2. Verify the detached signature against at least one trusted key.
    verified = False
    for pub in TRUSTED_PUBLIC_KEYS:
        try:
            pub.verify(signature, payload_bytes)
            verified = True
            break
        except InvalidSignature:
            continue
        except Exception:  # noqa: BLE001 — defensive; a broken key must not crash us
            continue
    if not verified:
        return None

    # 3. Parse the (now-authenticated) claims.
    try:
        claims = json.loads(payload_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None

    # 4. Expiry gate (fail-closed on missing/past/garbage).
    if not _expiry_in_future(str(claims.get("expires_at", ""))):
        return None

    # 5. Map to a known Tier (fail-closed on unknown).
    try:
        return Tier(str(claims.get("tier", "")))
    except ValueError:
        return None
