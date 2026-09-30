"""TDD (ADR-OBS-01 / PR F): anonymous USAGE analytics subsystem.

The ``usage`` subsystem answers *how* the app is used — ANONYMOUS, machine-only,
aggregate integer counters keyed by fixed route/action names. This is the
DSGVO-scoped PR, so the privacy assertions are load-bearing:

  * PRIVACY — api-hit counts are keyed by ROUTE TEMPLATE ("/portfolio-summary"),
    NEVER the raw path with IDs/query strings. The serialized usage body carries
    no ``user_id`` / query strings / concrete IDs / symbols / PII of any kind —
    only aggregate integer counters. An unknown / ID-laden route is IGNORED
    (bounded cardinality).
  * SAFETY — the counters are PURE OBSERVATION. If a bump raises, the
    request/endpoint MUST still return normally (the failure is swallowed); a
    counter failure may never break, slow, or alter a request/response.
  * WIRING — the ``usage`` subsystem is present in ``/engine-diagnostics`` and is
    fail-soft (a raising collector degrades to ``{"_error": ...}`` only for that
    subsystem, never a 500).

Auth is bypassed via ``app.dependency_overrides`` (same pattern as the sibling
diagnostics tests); auth wiring itself is tested elsewhere.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
import core.usage_counters as uc
from core.auth import require_engine_key
from core.engine.api_routes import app


@pytest.fixture(autouse=True)
def _reset_usage():
    uc.reset_usage_counters()
    yield
    uc.reset_usage_counters()


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# (a) bump_usage / bump_api_hit increment
# --------------------------------------------------------------------------- #


def test_bump_usage_increments():
    uc.bump_usage("strategy_swaps")
    uc.bump_usage("strategy_swaps")
    uc.bump_usage("panic_sells")

    snap = uc.get_usage_counters()
    assert snap["strategy_swaps"] == 2
    assert snap["panic_sells"] == 1


def test_bump_api_hit_increments_known_route():
    # A registered route template is counted.
    uc.bump_api_hit("/engine-diagnostics")
    uc.bump_api_hit("/engine-diagnostics")

    snap = uc.get_usage_counters()
    assert snap["api_hits"]["/engine-diagnostics"] == 2


# --------------------------------------------------------------------------- #
# (b) SAFETY — a poisoned bump is swallowed; the request/endpoint still returns
# --------------------------------------------------------------------------- #


def test_bump_usage_never_raises_even_if_poisoned(monkeypatch):
    # Poison the raw store mutation — every bump would raise inside.
    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(uc, "_record_usage", _boom)
    # The double-guarded public entry point MUST swallow it (no raise).
    uc.bump_usage("panic_sells")
    uc.bump_api_hit("/engine-diagnostics")


def test_api_hit_counter_failure_never_breaks_request(client_authed, monkeypatch):
    """SAFETY: if the api-hit bump raises, the request STILL returns normally."""

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(uc, "bump_api_hit", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200


def test_operator_action_counter_failure_never_breaks_endpoint(
    client_authed, monkeypatch
):
    """SAFETY: a poisoned operator-action bump must not break the endpoint."""

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(api_routes_mod, "bump_usage", _boom)

    # /reset-kill-switch is a pure operator action (no broker needed) — it must
    # still return its normal 200 body despite the poisoned counter.
    r = client_authed.post("/reset-kill-switch")
    assert r.status_code == 200
    assert r.json().get("status") in ("success", "error")


# --------------------------------------------------------------------------- #
# (c) PRIVACY — route templates only; no PII; unknown/ID-laden routes ignored
# --------------------------------------------------------------------------- #


def test_bump_api_hit_ignores_unknown_or_id_laden_route():
    """A raw path with an ID / a wholly unknown route is IGNORED (bounded)."""
    before = dict(uc.get_usage_counters()["api_hits"])

    # Raw path carrying a concrete ID/query — must NOT be counted.
    uc.bump_api_hit("/api/hitl/approve/abc-123-uuid")
    uc.bump_api_hit("/portfolio-summary?user_id=42")
    uc.bump_api_hit("/totally/unknown/route")

    after = uc.get_usage_counters()["api_hits"]
    # Nothing new admitted — cardinality stays bounded to registered templates.
    assert after == before
    # And none of the ID/PII fragments leaked in as a key.
    blob = json.dumps(after)
    assert "abc-123-uuid" not in blob
    assert "user_id" not in blob
    assert "42" not in blob


def test_usage_body_has_no_pii_via_diagnostics(client_authed):
    """The serialized usage subsystem carries no user_id / IDs / query strings."""
    # Drive an api-hit through the real middleware first.
    client_authed.get("/engine-diagnostics?user_id=secret&order_id=xyz")

    body = client_authed.get("/engine-diagnostics").json()
    usage = body["usage"]

    # api_hits keys are ROUTE TEMPLATES, never a raw path with a query string.
    for route_key in usage.get("api_hits", {}):
        assert "?" not in route_key, f"query string leaked into key: {route_key!r}"
        assert "user_id" not in route_key
        assert "secret" not in route_key
        assert "xyz" not in route_key

    # No PII anywhere in the whole usage body.
    blob = json.dumps(usage)
    for forbidden in ("user_id", "secret", "order_id", "xyz", "X-User-Id"):
        assert forbidden not in blob, f"PII leaked into usage body: {forbidden}"


def test_middleware_counts_route_template_not_raw_path(client_authed):
    """The api-hit counter keys by the matched route TEMPLATE, not the raw path."""
    client_authed.get("/engine-diagnostics")

    body = client_authed.get("/engine-diagnostics").json()
    api_hits = body["usage"]["api_hits"]
    # The template key is present (a real registered route path).
    assert "/engine-diagnostics" in api_hits
    assert api_hits["/engine-diagnostics"] >= 1


# --------------------------------------------------------------------------- #
# (d) WIRING — usage subsystem present + fail-soft in /engine-diagnostics
# --------------------------------------------------------------------------- #


def test_usage_subsystem_present_and_shaped(client_authed):
    body = client_authed.get("/engine-diagnostics").json()

    assert "usage" in body
    usage = body["usage"]
    for k in (
        "api_hits",
        "strategy_swaps",
        "panic_sells",
        "kill_switch_resets",
        "force_cycles",
        "hitl_approvals",
        "scans_run",
        "round_tables_run",
        "orders_submitted",
        "consensus_outcomes",
    ):
        assert k in usage, f"usage missing {k}"


def test_usage_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_usage", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["usage"] == {"_error": "RuntimeError"}
    # A sibling subsystem is unaffected.
    assert "_error" not in body["process"]


def test_usage_reads_existing_counters_not_reinstrumented(client_authed, monkeypatch):
    """scans_run / round_tables_run / consensus_outcomes / orders_submitted are READ
    from the already-shipped counters — not a second, parallel instrument."""
    from core.engine import order_executor as oe
    from core.round_table import runner as rt_runner

    oe.reset_exec_counters()
    rt_runner.reset_decision_counters()

    # Bump the EXISTING execution + decision counters directly.
    oe._bump_exec("submit_ok")
    rt_runner._bump_run()

    body = client_authed.get("/engine-diagnostics").json()
    usage = body["usage"]
    assert usage["orders_submitted"] == 1
    assert usage["round_tables_run"] == 1
    assert isinstance(usage["consensus_outcomes"], dict)
