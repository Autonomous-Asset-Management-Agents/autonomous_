"""ii-6 (PR-0a-ii, GAP2): the /api/hitl/ HTTP contract — routes + frozen DTOs + round-trip.

Pins the cross-lane contract the frontend Decisions-approval / Policy-settings adapter builds
against (Session A): the response JSON key-sets must match the DTOs EXACTLY (freeze, mirroring
the test_g1b_console_routes doctrine), and POST /policy must REJECT HITL_ENABLED with HTTP 422
(env-only activation, C2/M5 — never an API toggle). Auth: X-Engine-Key via require_engine_key.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-hitl"

# Frozen contract key-sets (the enforced cross-lane DTO surface).
_QUEUE_ITEM_KEYS = {
    "approval_id",
    "user_id",
    "symbol",
    "action",
    "qty",
    "price",
    "conviction",
    "target_weight",
    "created_at",
}
_POLICY_KEYS = {
    "HITL_ENABLED",
    "HITL_MAX_VALUE_PER_TRADE",
    "HITL_MAX_VALUE_PER_DAY",
    "HITL_AUTONOMOUS_UNLIMITED",
    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS",
    "HITL_EXPIRY_SECONDS",
}
_ACTION_KEYS = {"success", "approval_id", "detail"}


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app), api_routes


def _headers():
    return {"X-Engine-Key": _KEY}


def _run(coro):
    return asyncio.run(coro)


def _local_redis():
    from core.local_state_client import LocalStateClient

    return LocalStateClient()


def _push(**kw):
    from core.hitl_queue import HitlQueue

    defaults = {
        "user_id": "global",
        "symbol": "AAPL",
        "action": "BUY",
        "qty": 10.0,
        "price": 100.0,
        "conviction": 0.7,
        "target_weight": 0.05,
    }
    defaults.update(kw)
    return _run(HitlQueue.push(**defaults))


class HitlRoutesContract(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        # POST /policy mutates the process-wide _config_state — save + restore.
        import config

        self._orig_state = config._config_state
        self.addCleanup(lambda: setattr(config, "_config_state", self._orig_state))

    # ── GET /api/hitl/pending ────────────────────────────────────────────────

    def test_get_pending_keyset(self):
        redis = _local_redis()
        with patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=redis)
        ):
            _push()
            client, _ = _client()
            r = client.get("/api/hitl/pending", headers=_headers())
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0].keys()), _QUEUE_ITEM_KEYS)

    # ── GET /api/hitl/policy ─────────────────────────────────────────────────

    def test_get_policy_keyset(self):
        client, _ = _client()
        r = client.get("/api/hitl/policy", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json().keys()), _POLICY_KEYS)

    # ── POST /api/hitl/policy ────────────────────────────────────────────────

    def test_post_policy_round_trip(self):
        body = {
            "HITL_MAX_VALUE_PER_TRADE": 5000.0,
            "HITL_MAX_VALUE_PER_DAY": 25000.0,
            "HITL_AUTONOMOUS_UNLIMITED": False,
            "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS": True,
            "HITL_EXPIRY_SECONDS": 600,
        }
        with patch("core.hitl_gate.log_policy_event", AsyncMock()) as audit:
            client, _ = _client()
            r = client.post("/api/hitl/policy", headers=_headers(), json=body)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(set(r.json().keys()), _POLICY_KEYS)
            audit.assert_awaited_once()  # HITLPolicyEvent written before mutating
            g = client.get("/api/hitl/policy", headers=_headers())
        self.assertEqual(g.json()["HITL_MAX_VALUE_PER_TRADE"], 5000.0)
        self.assertEqual(g.json()["HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS"], True)
        self.assertEqual(g.json()["HITL_EXPIRY_SECONDS"], 600)

    def test_post_policy_rejects_hitl_enabled_422(self):
        # M5 / C2: HITL_ENABLED is env-only; the POST DTO forbids it → 422, never a silent 200.
        with patch("core.hitl_gate.log_policy_event", AsyncMock()):
            client, _ = _client()
            r = client.post(
                "/api/hitl/policy",
                headers=_headers(),
                json={"HITL_ENABLED": True, "HITL_MAX_VALUE_PER_TRADE": 5000.0},
            )
        self.assertEqual(r.status_code, 422)

    # ── POST /api/hitl/approve + /reject ─────────────────────────────────────

    def test_approve_keyset(self):
        redis = _local_redis()
        with patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=redis)
        ):
            aid = _push()
            client, _ = _client()
            r = client.post(
                "/api/hitl/approve", headers=_headers(), json={"approval_id": aid}
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json().keys()), _ACTION_KEYS)
        self.assertTrue(r.json()["success"])

    def test_reject_keyset_and_audits(self):
        redis = _local_redis()
        with patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=redis)
        ), patch("core.hitl_gate.log_execution_event", AsyncMock()) as audit:
            aid = _push()
            client, _ = _client()
            r = client.post(
                "/api/hitl/reject",
                headers=_headers(),
                json={"approval_id": aid, "reason": "too risky"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json().keys()), _ACTION_KEYS)
        audit.assert_awaited_once()  # a rejected human decision is audited (never silent)

    def test_post_policy_audit_failure_refuses_mutation_503(self):
        # Art-14: a policy change that cannot be audited must NOT mutate the running policy.
        import config

        before = config.get_config().HITL_MAX_VALUE_PER_TRADE
        with patch(
            "core.hitl_gate.log_policy_event",
            AsyncMock(side_effect=RuntimeError("disk full")),
        ):
            client, _ = _client()
            r = client.post(
                "/api/hitl/policy",
                headers=_headers(),
                json={
                    "HITL_MAX_VALUE_PER_TRADE": 9999.0,
                    "HITL_MAX_VALUE_PER_DAY": 1.0,
                    "HITL_AUTONOMOUS_UNLIMITED": False,
                    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS": False,
                    "HITL_EXPIRY_SECONDS": 600,
                },
            )
        self.assertEqual(r.status_code, 503)
        self.assertEqual(
            config.get_config().HITL_MAX_VALUE_PER_TRADE, before
        )  # unchanged

    def test_post_policy_rejects_absurd_expiry_422(self):
        with patch("core.hitl_gate.log_policy_event", AsyncMock()):
            client, _ = _client()
            r = client.post(
                "/api/hitl/policy",
                headers=_headers(),
                json={
                    "HITL_MAX_VALUE_PER_TRADE": 5000.0,
                    "HITL_MAX_VALUE_PER_DAY": 25000.0,
                    "HITL_AUTONOMOUS_UNLIMITED": False,
                    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS": False,
                    "HITL_EXPIRY_SECONDS": 999_999_999,  # > 24h upper bound
                },
            )
        self.assertEqual(r.status_code, 422)

    def test_reject_already_gone_does_not_audit(self):
        redis = _local_redis()
        with patch(
            "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=redis)
        ), patch("core.hitl_gate.log_execution_event", AsyncMock()) as audit:
            client, _ = _client()
            r = client.post(
                "/api/hitl/reject",
                headers=_headers(),
                json={"approval_id": "does-not-exist"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["success"])
        audit.assert_not_awaited()  # nothing removed → no phantom "rejected" audit row

    def test_apply_hitl_policy_update_is_thread_safe(self):
        # dev-env §2.8: the new global-state mutator must survive concurrent writers and leave a
        # consistent state (exactly one writer's value, never a torn/partial config).
        from concurrent.futures import ThreadPoolExecutor

        import config

        vals = [1000.0 * i for i in range(1, 33)]
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(
                pool.map(
                    lambda v: config.apply_hitl_policy_update(
                        {"HITL_MAX_VALUE_PER_TRADE": v}
                    ),
                    vals,
                )
            )
        final = config.get_config().HITL_MAX_VALUE_PER_TRADE
        self.assertIn(final, vals)  # one writer's value, not corrupt/torn
        self.assertIsInstance(
            config.get_config().HITL_EXPIRY_SECONDS, int
        )  # still valid

    # ── #1463: desktop (config.oss) parity + clean apply failure ─────────────

    def test_apply_hitl_policy_update_exists_and_mutates_in_oss_config(self):
        # #1463 root cause: config.oss.py (the DESKTOP edition's config) was
        # missing apply_hitl_policy_update entirely → POST /api/hitl/policy raised
        # an uncaught AttributeError → bare 500. The OSS config must expose the
        # same mutator (parity with config.py) and update its module-global limits.
        import importlib.util as u

        spec = u.spec_from_file_location(
            "config_oss_under_test", str(_ROOT / "config.oss.py")
        )
        m = u.module_from_spec(spec)
        spec.loader.exec_module(m)

        self.assertTrue(
            hasattr(m, "apply_hitl_policy_update"),
            "config.oss must expose apply_hitl_policy_update (desktop parity, #1463)",
        )
        m.apply_hitl_policy_update(
            {
                "HITL_MAX_VALUE_PER_TRADE": 4242.0,
                "HITL_ENABLED": True,  # env-only → must be ignored
                "NOT_A_REAL_KEY": 1,  # unknown → must be ignored
            }
        )
        cfg = m.get_config()
        self.assertEqual(cfg.HITL_MAX_VALUE_PER_TRADE, 4242.0)
        self.assertFalse(cfg.HITL_ENABLED)  # never settable via the API
        self.assertFalse(hasattr(cfg, "NOT_A_REAL_KEY"))

    def test_post_policy_apply_failure_returns_clean_503_not_500(self):
        # #1463: if apply_hitl_policy_update raises (e.g. an edition gap), the
        # handler must return a CLEAN 503 with a message — never an uncaught 500.
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes

        with patch("core.hitl_gate.log_policy_event", AsyncMock()), patch(
            "config.apply_hitl_policy_update", side_effect=RuntimeError("boom")
        ):
            client = TestClient(api_routes.app, raise_server_exceptions=False)
            r = client.post(
                "/api/hitl/policy",
                headers=_headers(),
                json={
                    "HITL_MAX_VALUE_PER_TRADE": 5000.0,
                    "HITL_MAX_VALUE_PER_DAY": 25000.0,
                    "HITL_AUTONOMOUS_UNLIMITED": False,
                    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS": False,
                    "HITL_EXPIRY_SECONDS": 600,
                },
            )
        self.assertEqual(r.status_code, 503)

    # ── auth ─────────────────────────────────────────────────────────────────

    def test_requires_engine_key(self):
        client, _ = _client()
        self.assertIn(client.get("/api/hitl/pending").status_code, (401, 403))
        self.assertIn(client.get("/api/hitl/policy").status_code, (401, 403))
        self.assertIn(
            client.post("/api/hitl/approve", json={"approval_id": "x"}).status_code,
            (401, 403),
        )


if __name__ == "__main__":
    unittest.main()
