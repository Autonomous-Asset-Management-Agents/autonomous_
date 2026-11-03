"""SIM — /run-simulation is entitlement-gated + /api/entitlement/status exposes the flag.

The desktop Simulation/backtest page is disabled across all tiers (central switch
core/entitlement/tier.py: ``simulation_enabled=False``). Defense-in-depth: even a
direct POST to /run-simulation must be refused when the resolved entitlement
disables simulation, and the backtest thread must NOT start. Mirrors the
/api/live/enable ``allow_live`` gate (#1800 Brick-4).
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_KEY = "test-engine-key"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    # No `with` → Starlette lifespan does NOT run, so our injected engine stub stands.
    return TestClient(api_routes.app), api_routes


class _StubEngine:
    def __init__(self):
        self.calls = []
        self.is_simulation = False
        self.last_simulation_result = None

    def run_simulation_in_thread(self, start, end, capital, mode):
        self.calls.append((start, end, capital, mode))


class RunSimulationGate(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

        self.engine = _StubEngine()
        import core.engine.api_routes as api_routes

        orig = api_routes.engine
        api_routes.engine = self.engine
        self.addCleanup(lambda: setattr(api_routes, "engine", orig))

    def _post(self):
        client, _ = _client()
        return client.post(
            "/run-simulation",
            headers={"X-Engine-Key": _KEY},
            json={
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "initial_capital": 100000,
                "symbol_sample_mode": "sp500",
            },
        )

    def test_refused_and_no_thread_when_simulation_disabled(self):
        with patch(
            "core.entitlement.resolve_entitlement",
            return_value=SimpleNamespace(simulation_enabled=False),
        ):
            r = self._post()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.engine.calls, [])  # backtest thread NOT started

    def test_starts_when_simulation_enabled(self):
        with patch(
            "core.entitlement.resolve_entitlement",
            return_value=SimpleNamespace(simulation_enabled=True),
        ):
            r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "success")
        self.assertEqual(len(self.engine.calls), 1)

    def test_entitlement_status_exposes_simulation_flag(self):
        # Real resolve path (no license / non-LOCAL) → simulation_enabled False.
        client, _ = _client()
        r = client.get("/api/entitlement/status", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertIn("simulation_enabled", r.json())
        self.assertFalse(r.json()["simulation_enabled"])


if __name__ == "__main__":
    unittest.main()
