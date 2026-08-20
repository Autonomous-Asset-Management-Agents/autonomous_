"""GTM-1 T3 (#1466): the first-run EULA acceptance is sealed onto the tamper-evident WORM chain.

The desktop wizard writes `<AAA_USER_DATA_DIR>/eula_acceptance.json`; on the first engine boot the
engine seals it onto the same SHA-256 hash chain as the HITL / live-enablement audits (BORA — the
engine owns the WORM write), idempotently (a `sealed_at` stamp prevents re-sealing).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _seal():
    from core.eula_seal import seal_eula_acceptance

    return seal_eula_acceptance


def _entries(d: Path):
    out = []
    for f in sorted(Path(d).glob("audit_log_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _write_gate(tmp: Path, **over):
    data = {
        "document": "eula",
        "version": "1.0.0",
        "text_sha256": "abc123def456",
        "acceptedAt": "2026-06-24T10:00:00+00:00",
        "app_version": "2.4.0",
    }
    data.update(over)
    (tmp / "eula_acceptance.json").write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    return tmp_path


def test_seals_acceptance_onto_the_worm_chain(env):
    _write_gate(env)
    assert asyncio.run(_seal()()) is True
    eula = [e for e in _entries(env) if e.get("event_type") == "eula_acceptance"]
    assert len(eula) == 1
    e = eula[0]
    assert e["version"] == "1.0.0" and e["text_sha256"] == "abc123def456"
    assert e["document"] == "eula" and e["app_version"] == "2.4.0"
    # on the tamper-evident chain
    assert "prev_hash" in e and "hash" in e
    # string-only (float-free) preimage — every field is a str
    for k in (
        "timestamp",
        "actor",
        "document",
        "version",
        "text_sha256",
        "app_version",
    ):
        assert isinstance(e[k], str)
    # gate-file stamped sealed_at (idempotency marker)
    data = json.loads((env / "eula_acceptance.json").read_text(encoding="utf-8"))
    assert data.get("sealed_at")


def test_idempotent_no_double_seal(env):
    _write_gate(env)
    assert asyncio.run(_seal()()) is True
    assert asyncio.run(_seal()()) is False  # already sealed → no-op
    eula = [e for e in _entries(env) if e.get("event_type") == "eula_acceptance"]
    assert len(eula) == 1  # not duplicated


def test_no_gate_file_is_noop(env):
    assert asyncio.run(_seal()()) is False
    assert _entries(env) == []


def test_no_user_data_dir_is_noop(monkeypatch):
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    assert asyncio.run(_seal()()) is False
