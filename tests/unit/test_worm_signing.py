"""C4 (#2369): Ed25519 WORM record-hash signing round-trip + fail-safe."""

from unittest.mock import patch

from core.round_table import worm_signing


def test_sign_verify_roundtrip():
    seed, pub = worm_signing.generate_keypair_b64()
    h = "a" * 64
    with patch.dict("os.environ", {"AAA_WORM_SIGNING_KEY": seed}):
        sig = worm_signing.sign_worm_hash(h)
    assert sig, "a provisioned key must produce a signature"
    assert worm_signing.verify_worm_hash(h, sig, pub) is True
    # a different hash must NOT verify under the same signature
    assert worm_signing.verify_worm_hash("b" * 64, sig, pub) is False
    # a different key must NOT verify
    _, other_pub = worm_signing.generate_keypair_b64()
    assert worm_signing.verify_worm_hash(h, sig, other_pub) is False


def test_no_key_returns_none_fail_safe():
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("AAA_WORM_SIGNING_KEY", None)
        assert worm_signing.sign_worm_hash("a" * 64) is None


def test_invalid_key_returns_none():
    with patch.dict("os.environ", {"AAA_WORM_SIGNING_KEY": "not-base64!!!"}):
        assert worm_signing.sign_worm_hash("a" * 64) is None
