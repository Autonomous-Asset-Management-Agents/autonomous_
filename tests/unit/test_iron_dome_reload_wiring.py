# ADR-SEC-06 (#1583) §5a — endpoint -> reload wiring. TDD RED first.
# After an admin policy change is persisted, the running ComplianceGuardian + RiskManagers
# must pick it up LIVE (reload_policy) — no restart. This closes the loop:
# write (#1595) -> audit (#1597) -> persist -> reload (#1596) -> immediately effective.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app
from core.governance.iron_dome_admin_auth import require_iron_dome_admin


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[require_iron_dome_admin] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@patch("core.engine.api_routes.engine")
@patch("core.engine.api_routes._save_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes.record_iron_dome_policy_change", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_endpoint_reloads_running_guardians_live(
    mock_load, mock_record, mock_save, mock_engine, client_authed
):
    mock_load.return_value = {}
    guardian = MagicMock()
    risk = MagicMock()
    mock_engine.compliance_guardian = guardian
    mock_engine.live_risk_manager = risk
    mock_engine.sim_risk_manager = None  # not initialised -> skipped, no crash

    r = client_authed.post("/api/admin/iron-dome-policy", json={"max_daily_trades": 5})

    assert r.status_code == 200
    guardian.reload_policy.assert_called_once()
    risk.reload_policy.assert_called_once()
    # the live reload uses the effective (clamped) policy that was persisted
    assert guardian.reload_policy.call_args.args[0]["max_daily_trades"] == 5


@patch("core.engine.api_routes.engine", None)
@patch("core.engine.api_routes._save_iron_dome_policy", new_callable=AsyncMock)
@patch("core.engine.api_routes.record_iron_dome_policy_change", new_callable=AsyncMock)
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_endpoint_survives_when_engine_not_started(
    mock_load, mock_record, mock_save, client_authed
):
    # engine is None during startup — the write must still succeed (reload is best-effort).
    mock_load.return_value = {}
    r = client_authed.post("/api/admin/iron-dome-policy", json={"max_daily_trades": 5})
    assert r.status_code == 200
