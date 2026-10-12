# UXC-1 S3 (#3156, Epic #3151) — POST /api/settings-deviation-ack (Plan Rev. 2
# Option A + Rev. 3 Backend-Komposition): die keep/reset-Entscheidung des Operators
# beim Live-Wechsel landet auf der Art-14-Kette, BEVOR liveEnable läuft.
# decision="reset" appendet Ack- UND Settings-Event in EINEM Request (kein Split-Brain).

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_noop_async = AsyncMock()


@pytest.fixture
def client(monkeypatch):
    from core.engine import api_routes

    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    monkeypatch.setattr(
        api_routes, "_enforce_replay_distinct_nonce", _noop_async, raising=False
    )
    yield TestClient(api_routes.app)
    api_routes.app.dependency_overrides.clear()


@pytest.fixture
def chain(monkeypatch):
    """Sammelt beide WORM-Writer-Aufrufe in Reihenfolge."""
    from core import hitl_gate

    calls = []

    async def ack(**kw):
        calls.append(("ack", kw))

    async def settings(**kw):
        calls.append(("settings", kw))

    monkeypatch.setattr(
        hitl_gate, "log_settings_deviation_ack", AsyncMock(side_effect=ack)
    )
    monkeypatch.setattr(
        hitl_gate,
        "log_settings_change_event",
        AsyncMock(side_effect=settings),
        raising=False,
    )
    return calls


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace())


def _body(decision="keep", **kw):
    return {
        "decision": decision,
        "deviations": [
            {"key": "VOL_TARGET_DAILY_VOL", "current": "0.02", "default": "0.015"}
        ],
        "nonce": "n-ack-1",
        "switch_id": "sw-1",
        **kw,
    }


def test_deviation_ack_keep_single_record(client, chain):
    r = client.post("/api/settings-deviation-ack", json=_body("keep"))
    assert r.status_code == 201
    assert [c[0] for c in chain] == [
        "ack"
    ], "keep ⇒ genau EIN Event, kein Settings-Event"
    assert chain[0][1]["decision"] == "keep"
    assert chain[0][1]["strict"] is True
    assert chain[0][1]["switch_id"] == "sw-1"


def test_deviation_ack_nonce_replay_409(client, chain, monkeypatch):
    from fastapi import HTTPException

    from core.engine import api_routes

    async def replay(scope, nonce):
        assert scope == "settings_deviation_ack"
        raise HTTPException(status_code=409, detail="nonce bereits auf der Kette")

    monkeypatch.setattr(
        api_routes,
        "_enforce_replay_distinct_nonce",
        AsyncMock(
            side_effect=replay
        ),  # §5.2: AsyncMock, nie ein nackter async-def-Stub
    )
    r = client.post("/api/settings-deviation-ack", json=_body("keep"))
    assert r.status_code == 409
    assert chain == [], "409 VOR jedem Append"


def test_deviation_ack_empty_deviations_422(client, chain):
    r = client.post(
        "/api/settings-deviation-ack",
        json={"decision": "keep", "deviations": [], "nonce": "n-x"},
    )
    assert r.status_code == 422
    assert chain == []


def test_deviation_ack_invalid_decision_422(client, chain):
    r = client.post("/api/settings-deviation-ack", json=_body("vielleicht"))
    assert r.status_code == 422
    assert chain == []


def test_deviation_ack_worm_failure_fails_closed(client, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_settings_deviation_ack",
        AsyncMock(side_effect=RuntimeError("Kette nicht beschreibbar")),
    )
    with pytest.raises(RuntimeError):
        client.post("/api/settings-deviation-ack", json=_body("keep"))


def test_deviation_ack_reset_two_records_in_order(client, chain):
    """Rev. 3: decision="reset" ⇒ 201 UND beide Events (Reihenfolge ack → settings)."""
    pytest.importorskip("core.trading_settings")  # scharf nach S2-Merge (#3170)
    r = client.post("/api/settings-deviation-ack", json=_body("reset"))
    assert r.status_code == 201
    assert [c[0] for c in chain] == ["ack", "settings"]
    assert chain[0][1]["decision"] == "reset"
    changes = chain[1][1]["changes"]
    assert any(c["key"] == "VOL_TARGET_DAILY_VOL" for c in changes)
    assert chain[1][1]["strict"] is True


def test_deviation_ack_reset_guardrail_key_422(client, chain):
    """Ein Leitplanken-Key in den Deviations wird beim Reset abgelehnt (Registry)."""
    pytest.importorskip("core.trading_settings")
    r = client.post(
        "/api/settings-deviation-ack",
        json={
            "decision": "reset",
            "deviations": [
                {"key": "HARD_STOP_LOSS_PCT", "current": "-3.0", "default": "-8.0"}
            ],
            "nonce": "n-g",
        },
    )
    assert r.status_code == 422


def test_event_serialisation_str_only():
    from core.round_table.senate_log import (
        SettingsDeviationAckEvent,
        _hitl_event_to_dict,
    )

    ev = SettingsDeviationAckEvent(
        timestamp="2026-09-02T19:00:00+00:00",
        actor="operator",
        decision="keep",
        deviations='[{"current":"0.02","default":"0.015","key":"VOL_TARGET_DAILY_VOL"}]',
        nonce="n-ack-1",
        switch_id="sw-1",
    )
    d = _hitl_event_to_dict(ev)
    assert d["event_type"] == "settings_deviation_ack"
    assert all(isinstance(v, str) for v in d.values())
    assert None not in d.values()


async def test_logger_rejects_empty_deviations():
    from core import hitl_gate

    with pytest.raises(ValueError):
        await hitl_gate.log_settings_deviation_ack(
            decision="keep", deviations=[], nonce="n", switch_id="s"
        )
