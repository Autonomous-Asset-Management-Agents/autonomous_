"""GTM-2 (#1809) T4: offline license-key activation.

``activate_license`` is the single offline entry point for the paid Private/Senior
path: a user pastes the signed key they got after checkout, and — iff it verifies
against a trusted Ed25519 key AND is unexpired — it is persisted as the local
``license.json`` that ``resolve_entitlement`` reads. It shares ONE verifier with
resolution (crypto.verify_token), so there is no second crypto implementation to
drift. Fail-closed: an invalid/expired/untrusted key, a non-LOCAL deployment, or a
missing user-data dir all return None and NEVER write a license file.

Mirrors the test_key + tmp_path pattern from test_beta_license_gen.py — fully
in-memory/offline: no network, no cloud, no login.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entitlement import Tier, activate_license
from core.entitlement import crypto as crypto_mod
from scripts.beta_license_gen import generate_beta_license


@pytest.fixture()
def signed_pro(monkeypatch):
    """Mint a valid PRO token and make its key the ONLY trusted verifier key."""
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [key.public_key()])
    return generate_beta_license(key).token


def _license_path(udd: str) -> Path:
    return Path(udd) / "license.json"


def test_valid_token_persists_and_returns_pro(signed_pro, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    tier = activate_license(signed_pro)

    assert tier is Tier.PRO
    written = json.loads(_license_path(str(tmp_path)).read_text(encoding="utf-8"))
    assert written == {"token": signed_pro}


def test_invalid_token_rejected_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    tier = activate_license("this-is-not-a-signed-token")

    assert tier is None
    assert not _license_path(str(tmp_path)).exists()


def test_non_local_refuses_even_valid_token(signed_pro, tmp_path, monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)  # cloud/dev, not a desktop
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    tier = activate_license(signed_pro)

    assert tier is None
    assert not _license_path(str(tmp_path)).exists()
