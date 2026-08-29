"""#2277 INC-3 (§4b R4): POST /api/telemetry/switch-reconcile → `health_reconcile` OTEL span.

The console's `reconcilePaper()` posts ONCE, fail-open, so a failed Live⇄Paper reconcile is
reconstructable from the diagnose-export. The engine turns it into a `health_reconcile` span
carrying `{switch_id, attempts, settled_status, paper_trading}` (the §4b R4 attributes).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-reconcile"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app)


def _headers():
    return {"X-Engine-Key": _KEY}


class _Span:
    def __init__(self, name, sink):
        self.name = name
        self.attrs = {}
        sink.append((name, self.attrs))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_attribute(self, k, v):
        self.attrs[k] = v


class SwitchReconcileRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _post(self, body):
        import core.engine.api_routes as api_routes

        spans = []

        class _Tracer:
            def start_as_current_span(self, name, **kw):
                return _Span(name, spans)

        client = _client()
        with patch.object(api_routes, "_tracer", _Tracer()):
            r = client.post(
                "/api/telemetry/switch-reconcile", headers=_headers(), json=body
            )
        return r, spans

    def test_emits_health_reconcile_span_with_all_attributes(self):
        r, spans = self._post(
            {
                "switch_id": "sw-99",
                "attempts": 3,
                "settled_status": "healthy",
                "paper_trading": False,
            }
        )
        self.assertIn(r.status_code, (200, 202, 204))
        hr = [a for (n, a) in spans if n == "health_reconcile"]
        self.assertEqual(len(hr), 1)
        self.assertEqual(hr[0].get("switch_id"), "sw-99")
        self.assertEqual(hr[0].get("attempts"), 3)
        self.assertEqual(hr[0].get("settled_status"), "healthy")
        self.assertEqual(hr[0].get("paper_trading"), False)

    def test_requires_engine_key(self):
        client = _client()
        r = client.post("/api/telemetry/switch-reconcile", json={"attempts": 1})
        self.assertIn(r.status_code, (401, 403))

    def test_minimal_body_still_emits_span(self):
        # Fail-open contract: even a sparse body must not 4xx/5xx — a telemetry post never blocks.
        r, spans = self._post({"attempts": 0})
        self.assertIn(r.status_code, (200, 202, 204))
        self.assertEqual(len([n for (n, _a) in spans if n == "health_reconcile"]), 1)


if __name__ == "__main__":
    unittest.main()
