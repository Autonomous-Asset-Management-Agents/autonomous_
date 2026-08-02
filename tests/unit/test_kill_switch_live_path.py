"""LIVE-1 T3 (#1426): the kill-switch is reachable on the live path (RTS 6 Art. 5).

RTS 6 Art. 5 requires the ABILITY TO IMMEDIATELY STOP ALL ALGORITHMS. The enforcement point is
``order_executor.py:654`` (``kill_switch.check_halt(user_id)`` before every broker submit). This
suite pins the operator → halt → order-block chain:
  * ``POST /panic-sell`` HALTS all algorithms (trips the kill-switch) — independently of broker
    reachability — so the trading loop cannot re-enter after the emergency liquidation.
  * a tripped switch makes the order-path guard (``check_halt``) raise.
  * ``POST /reset-kill-switch`` clears the halt so trading can resume.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-ks"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app), api_routes


def _headers():
    return {"X-Engine-Key": _KEY}


class KillSwitchLivePath(unittest.TestCase):
    def setUp(self):
        from core.kill_switch import kill_switch

        self.ks = kill_switch
        self.ks.reset()  # clean singleton state before + after each test
        self.addCleanup(self.ks.reset)
        env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        env.start()
        self.addCleanup(env.stop)
        # Silence trip() side effects: the Slack alert (network) and the fire-and-forget
        # mass-cancel thread (needs broker creds) — neither is under test here.
        p1 = patch("core.kill_switch.send_slack_alert", lambda *a, **k: None)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = patch.object(
            type(self.ks), "_run_async_mass_cancel", lambda self, *a, **k: None
        )
        p2.start()
        self.addCleanup(p2.stop)

    def test_panic_sell_trips_kill_switch_even_without_broker(self):
        # RTS 6 Art. 5: the operator's panic HALTS all algorithms regardless of broker reachability.
        client, api_routes = _client()
        with patch.object(api_routes, "engine", SimpleNamespace(api=None)):
            r = client.post("/panic-sell", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(
            self.ks.is_halted(), "panic-sell must trip the kill-switch (halt algos)"
        )

    def test_tripped_kill_switch_blocks_the_order_path(self):
        # The exact guard order_executor.py:654 runs before every broker submit.
        self.ks.trip("test emergency")
        self.assertTrue(self.ks.is_halted())
        with self.assertRaisesRegex(Exception, "HALTED"):
            self.ks.check_halt("global")

    def test_reset_route_clears_the_halt(self):
        self.ks.trip("test emergency")
        self.assertTrue(self.ks.is_halted())
        client, _ = _client()
        r = client.post("/reset-kill-switch", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.ks.is_halted())
        self.ks.check_halt("global")  # must no longer raise

    def test_panic_sell_requires_engine_key(self):
        client, _ = _client()
        r = client.post("/panic-sell")
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(self.ks.is_halted())  # an unauthenticated call must not halt


if __name__ == "__main__":
    unittest.main()
