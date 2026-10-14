# tests/unit/test_entitlement_issuer.py
# GTM-1 (#1839) — server-side Ed25519 tier-token ISSUER (the minting counterpart to
# #1800's verifier). TDD: the strongest contract is the WIRE ROUND-TRIP — a token minted
# by mint_tier_token must verify back to the exact Tier via crypto.verify_token, and every
# fail-closed property of the verifier (tamper / expiry / wrong key) must hold end-to-end.
#
# Brick 1: mint_tier_token(tier, issued_to, valid_days) claims + round-trip.
# Brick 2: private-key load from Secret Manager (mocked) + cloud-only K_SERVICE guard.
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import sys
import types
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
from core.entitlement import Tier
from core.entitlement import crypto as crypto_mod
from core.entitlement import issuer as issuer_mod


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def signing_key(monkeypatch):
    """Provision one throwaway Ed25519 keypair, wired as BOTH the issuer's signing key
    (via the lazy loader) and the ONLY trusted verification key, for a true round-trip.
    """
    priv = Ed25519PrivateKey.generate()
    # The issuer signs with this private key (bypass Secret Manager for the pure-mint tests).
    monkeypatch.setattr(issuer_mod, "_load_private_key", lambda: priv)
    # The verifier trusts only the matching public key.
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [priv.public_key()])
    return priv


# --------------------------------------------------------------------------- #
# Brick 1 — mint_tier_token: the wire round-trip (Gherkin: valid token verifies)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tier", [Tier.PRO, Tier.INSTITUTIONAL])
def test_minted_token_round_trips_to_tier(signing_key, tier):
    """Scenario 1 — a freshly minted token verifies back to the exact Tier."""
    token = issuer_mod.mint_tier_token(tier, issued_to="customer-42")
    assert crypto_mod.verify_token(token) is tier


def test_issued_to_is_hashed_never_raw(signing_key):
    """The raw licensee id must NEVER hit the wire — only its sha256 hex digest."""
    raw_id = "customer-42@example.com"
    token = issuer_mod.mint_tier_token(Tier.PRO, issued_to=raw_id)
    claims = _decode_claims(token)
    assert raw_id not in json.dumps(claims)
    assert claims["issued_to"] == hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


def test_nonce_is_unique_per_mint(signing_key):
    """Each mint gets a fresh random nonce (replay/dedup aid)."""
    a = _decode_claims(issuer_mod.mint_tier_token(Tier.PRO, issued_to="x"))
    b = _decode_claims(issuer_mod.mint_tier_token(Tier.PRO, issued_to="x"))
    assert a["nonce"] != b["nonce"]
    assert len(a["nonce"]) == 32  # secrets.token_hex(16) -> 32 hex chars


# (7 = explicit valid_days; 30 = the ENTITLEMENT_TOKEN_VALID_DAYS default)
@pytest.mark.parametrize("kwargs,want_days", [({"valid_days": 7}, 7), ({}, 30)])
def test_expires_at_matches_requested_lifetime(signing_key, kwargs, want_days):
    token = issuer_mod.mint_tier_token(Tier.PRO, issued_to="x", **kwargs)
    exp = datetime.fromisoformat(_decode_claims(token)["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta_days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400.0
    assert want_days - 0.1 < delta_days < want_days + 0.1


# --------------------------------------------------------------------------- #
# Brick 1 — fail-closed round-trips (Gherkin: tamper -> None, expired -> None)
# --------------------------------------------------------------------------- #
def test_tampered_minted_token_fails_verification(signing_key):
    """Scenario 2 — escalating the tier in a minted token without re-signing -> None."""
    token = issuer_mod.mint_tier_token(Tier.PRO, issued_to="x")
    obj = json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))
    inner = json.loads(base64.b64decode(obj["payload"]).decode("utf-8"))
    inner["tier"] = "INSTITUTIONAL"
    obj["payload"] = base64.b64encode(
        json.dumps(inner, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    tampered = base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")
    assert crypto_mod.verify_token(tampered) is None


def test_expired_minted_token_fails_verification(signing_key):
    """Scenario 3 — a token minted already-expired (valid_days=-1) -> None."""
    token = issuer_mod.mint_tier_token(Tier.PRO, issued_to="x", valid_days=-1)
    assert crypto_mod.verify_token(token) is None


def test_token_from_untrusted_key_fails_verification(monkeypatch):
    """A token minted by a key that is NOT in TRUSTED_PUBLIC_KEYS -> None (unknown issuer)."""
    attacker = Ed25519PrivateKey.generate()
    trusted = Ed25519PrivateKey.generate()
    monkeypatch.setattr(issuer_mod, "_load_private_key", lambda: attacker)
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [trusted.public_key()])
    token = issuer_mod.mint_tier_token(Tier.INSTITUTIONAL, issued_to="x")
    assert crypto_mod.verify_token(token) is None


# --------------------------------------------------------------------------- #
# Brick 2 — private-key load from Secret Manager (mocked) + cloud-only guard
# --------------------------------------------------------------------------- #
def _fake_secret_manager(monkeypatch, private_key: Ed25519PrivateKey):
    """Install a fake google.cloud.secretmanager whose client returns the base64 of
    ``private_key``'s 32 raw private bytes — exactly what the real secret stores. Never
    touches GCP."""
    raw_priv = private_key.private_bytes_raw()
    b64 = base64.b64encode(raw_priv).decode("ascii")

    class _FakeResponse:
        class payload:  # noqa: N801 — mirrors the SM response.payload.data shape
            data = b64.encode("utf-8")

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def access_secret_version(self, request):  # noqa: D401
            _FakeClient.last_request = request
            return _FakeResponse()

    fake_sm = types.SimpleNamespace(SecretManagerServiceClient=_FakeClient)
    fake_cloud = types.SimpleNamespace(secretmanager=fake_sm)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_sm)
    return _FakeClient


def test_guard_raises_without_k_service(monkeypatch):
    """The cloud-only guard must raise RuntimeError when K_SERVICE is absent (desktop)."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="cloud-only"):
        issuer_mod._load_private_key()


def test_mint_raises_without_k_service(monkeypatch):
    """mint_tier_token itself must be cloud-gated end-to-end (defence in depth)."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="cloud-only"):
        issuer_mod.mint_tier_token(Tier.PRO, issued_to="x")


def test_key_loads_from_secret_manager_when_cloud(monkeypatch):
    """With K_SERVICE set + a mocked secret, the private key loads and equals the original."""
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("K_SERVICE", "issuer-service")
    monkeypatch.setattr(config, "GCP_PROJECT_ID", "test-project")
    fake_client = _fake_secret_manager(monkeypatch, priv)

    loaded = issuer_mod._load_private_key()

    assert loaded.private_bytes_raw() == priv.private_bytes_raw()
    # It asked Secret Manager for the correct secret name + version.
    assert fake_client.last_request["name"] == (
        "projects/test-project/secrets/entitlement-signing-key/versions/latest"
    )


def test_mint_via_secret_manager_round_trips(monkeypatch):
    """End-to-end: K_SERVICE + mocked secret -> mint -> verify -> Tier (no crypto shortcut)."""
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("K_SERVICE", "issuer-service")
    monkeypatch.setattr(config, "GCP_PROJECT_ID", "test-project")
    _fake_secret_manager(monkeypatch, priv)
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [priv.public_key()])

    token = issuer_mod.mint_tier_token(Tier.PROFESSIONAL, issued_to="cust")
    assert crypto_mod.verify_token(token) is Tier.PROFESSIONAL


def test_import_issuer_never_touches_gcp():
    """Importing the module must not construct any SM client (key access is lazy)."""
    importlib.reload(issuer_mod)  # a bare reload must not raise / hit GCP


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _decode_claims(token: str) -> dict:
    obj = json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))
    return json.loads(base64.b64decode(obj["payload"]).decode("utf-8"))
