# tests/unit/test_hitl_audit.py
# ii-2 (PR-0a-ii, GAP2): HITL audit infrastructure on the existing tamper-evident chain.
#
# N7 refactor: the SHA-256 hash-chain write is extracted from `_async_log_to_jsonl`
# (SenateSession-typed) into a generic `_write_to_hash_chain(entry: dict)`, so both
# `log_session` and the new `log_hitl_event` share ONE tamper-evident chain. D1: two clean
# dataclasses (HITLPolicyEvent / HITLExecutionEvent). Dormant: nothing calls log_hitl_event
# until the HITL order path (PR-0a-ii-4/-5).
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.round_table.senate_log import (  # noqa: E402
    HITLExecutionEvent,
    HITLPolicyEvent,
    LocalJSONAuditLogger,
    SenateProtocol,
    SenateSession,
)


def _run(coro):
    return asyncio.run(coro)


def _logger(tmp_path, monkeypatch):
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    return LocalJSONAuditLogger()


def _entries(tmp_path):
    out = []
    for f in sorted(Path(tmp_path).glob("audit_log_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


_SESSION = SenateSession(
    session_id="s1",
    symbol="AAPL",
    timestamp="2026-06-14T00:00:00Z",
    votes=[],
    consensus_score=0.7,
    gatekeeper_approved=True,
    gatekeeper_reason="ok",
    signal_action="BUY",
)


def _policy_event():
    return HITLPolicyEvent(
        timestamp="2026-06-14T00:00:01Z",
        actor="operator",
        old_policy={"HITL_MAX_VALUE_PER_TRADE": 0.0},
        new_policy={"HITL_MAX_VALUE_PER_TRADE": 5000.0},
    )


def _exec_event():
    return HITLExecutionEvent(
        timestamp="2026-06-14T00:00:02Z",
        symbol="AAPL",
        action="BUY",
        branch="under_limit",
        policy_hash="abc123",
        order_value=3000.0,
        day_notional_after=3000.0,
    )


# --- N7: log_session still hash-chains after the refactor (behaviour-preserving) ---


def test_log_session_still_hash_chains(tmp_path, monkeypatch):
    lg = _logger(tmp_path, monkeypatch)
    _run(lg._async_log_to_jsonl(_SESSION))
    entries = _entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["symbol"] == "AAPL" and e["session_id"] == "s1"
    assert e["prev_hash"] == "0" * 64
    assert len(e["hash"]) == 64


# --- log_hitl_event writes a hash-chained, typed entry ---


def test_log_hitl_policy_event(tmp_path, monkeypatch):
    lg = _logger(tmp_path, monkeypatch)
    _run(lg.log_hitl_event(_policy_event()))
    e = _entries(tmp_path)[0]
    assert e["event_type"] == "hitl_policy"
    assert e["actor"] == "operator"
    assert e["new_policy"] == {"HITL_MAX_VALUE_PER_TRADE": 5000.0}
    assert e["prev_hash"] == "0" * 64 and len(e["hash"]) == 64


def test_log_hitl_execution_event(tmp_path, monkeypatch):
    lg = _logger(tmp_path, monkeypatch)
    _run(lg.log_hitl_event(_exec_event()))
    e = _entries(tmp_path)[0]
    assert e["event_type"] == "hitl_execution"
    assert e["branch"] == "under_limit"
    assert e["policy_hash"] == "abc123"
    assert e["order_value"] == 3000.0


# --- ONE shared tamper-evident chain across session + HITL events ---


def test_one_shared_hash_chain(tmp_path, monkeypatch):
    lg = _logger(tmp_path, monkeypatch)
    _run(lg._async_log_to_jsonl(_SESSION))
    _run(lg.log_hitl_event(_policy_event()))
    entries = _entries(tmp_path)
    assert len(entries) == 2
    # the HITL entry chains onto the session entry's hash
    assert entries[1]["prev_hash"] == entries[0]["hash"]


def test_chain_is_tamper_evident(tmp_path, monkeypatch):
    lg = _logger(tmp_path, monkeypatch)
    _run(lg.log_hitl_event(_policy_event()))
    e = _entries(tmp_path)[0]
    stored = e.pop("hash")
    recomputed = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
    assert recomputed == stored  # hash covers the entry (incl. prev_hash)
    e["actor"] = "attacker"  # tamper
    assert hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest() != stored


# --- SenateProtocol.log_hitl_event is a fail-soft no-op (abstract contract satisfied) ---


def test_senate_protocol_log_hitl_event_failsoft(tmp_path, monkeypatch):
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    sp = SenateProtocol()
    # must satisfy the abstract contract and never raise
    _run(sp.log_hitl_event(_policy_event()))
