#!/usr/bin/env python3
"""entitlement_keygen.py — GTM-1 (#1839) Ed25519 keypair generator for tier tokens.

Generates the signing keypair that anchors the entitlement trust chain:

  * The PRIVATE key is stored in GCP Secret Manager as ``entitlement-signing-key`` and
    loaded ONLY by the cloud issuer (core/entitlement/issuer.py). It NEVER touches a
    desktop and must NEVER be committed.
  * The PUBLIC key is baked into crypto.TRUSTED_PUBLIC_KEYS and/or supplied via the
    ``AAA_ENTITLEMENT_PUBKEYS`` env var so the desktop verifier (crypto.verify_token)
    trusts tokens the issuer mints.

Both keys are emitted as base64 of their 32 RAW bytes — the exact format
crypto._load_env_pubkeys() and Ed25519{Private,Public}Key.from_*_bytes expect.

Operator, run at key-provisioning / rotation time only:

    python -m scripts.entitlement_keygen

Then follow the printed instructions. This is an operator tool, not called at runtime.
"""

from __future__ import annotations

import base64
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_keypair_b64() -> Tuple[str, str]:
    """Generate a fresh Ed25519 keypair.

    Returns:
        (private_b64, public_b64): base64 of the 32 raw private bytes and the 32 raw
        public bytes, respectively. The public value is directly usable as an
        AAA_ENTITLEMENT_PUBKEYS entry and for baking into TRUSTED_PUBLIC_KEYS.
    """
    priv = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(priv.private_bytes_raw()).decode("ascii")
    public_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    return private_b64, public_b64


def main() -> None:
    """Emit the keypair to stdout with operator instructions.

    NOTE: printing the PRIVATE key to stdout is intentional and the ONLY place it is
    ever emitted — the operator captures it here to store in Secret Manager. It must
    never be committed, logged elsewhere, or shipped to a desktop.
    """
    private_b64, public_b64 = generate_keypair_b64()

    print("=" * 78)
    print("GTM-1 (#1839) — Ed25519 entitlement signing keypair")
    print("=" * 78)
    print()
    print("PRIVATE KEY (base64 of 32 raw bytes) — SECRET, DO NOT COMMIT:")
    print(f"  {private_b64}")
    print()
    print("  Store it in GCP Secret Manager as 'entitlement-signing-key', e.g.:")
    print(
        "    printf '%s' '<PRIVATE_KEY_B64>' | \\\n"
        "      gcloud secrets create entitlement-signing-key --data-file=-"
    )
    print(
        "    # (or `gcloud secrets versions add entitlement-signing-key "
        "--data-file=-` to rotate)"
    )
    print()
    print("PUBLIC KEY (base64 of 32 raw bytes) — safe to distribute:")
    print(f"  {public_b64}")
    print()
    print("  Trust it in the desktop verifier either by:")
    print("    * setting  AAA_ENTITLEMENT_PUBKEYS=<PUBLIC_KEY_B64>  (comma-sep for")
    print("      rotation, current key first), or")
    print(
        "    * baking it into crypto.TRUSTED_PUBLIC_KEYS via "
        "Ed25519PublicKey.from_public_bytes(base64.b64decode(...))."
    )
    print("=" * 78)


if __name__ == "__main__":
    main()
