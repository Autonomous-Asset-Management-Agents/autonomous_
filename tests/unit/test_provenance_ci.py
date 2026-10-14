"""#2917 (Epic #2912) Inc 5 / CI — reproduce a seal against an actual tree.

`verify_seal` (Inc 2) checks a seal's INTERNAL consistency (manifest → root) without touching the
filesystem — good for a sealed/undisclosed tree. The reproducibility CI needs the opposite: re-hash
the ACTUAL files at a commit and confirm they still produce the committed manifest + root, so any
post-hoc tampering with the tree or the stored seal is caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ps():
    from scripts import provenance_seal

    return provenance_seal


def _tree(base: Path) -> None:
    (base / "pkg").mkdir(parents=True, exist_ok=True)
    (base / "a.py").write_bytes(b"a\n")
    (base / "pkg" / "b.py").write_bytes(b"b\n")


def test_verify_against_tree_ok(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    rep = ps.verify_against_tree(seal, tmp_path)
    assert rep == {"manifest_matches": True, "root_ok": True, "ok": True}


def test_verify_against_tree_detects_modified_file(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    (tmp_path / "a.py").write_bytes(b"TAMPERED\n")
    rep = ps.verify_against_tree(seal, tmp_path)
    assert (
        rep["manifest_matches"] is False
        and rep["root_ok"] is False
        and rep["ok"] is False
    )


def test_verify_against_tree_detects_added_file(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    (tmp_path / "pkg" / "c.py").write_bytes(b"c\n")  # extra file not in the seal
    rep = ps.verify_against_tree(seal, tmp_path)
    assert rep["ok"] is False


def test_verify_against_tree_detects_forged_stored_root(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    seal["merkle_root"] = "0" * 64  # tree is fine, but the stored seal was forged
    rep = ps.verify_against_tree(seal, tmp_path)
    assert (
        rep["manifest_matches"] is True
        and rep["root_ok"] is False
        and rep["ok"] is False
    )
