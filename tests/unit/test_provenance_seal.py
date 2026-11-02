"""#2913 (Epic #2912) Inc 1 — provenance-seal core: deterministic Merkle manifest + sign/verify.

The tool seals a clean checkout into a signed, byte-stable Merkle manifest so a later timestamp
anchors the WHOLE tree. Properties under test (the plan's Gherkin, §5): determinism, no-file-content
in the manifest, selective disclosure via Merkle proofs, tamper detection, and Ed25519 signature
round-trip against the existing WORM key (`core/round_table/worm_signing.py`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ps():
    from scripts import provenance_seal

    return provenance_seal


def _tree(base: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


_FILES = {
    "a.py": b"print('a')\n",
    "pkg/b.py": b"x = 1\n",
    "pkg/sub/c.txt": b"hello\n",
    "README.md": b"# demo\n",
}


def test_root_is_deterministic_and_hex(tmp_path):
    ps = _ps()
    _tree(tmp_path, _FILES)
    m1 = ps.build_manifest(tmp_path)
    m2 = ps.build_manifest(tmp_path)
    assert m1 == m2  # stable ordering + hashes
    r1 = ps.merkle_root(ps.manifest_leaves(m1))
    r2 = ps.merkle_root(ps.manifest_leaves(m2))
    assert r1 == r2
    assert len(r1) == 64 and int(r1, 16) >= 0  # 64-hex sha256


def test_manifest_carries_no_file_content(tmp_path):
    ps = _ps()
    secret = b"SUPER-SECRET-HEADLESS-SOURCE-LINE-42"
    _tree(tmp_path, {**_FILES, "engine/secret.py": secret})
    manifest = ps.build_manifest(tmp_path)
    blob = json.dumps(manifest)
    assert secret.decode() not in blob  # only hashes, never bodies
    for entry in manifest:
        assert set(entry.keys()) == {"path", "sha256"}
        assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) >= 0


def test_selective_disclosure_proof(tmp_path):
    ps = _ps()
    _tree(tmp_path, _FILES)
    manifest = ps.build_manifest(tmp_path)
    leaves = ps.manifest_leaves(manifest)
    root = ps.merkle_root(leaves)
    idx = 2
    proof = ps.merkle_proof(leaves, idx)
    # the disclosed leaf verifies against the root WITHOUT revealing the other files
    assert ps.verify_proof(leaves[idx], proof, root) is True
    # a different leaf under the same proof must NOT verify
    assert ps.verify_proof(leaves[(idx + 1) % len(leaves)], proof, root) is False


def test_tamper_changes_root_and_breaks_old_proof(tmp_path):
    ps = _ps()
    _tree(tmp_path, _FILES)
    m0 = ps.build_manifest(tmp_path)
    leaves0 = ps.manifest_leaves(m0)
    root0 = ps.merkle_root(leaves0)
    proof0 = ps.merkle_proof(leaves0, 0)
    assert ps.verify_proof(leaves0[0], proof0, root0) is True
    # flip one byte in a DIFFERENT file
    (tmp_path / "pkg/b.py").write_bytes(b"x = 2\n")
    root1 = ps.merkle_root(ps.manifest_leaves(ps.build_manifest(tmp_path)))
    assert root1 != root0  # tamper detected at the root
    assert (
        ps.verify_proof(leaves0[0], proof0, root1) is False
    )  # stale proof no longer valid


def test_root_binds_path_not_only_content(tmp_path, tmp_path_factory):
    ps = _ps()
    a = tmp_path / "a"
    b = tmp_path / "b"
    _tree(a, {"x/one.py": b"same\n"})
    _tree(b, {"y/one.py": b"same\n"})  # identical content, different path
    ra = ps.merkle_root(ps.manifest_leaves(ps.build_manifest(a)))
    rb = ps.merkle_root(ps.manifest_leaves(ps.build_manifest(b)))
    assert ra != rb  # a moved/renamed file changes the root


def test_single_file_root_equals_its_leaf(tmp_path):
    ps = _ps()
    _tree(tmp_path, {"only.py": b"1\n"})
    leaves = ps.manifest_leaves(ps.build_manifest(tmp_path))
    assert len(leaves) == 1
    assert ps.merkle_root(leaves) == leaves[0]


def test_excludes_vcs_and_build_artifacts(tmp_path):
    ps = _ps()
    _tree(
        tmp_path,
        {
            "a.py": b"a\n",
            ".git/config": b"[core]\n",
            "__pycache__/a.cpython-314.pyc": b"\x00\x00",
            "node_modules/dep/index.js": b"//x\n",
        },
    )
    paths = {e["path"] for e in ps.build_manifest(tmp_path)}
    assert paths == {
        "a.py"
    }  # VCS/build/node artefacts are not part of the provenance tree


def test_flat_anchor_is_stable_cross_check(tmp_path):
    ps = _ps()
    _tree(tmp_path, _FILES)
    m = ps.build_manifest(tmp_path)
    assert ps.flat_anchor(m) == ps.flat_anchor(ps.build_manifest(tmp_path))
    assert len(ps.flat_anchor(m)) == 64


def test_sign_and_verify_root_roundtrip(tmp_path, monkeypatch):
    ps = _ps()
    from core.round_table.worm_signing import generate_keypair_b64, verify_worm_hash

    priv_b64, pub_b64 = generate_keypair_b64()
    monkeypatch.setenv("AAA_WORM_SIGNING_KEY", priv_b64)
    _tree(tmp_path, _FILES)
    root = ps.merkle_root(ps.manifest_leaves(ps.build_manifest(tmp_path)))
    sig = ps.sign_root(root)
    assert isinstance(sig, str) and sig
    assert verify_worm_hash(root, sig, pub_b64) is True
    assert verify_worm_hash("0" * 64, sig, pub_b64) is False  # wrong root


def test_sign_root_is_none_without_key(tmp_path, monkeypatch):
    ps = _ps()
    monkeypatch.delenv("AAA_WORM_SIGNING_KEY", raising=False)
    _tree(tmp_path, _FILES)
    root = ps.merkle_root(ps.manifest_leaves(ps.build_manifest(tmp_path)))
    assert ps.sign_root(root) is None  # unsigned when no key provisioned (fail-safe)
