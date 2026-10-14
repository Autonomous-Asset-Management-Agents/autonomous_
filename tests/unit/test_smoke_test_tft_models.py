# scripts/smoke_test_tft_models — TFT provisioning boot-verify (model-provenance Issue 3)
# Bounded, deterministic sample of per-file SHA-256 vs the manifest. Dormant + non-blocking
# by default (no manifest → ok; mismatch → ok unless strict). The per-load gate (#1142) is
# the real enforcement; this is a boot-time provisioning-integrity diagnostic.

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import smoke_test_tft_models as st  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(tmp: Path, symbols=("AAPL", "MSFT")):
    entries = []
    for sym in symbols:
        d = tmp / sym
        d.mkdir(parents=True)
        for name, data in (
            ("checkpoint.pt", f"{sym}-ck".encode()),
            ("training_ds.pkl", f"{sym}-ds".encode()),
        ):
            (d / name).write_bytes(data)
            entries.append(
                {
                    "symbol": sym,
                    "filename": f"{sym}/{name}",
                    "sha256": _sha(d / name),
                    "size_bytes": len(data),
                }
            )
    return entries


def _write_manifest(tmp: Path, entries):
    mp = tmp / "tft_models_manifest.json"
    mp.write_text(json.dumps({"models": entries, "incomplete": []}), encoding="utf-8")
    return mp


def test_no_manifest_is_dormant_ok(tmp_path):
    ok, report = st.verify_tft_provisioning(tmp_path, tmp_path / "absent.json")
    assert ok is True
    assert report["status"] == "no-manifest-dormant"
    assert report["checked"] == 0


def test_matching_tree_ok(tmp_path):
    entries = _tree(tmp_path)
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp)
    assert ok is True
    assert report["status"] == "ok"
    assert report["checked"] == len(entries)
    assert report["manifest_entries"] == len(entries)


def test_tampered_file_reported_nonblocking_by_default(tmp_path):
    entries = _tree(tmp_path)
    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(b"TAMPERED")  # SHA now differs
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp)
    assert ok is True  # non-blocking by default — the per-load gate enforces
    assert report["status"] == "MISMATCH"
    assert any("AAPL/checkpoint.pt" in m for m in report["mismatches"])


def test_tampered_file_strict_fails(tmp_path):
    entries = _tree(tmp_path)
    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(b"TAMPERED")
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp, strict=True)
    assert ok is False
    assert report["status"] == "MISMATCH"


def test_missing_file_reported(tmp_path):
    entries = _tree(tmp_path)
    (tmp_path / "MSFT" / "training_ds.pkl").unlink()
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp, strict=True)
    assert ok is False
    assert "MSFT/training_ds.pkl" in report["missing"]


def test_path_traversal_entry_flagged(tmp_path):
    entries = _tree(tmp_path)
    entries.append({"symbol": "X", "filename": "../../etc/passwd", "sha256": "00" * 32})
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp, sample_size=100, strict=True)
    assert ok is False
    assert any("escapes root" in m for m in report["mismatches"])


def test_sample_is_bounded(tmp_path):
    # 50 symbols → 100 entries; a sample of 5 must check at most 5.
    entries = _tree(tmp_path, symbols=tuple(f"S{i:02d}" for i in range(50)))
    mp = _write_manifest(tmp_path, entries)
    ok, report = st.verify_tft_provisioning(tmp_path, mp, sample_size=5)
    assert ok is True
    assert report["checked"] <= 5
    assert report["manifest_entries"] == 100


def test_bounded_sample_deterministic():
    entries = [{"filename": f"{i:03d}/checkpoint.pt"} for i in range(100)]
    a = st._bounded_sample(entries, 7)
    b = st._bounded_sample(entries, 7)
    assert a == b
    assert len(a) == 7
