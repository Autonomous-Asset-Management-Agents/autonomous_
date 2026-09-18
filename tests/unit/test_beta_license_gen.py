# tests/unit/test_beta_license_gen.py
# ADR-GTM-1b — Brick 1: scripts/beta_license_gen.py mints the ONE bundled, offline
# Senior (PRO) license shipped inside the desktop. This test closes the trust chain
# the exact way the desktop does it at runtime:
#
#   1. the token the script signs must round-trip through crypto.verify_token() to
#      Tier.PRO when the script's own public key is the only trusted key, and
#   2. the license.json body the script emits, dropped into a LOCAL AAA_USER_DATA_DIR,
#      must make resolve_entitlement() return PRO (fail-open to Senior for the beta).
#
# Everything is in-memory / tmp_path — NO network, NO cloud, NO login. Mirrors the
# test_key fixture pattern from test_entitlement_resolve.py.
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.entitlement import Tier
from core.entitlement import crypto as crypto_mod
from core.entitlement import resolve_entitlement
from scripts import beta_license_gen as gen


@pytest.fixture()
def fixed_key():
    """A throwaway Ed25519 private key; the only key the verifier trusts."""
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def trust_only(monkeypatch):
    """Monkeypatch TRUSTED_PUBLIC_KEYS to a single caller-supplied public key."""

    def _install(pub: Ed25519PublicKey) -> None:
        monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [pub])

    return _install


# --------------------------------------------------------------------------- #
# Claims shape
# --------------------------------------------------------------------------- #
def test_build_senior_claims_shape():
    claims = gen.build_senior_claims()
    assert claims["tier"] == Tier.PRO.value == "PRO"
    assert claims["issued_to"] == "beta-bundle"
    assert isinstance(claims["nonce"], str) and claims["nonce"]
    # expires_at is a future ISO-8601 datetime.
    exp = datetime.fromisoformat(claims["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc)


def test_build_senior_claims_nonce_is_unique():
    assert gen.build_senior_claims()["nonce"] != gen.build_senior_claims()["nonce"]


# --------------------------------------------------------------------------- #
# Trust chain: minted token round-trips to PRO
# --------------------------------------------------------------------------- #
def test_generate_result_token_verifies_to_pro(fixed_key, trust_only):
    result = gen.generate_beta_license(fixed_key)
    trust_only(Ed25519PublicKey.from_public_bytes(base64.b64decode(result.public_b64)))
    assert crypto_mod.verify_token(result.token) is Tier.PRO


def test_public_b64_is_the_signing_keys_public_half(fixed_key):
    result = gen.generate_beta_license(fixed_key)
    expected = base64.b64encode(fixed_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    assert result.public_b64 == expected


def test_private_b64_reconstructs_the_signing_key(fixed_key):
    result = gen.generate_beta_license(fixed_key)
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(result.private_b64))
    # Same public half → same key.
    assert (
        priv.public_key().public_bytes_raw()
        == fixed_key.public_key().public_bytes_raw()
    )


def test_generate_without_key_creates_a_fresh_pair(trust_only):
    result = gen.generate_beta_license()  # no key -> generate one
    trust_only(Ed25519PublicKey.from_public_bytes(base64.b64decode(result.public_b64)))
    assert crypto_mod.verify_token(result.token) is Tier.PRO


# --------------------------------------------------------------------------- #
# license.json body drives resolve_entitlement() -> PRO in a LOCAL desktop
# --------------------------------------------------------------------------- #
def test_license_json_resolves_pro_in_local_dir(
    fixed_key, trust_only, monkeypatch, tmp_path
):
    result = gen.generate_beta_license(fixed_key)
    trust_only(Ed25519PublicKey.from_public_bytes(base64.b64decode(result.public_b64)))

    # license_json is exactly the bundled resource body: {"token": "<encoded>"}.
    assert set(result.license_json.keys()) == {"token"}
    assert result.license_json["token"] == result.token

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "license.json").write_text(
        json.dumps(result.license_json), encoding="utf-8"
    )

    ent = resolve_entitlement()
    assert ent.tier is Tier.PRO
    assert ent.allow_live is True


def test_no_license_in_local_dir_is_basic(monkeypatch, tmp_path):
    # Control: without the bundled license, a LOCAL desktop fails closed to BASIC.
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC
    assert ent.allow_live is False


# --------------------------------------------------------------------------- #
# main() operator smoke — emits token, license.json, public + private keys
# --------------------------------------------------------------------------- #
def test_main_prints_all_four_artifacts(capsys):
    gen.main([])  # explicit empty argv — don't inherit pytest's sys.argv
    out = capsys.readouterr().out
    assert "AAA_ENTITLEMENT_PUBKEYS" in out  # public-key wiring instruction
    assert "beta_senior_license.json" in out  # where to paste the token
    assert "DO NOT COMMIT" in out  # private-key warning
    assert '"token"' in out  # the license.json body
