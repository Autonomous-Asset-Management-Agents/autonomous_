"""SIM-1 T1 (#1484): GET /simulation-result — the reload-safe poll target (Dual-Design Option B).

The Console Simulation page polls this endpoint to render the equity curve vs. the S&P 500 even
across a page reload (the engine process keeps the result in memory while it runs). Auth-gated like
the sibling console GETs (X-Engine-Key via require_engine_key).
"""

import os
import unittest
from unittest.mock import patch

_KEY = "test-engine-key"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    # No `with` → Starlette lifespan does NOT run, so our injected engine stub stands.
    return TestClient(api_routes.app), api_routes


class _StubEngine:
    def __init__(self, is_simulation=False, last_simulation_result=None):
        self.is_simulation = is_simulation
        self.last_simulation_result = last_simulation_result


class SimulationResultRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _inject(self, engine):
        import core.engine.api_routes as api_routes

        orig = api_routes.engine
        api_routes.engine = engine
        self.addCleanup(lambda: setattr(api_routes, "engine", orig))

    def test_requires_auth(self):
        client, _ = _client()
        r = client.get("/simulation-result")
        self.assertIn(r.status_code, (401, 403))

    def test_idle_when_no_result(self):
        self._inject(_StubEngine(is_simulation=False, last_simulation_result=None))
        client, _ = _client()
        r = client.get("/simulation-result", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "idle")

    def test_running_while_in_flight(self):
        self._inject(_StubEngine(is_simulation=True))
        client, _ = _client()
        r = client.get("/simulation-result", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "running")

    def test_complete_returns_equity_and_benchmark(self):
        result = {
            "status": "complete",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_capital": 100000,
            "strategy_equity": [{"date": "2024-01-01", "equity": 100000}],
            "spy_equity": [{"date": "2024-01-01", "equity": 100000}],
            "final_equity": 110000,
            "total_return": 10.0,
            "trades_count": 5,
        }
        self._inject(_StubEngine(is_simulation=False, last_simulation_result=result))
        client, _ = _client()
        r = client.get("/simulation-result", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["total_return"], 10.0)
        self.assertEqual(len(body["strategy_equity"]), 1)
        self.assertIn("spy_equity", body)  # the S&P 500 benchmark curve is present

    def test_running_takes_precedence_over_a_stale_result(self):
        # a fresh run must report "running" even if a previous result is still cached
        self._inject(
            _StubEngine(
                is_simulation=True, last_simulation_result={"status": "complete"}
            )
        )
        client, _ = _client()
        r = client.get("/simulation-result", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.json()["status"], "running")


if __name__ == "__main__":
    unittest.main()
