# tests/unit/test_entitlement_crypto.py
# GTM-1 (#1800) — Ed25519 token verification for the signed Tier-Entitlement layer.
#
# TDD Brick-1: verify_token must be fail-closed. It returns the encoded Tier only for a
# well-formed token whose detached Ed25519 signature verifies against one of the TRUSTED
# public keys AND whose expiry is in the future. Anything else (bad signature, tampered
# payload, expired, garbage, wrong key) -> None.
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entitlement import Tier
from core.entitlement import crypto as crypto_mod


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _mint(private_key: Ed25519PrivateKey, payload: dict) -> str:
    """Mint a token exactly the way the production wire-format expects it.

    Wire format is delegated to the module under test so the test can never drift from
    the implementation's canonicalisation.
    """
    return crypto_mod._encode_token(private_key, payload)  # noqa: SLF001


@pytest.fixture()
def test_key(monkeypatch):
    """Provision a throwaway Ed25519 keypair as the ONLY trusted key for the duration."""
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [priv.public_key()])
    return priv


def test_valid_pro_token_returns_pro(test_key):
    token = _mint(
        test_key,
        {
            "tier": "PRO",
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "abc123",
        },
    )
    assert crypto_mod.verify_token(token) is Tier.PRO


def test_valid_basic_token_returns_basic(test_key):
    token = _mint(
        test_key,
        {
            "tier": "BASIC",
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    assert crypto_mod.verify_token(token) is Tier.BASIC


def test_tampered_payload_returns_none(test_key):
    """Flipping a byte in the payload after signing must fail verification."""
    token = _mint(
        test_key,
        {
            "tier": "PRO",
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    obj = json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))
    # Escalate the tier in the payload without re-signing -> signature no longer matches.
    inner = json.loads(base64.b64decode(obj["payload"]).decode("utf-8"))
    inner["tier"] = "INSTITUTIONAL"
    obj["payload"] = base64.b64encode(
        json.dumps(inner, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    tampered = base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")
    assert crypto_mod.verify_token(tampered) is None


def test_expired_token_returns_none(test_key):
    token = _mint(
        test_key,
        {
            "tier": "PRO",
            "expires_at": _past_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    assert crypto_mod.verify_token(token) is None


def test_wrong_key_returns_none(monkeypatch):
    """A token signed by a key that is NOT in TRUSTED_PUBLIC_KEYS must fail."""
    attacker = Ed25519PrivateKey.generate()
    trusted = Ed25519PrivateKey.generate()
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [trusted.public_key()])
    token = _mint(
        attacker,
        {
            "tier": "INSTITUTIONAL",
            "expires_at": _future_iso(),
            "issued_to": "attacker",
            "nonce": "n",
        },
    )
    assert crypto_mod.verify_token(token) is None


def test_key_rotation_predecessor_still_verifies(monkeypatch):
    """verify_token must try EACH trusted key (current + predecessors) until one verifies."""
    old = Ed25519PrivateKey.generate()
    new = Ed25519PrivateKey.generate()
    # new is 'current', old is a still-trusted predecessor.
    monkeypatch.setattr(
        crypto_mod, "TRUSTED_PUBLIC_KEYS", [new.public_key(), old.public_key()]
    )
    token = _mint(
        old,
        {
            "tier": "PRO",
            "expires_at": _future_iso(),
            "issued_to": "rotated",
            "nonce": "n",
        },
    )
    assert crypto_mod.verify_token(token) is Tier.PRO


def test_garbage_returns_none(test_key):
    assert crypto_mod.verify_token("not-a-token") is None
    assert crypto_mod.verify_token("") is None
    assert crypto_mod.verify_token(base64.b64encode(b"{}").decode()) is None


def test_unknown_tier_string_returns_none(test_key):
    token = _mint(
        test_key,
        {
            "tier": "SUPER_ADMIN",
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    assert crypto_mod.verify_token(token) is None
