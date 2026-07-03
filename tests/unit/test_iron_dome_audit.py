# ADR-SEC-06 (#1583) · sub-issue #1597 — WORM audit (Art-14 wiring). TDD RED first.
# Every admin Iron Dome policy change is recorded on the EU AI Act Art-14 hash chain
# (the same tamper-evident recorder the HITL policy uses) BEFORE the mutation, with
# strict=True so a failed audit re-raises and an unaudited compliance change is refused.

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app
from core.governance.iron_dome_admin_auth import require_iron_dome_admin
from core.governance.iron_dome_audit import record_iron_dome_policy_change


def test_record_calls_log_policy_event_strict():
    # WORM guarantee: the Art-14 recorder is invoked with strict=True (a failed write
    # re-raises), with the old→new transition and an actor.
    seen = {}

    async def fake_log(old, new, actor, *, strict):
        seen.update(old=old, new=new, actor=actor, strict=strict)

    with patch("core.governance.iron_dome_audit.log_policy_event", fake_log), patch(
        "core.governance.iron_dome_audit._write_audit_mirror", new_callable=AsyncMock
    ):
        asyncio.run(
            record_iron_dome_policy_change(
                {"max_daily_trades": 10}, {"max_daily_trades": 5}
            )
        )
    assert seen["strict"] is True
    assert seen["old"] == {"max_daily_trades": 10}
    assert seen["new"] == {"max_daily_trades": 5}


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[require_iron_dome_admin] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@patch("core.engine.api_routes._save_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes.record_iron_dome_policy_change", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_endpoint_audits_before_save(mock_load, mock_record, mock_save, client_authed):
    mock_load.return_value = {"max_daily_trades": 10}
    r = client_authed.post("/api/admin/iron-dome-policy", json={"max_daily_trades": 5})
    assert r.status_code == 200
    mock_record.assert_awaited_once()
    old_arg = mock_record.call_args.args[0]
    new_arg = mock_record.call_args.args[1]
    assert old_arg == {"max_daily_trades": 10}
    assert new_arg["max_daily_trades"] == 5
    mock_save.assert_awaited_once()


@patch("core.engine.api_routes._save_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes.record_iron_dome_policy_change", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_endpoint_refuses_save_when_audit_fails(
    mock_load, mock_record, mock_save, client_authed
):
    # WORM: a failed audit must block the persistence — no unaudited compliance change.
    mock_load.return_value = {}
    mock_record.side_effect = RuntimeError("audit chain unavailable")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/admin/iron-dome-policy", json={"max_daily_trades": 5})
    assert r.status_code == 500
    mock_save.assert_not_awaited()
