"""LIVE-1 T4 (#1427): the Art.-14 live-enablement WORM endpoints.

`POST /api/live/enable` / `/disable` record a deliberate live-trading enablement/revocation
onto the SAME tamper-evident SHA-256 hash chain as the HITL audits (senate_log
`_write_to_hash_chain`), **before** the desktop shell is allowed to flip SHADOW_MODE off
(audit-before-enable, EU AI Act Art. 14). A strict WORM-write failure must block the success
response — capital may never go live on an unaudited decision. Auth: X-Engine-Key.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-live"
_LIVE_KEYS = {"event_type", "timestamp", "actor", "action", "acknowledgment", "nonce"}


def _client(raise_server_exceptions=True):
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app, raise_server_exceptions=raise_server_exceptions)


def _headers():
    return {"X-Engine-Key": _KEY}


class LiveEnablementRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = patch.dict(
            os.environ, {"ENGINE_API_KEY": _KEY, "SENATE_LOG_DIR": self.tmp}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        # Force the hitl_gate fallback audit logger to re-create against the tmp SENATE_LOG_DIR.
        import core.hitl_gate as hg
        import core.round_table.runner as runner

        old_senate = runner._senate
        runner._senate = None
        self.addCleanup(lambda: setattr(runner, "_senate", old_senate))

        hg._fallback_audit_logger = None
        self.addCleanup(lambda: setattr(hg, "_fallback_audit_logger", None))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _entries(self):
        out = []
        for f in sorted(Path(self.tmp).glob("audit_log_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def _live(self):
        return [e for e in self._entries() if e.get("event_type") == "live_enablement"]

    def test_enable_writes_worm_record_and_returns_201(self):
        client = _client()
        r = client.post(
            "/api/live/enable",
            headers=_headers(),
            json={
                "acknowledgment": "I accept live trading on my own account",
                "nonce": "n-1",
            },
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["success"])
        live = self._live()
        self.assertEqual(len(live), 1)
        e = live[0]
        self.assertEqual(e["action"], "enable")
        self.assertEqual(e["nonce"], "n-1")
        # On the tamper-evident chain: carries prev_hash + hash like every other entry.
        self.assertIn("prev_hash", e)
        self.assertIn("hash", e)
        # Frozen field-set (the JS verifier hashes exactly these keys + prev_hash).
        self.assertEqual(set(e) - {"prev_hash", "hash"}, _LIVE_KEYS)

    def test_enable_record_is_float_free(self):
        # Float repr can diverge between json.dumps and JS JSON.stringify → the live-enable
        # payload MUST be string-only so the JS verifier hashes byte-identically.
        client = _client()
        client.post(
            "/api/live/enable",
            headers=_headers(),
            json={"acknowledgment": "x", "nonce": "n"},
        )
        e = self._live()[0]
        for k in _LIVE_KEYS:
            self.assertIsInstance(
                e[k], str, f"{k} must be a string (float-free preimage)"
            )

    def test_disable_records_revocation_on_same_chain(self):
        client = _client()
        client.post(
            "/api/live/enable",
            headers=_headers(),
            json={"acknowledgment": "x", "nonce": "n1"},
        )
        r = client.post(
            "/api/live/disable",
            headers=_headers(),
            json={"acknowledgment": "x", "nonce": "n2"},
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual([e["action"] for e in self._live()], ["enable", "disable"])

    def test_audit_before_enable_strict_failure_blocks_success(self):
        # A failed WORM write must NOT yield a success response (audit-before-enable).
        client = _client(raise_server_exceptions=False)
        with patch("core.hitl_gate._resolve_audit_logger") as m:
            m.return_value.log_hitl_event = AsyncMock(
                side_effect=RuntimeError("disk full")
            )
            r = client.post(
                "/api/live/enable",
                headers=_headers(),
                json={"acknowledgment": "x", "nonce": "n"},
            )
        self.assertGreaterEqual(r.status_code, 500)
        self.assertEqual(self._live(), [])

    def test_requires_engine_key(self):
        client = _client()
        r = client.post("/api/live/enable", json={"acknowledgment": "x", "nonce": "n"})
        # require_engine_key rejects a missing key with 401 or 403 (mirrors test_hitl_routes:267).
        self.assertIn(r.status_code, (401, 403))


def test_worm_preimage_parity_constant():
    """Pin the SHA-256 of a sample live-enablement entry via the REAL json.dumps(sort_keys=True).

    The JS verifier test (desktop/electron/__tests__/verify-audit-chain.test.mjs) asserts its
    `pyJsonDumps` mirror produces the SAME preimage + hash for the IDENTICAL entry. If Python's
    json serialisation ever drifts (separators, sort, ensure_ascii), THIS test breaks too — so the
    JS↔Python WORM-hash parity (the #1415 Gatekeeper hazard) can never desync silently.
    """
    import hashlib

    entry = {
        "event_type": "live_enablement",
        "timestamp": "2026-06-23T10:00:00+00:00",
        "actor": "operator",
        "action": "enable",
        "acknowledgment": "Ich akzeptiere Live-Trading auf eigenes Konto (5000 EUR / 5.000 €)",
        "nonce": "nonce-abc-123",
        "prev_hash": "0" * 64,
    }
    preimage = json.dumps(entry, sort_keys=True)
    expected_hash = "e63abb542f739e6b7b053578f76d4c1140988d7956ae4c4ff453afbeb4e80280"
    assert hashlib.sha256(preimage.encode()).hexdigest() == expected_hash


if __name__ == "__main__":
    unittest.main()
