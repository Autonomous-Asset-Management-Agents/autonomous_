"""#2914 (Epic #2912) Inc 2 — seal verification + OpenTimestamps anchoring wrapper.

`verify_seal` re-derives the Merkle root + flat anchor from a seal's manifest (NO file access —
so a sealed/undisclosed tree still verifies) and checks the signature. The OTS wrapper shells out
to the free `ots` client through an INJECTABLE runner, so unit tests need neither the binary nor
the network; the real stamp/verify is an operational step (see VERIFY.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


class _FakeRunner:
    """Records commands; returns a canned (returncode, stdout, stderr)."""

    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.calls = []
        self._rc, self._out, self._err, self._raises = (
            returncode,
            stdout,
            stderr,
            raises,
        )

    def __call__(self, cmd):
        if self._raises:
            raise self._raises
        self.calls.append(list(cmd))
        return type(
            "R", (), {"returncode": self._rc, "stdout": self._out, "stderr": self._err}
        )()


def test_verify_seal_ok_without_signature(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    rep = ps.verify_seal(seal)
    assert rep["root_ok"] is True
    assert rep["flat_ok"] is True
    assert rep["signature_ok"] is None  # no pubkey / no signature supplied
    assert rep["ok"] is True


def test_verify_seal_detects_wrong_root(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    seal["merkle_root"] = "0" * 64
    rep = ps.verify_seal(seal)
    assert rep["root_ok"] is False and rep["ok"] is False


def test_verify_seal_detects_tampered_manifest(tmp_path):
    ps = _ps()
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    seal["manifest"][0]["sha256"] = (
        "f" * 64
    )  # forged file hash → recomputed root diverges
    rep = ps.verify_seal(seal)
    assert rep["root_ok"] is False


def test_verify_seal_signature_roundtrip(tmp_path, monkeypatch):
    ps = _ps()
    from core.round_table.worm_signing import generate_keypair_b64

    priv_b64, pub_b64 = generate_keypair_b64()
    monkeypatch.setenv("AAA_WORM_SIGNING_KEY", priv_b64)
    _tree(tmp_path)
    seal = ps.seal_tree(tmp_path)
    seal["signature"] = ps.sign_root(seal["merkle_root"])
    assert ps.verify_seal(seal, pub_b64)["signature_ok"] is True
    _, other_pub = generate_keypair_b64()
    assert ps.verify_seal(seal, other_pub)["signature_ok"] is False


def test_ots_stamp_invokes_client_and_writes_root(tmp_path):
    ps = _ps()
    runner = _FakeRunner(returncode=0)
    root = "a" * 64
    proof = ps.ots_stamp(root, tmp_path, runner=runner)
    # the root was written to a file and `ots stamp <file>` was invoked
    root_file = tmp_path / "merkle_root.txt"
    assert root_file.read_text(encoding="utf-8").strip() == root
    assert runner.calls and runner.calls[0][:2] == ["ots", "stamp"]
    assert runner.calls[0][-1].endswith("merkle_root.txt")
    assert proof.name == "merkle_root.txt.ots"


def test_ots_stamp_raises_on_failure(tmp_path):
    ps = _ps()
    runner = _FakeRunner(returncode=1, stderr="calendar unreachable")
    with pytest.raises(RuntimeError):
        ps.ots_stamp("b" * 64, tmp_path, runner=runner)


def test_ots_available_false_when_client_missing():
    ps = _ps()
    runner = _FakeRunner(raises=FileNotFoundError())
    assert ps.ots_available(runner=runner) is False


def test_ots_available_true_when_present():
    ps = _ps()
    runner = _FakeRunner(returncode=0, stdout="Opentimestamps 0.7.0")
    assert ps.ots_available(runner=runner) is True
