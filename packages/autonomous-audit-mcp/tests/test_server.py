"""Tests for the MCP wrapper's plain logic (`_do_*`) — no MCP client needed.

The @mcp.tool wrappers just delegate to these, so testing the logic here covers behaviour.
"""

import json

from autonomous_audit_mcp.server import _do_record, _do_verify, _do_report, _do_head


def _log(tmp_path):
    return str(tmp_path / "decisions.jsonl")


def test_record_then_verify_intact(tmp_path):
    log = _log(tmp_path)
    r1 = _do_record(log, {"symbol": "AAPL", "side": "buy", "qty": "10",
                          "timestamp": "2026-07-08T09:30:00+00:00"})
    assert len(r1["hash"]) == 64 and r1["record_count"] == 1
    _do_record(log, {"symbol": "MSFT", "side": "sell", "qty": "5",
                     "timestamp": "2026-07-08T10:00:00+00:00"})
    v = _do_verify(log)
    assert v["intact"] is True and v["error"] is None and v["record_count"] == 2
    assert v["first_timestamp"] == "2026-07-08T09:30:00+00:00"
    assert v["last_timestamp"] == "2026-07-08T10:00:00+00:00"
    assert "not investment advice" in v["disclosure"]


def test_verify_detects_tamper(tmp_path):
    log = _log(tmp_path)
    _do_record(log, {"symbol": "AAPL", "side": "buy", "qty": "10"})
    _do_record(log, {"symbol": "MSFT", "side": "sell", "qty": "5"})
    rows = [json.loads(x) for x in open(log, encoding="utf-8") if x.strip()]
    rows[0]["qty"] = "999"  # tamper, keep old hash
    with open(log, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    v = _do_verify(log)
    assert v["intact"] is False and "line 1" in v["error"] and "tamper" in v["error"]


def test_report_writes_html_with_disclosure(tmp_path):
    log = _log(tmp_path)
    _do_record(log, {"symbol": "AAPL", "side": "buy", "qty": "10"})
    out = str(tmp_path / "report.html")
    r = _do_report(log, out)
    html = open(out, encoding="utf-8").read()
    assert r["report_path"] == out and r["intact"] is True and r["record_count"] == 1
    assert "not investment advice" in html and "tamper-evidence" in html


def test_chain_head_returns_last_hash(tmp_path):
    log = _log(tmp_path)
    _do_record(log, {"symbol": "AAPL", "side": "buy", "qty": "10"})
    last = _do_record(log, {"symbol": "MSFT", "side": "sell", "qty": "5"})
    h = _do_head(log)
    assert h["head_hash"] == last["hash"] and h["record_count"] == 2 and h["intact"] is True


def test_verify_missing_file_is_not_intact(tmp_path):
    v = _do_verify(str(tmp_path / "nope.jsonl"))
    assert v["intact"] is False and v["record_count"] == 0
