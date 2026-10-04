# tests/unit/test_telemetry_rotation.py
"""The desktop-engine stall root cause: `export()` called `prune_store` (an O(file-size)
read+parse+rewrite) synchronously at every span-end once telemetry.jsonl hit the 50 MB cap,
starving the asyncio event loop (py-spy: 53 % on-CPU in prune_store:readlines).

The fix moves size-bounding OFF the hot path: `export()` rotates in O(1) (rename to a
``telemetry.1.jsonl`` sibling — the .jsonl suffix keeps the diagnostics export complete),
and `prune_store` (startup-only, age-based) tolerates non-UTF-8 bytes instead of silently
dying on them (which had let the file grow unbounded to 114 MB).
"""

import json
import os
from unittest.mock import patch

from core.telemetry_local import (
    DEFAULT_RETENTION_BYTES,
    LocalScrubbingSpanExporter,
    prune_store,
)


class _Span:
    """Minimal ReadableSpan stand-in for the exporter's _span_to_record."""

    def __init__(self, name="s"):
        self.name = name
        self.status = None
        self.start_time = 1
        self.end_time = 2
        self.attributes = {}
        self.events = []


def _exp(tmp_path, max_bytes):
    return LocalScrubbingSpanExporter(
        str(tmp_path), max_age_days=7, max_bytes=max_bytes
    )


def test_export_over_cap_rotates_not_rewrites(tmp_path):
    """Over the cap, export rotates O(1) and does NOT call the O(file) prune_store."""
    exp = _exp(tmp_path, max_bytes=1000)
    p = tmp_path / "telemetry.jsonl"
    p.write_text("x" * 2000 + "\n", encoding="utf-8")  # oversize (after startup prune)

    # If export tried the O(file) prune path, this would blow up. It must not.
    with patch(
        "core.telemetry_local.prune_store",
        side_effect=AssertionError("prune_store must not run on the export hot path"),
    ):
        exp.export([_Span("new")])

    rotated = tmp_path / "telemetry.1.jsonl"
    assert rotated.exists(), "old content must be rotated to telemetry.1.jsonl"
    assert "x" * 2000 in rotated.read_text(encoding="utf-8")
    # active file is fresh + small, holding just the new span
    assert p.exists() and os.path.getsize(p) < 1000
    assert json.loads(p.read_text(encoding="utf-8").strip())["name"] == "new"


def test_corrupt_0x80_byte_does_not_kill_retention(tmp_path):
    """A raw non-UTF-8 byte must not make prune_store raise-and-give-up (the 114 MB runaway)."""
    p = tmp_path / "telemetry.jsonl"
    with open(p, "wb") as fh:
        fh.write(b'{"name": "keep"}\n')
        fh.write(b"\x80\n")  # invalid UTF-8 start byte — this killed retention
        fh.write(b"x" * 3000 + b"\n")  # oversize, unparseable junk
    prune_store(str(tmp_path), max_age_days=7, max_bytes=1000)  # must not raise
    assert os.path.getsize(p) <= 1200, "store must be bounded despite the bad byte"


def test_rotation_bounds_size_and_keeps_one(tmp_path):
    """Repeated oversized exports keep the active file bounded and retain exactly one rotation."""
    exp = _exp(tmp_path, max_bytes=500)
    p = tmp_path / "telemetry.jsonl"
    for _ in range(5):
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("y" * 600 + "\n")  # push over cap between exports
        exp.export([_Span("s")])
    files = os.listdir(tmp_path)
    assert "telemetry.jsonl" in files
    assert "telemetry.1.jsonl" in files
    assert not any(f.endswith(".2.jsonl") for f in files), "only one rotation kept"
    assert os.path.getsize(p) < 2000, "active file stays bounded"


def test_rotated_name_has_jsonl_suffix(tmp_path):
    """The rotated file must end in .jsonl so telemetry-export.cjs still bundles it."""
    exp = _exp(tmp_path, max_bytes=100)
    (tmp_path / "telemetry.jsonl").write_text("z" * 200 + "\n", encoding="utf-8")
    exp.export([_Span()])
    sib = [
        f for f in os.listdir(tmp_path) if f != "telemetry.jsonl" and "telemetry" in f
    ]
    assert sib and all(f.endswith(".jsonl") for f in sib)


def test_export_writes_span_immediately(tmp_path):
    """Crash-write immediacy preserved: a span is on disk right after export()."""
    exp = _exp(tmp_path, max_bytes=DEFAULT_RETENTION_BYTES)
    exp.export([_Span("imm")])
    assert "imm" in (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8")
