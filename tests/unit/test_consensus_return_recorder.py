"""#3197 consensus-return harness recorder — dark, env-gated, fail-safe."""

from __future__ import annotations

import json
from types import SimpleNamespace

import core.round_table.consensus_return_recorder as rec


def _vote(name, score, weight):
    return SimpleNamespace(agent_name=name, score=score, weight=weight)


def test_noop_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rec.get_config(), "CONSENSUS_RETURN_HARNESS_PATH", "", raising=False
    )
    # Must not raise and must not create any file.
    rec.record_consensus_observation(
        {"current_time": "2026-07-15T09:30:00"}, "AAPL", 0.8, []
    )


def test_writes_jsonl_row_when_env_set(tmp_path, monkeypatch):
    out = tmp_path / "obs.jsonl"
    monkeypatch.setattr(
        rec.get_config(),
        "CONSENSUS_RETURN_HARNESS_PATH",
        str(out),
        raising=False,
    )
    votes = [
        _vote("MomentumAgent", 0.9, 0.45),
        _vote("ValuationAgent", 0.3, 0.35),
    ]
    rec.record_consensus_observation(
        {"current_time": "2026-07-15T09:30:00-04:00"}, "AAPL", 0.72, votes
    )
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-07-15"
    assert r["symbol"] == "AAPL"
    assert r["consensus"] == 0.72
    assert r["votes"]["MomentumAgent"] == {"score": 0.9, "weight": 0.45}
    assert r["votes"]["ValuationAgent"]["score"] == 0.3


def test_appends_multiple(tmp_path, monkeypatch):
    out = tmp_path / "obs.jsonl"
    monkeypatch.setattr(
        rec.get_config(),
        "CONSENSUS_RETURN_HARNESS_PATH",
        str(out),
        raising=False,
    )
    for s in ("A", "B", "C"):
        rec.record_consensus_observation({"current_time": "2026-07-16"}, s, 0.7, [])
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_fail_safe_on_bad_path(monkeypatch):
    # An unwritable path must be swallowed, never raised.
    monkeypatch.setattr(
        rec.get_config(),
        "CONSENSUS_RETURN_HARNESS_PATH",
        "/nonexistent_dir_xyz/obs.jsonl",
        raising=False,
    )
    rec.record_consensus_observation({"current_time": "2026-07-16"}, "X", 0.7, [])
