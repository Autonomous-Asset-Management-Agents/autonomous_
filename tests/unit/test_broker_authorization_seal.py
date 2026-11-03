"""#5135 (GTM-1): the Alpaca authorization answer is sealed onto the tamper-evident WORM chain.

The desktop writes one answer file per DENY/ALLOW to `<AAA_USER_DATA_DIR>/broker_authorizations/`;
on the next engine boot the engine seals each onto the SHA-256 hash chain (BORA — the engine owns
the WORM write, same pattern as `core/eula_seal.py`), idempotently via a `sealed_at` stamp.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytestmark = pytest.mark.vc0

# The SAME golden is pinned on the renderer side (src/test/brokerAuthorization.test.tsx).
GOLDEN_TEXT = (
    "Authorize autonomous_\n\n"
    "By allowing autonomous_ to access your Alpaca account, you are granting autonomous_ access "
    "to your account information and authorization to place transactions at your direction.\n\n"
    "Alpaca does not warrant or guarantee that autonomous_ will work as advertised or expected. "
    "Before authorizing, learn more about autonomous_."
)
GOLDEN_SHA256 = "7d047c040ce2604c069f717b7adb058cbe4ca4ffff70165bf4d7a15fabfb219d"

_FIELDS = (
    "timestamp",
    "actor",
    "version",
    "text_sha256",
    "answer",
    "slot",
    "key_masked",
)


def _seal():
    from core.broker_authorization_seal import seal_broker_authorizations

    return seal_broker_authorizations


def _entries(d: Path):
    out = []
    for f in sorted(Path(d).glob("audit_log_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return [e for e in out if e.get("event_type") == "broker_authorization"]


def _write_answer(tmp: Path, name: str, **over):
    data = {
        "answeredAt": "2026-11-03T10:00:00.000Z",
        "actor": "operator",
        "version": "1.0.0",
        "text_sha256": GOLDEN_SHA256,
        "answer": "allow",
        "slot": "paper",
        "key_masked": "••••WXYZ",
    }
    data.update(over)
    d = tmp / "broker_authorizations"
    d.mkdir(exist_ok=True)
    (d / name).write_text(json.dumps(data), encoding="utf-8")
    return d / name


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    return tmp_path


def test_golden_text_sha256_is_pinned():
    from core.broker_authorization import (
        AUTHORIZATION_TEXT,
        AUTHORIZATION_TEXT_SHA256,
        AUTHORIZATION_VERSION,
    )

    assert AUTHORIZATION_TEXT == GOLDEN_TEXT
    assert (
        hashlib.sha256(AUTHORIZATION_TEXT.encode("utf-8")).hexdigest() == GOLDEN_SHA256
    )
    assert AUTHORIZATION_TEXT_SHA256 == GOLDEN_SHA256
    assert AUTHORIZATION_VERSION == "1.0.0"


def test_seals_allow_and_deny_in_order(env):
    _write_answer(env, "2026-11-03T10-00-01.000Z-b.json", answer="deny", slot="paper")
    _write_answer(env, "2026-11-03T10-00-00.000Z-a.json", answer="allow", slot="paper")
    assert asyncio.run(_seal()()) == 2
    entries = _entries(env)
    assert [e["answer"] for e in entries] == [
        "allow",
        "deny",
    ]  # chronological by file name
    for e in entries:
        assert "prev_hash" in e and "hash" in e
        for k in _FIELDS:
            assert isinstance(e[k], str)
        assert e["version"] == "1.0.0" and e["text_sha256"] == GOLDEN_SHA256
        assert e["slot"] == "paper" and e["key_masked"] == "••••WXYZ"
    for f in (env / "broker_authorizations").glob("*.json"):
        assert json.loads(f.read_text(encoding="utf-8")).get("sealed_at")


def test_idempotent_no_double_seal(env):
    _write_answer(env, "2026-11-03T10-00-00.000Z-a.json")
    assert asyncio.run(_seal()()) == 1
    assert asyncio.run(_seal()()) == 0
    assert len(_entries(env)) == 1


def test_unmasked_key_is_dropped_and_warned(env, caplog):
    _write_answer(env, "2026-11-03T10-00-00.000Z-a.json", key_masked="PKABCDEF1234")
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(_seal()()) == 1
    (e,) = _entries(env)
    assert e["key_masked"] == ""
    assert "PKABCDEF1234" not in json.dumps(e)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_invalid_answer_is_not_sealed(env, caplog):
    f = _write_answer(env, "2026-11-03T10-00-00.000Z-a.json", answer="yes")
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(_seal()()) == 0
    assert _entries(env) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not json.loads(f.read_text(encoding="utf-8")).get("sealed_at")


def test_no_user_data_dir_is_noop(monkeypatch):
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    assert asyncio.run(_seal()()) == 0


def test_no_answer_dir_is_noop(env):
    assert asyncio.run(_seal()()) == 0
    assert _entries(env) == []


def test_never_raises_on_unreadable_file(env, caplog):
    d = env / "broker_authorizations"
    d.mkdir()
    (d / "2026-11-03T09-00-00.000Z-x.json").write_text("{not json", encoding="utf-8")
    _write_answer(env, "2026-11-03T10-00-00.000Z-a.json")
    with caplog.at_level(logging.WARNING):
        assert (
            asyncio.run(_seal()()) == 1
        )  # the broken file is skipped, the good one sealed
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_event_dispatch():
    from core.round_table.senate_log import (
        BrokerAuthorizationEvent,
        _hitl_event_to_dict,
    )

    ev = BrokerAuthorizationEvent(
        timestamp="t",
        actor="operator",
        version="1.0.0",
        text_sha256="h",
        answer="allow",
        slot="paper",
        key_masked="••••WXYZ",
    )
    d = _hitl_event_to_dict(ev)
    assert d["event_type"] == "broker_authorization"
    assert set(d) == {"event_type", *_FIELDS}
