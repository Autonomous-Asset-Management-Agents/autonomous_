"""Tests for scripts/check_doc_anchors.py — the doc anchor linter.

Gates against code reference anchor drift.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Path to repo root: tests/unit -> repo root
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCRIPT = os.path.join(REPO, "scripts", "check_doc_anchors.py")


def _run(md_text):
    fd, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        return subprocess.run(
            [sys.executable, SCRIPT, path],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
    finally:
        os.unlink(path)


def test_out_of_range_anchor_fails():
    r = _run("- `core/compliance.py#L99999`\n")
    assert r.returncode == 1, r.stdout
    assert "out of range" in r.stdout


def test_missing_file_anchor_fails():
    r = _run("- ghost `core/does_not_exist_xyz.py#L10`\n")
    assert r.returncode == 1, r.stdout
    assert "missing file" in r.stdout


def test_valid_in_range_anchor_passes():
    r = _run("- valid `core/compliance.py#L69`\n")
    assert r.returncode == 0, r.stdout
