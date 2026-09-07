"""REPORTS-UX Wave 1 (#2710, plan docs/5_engineering_and_devops/specs/reports-ux):
the report body must never be truncated in the expanded reader.

Proof-first root cause: ``_serialize_specialist_report`` caps the note body
(bull_case[:1000], bear_case[:1000], investment_thesis/summary/news_summary[:1500],
about[:900]). The bulk /specialist-reports list KEEPS the caps (a ~500-symbol
payload must stay light); a per-symbol GET /specialist-report/{symbol} serves the
body UNCAPPED (full=True). full=False stays byte-identical → no DTO/BORA drift.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-reports-ux"


def _report(bull_len: int = 3000):
    """A report whose prose body is far longer than the list caps."""
    return SimpleNamespace(
        symbol="NVDA",
        recommendation="buy",
        confidence=0.78,
        escalate=True,
        escalate_reason="human review",
        updated_at=datetime.now(timezone.utc),
        about="A" * 2000,
        company_summary="",
        investment_thesis="T" * 3000,
        bull_case="B" * bull_len,
        bear_case="R" * 2500,
        news_summary="N" * 3000,
        alternative_signals="S" * 2000,
        summary="M" * 3000,
    )


class SerializerFullParam(unittest.TestCase):
    def test_default_caps_the_body(self):
        from core.engine.api_routes import _serialize_specialist_report

        out = _serialize_specialist_report("NVDA", _report())
        self.assertEqual(len(out["bull_case"]), 1000)
        self.assertEqual(len(out["bear_case"]), 1000)
        self.assertEqual(len(out["investment_thesis"]), 1500)
        self.assertEqual(len(out["news_summary"]), 1500)
        self.assertEqual(len(out["summary"]), 1500)
        self.assertEqual(len(out["about"]), 900)

    def test_full_true_returns_the_whole_body(self):
        from core.engine.api_routes import _serialize_specialist_report

        r = _report(bull_len=3000)
        out = _serialize_specialist_report("NVDA", r, full=True)
        # The hard requirement: nothing is cut when full=True.
        self.assertEqual(len(out["bull_case"]), 3000)
        self.assertEqual(len(out["bear_case"]), 2500)
        self.assertEqual(len(out["investment_thesis"]), 3000)
        self.assertEqual(len(out["news_summary"]), 3000)
        self.assertEqual(len(out["summary"]), 3000)
        self.assertEqual(len(out["about"]), 2000)
        self.assertEqual(out["bull_case"], r.bull_case)


class SingleSymbolEndpoint(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _client(self):
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes

        return TestClient(api_routes.app), api_routes

    def _stub_engine(self, api):
        class _Registry:
            def get_report(self, symbol):
                return _report() if symbol == "NVDA" else None

            def get_all_reports(self):
                return {"NVDA": _report()}

            def get_escalations(self):
                return []

            def get_status(self):
                return {"running": True}

        return SimpleNamespace(specialist_registry=_Registry())

    def test_full_report_is_untruncated(self):
        client, api = self._client()
        with patch.object(api, "engine", self._stub_engine(api)):
            r = client.get("/specialist-report/NVDA", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        report = r.json()["report"]
        self.assertEqual(len(report["bull_case"]), 3000)  # not 1000
        self.assertEqual(len(report["investment_thesis"]), 3000)

    def test_lowercase_symbol_resolves(self):
        client, api = self._client()
        with patch.object(api, "engine", self._stub_engine(api)):
            r = client.get("/specialist-report/nvda", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["report"]["bull_case"]), 3000)

    def test_unknown_symbol_404(self):
        client, api = self._client()
        with patch.object(api, "engine", self._stub_engine(api)):
            r = client.get("/specialist-report/ZZZZ", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 404)

    def test_summary_projection_omits_body(self):
        client, api = self._client()
        with patch.object(api, "engine", self._stub_engine(api)):
            r = client.get(
                "/specialist-report/NVDA?fields=summary",
                headers={"X-Engine-Key": _KEY},
            )
        self.assertEqual(r.status_code, 200)
        report = r.json()["report"]
        self.assertIn("recommendation", report)
        self.assertNotIn("bull_case", report)

    def test_requires_engine_key(self):
        client, _ = self._client()
        r = client.get("/specialist-report/NVDA")
        self.assertIn(r.status_code, (401, 403))

    def test_bulk_list_still_capped(self):
        """Regression: the ~500-symbol list must NOT be uncapped (payload)."""
        client, api = self._client()
        with patch.object(api, "engine", self._stub_engine(api)):
            r = client.get("/specialist-reports", headers={"X-Engine-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["reports"][0]["bull_case"]), 1000)


if __name__ == "__main__":
    unittest.main()
