# tests/unit/test_entitlement_keygen.py
# GTM-1 (#1839) — Brick 4: scripts/entitlement_keygen.py generates the Ed25519 keypair
# that anchors the whole entitlement trust chain. This test closes that chain end-to-end:
# the PUBLIC key base64 the script emits, fed through Ed25519PublicKey.from_public_bytes,
# must verify a token signed by the matching PRIVATE key the script emits.
from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from scripts import entitlement_keygen as keygen


def test_generate_keypair_b64_round_trips():
    """The emitted (private_b64, public_b64) must be a matching, usable Ed25519 pair.

    Reconstruct the private key from its base64, sign a message, and verify with the
    public key reconstructed from ITS base64 — proving the trust chain closes.
    """
    private_b64, public_b64 = keygen.generate_keypair_b64()

    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))

    message = b"entitlement-trust-chain"
    pub.verify(priv.sign(message), message)  # raises InvalidSignature on mismatch


def test_public_b64_is_32_raw_bytes():
    """The public key format must be base64 of the 32 raw bytes — the exact format
    crypto._load_env_pubkeys()/AAA_ENTITLEMENT_PUBKEYS and TRUSTED_PUBLIC_KEYS expect.
    """
    _private_b64, public_b64 = keygen.generate_keypair_b64()
    raw = base64.b64decode(public_b64)
    assert len(raw) == 32
    # And it is loadable exactly the way the verifier loads env / baked-in keys.
    Ed25519PublicKey.from_public_bytes(raw)


def test_emitted_pubkey_verifies_a_minted_token(monkeypatch):
    """The emitted public key, loaded like the verifier does, trusts a token minted by
    the emitted private key — the full issuer trust chain, end to end."""
    from core.entitlement import Tier
    from core.entitlement import crypto as crypto_mod
    from core.entitlement import issuer as issuer_mod

    private_b64, public_b64 = keygen.generate_keypair_b64()
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))

    # Verifier trusts ONLY the emitted public key (loaded the env/baked-in way).
    trusted_pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [trusted_pub])
    # Issuer signs with the emitted private key.
    monkeypatch.setattr(issuer_mod, "_load_private_key", lambda: priv)

    token = issuer_mod.mint_tier_token(Tier.PRO, issued_to="keygen-e2e")
    assert crypto_mod.verify_token(token) is Tier.PRO


def test_main_prints_both_keys(capsys):
    """Running the script prints both keys + the Secret Manager storage instruction,
    and never crashes (operator-facing smoke)."""
    keygen.main()
    out = capsys.readouterr().out
    assert "entitlement-signing-key" in out  # storage instruction present
    assert "AAA_ENTITLEMENT_PUBKEYS" in out  # public-key wiring instruction present
