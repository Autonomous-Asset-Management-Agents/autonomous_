# r6/12 — KILL-SWITCH OBSERVABILITY (#… killswitch-observability). TDD RED first.
#
# Observability/audit MUST NEVER interfere with the kill switch's core function: a trip
# still halts and a reset still resets even if the audit/logging raises. These tests pin
# both the new surface (last_trip / status / /reset-kill-switch fields) AND the safety
# invariant (an audit failure never propagates into trip()/reset()).

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app
from core.kill_switch import KillSwitch


@pytest.fixture
def ks():
    # KillSwitch is a process singleton; reset its observable state per test so
    # order-independence holds. Redis is absent in unit tests → local state only.
    switch = KillSwitch()
    switch.reset()
    switch._last_trip = None
    yield switch
    switch.reset()
    switch._last_trip = None


def test_trip_sets_last_trip_and_status_reflects_it(ks):
    assert ks.last_trip() is None
    assert ks.status()["halted"] is False

    ks.trip("CycleWatchdog: 5 consecutive cycles without Round Table completions")

    lt = ks.last_trip()
    assert lt is not None
    assert lt["reason"].startswith("CycleWatchdog: 5 consecutive")
    assert lt["scope"] == "GLOBALLY"
    assert "at" in lt and lt["at"]
    assert lt["user_id"] is None

    status = ks.status()
    assert status["halted"] is True
    assert status["last_trip"]["reason"] == lt["reason"]


def test_reset_clears_last_trip(ks):
    ks.trip("panic-sell: operator emergency halt")
    assert ks.last_trip() is not None

    ks.reset()

    assert ks.is_halted() is False
    assert ks.last_trip() is None
    assert ks.status()["last_trip"] is None


def test_audit_failure_never_propagates_into_trip_or_reset(ks, monkeypatch):
    # SAFETY INVARIANT: if the audit sink raises, trip() must still halt and
    # reset() must still reset. Force _audit to blow up and assert both hold.
    def boom(*_a, **_k):
        raise RuntimeError("audit sink exploded")

    monkeypatch.setattr("core.kill_switch._audit", boom)

    ks.trip("boom-trip: audit is broken but the halt must still land")
    assert ks.is_halted() is True  # halt survived a failing audit

    ks.reset()
    assert ks.is_halted() is False  # reset survived a failing audit


@pytest.fixture
def client():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_reset_endpoint_returns_last_trip_reason(client, ks):
    ks.trip("CycleWatchdog: watchdog tripped — reset should surface this reason")
    r = client.post("/reset-kill-switch")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["was_halted"] is True
    assert body["last_trip_reason"].startswith("CycleWatchdog:")
    assert body["still_halted"] is False
    assert body["retrip_reason"] is None


def test_reset_endpoint_reports_immediate_retrip(client, ks, monkeypatch):
    # The user's exact pain point: a reset that "doesn't stick" because the
    # underlying condition re-trips instantly. is_halted stays True after reset
    # and a fresh last_trip is present → the endpoint must self-explain.
    ks.trip("CycleWatchdog: original trip")

    real_is_halted = KillSwitch.is_halted

    calls = {"n": 0}

    def fake_is_halted(self, user_id=None):
        # First call (the pre-reset was_halted read) is real; after reset we
        # force it to stay halted to simulate an immediate re-trip.
        calls["n"] += 1
        if calls["n"] == 1:
            return real_is_halted(self, user_id)
        return True

    monkeypatch.setattr(KillSwitch, "is_halted", fake_is_halted)
    # Simulate the re-trip having recorded a fresh reason.
    monkeypatch.setattr(
        ks, "last_trip", lambda: {"reason": "CycleWatchdog: re-tripped immediately"}
    )

    r = client.post("/reset-kill-switch")
    assert r.status_code == 200
    body = r.json()
    assert body["still_halted"] is True
    assert body["retrip_reason"] == "CycleWatchdog: re-tripped immediately"
    assert "RE-TRIPPED" in body["message"]
