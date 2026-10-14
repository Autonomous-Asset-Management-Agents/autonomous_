# tests/unit/test_portfolio_constraints_endpoint.py
"""#2653 (Epic #2655, Weg 1) — the DEDICATED portfolio-constraints endpoint (NOT iron-dome).

POST /api/admin/portfolio-constraints/{propose,approve} + GET .../pending. Reuses the four-eyes
MACHINERY (distinct approver in the enterprise edition) and the Art-14 WORM chain (strict write
BEFORE the mutation — a failed audit refuses the change), against the NEW constraint store; the
Iron-Dome policy object stays untouched (epic guardrail). Archon guards: expired token -> 400
(Token Expired); non-relative caps -> 400; token must match the staged pending proposal.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine import eod_assessment as eod
from core.engine.api_routes import app
from core.governance.portfolio_constraints import (
    load_approved_constraints,
    load_pending_proposal,
)


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    yield


@pytest.fixture
def client():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _propose(client, caps=None, actor="alice"):
    return client.post(
        "/api/admin/portfolio-constraints/propose",
        json={"caps": caps or {"Technology": 0.20}, "actor": actor},
    )


class TestPropose:
    def test_propose_stages_pending_and_returns_token(self, client):
        r = _propose(client)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert body["token"]
        pending = load_pending_proposal()
        assert pending["caps"] == {"Technology": 0.20}
        assert pending["actor"] == "alice"

    def test_non_relative_caps_are_rejected_400(self, client):
        r = _propose(client, caps={"Technology": 5000})
        assert r.status_code == 400
        assert load_pending_proposal() is None


class TestApprove:
    def _approve(self, client, token, approver="bob"):
        return client.post(
            "/api/admin/portfolio-constraints/approve",
            json={"token": token, "approver": approver},
        )

    def test_full_four_eyes_flow_persists_and_worm_audits(self, client):
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}), patch(
            "core.engine.api_routes.log_policy_event", new_callable=AsyncMock
        ) as worm:
            token = _propose(client, actor="alice").json()["token"]
            r = self._approve(client, token, approver="bob")
        assert r.status_code == 200
        assert load_approved_constraints() == {"Technology": 0.20}
        assert load_pending_proposal() is None  # pending slot cleared
        worm.assert_awaited_once()
        # actor carries the accountable initiator->approver identity; strict chain write
        assert worm.await_args.args[2] == "alice->bob"
        assert worm.await_args.kwargs.get("strict") is True

    def test_self_approve_is_refused_in_enterprise(self, client):
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "ENTERPRISE"}):
            token = _propose(client, actor="alice").json()["token"]
            r = self._approve(client, token, approver="alice")
        assert r.status_code == 403
        assert load_approved_constraints() == {}

    def test_local_single_operator_may_approve(self, client):
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
            "core.engine.api_routes.log_policy_event", new_callable=AsyncMock
        ):
            token = _propose(client, actor="operator").json()["token"]
            r = self._approve(client, token, approver="operator")
        assert r.status_code == 200

    def test_expired_token_is_400_token_expired(self, client):
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}):
            _propose(client, actor="alice")
            expired = eod.mint_approval_token({"Technology": 0.20}, ttl_hours=-1)
            r = self._approve(client, expired)
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()
        assert load_approved_constraints() == {}

    def test_token_not_matching_pending_is_409(self, client):
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}):
            _propose(client, actor="alice")
            other = eod.mint_approval_token({"Technology": 0.20})  # valid but unbound
            r = self._approve(client, other)
        assert r.status_code == 409

    def test_no_pending_is_404(self, client):
        token = eod.mint_approval_token({"Technology": 0.20})
        r = self._approve(client, token)
        assert r.status_code == 404

    def test_failed_worm_audit_refuses_the_mutation(self, client):
        # audit-before-mutate: the strict chain write re-raises -> 500, NOTHING persisted.
        raw = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
            "core.engine.api_routes.log_policy_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("chain write failed"),
        ):
            token = _propose(client, actor="alice").json()["token"]
            r = raw.post(
                "/api/admin/portfolio-constraints/approve",
                json={"token": token, "approver": "bob"},
            )
        assert r.status_code >= 500
        assert load_approved_constraints() == {}  # refused, never applied unaudited


class TestPendingRead:
    def test_pending_endpoint_serves_review_data(self, client):
        _propose(client, caps={"Energy": 0.25}, actor="alice")
        r = client.get("/api/admin/portfolio-constraints/pending")
        assert r.status_code == 200
        body = r.json()
        assert body["pending"]["caps"] == {"Energy": 0.25}
        assert body["approved"] == {}
