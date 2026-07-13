"""TDD (ADR-OBS-01 / PR A): GET /engine-diagnostics — machine-health aggregation.

Invariants under test:
  * ALWAYS HTTP 200 while the process lives (health lives in ``overall_status``, never a 500).
  * Fail-soft per subsystem: a ``_collect_*`` that raises → ``{"_error": "<Class>"}`` for that
    subsystem, the endpoint still returns 200 with the other subsystems intact.
  * Kill-switch trip is surfaced (halted + reason) WITHOUT leaking a raw ``user_id``.
  * Privacy: the serialized body contains no ``equity`` / ``user_id`` keys.

Auth is bypassed via ``app.dependency_overrides`` (same pattern as
test_iron_dome_admin_endpoint.py); auth wiring itself is tested elsewhere.
"""

import json

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app

_SUBSYSTEMS = (
    "process",
    "loops",
    "kill_switch",
    "governance",
    "hitl",
    "risk",
    "compliance",
    "db",
)


@pytest.fixture
def client_authed():
    # Bypass the engine-key auth to exercise the route logic in isolation.
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_returns_200_with_all_subsystem_keys(client_authed):
    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    # Top-level machine-health contract.
    assert "overall_status" in body
    assert "engine_ready" in body
    assert "generated_at" in body
    # Every subsystem key is present.
    for name in _SUBSYSTEMS:
        assert name in body, f"missing subsystem: {name}"


def test_failing_collector_is_isolated_and_endpoint_still_200(
    client_authed, monkeypatch
):
    # Force ONE collector to raise; the endpoint must still return 200 and mark ONLY
    # that subsystem with an ``_error`` marker while the others stay intact.
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_process", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["process"] == {"_error": "RuntimeError"}
    # A sibling subsystem is unaffected (still a normal dict, no error marker).
    assert "_error" not in body["kill_switch"]


def test_kill_switch_trip_surfaced_without_user_id(client_authed):
    from core.kill_switch import kill_switch

    kill_switch.trip("unit-test halt reason")
    try:
        r = client_authed.get("/engine-diagnostics")
        assert r.status_code == 200
        ks = r.json()["kill_switch"]
        assert ks.get("halted") is True
        # The trip reason is surfaced for operators…
        assert "unit-test halt reason" in json.dumps(ks)
        # …but the raw user_id / scope are NEVER exposed.
        assert "user_id" not in json.dumps(ks)
        assert "scope" not in ks
    finally:
        kill_switch.reset()


def test_privacy_no_forbidden_keys_in_body(client_authed):
    body = client_authed.get("/engine-diagnostics").json()
    serialized = json.dumps(body)
    assert "equity" not in serialized
    assert "user_id" not in serialized
