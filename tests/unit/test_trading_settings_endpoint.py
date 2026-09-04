# UXC-1 S2 (#3155) — GET/POST /api/trading-settings: Vertrag des Endpunkts.
# Muster test_portfolio_shape_endpoint_3132.py: Audit VOR Änderung, strict=True,
# No-op ohne Kette, Replay-Nonce 409, unbekannter Key 422, GET = Engine-Ist.

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
def worm(monkeypatch):
    from core import hitl_gate

    written = []

    async def side_effect(**kw):
        written.append(kw)

    monkeypatch.setattr(
        hitl_gate, "log_settings_change_event", AsyncMock(side_effect=side_effect)
    )
    return written


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace())


def _body(**settings):
    return {"settings": settings, "acknowledgment": "bestaetigt", "nonce": "n-1"}


def test_post_change_writes_worm_before_success(client, worm):
    r = client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=5.0))
    assert r.status_code == 201
    body = r.json()
    assert body["applied"]["STOP_LOSS_PCT"] == "5.0"
    assert body["changes"] == [{"key": "STOP_LOSS_PCT", "from": "7.0", "to": "5.0"}]
    assert len(worm) == 1
    assert worm[0]["strict"] is True
    assert worm[0]["nonce"] == "n-1"


def test_post_failed_worm_write_blocks_success(client, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_settings_change_event",
        AsyncMock(side_effect=RuntimeError("chain kaputt")),
    )
    with pytest.raises(RuntimeError):
        client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=5.0))


def test_post_noop_writes_nothing(client, worm):
    r = client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=7.0))
    assert r.status_code == 201
    assert r.json()["changes"] == []
    assert worm == [], "Nicht-Änderung darf die Kette nicht beschreiben"


def test_post_values_are_clamped_and_reported(client, worm):
    r = client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=15.0))
    assert r.status_code == 201
    applied = r.json()["applied"]
    assert float(applied["STOP_LOSS_PCT"]) < 8.0, "strikt innerhalb HARD_STOP_LOSS_PCT"


def test_post_unknown_or_guardrail_key_is_422(client, worm):
    r = client.post("/api/trading-settings", json=_body(HARD_STOP_LOSS_PCT=-3.0))
    assert r.status_code == 422
    assert worm == []
    r = client.post("/api/trading-settings", json=_body(VOELLIG_UNBEKANNT=1))
    assert r.status_code == 422


def test_post_replayed_nonce_is_409_before_append(client, worm, monkeypatch):
    from fastapi import HTTPException

    from core.engine import api_routes

    async def replay(scope, nonce):
        raise HTTPException(status_code=409, detail="nonce bereits auf der Kette")

    monkeypatch.setattr(api_routes, "_enforce_replay_distinct_nonce", replay)
    r = client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=5.0))
    assert r.status_code == 409
    assert worm == [], "409 VOR dem Append"


def test_get_returns_engine_ist_bounds_defaults_and_agents(client, monkeypatch):
    import config

    monkeypatch.setattr(
        config, "get_config", lambda: SimpleNamespace(STOP_LOSS_PCT=5.5)
    )
    r = client.get("/api/trading-settings")
    assert r.status_code == 200
    body = r.json()
    # Engine-Ist (env-injizierte Abweichung sichtbar), nie UI-State
    assert body["settings"]["STOP_LOSS_PCT"] == "5.5"
    # Auslieferungs-Defaults + Bounds für S4/S5
    meta = {m["key"]: m for m in body["meta"]}
    assert meta["STOP_LOSS_PCT"]["default"] == "7.0"
    assert meta["ROUND_TABLE_TOP_K_EVAL"]["max"] >= 30
    # read-only Leitplanke sichtbar, aber nicht editierbar (nicht in settings/meta)
    assert body["guardrails"]["HARD_STOP_LOSS_PCT"] == "-8.0"
    assert "HARD_STOP_LOSS_PCT" not in body["settings"]
    # Agents-Vertrag für die S4-Karte
    assert len(body["agents"]) == 11
    ddg = next(a for a in body["agents"] if a["name"] == "DrawdownGuardAgent")
    assert ddg["role"] == "veto_guard"
    assert ddg["role_title"] == "Risk Manager"
    assert body["implied_vol_forecast_enabled"] is True


# ---------------------------------------------------------------------------
# OTel (Owner-Anforderung 02.09., Epic-Leitplanke 7): settings.apply-Span
# ---------------------------------------------------------------------------


@pytest.fixture
def spans(monkeypatch):
    from core.engine import api_routes

    captured = []

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            captured.append((k, v))

    class _Tracer:
        def start_as_current_span(self, name):
            captured.append(("__name__", name))
            return _Span()

    monkeypatch.setattr(api_routes, "_tracer", _Tracer())
    return captured


def test_settings_apply_emits_local_span(client, worm, spans):
    r = client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=5.0))
    assert r.status_code == 201
    assert ("__name__", "settings.apply") in spans
    attrs = dict(s for s in spans if s[0] != "__name__")
    assert attrs.get("nonce") == "n-1"
    assert "STOP_LOSS_PCT" in str(attrs.get("keys", ""))
    # Alt→neu-WERTE gehören NICHT in die Telemetrie (Single Source = WORM)
    assert "5.0" not in str(attrs.values())
    assert "7.0" not in str(attrs.values())


def test_settings_span_absent_on_noop_and_reject(client, worm, spans):
    client.post("/api/trading-settings", json=_body(STOP_LOSS_PCT=7.0))  # no-op
    client.post("/api/trading-settings", json=_body(VOELLIG_UNBEKANNT=1))  # 422
    assert ("__name__", "settings.apply") not in spans
