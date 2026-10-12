"""End-to-end: does the paper⇄live account switch actually work across the language seam?

Audit #14/#16/#17 (coverage-gap): every layer of the flip is tested in ISOLATION, but nothing wires
them together — so "does live⇄paper work end to end?" is answered by no test. In particular the ONE
seam that decides whether the engine boots live is the cross-language WORM handshake:

    Python engine writes the enable/disable record  (senate_log `_write_to_hash_chain`)
        ↓  same file, same SHA-256 preimage
    JS shell reads + verifies it                     (desktop/electron/audit-chain.cjs verifyAuditChain)
        ↓  verified === true
    native-engine-manager flips PAPER_TRADING=false and boots live.

Today both sides are pinned only against a HAND-AUTHORED constant (test_live_enablement.py:148 and
verify-audit-chain.test.mjs) — NO test writes via the real ``POST /api/live/enable`` and then reads
that real file with the real ``verifyAuditChain``. If senate_log's serialisation or file location
ever drifts from audit-chain.cjs, the enable becomes unverifiable, the shell fail-closes to paper,
and live silently never arms — while the pinned-constant parity tests stay green.

This module drives the REAL round-trip: it POSTs to the FastAPI app, then shells out to Node running
the REAL audit-chain.cjs against the SAME file, and asserts the arming decision the shell would make.
It is GREEN when the seam holds (locking the contract) and goes RED if the two sides ever desync.

The true OS-level boot (real uvicorn bind + `_select_alpaca_account` in a live subprocess) is a
heavier nightly test; its gap is left visible as an explicit skip (see the bottom of this file)
rather than silently omitted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_KEY = "test-engine-key-live-e2e"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUDIT_CHAIN_CJS = _REPO_ROOT / "desktop" / "electron" / "audit-chain.cjs"
_NODE = shutil.which("node")


@pytest.fixture
def worm():
    """Yield (client, worm_dir): a TestClient whose live endpoints write the WORM chain into a tmp
    dir that the Node verifier then reads via the SAME SENATE_LOG_DIR the desktop shell would use.
    """
    tmp = tempfile.mkdtemp()
    env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY, "SENATE_LOG_DIR": tmp})
    env.start()

    import core.engine.api_routes as api_routes
    import core.hitl_gate as hg
    import core.round_table.runner as runner

    old_senate = runner._senate
    runner._senate = None
    old_fallback = hg._fallback_audit_logger
    hg._fallback_audit_logger = None
    try:
        yield TestClient(api_routes.app), tmp
    finally:
        runner._senate = old_senate
        hg._fallback_audit_logger = old_fallback
        env.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def _headers():
    return {"X-Engine-Key": _KEY}


def _verify_via_node(worm_dir: str) -> dict:
    """Run the REAL desktop shell verifier (audit-chain.cjs) over the WORM dir the engine wrote.

    audit-chain.cjs resolves the log dir from SENATE_LOG_DIR (the explicit branch the desktop shell
    always sets), so pointing Node at the same dir reproduces exactly what native-engine-manager
    reads at boot to decide PAPER_TRADING.
    """
    script = (
        "const {verifyAuditChain} = require(process.env.AUDIT_CHAIN);"
        'process.stdout.write(JSON.stringify(verifyAuditChain("")));'
    )
    proc = subprocess.run(
        [_NODE, "-e", script],
        env={
            **os.environ,
            "SENATE_LOG_DIR": worm_dir,
            "AUDIT_CHAIN": str(_AUDIT_CHAIN_CJS),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node verifier failed: {proc.stderr or proc.stdout}"
    return json.loads(proc.stdout)


needs_node = pytest.mark.skipif(
    _NODE is None or not _AUDIT_CHAIN_CJS.exists(),
    reason="node and desktop/electron/audit-chain.cjs are required for the cross-language WORM seam",
)


@needs_node
def test_enable_written_by_python_is_verified_by_the_js_shell(worm):
    """POST enable → the Node shell's verifyAuditChain authorises a live boot (verified, nonce match)."""
    client, worm_dir = worm
    nonce = "e2e-enable-nonce"
    r = client.post(
        "/api/live/enable",
        headers=_headers(),
        json={
            "acknowledgment": "I accept live trading on my own account",
            "nonce": nonce,
        },
    )
    assert r.status_code == 201

    verdict = _verify_via_node(worm_dir)
    assert (
        verdict["verified"] is True
    ), f"shell would refuse to boot live: {verdict.get('reason')}"
    assert verdict["nonce"] == nonce  # the shell reads back the exact arming decision


@needs_node
def test_full_paper_live_paper_roundtrip_across_the_seam(worm):
    """The complete user flow at the arming-decision seam: paper → arm live → back to paper.

    Mirrors what the desktop shell does on each engine (re)boot: read the WORM chain and decide
    whether to flip PAPER_TRADING off. This is the end-to-end coverage that finding #14 says no test
    provides today.
    """
    client, worm_dir = worm

    # 0) Fresh chain, no arming decision → the shell boots PAPER (fail-closed).
    assert _verify_via_node(worm_dir)["verified"] is False

    # 1) Operator arms live → enable recorded → shell would boot LIVE.
    r = client.post(
        "/api/live/enable",
        headers=_headers(),
        json={"acknowledgment": "arm live", "nonce": "rt-enable"},
    )
    assert r.status_code == 201
    armed = _verify_via_node(worm_dir)
    assert armed["verified"] is True and armed["nonce"] == "rt-enable"

    # 2) Operator switches back to paper → disable recorded → shell returns to PAPER (revocation
    #    beats the earlier enable on the same chain).
    r = client.post(
        "/api/live/disable",
        headers=_headers(),
        json={"acknowledgment": "back to paper", "nonce": "rt-disable"},
    )
    assert r.status_code == 201
    reverted = _verify_via_node(worm_dir)
    assert (
        reverted["verified"] is False
    ), "a later disable must revoke the enable → fail-closed paper"


@needs_node
def test_tampered_enable_record_is_rejected_by_the_shell(worm):
    """Defence-in-depth: if the on-disk enable record is altered after the fact, the shell's hash
    check fails and it refuses to boot live — proving the verifier actually recomputes the SHA-256
    over the Python-written preimage rather than trusting the stored hash."""
    client, worm_dir = worm
    r = client.post(
        "/api/live/enable",
        headers=_headers(),
        json={"acknowledgment": "arm live", "nonce": "tamper-n"},
    )
    assert r.status_code == 201

    logs = sorted(Path(worm_dir).glob("audit_log_*.jsonl"))
    assert logs, "the enable record must have been written to SENATE_LOG_DIR"
    lines = logs[-1].read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    rec["acknowledgment"] = (
        rec["acknowledgment"] + " (tampered)"
    )  # change the preimage, keep the hash
    lines[-1] = json.dumps(rec)
    logs[-1].write_text("\n".join(lines) + "\n", encoding="utf-8")

    verdict = _verify_via_node(worm_dir)
    assert verdict["verified"] is False
    assert "hash" in verdict["reason"].lower()


@pytest.mark.skip(
    reason=(
        "Audit #14 LAYER 2 (nightly gap, not silently dropped): a real-subprocess boot test — spawn "
        "`python -m core.engine` with DEPLOYMENT_MODE=LOCAL, PAPER_TRADING=false, HITL_ENABLED=true "
        "on a free port, poll /health until 200, assert paper_trading===false AND "
        "ALPACA_BASE_URL=='https://api.alpaca.markets', kill, respawn with PAPER_TRADING=true, assert "
        "paper_trading===true. This exercises __main__ preflight + live_trading_guard + "
        "_select_alpaca_account + real uvicorn bind, which the fast contract layer above cannot."
    )
)
def test_real_subprocess_boot_flip_nightly():  # pragma: no cover - documents the coverage boundary
    raise AssertionError("nightly-only; see skip reason for the intended assertions")
