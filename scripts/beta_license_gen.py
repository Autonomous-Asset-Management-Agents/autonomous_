#!/usr/bin/env python3
"""beta_license_gen.py — ADR-GTM-1b offline generator for the BUNDLED Senior license.

Decentralized free-beta Senior unlock: instead of a cloud call + login, the desktop
ships ONE Ed25519-signed Senior (PRO) license baked into its resources. The engine's
existing offline verifier (core/entitlement/crypto.verify_token / resolve_entitlement)
trusts it via AAA_ENTITLEMENT_PUBKEYS — no network, no login, no central issuer.

This is an OFFLINE dev/ops tool (NOT the cloud-guarded issuer.mint_tier_token). Ops run
it ONCE at provisioning:

    python -m scripts.beta_license_gen

It (a) mints a long-lived Senior token signed by a fresh (or supplied) Ed25519 key and
(b) prints the four artifacts an operator needs:

    1. the signed TOKEN,
    2. the license.json body  {"token": "<token>"}  to paste into the bundled resource
       desktop/resources/beta_senior_license.json,
    3. the base64 PUBLIC key  for  AAA_ENTITLEMENT_PUBKEYS  in the desktop build env, and
    4. the base64 PRIVATE key  for secure OFFLINE storage (rotation) — DO NOT COMMIT.

Nothing secret is committed: the repo ships only a PLACEHOLDER resource; the operator
pastes the real token from this script's output and sets the public key in the build env.

Supply an existing signing key (to keep one stable public key across rebuilds) via the
--private-key CLI arg or the AAA_BETA_SIGNING_KEY env var (base64 of the 32 raw bytes).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entitlement import crypto as crypto_mod
from core.entitlement.tier import Tier

# One year of validity for the bundled beta license (ops re-mint before it lapses).
DEFAULT_VALID_DAYS = 365
# The licensee id embedded in every bundled beta token — a fixed, non-PII label.
BETA_ISSUED_TO = "beta-bundle"
# Where the operator pastes the token (documented in the printed instructions).
BUNDLED_RESOURCE = "desktop/resources/beta_senior_license.json"
# Optional pre-existing signing key so rebuilds keep a stable public key.
SIGNING_KEY_ENV = "AAA_BETA_SIGNING_KEY"


@dataclass(frozen=True)
class BetaLicense:
    """The full set of artifacts for one bundled Senior license.

    Attributes:
        token:        the signed wire token (base64 envelope) — verify_token -> PRO.
        license_json: the exact bundled-resource body, ``{"token": <token>}``.
        public_b64:   base64 of the 32 raw Ed25519 public bytes (AAA_ENTITLEMENT_PUBKEYS).
        private_b64:  base64 of the 32 raw Ed25519 private bytes (SECRET — offline only).
        claims:       the signed claims dict (for observability / debugging).
    """

    token: str
    license_json: dict
    public_b64: str
    private_b64: str
    claims: dict


def build_senior_claims(
    issued_to: str = BETA_ISSUED_TO, valid_days: int = DEFAULT_VALID_DAYS
) -> dict:
    """Build the Senior (PRO) claims dict in the canonical GTM-1 schema.

    Shape: ``{tier, expires_at, issued_to, nonce}`` (see crypto.py wire format).
    ``tier`` is the canonical ``Tier.PRO.value`` string; ``expires_at`` is an ISO-8601
    UTC datetime ``now + valid_days``; ``nonce`` is a fresh random hex string.
    """
    expires_at = (datetime.now(timezone.utc) + timedelta(days=valid_days)).isoformat()
    return {
        "tier": Tier.PRO.value,
        "expires_at": expires_at,
        "issued_to": issued_to,
        "nonce": secrets.token_hex(16),
    }


def generate_beta_license(
    private_key: Optional[Ed25519PrivateKey] = None,
    *,
    issued_to: str = BETA_ISSUED_TO,
    valid_days: int = DEFAULT_VALID_DAYS,
) -> BetaLicense:
    """Mint one bundled Senior license, signed by ``private_key`` (or a fresh key).

    Uses the SAME canonical signer the verifier shares (``crypto._encode_token``) so the
    token always round-trips through ``verify_token`` when its public key is trusted.
    """
    if private_key is None:
        private_key = Ed25519PrivateKey.generate()

    claims = build_senior_claims(issued_to=issued_to, valid_days=valid_days)
    token = crypto_mod._encode_token(private_key, claims)  # shared canonical signer

    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")

    return BetaLicense(
        token=token,
        license_json={"token": token},
        public_b64=public_b64,
        private_b64=private_b64,
        claims=claims,
    )


def _load_signing_key(source: Optional[str]) -> Optional[Ed25519PrivateKey]:
    """Reconstruct an Ed25519 private key from base64 (CLI arg > env), or None."""
    raw_b64 = (source or os.getenv(SIGNING_KEY_ENV, "")).strip()
    if not raw_b64:
        return None
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw_b64))


def main(argv: Optional[list] = None) -> None:
    """Emit the four artifacts + operator instructions to stdout.

    Printing the PRIVATE key is intentional and the ONLY place it is emitted — the
    operator captures it here for secure OFFLINE storage (rotation). It must never be
    committed, logged elsewhere, or shipped inside the desktop.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private-key",
        default=None,
        help=(
            "base64 of the 32 raw Ed25519 private bytes to reuse (keeps a stable public "
            f"key across rebuilds). Falls back to ${SIGNING_KEY_ENV}; else a fresh key."
        ),
    )
    parser.add_argument(
        "--valid-days",
        type=int,
        default=DEFAULT_VALID_DAYS,
        help=f"license validity in days (default {DEFAULT_VALID_DAYS}).",
    )
    args = parser.parse_args(argv)

    license_ = generate_beta_license(
        _load_signing_key(args.private_key), valid_days=args.valid_days
    )

    bar = "=" * 78
    print(bar)
    print("ADR-GTM-1b — bundled offline Senior (PRO) license")
    print(bar)
    print()
    print(
        f"Signed for {license_.claims['issued_to']!r}, "
        f"expires_at {license_.claims['expires_at']}."
    )
    print()
    print("1. SIGNED TOKEN:")
    print(f"  {license_.token}")
    print()
    print(f"2. license.json body — paste into  {BUNDLED_RESOURCE}:")
    print(f"  {json.dumps(license_.license_json)}")
    print()
    print("3. PUBLIC KEY (base64, 32 raw bytes) — set in the desktop BUILD env:")
    print(f"     AAA_ENTITLEMENT_PUBKEYS={license_.public_b64}")
    print("   (comma-separate for rotation, current key first.)")
    print()
    print("4. PRIVATE KEY (base64, 32 raw bytes) — SECRET, DO NOT COMMIT:")
    print(f"  {license_.private_b64}")
    print("   Store OFFLINE (e.g. a password manager). Reuse via --private-key /")
    print(f"   ${SIGNING_KEY_ENV} to keep one stable public key across rebuilds.")
    print(bar)


if __name__ == "__main__":
    main()
