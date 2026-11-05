"""Unit test for scripts/build_mac_boot_manifest.py (#2747/S8)."""

import hashlib
import importlib.util
import os

# Load the script by path (lives in repo-root scripts/).
_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SPEC = importlib.util.spec_from_file_location(
    "build_mac_boot_manifest",
    os.path.join(_ROOT, "scripts", "build_mac_boot_manifest.py"),
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]


def test_manifest_records_sha256_size_and_basename(tmp_path):
    dmg = tmp_path / "AAAgents-arm64.dmg"
    payload = b"fake-dmg-bytes" * 100
    dmg.write_bytes(payload)

    manifest = _MOD.build_manifest(
        "0.4.0", [str(dmg)], generated_at="2026-08-10T00:00:00Z"
    )

    assert manifest["version"] == "0.4.0"
    assert manifest["platform"] == "darwin-arm64"
    assert manifest["schema_version"] == 1
    assert len(manifest["assets"]) == 1
    asset = manifest["assets"][0]
    assert asset["file"] == "AAAgents-arm64.dmg"  # basename only
    assert asset["bytes"] == len(payload)
    assert asset["sha256"] == hashlib.sha256(payload).hexdigest()


def test_manifest_hashes_multiple_assets(tmp_path):
    a = tmp_path / "AAAgents-arm64.zip"
    b = tmp_path / "engine-src-0.4.0.tar.gz"
    a.write_bytes(b"zip")
    b.write_bytes(b"tar")
    manifest = _MOD.build_manifest("0.4.0", [str(a), str(b)])
    files = [x["file"] for x in manifest["assets"]]
    assert files == ["AAAgents-arm64.zip", "engine-src-0.4.0.tar.gz"]
