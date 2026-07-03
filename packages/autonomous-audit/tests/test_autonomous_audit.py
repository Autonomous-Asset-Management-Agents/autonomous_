"""MOD-3a-1: standalone autonomous_audit hash-chain (extracted from senate_log.py) + report/CLI.

Locks the tamper-evident contract: canonical pre-image parity (so JS verifies it),
chain linkage, tamper + break detection, restart-safe resume, the fail-loud disk guard,
and the human-readable report + CLI surfaces.
"""

from __future__ import annotations

import hashlib
import json
import types

import pytest

import autonomous_audit as aud
from autonomous_audit import cli
from autonomous_audit.report import render_html_report, summarize_entry


def _read(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_stamps_prev_hash_and_hash(tmp_path):
    w = aud.AuditChainWriter(tmp_path / "audit.jsonl")
    h = w.append({"decision": "BUY", "symbol": "AAPL"})
    (entry,) = _read(tmp_path / "audit.jsonl")
    assert entry["prev_hash"] == aud.GENESIS_HASH
    assert entry["hash"] == h == aud.entry_hash(entry)


def test_chain_links_second_to_first(tmp_path):
    w = aud.AuditChainWriter(tmp_path / "audit.jsonl")
    h1 = w.append({"n": 1})
    h2 = w.append({"n": 2})
    e1, e2 = _read(tmp_path / "audit.jsonl")
    assert e1["prev_hash"] == aud.GENESIS_HASH
    assert e2["prev_hash"] == h1
    assert h1 != h2 and w.last_hash == h2


def test_canonical_preimage_parity(tmp_path):
    # Pins the EXACT byte-string hashed (json.dumps(sort_keys=True) over {record, prev_hash},
    # no hash field) — the contract the JS verifier (audit-chain.cjs) must match.
    w = aud.AuditChainWriter(tmp_path / "audit.jsonl")
    h = w.append({"b": 2, "a": 1})
    expected = hashlib.sha256(
        json.dumps(
            {"a": 1, "b": 2, "prev_hash": aud.GENESIS_HASH}, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    assert h == expected


def test_verify_clean_chain(tmp_path):
    w = aud.AuditChainWriter(tmp_path / "audit.jsonl")
    for i in range(5):
        w.append({"n": i})
    assert aud.verify_chain(tmp_path / "audit.jsonl") == (True, None)


def test_verify_detects_tampered_field(tmp_path):
    p = tmp_path / "audit.jsonl"
    w = aud.AuditChainWriter(p)
    w.append({"decision": "BUY"})
    w.append({"decision": "SELL"})
    rows = _read(p)
    rows[0]["decision"] = "HOLD"  # edit a field, keep the old hash
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ok, err = aud.verify_chain(p)
    assert ok is False and "line 1" in err and "tamper" in err


def test_verify_detects_deleted_entry(tmp_path):
    p = tmp_path / "audit.jsonl"
    w = aud.AuditChainWriter(p)
    w.append({"n": 1})
    w.append({"n": 2})
    w.append({"n": 3})
    rows = _read(p)
    del rows[1]  # drop the middle entry -> prev_hash linkage breaks
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ok, err = aud.verify_chain(p)
    assert ok is False and "prev_hash break" in err


def test_resume_continues_chain_across_restart(tmp_path):
    p = tmp_path / "audit.jsonl"
    h1 = aud.AuditChainWriter(p).append({"n": 1})
    w2 = aud.AuditChainWriter(p)  # fresh writer on the existing file
    assert w2.last_hash == h1
    w2.append({"n": 2})
    assert aud.verify_chain(p) == (True, None)


def test_disk_guard_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(
        aud.shutil, "disk_usage", lambda _p: types.SimpleNamespace(free=1)
    )
    w = aud.AuditChainWriter(tmp_path / "audit.jsonl")
    with pytest.raises(aud.AuditDiskFullError):
        w.append({"n": 1})


def test_verify_empty_log(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("", encoding="utf-8")
    ok, err = aud.verify_chain(p)
    assert ok is False and "empty" in err


def test_parity_with_deployed_js_verifier():
    """Byte-parity against the pinned vector in
    desktop/electron/__tests__/verify-audit-chain.test.mjs — proves a hash written by this
    module verifies unchanged in the deployed JS verifier (audit-chain.cjs), incl. the
    ensure_ascii escape of the non-ASCII euro sign."""
    sample = {
        "event_type": "live_enablement",
        "timestamp": "2026-06-23T10:00:00+00:00",
        "actor": "operator",
        "action": "enable",
        "acknowledgment": "Ich akzeptiere Live-Trading auf eigenes Konto (5000 EUR / 5.000 €)",
        "nonce": "nonce-abc-123",
        "prev_hash": "0" * 64,
    }
    expected_preimage = (
        '{"acknowledgment": "Ich akzeptiere Live-Trading auf eigenes Konto '
        '(5000 EUR / 5.000 \\u20ac)", "action": "enable", "actor": "operator", '
        '"event_type": "live_enablement", "nonce": "nonce-abc-123", '
        '"prev_hash": "0000000000000000000000000000000000000000000000000000000000000000", '
        '"timestamp": "2026-06-23T10:00:00+00:00"}'
    )
    expected_hash = "e63abb542f739e6b7b053578f76d4c1140988d7956ae4c4ff453afbeb4e80280"
    assert aud.canonical_preimage(sample) == expected_preimage
    assert aud.entry_hash(sample) == expected_hash


# --- T3: human-readable report -------------------------------------------------


def test_report_renders_clean_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    w = aud.AuditChainWriter(p)
    w.append(
        {
            "symbol": "AAPL",
            "signal_action": "BUY",
            "consensus_score": 0.7,
            "gatekeeper_approved": True,
            "votes": [{"agent": "DrawdownGuard"}],
            "timestamp": "2026-07-02T09:30:00+00:00",
        }
    )
    report = render_html_report(p)
    assert "Chain intact" in report
    assert "Round-Table Decision" in report and "AAPL" in report and "BUY" in report


def test_report_flags_broken_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    w = aud.AuditChainWriter(p)
    w.append({"a": 1})
    w.append({"a": 2})
    rows = _read(p)
    rows[0]["a"] = 999  # tamper -> hash mismatch
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert "Chain BROKEN" in render_html_report(p)


def test_summarize_live_enablement():
    s = summarize_entry(
        {
            "event_type": "live_enablement",
            "actor": "operator",
            "action": "enable",
            "acknowledgment": "ack",
            "timestamp": "t",
            "hash": "abcdef123456",
        }
    )
    assert (
        s["kind"] == "Live-Trading"
        and s["verdict"] == "enable"
        and s["subject"] == "operator"
    )


# --- T4: CLI -------------------------------------------------------------------


def test_cli_verify_exit_codes(tmp_path):
    p = tmp_path / "audit.jsonl"
    aud.AuditChainWriter(p).append({"a": 1})
    assert cli.main(["verify", str(p)]) == 0
    row = _read(p)[0]
    row["a"] = 2  # tamper, keep old hash
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert cli.main(["verify", str(p)]) == 1


def test_cli_report_writes_file(tmp_path):
    p = tmp_path / "audit.jsonl"
    aud.AuditChainWriter(p).append(
        {"symbol": "AAPL", "signal_action": "BUY", "timestamp": "t"}
    )
    out = tmp_path / "r.html"
    assert cli.main(["report", str(p), "-o", str(out)]) == 0
    assert out.exists() and "AAPL" in out.read_text(encoding="utf-8")


def test_cli_demo_creates_artifacts(tmp_path):
    d = tmp_path / "demo"
    assert cli.main(["demo", str(d)]) == 0
    assert (d / "audit_log_demo.jsonl").exists()
    assert (d / "audit_report_demo.html").exists()


# --- B-2 (#1719): robustness -------------------------------------------------


def test_verify_missing_file_returns_clean(tmp_path):
    ok, err = aud.verify_chain(tmp_path / "nope.jsonl")
    assert ok is False and "file not found" in err  # no exception, clean tuple


def test_cli_verify_missing_file_exits_1(tmp_path):
    assert cli.main(["verify", str(tmp_path / "nope.jsonl")]) == 1  # no traceback


def test_resume_raises_on_corrupt_line(tmp_path):
    p = tmp_path / "audit.jsonl"
    aud.AuditChainWriter(p).append({"n": 1})
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")  # corrupt mid-chain line
    with pytest.raises(aud.AuditIntegrityError):
        aud.AuditChainWriter(p)  # resume must fail LOUD, not silently skip


def test_report_contains_full_disclosure(tmp_path):
    p = tmp_path / "audit.jsonl"
    aud.AuditChainWriter(p).append(
        {"symbol": "AAPL", "signal_action": "BUY", "timestamp": "t"}
    )
    report = render_html_report(p)
    assert "not investment advice" in report and "tamper-evidence" in report


def test_demo_records_are_string_typed(tmp_path):
    d = tmp_path / "demo"
    assert cli.main(["demo", str(d)]) == 0
    rows = _read(d / "audit_log_demo.jsonl")
    # numeric/bool fields must be string-typed (cross-language parity contract)
    assert rows[0]["consensus_score"] == "0.71"
    assert rows[0]["gatekeeper_approved"] == "true"
    assert rows[1]["order_value"] == "4200.0"
