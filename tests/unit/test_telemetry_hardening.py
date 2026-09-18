# tests/unit/test_telemetry_hardening.py
"""Hardening for the telemetry rotation fix (critical-review follow-up):
thread-safety (concurrent export must not lose or tear spans — the likely source
of the 0x80 torn-write corruption), bounded/streaming startup prune, age-pruning
of the rotated sibling, and the max_bytes=None branch.
"""

import json
import os
import threading
import time

from core.telemetry_local import (
    DEFAULT_RETENTION_BYTES,
    LocalScrubbingSpanExporter,
    prune_store,
)


class _Span:
    def __init__(self, name="s"):
        self.name = name
        self.status = None
        self.start_time = time.time_ns()
        self.end_time = time.time_ns()
        self.attributes = {}
        self.events = []


def _valid_lines(path):
    """Return the parseable JSON records in a jsonl file (None if absent)."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            out.append(json.loads(ln))  # raises if a line is torn -> test fails
    return out


def test_concurrent_appends_no_loss_no_tear(tmp_path):
    """Under cap (no rotation): N threads appending concurrently must lose no span
    and tear no line. Without the lock, interleaved open('a')+write drops/corrupts."""
    exp = LocalScrubbingSpanExporter(str(tmp_path), max_bytes=DEFAULT_RETENTION_BYTES)
    n_threads, per = 8, 100

    def worker(t):
        for i in range(per):
            assert exp.export([_Span(f"t{t}-{i}")]) is not None

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    recs = _valid_lines(tmp_path / "telemetry.jsonl")  # json.loads asserts no tear
    assert len(recs) == n_threads * per  # no lost appends
    assert len({r["name"] for r in recs}) == n_threads * per  # all distinct spans


def test_concurrent_export_over_cap_no_failure_no_tear(tmp_path):
    """Over cap (rotation fires): concurrent export must not return FAILURE, must not
    tear a line, and must keep at most one rotation. Guards the delete-race."""
    exp = LocalScrubbingSpanExporter(str(tmp_path), max_bytes=4096)
    pad = "x" * 400
    failures = []

    def worker(t):
        for i in range(120):
            r = exp.export([_Span(f"t{t}-{i}-{pad}")])
            if r is None or "SUCCESS" not in str(r):
                failures.append(r)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert (
        not failures
    ), f"export returned non-SUCCESS under concurrency: {failures[:3]}"
    _valid_lines(tmp_path / "telemetry.jsonl")  # no torn line
    _valid_lines(tmp_path / "telemetry.1.jsonl")  # no torn line
    assert not any(f.endswith(".2.jsonl") for f in os.listdir(tmp_path))


def test_startup_prune_bounds_large_store_and_keeps_newest(tmp_path):
    """__init__ -> prune_store repairs a >cap store with a mid-record 0x80 byte,
    bounds it to ~max_bytes (streamed), and keeps the NEWEST valid records."""
    p = tmp_path / "telemetry.jsonl"
    now = time.time_ns()
    with open(p, "wb") as fh:
        for i in range(600):
            rec = {"name": f"rec{i}", "end_time": now, "pad": "y" * 2000}
            fh.write(json.dumps(rec).encode("utf-8") + b"\n")
            if i == 5:
                fh.write(b'{"name": "tor\x80n"}\n')  # mid-store non-UTF-8 byte
    cap = 200 * 1024
    LocalScrubbingSpanExporter(str(tmp_path), max_bytes=cap)  # __init__ prunes
    assert os.path.getsize(p) <= cap + 4096  # bounded to ~cap
    names = {r["name"] for r in _valid_lines(p)}
    assert "rec599" in names  # newest kept
    assert "rec0" not in names  # oldest dropped by size trim


def test_rotated_sibling_age_pruned_by_mtime(tmp_path):
    """telemetry.1.jsonl older than max_age_days is dropped; a fresh one is kept."""
    old = tmp_path / "telemetry.1.jsonl"
    old.write_text('{"name": "old"}\n', encoding="utf-8")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old, (ten_days_ago, ten_days_ago))
    prune_store(str(tmp_path), max_age_days=7)
    assert not old.exists()  # aged out

    fresh = tmp_path / "telemetry.1.jsonl"
    fresh.write_text('{"name": "fresh"}\n', encoding="utf-8")
    prune_store(str(tmp_path), max_age_days=7)
    assert fresh.exists()  # recent rotation retained


def test_rotation_disabled_when_max_bytes_none(tmp_path):
    """max_bytes=None disables the size cap: no rotation, span still appended."""
    exp = LocalScrubbingSpanExporter(str(tmp_path), max_bytes=None)
    p = tmp_path / "telemetry.jsonl"
    p.write_text("z" * 200000 + "\n", encoding="utf-8")  # far over any default
    exp.export([_Span("kept")])
    assert not (tmp_path / "telemetry.1.jsonl").exists()  # no rotation
    assert "kept" in p.read_text(encoding="utf-8")  # appended in place
