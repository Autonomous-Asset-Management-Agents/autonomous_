"""G1b (#1050): the three desktop-console GET routes on the engine app.

Routes (main Round-Table nomenclature; bundle paths /senate-* are NOT carried over):
  GET /specialist-reports     — registry reports; documented empty-state while the
                                StockSpecialistRegistry is disabled on main (G1 spec)
  GET /round-table-decisions  — latest-per-symbol decisions from the G1a store
  GET /round-table/{symbol}   — single-symbol verdict in the console's senators shape

DTO contract: the /specialist-reports report objects must carry EXACTLY the
key-set of the fixture frozen from the live bundle engine (the console renders
those keys; values may be empty until the corresponding features port).
Auth: same pattern as sibling engine GETs (X-Engine-Key via require_engine_key).
"""

from __future__ import annotations

import json
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

_FIXTURES = _ROOT / "tests" / "fixtures" / "g1"
_KEY = "test-engine-key-g1b"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app), api_routes


def _headers():
    return {"X-Engine-Key": _KEY}


def _stub_report():
    """A report object carrying main's SpecialistReport surface (ml_* incl.)."""
    return SimpleNamespace(
        symbol="AAPL",
        sentiment_score=61.0,
        recommendation="buy",
        confidence=0.55,
        escalate=False,
        escalate_reason="",
        reasons=["r1", "r2"],
        news_summary="news",
        alternative_signals="",
        insider_trades=[],
        political_trades=[],
        material_events=[],
        reddit_mentions=0,
        wiki_spike=False,
        short_interest_pct=None,
        updated_at=datetime.now(timezone.utc),
        ml_direction="up",
        ml_confidence=0.4,
        ml_base_return_pct=1.2,
        ml_bear_return_pct=-0.5,
        ml_bull_return_pct=2.0,
        signal_quality="converged",
        walkforward_ic=0.06,
        walkforward_sharpe=0.4,
    )


class _StubRegistry:
    def get_all_reports(self):
        return {"AAPL": _stub_report()}

    def get_escalations(self):
        return []

    def get_status(self):
        return {"running": True}


class SpecialistReportsRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_empty_state_while_registry_disabled(self):
        client, api = _client()
        r = client.get("/specialist-reports", headers=_headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["reports"], [])
        for key in ("status", "message", "reports", "registry_status"):
            self.assertIn(key, body)

    def test_dto_keyset_matches_bundle_fixture(self):
        fixture = json.loads(
            (_FIXTURES / "specialist_reports_fixture.json").read_text(encoding="utf-8")
        )
        expected_keys = set(fixture["reports"][0].keys())

        client, api = _client()
        stub_engine = SimpleNamespace(specialist_registry=_StubRegistry())
        with patch.object(api, "engine", stub_engine):
            r = client.get("/specialist-reports", headers=_headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 1)
        got_keys = set(body["reports"][0].keys())
        self.assertEqual(
            got_keys,
            expected_keys,
            f"DTO drift: missing={expected_keys - got_keys} extra={got_keys - expected_keys}",
        )

    def test_requires_engine_key(self):
        client, _ = _client()
        r = client.get("/specialist-reports")  # no header
        self.assertIn(r.status_code, (401, 403))

    def test_extreme_bearish_zero_is_not_masked(self):
        """PR-review P0-1: sentiment_score 0.0 (maximal bearish) must NEVER be
        truthiness-masked into neutral 50.0 — financial edge-case rule."""

        class _ZeroRegistry(_StubRegistry):
            def get_all_reports(self):
                rep = _stub_report()
                rep.sentiment_score = 0.0
                return {"AAPL": rep}

        client, api = _client()
        stub_engine = SimpleNamespace(specialist_registry=_ZeroRegistry())
        with patch.object(api, "engine", stub_engine):
            r = client.get("/specialist-reports", headers=_headers())
        self.assertEqual(r.json()["reports"][0]["sentiment_score"], 0.0)

    def test_explicit_zero_insider_total_wins_over_list_fallback(self):
        """PR-review P2-1: an explicit insider_trades_total=0 must be honored,
        not truthiness-skipped into the list-length fallback."""

        class _ZeroTotalRegistry(_StubRegistry):
            def get_all_reports(self):
                rep = _stub_report()
                rep.insider_trades_total = 0
                rep.insider_trades = [1, 2, 3]  # fallback would yield 3
                return {"AAPL": rep}

        client, api = _client()
        stub_engine = SimpleNamespace(specialist_registry=_ZeroTotalRegistry())
        with patch.object(api, "engine", stub_engine):
            r = client.get("/specialist-reports", headers=_headers())
        self.assertEqual(r.json()["reports"][0]["insider_trades_count"], 0)


class RoundTableDecisionsRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        from core.round_table import recent_decisions as rd

        rd.clear_recent_round_table_decisions()
        self.rd = rd

    def test_empty_state(self):
        client, _ = _client()
        r = client.get("/round-table-decisions", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "total": 0, "decisions": []})

    def test_seeded_store_round_trips(self):
        self.rd.record_round_table_decision(
            {
                "symbol": "MSFT",
                "consensus_score": 0.62,
                "votes": [
                    {
                        "name": "TrendAgent",
                        "agent_name": "TrendAgent",
                        "score": 0.7,
                        "weight": 1.0,
                        "reasoning": "up",
                        "vetoed": False,
                        "signal": "BUY",
                    }
                ],
                "gatekeeper_approved": True,
                "gatekeeper_reason": "ok",
                "signal_action": "BUY",
                "timestamp": "2026-06-12T10:00:00+00:00",
                "session_id": "s1",
            }
        )
        client, _ = _client()
        r = client.get("/round-table-decisions", headers=_headers())
        body = r.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["decisions"][0]["symbol"], "MSFT")


class RoundTableSymbolRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        from core.round_table import recent_decisions as rd

        rd.clear_recent_round_table_decisions()
        self.rd = rd

    def test_unknown_symbol_error_shaped_200(self):
        client, _ = _client()
        r = client.get("/round-table/ZZZZ", headers=_headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["senators"], [])

    def test_votes_map_to_console_senators_shape(self):
        self.rd.record_round_table_decision(
            {
                "symbol": "NVDA",
                "consensus_score": 0.71,
                "votes": [
                    {
                        "agent_name": "TrendAgent",
                        "score": 0.8,
                        "weight": 1.0,
                        "reasoning": "up",
                        "vetoed": False,
                        "signal": "BUY",
                    },
                    {
                        "agent_name": "RiskAgent",
                        "score": 0.2,
                        "weight": 1.0,
                        "reasoning": "down",
                        "vetoed": False,
                        "signal": "SELL",
                    },
                    {
                        "agent_name": "NewsAgent",
                        "score": 0.5,
                        "weight": 0.5,
                        "reasoning": "flat",
                        "vetoed": False,
                        "signal": "HOLD",
                    },
                ],
                "timestamp": "2026-06-12T10:00:00+00:00",
                "session_id": "s2",
            }
        )
        client, _ = _client()
        r = client.get("/round-table/nvda", headers=_headers())  # case-insensitive
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["symbol"], "NVDA")
        votes = {s["name"]: s["vote"] for s in body["senators"]}
        self.assertEqual(votes["TrendAgent"], "BULL")
        self.assertEqual(votes["RiskAgent"], "BEAR")
        self.assertEqual(votes["NewsAgent"], "ABSTAIN")
        self.assertIsNotNone(body["score"])


class KillSwitchStopRoutesTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ENGINE_API_KEY": _KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("core.engine.api_routes.engine")
    @patch("core.engine.api_routes.hitl_gate")
    def test_stop_route_does_not_write_live_enablement(
        self, mock_hitl_gate, mock_engine
    ):
        # Regression (#1983 fix): /stop must NOT emit a live_enablement=disable WORM record.
        # verifyAuditChain reads the latest live_enablement with action!="enable" as a
        # revocation, so a routine stop would silently demote a live user to paper on the
        # next engine boot. Stopping the strategy is not a change of live authorization.
        mock_hitl_gate.log_live_enablement_event = unittest.mock.AsyncMock()
        client, api = _client()
        r = client.post("/stop", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "success"})
        mock_engine.stop_strategy.assert_called_once()
        mock_hitl_gate.log_live_enablement_event.assert_not_called()

    @patch("core.engine.api_routes.hitl_gate")
    @patch("core.kill_switch.kill_switch")
    def test_reset_kill_switch_route_does_not_write_live_enablement(
        self, mock_kill_switch, mock_hitl_gate
    ):
        # Regression (#1983 fix): resetting the kill switch to RESUME trading must NOT emit a
        # live_enablement=disable record — that would revoke live authorization (the opposite
        # of the operator's intent) and demote to paper on next boot. The reset is already
        # durably audited independently (core/kill_switch.py -> kill_switch_audit.log).
        mock_hitl_gate.log_live_enablement_event = unittest.mock.AsyncMock()
        mock_kill_switch.last_trip.return_value = {"reason": "test"}
        mock_kill_switch.is_halted.side_effect = [True, False]
        client, api = _client()
        r = client.post("/reset-kill-switch", headers=_headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "success")
        mock_kill_switch.reset.assert_called_once()
        mock_hitl_gate.log_live_enablement_event.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
