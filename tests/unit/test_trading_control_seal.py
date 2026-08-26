"""#2863 Inc 4: the operator's trading-control settings are sealed onto the tamper-evident WORM chain.

The desktop launcher persists the two operator-tunable controls — the volatility-sizing master
switch (``VOL_TARGETING_SIZING_ENABLED``) and its daily-vol target (``VOL_TARGET_DAILY_VOL``) — into
``setup.json`` and injects them as env at engine boot (#2865). On boot the engine seals the EFFECTIVE
state onto the SAME SHA-256 hash chain as the HITL / live-enablement / EULA audits (BORA — the engine
owns the WORM write; no JS-side crypto). It is:

* baseline-on-first-boot — every run's governing risk-control state is on-chain (compliance fact);
* idempotent on an unchanged reboot (a fingerprint marker prevents chain spam);
* re-sealed on a change (exactly one new record per distinct control transition).

All fields are ``str`` (float-free) so the SHA-256 preimage is byte-stable for the JS verifier
(``audit-chain.cjs``), mirroring ``LiveEnableEvent`` / ``EulaAcceptanceEvent``.
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
    from core.trading_control_seal import seal_trading_control

    return seal_trading_control


def _entries(d: Path):
    out = []
    for f in sorted(Path(d).glob("audit_log_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _tc(d: Path):
    return [e for e in _entries(d) if e.get("event_type") == "trading_control"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    import config

    # Pin the effective controls the same way the engine reads them (risk_manager idiom):
    # the config module attribute, resolved edition-neutrally.
    monkeypatch.setattr(config, "VOL_TARGETING_SIZING_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "VOL_TARGET_DAILY_VOL", 0.015, raising=False)
    return tmp_path


def test_seals_baseline_on_first_boot(env):
    assert asyncio.run(_seal()()) is True
    recs = _tc(env)
    assert len(recs) == 1
    e = recs[0]
    assert e["vol_targeting_sizing_enabled"] == "false"
    assert e["vol_target_daily_vol"] == "0.015"
    # on the tamper-evident chain
    assert "prev_hash" in e and "hash" in e
    # string-only (float-free) preimage — every field is a str
    for k in (
        "timestamp",
        "actor",
        "vol_targeting_sizing_enabled",
        "vol_target_daily_vol",
        "app_version",
    ):
        assert isinstance(e[k], str)
    # fingerprint marker written for idempotency
    marker = json.loads((env / "trading_control_seal.json").read_text(encoding="utf-8"))
    assert marker["vol_targeting_sizing_enabled"] == "false"
    assert marker["vol_target_daily_vol"] == "0.015"
    assert marker.get("sealed_at")


def test_idempotent_unchanged_reboot(env):
    assert asyncio.run(_seal()()) is True
    assert asyncio.run(_seal()()) is False  # unchanged → no-op
    assert len(_tc(env)) == 1  # not duplicated


def test_reseals_on_change(env, monkeypatch):
    import config

    assert asyncio.run(_seal()()) is True  # baseline: OFF / 0.015
    monkeypatch.setattr(config, "VOL_TARGETING_SIZING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "VOL_TARGET_DAILY_VOL", 0.02, raising=False)
    assert asyncio.run(_seal()()) is True  # changed → new record
    recs = _tc(env)
    assert len(recs) == 2
    assert recs[-1]["vol_targeting_sizing_enabled"] == "true"
    assert recs[-1]["vol_target_daily_vol"] == "0.02"
    # chain continuity: the new record links to the previous entry's hash
    assert recs[-1]["prev_hash"] == recs[-2]["hash"]


def test_no_user_data_dir_is_noop(monkeypatch):
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    assert asyncio.run(_seal()()) is False


def test_never_raises_on_write_failure(env, monkeypatch):
    # A broken logger must NOT crash the boot — the seal is best-effort (returns False).
    import core.trading_control_seal as mod

    class _Boom:
        async def log_hitl_event(self, event):
            raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "LocalJSONAuditLogger", lambda: _Boom())
    assert asyncio.run(_seal()()) is False
    assert _tc(env) == []
