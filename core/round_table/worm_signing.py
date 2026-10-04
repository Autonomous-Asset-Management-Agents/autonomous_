"""C4 (#2369): Ed25519 detached signatures over WORM ``live_enablement`` record hashes.

A local party who can write the audit directory could forge an ``enable`` by recomputing the
plain SHA-256 chain (DD finding M4). Signing the record's ``hash`` hex string with an engine-held
Ed25519 key (private seed in the OS keychain / ``AAA_WORM_SIGNING_KEY`` env, NEVER handed to the
Electron shell) means a raw file append no longer verifies — the shell's ``verifyAuditChain``
requires a signature valid under the matching public key (``AAA_WORM_PUBKEYS``). Signing the
already-computed ``hash`` string (not a re-serialised payload) avoids any Python<->JS canonical-form
byte-parity risk: the shell already recomputes the hash for its integrity check.

Fail-safe (CLAUDE.md §5.6): with no key provisioned the record is written UNSIGNED and the shell
fails CLOSED to PAPER — never to unverifiable-live.
"""

import base64
import logging
import os
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)


def _load_signing_key() -> Optional[Ed25519PrivateKey]:
    raw = os.getenv("AAA_WORM_SIGNING_KEY", "").strip()
    if not raw:
        return None
    try:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001 — a bad key must never crash the write path
        logger.warning(
            "[WORM] invalid AAA_WORM_SIGNING_KEY — records will be UNSIGNED: %s", exc
        )
        return None


def sign_worm_hash(hash_hex: str) -> Optional[str]:
    """Detached Ed25519 signature (base64) over the record's ``hash`` hex string.

    Returns ``None`` (and WARNs) when no key is provisioned — the record is written unsigned and
    the desktop shell fails closed to PAPER.
    """
    key = _load_signing_key()
    if key is None:
        logger.warning(
            "[WORM] no signing key (AAA_WORM_SIGNING_KEY unset) — live_enablement record written "
            "UNSIGNED; the desktop shell will fail closed to PAPER."
        )
        return None
    try:
        return base64.b64encode(key.sign(hash_hex.encode("utf-8"))).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WORM] signing failed — record UNSIGNED: %s", exc)
        return None


def verify_worm_hash(hash_hex: str, sig_b64: str, pub_b64: str) -> bool:
    """Verify a detached signature over ``hash_hex`` under a base64 raw Ed25519 public key.
    Python-side mirror of the shell verifier (used in tests / any Python consumer)."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(sig_b64), hash_hex.encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001
        return False


def generate_keypair_b64():
    """Provisioning helper → (private_seed_b64, public_b64), raw Ed25519 (32 bytes each)."""
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(seed).decode("ascii"), base64.b64encode(pub).decode("ascii")
