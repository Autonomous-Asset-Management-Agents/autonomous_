# ADR-SEC-06 (#1583 §1) · #1619 — boot-load: apply the stored policy at engine startup.
# TDD RED first. apply_policy() is the reusable, null-safe applier the boot path uses to
# push the persisted SystemConfig policy into the freshly-created guardians, so an admin's
# change SURVIVES a restart (otherwise the guardian resets to config defaults).

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from core.auth import require_engine_key
from core.engine.api_routes import app
from core.governance.iron_dome_policy import apply_policy


def test_apply_policy_reloads_each_target():
    g1, g2 = MagicMock(), MagicMock()
    apply_policy({"max_daily_trades": 5}, [g1, g2])
    g1.reload_policy.assert_called_once_with({"max_daily_trades": 5})
    g2.reload_policy.assert_called_once_with({"max_daily_trades": 5})


def test_apply_policy_skips_none_targets():
    g = MagicMock()
    apply_policy({"x": 1}, [None, g, None])  # not-yet-created guardians are skipped
    g.reload_policy.assert_called_once()


def test_apply_policy_skips_targets_without_reload_policy():
    class NoReload:
        pass

    # A target lacking reload_policy must be skipped, never raise.
    apply_policy({"x": 1}, [NoReload()])


@patch("core.engine.api_routes.engine")
@patch("core.engine.api_routes._load_iron_dome_policy_value", new_callable=AsyncMock)
def test_start_live_applies_stored_policy_to_guardians(mock_load, mock_engine):
    # Restart scenario: a stored policy must be applied to the guardians when live trading
    # starts, so the admin's runtime change is not lost across a restart.
    app.dependency_overrides[require_engine_key] = lambda: None
    try:
        mock_load.return_value = {"max_daily_trades": 5}
        mock_engine.start_live_strategy.return_value = True
        guardian = MagicMock()
        mock_engine.compliance_guardian = guardian
        mock_engine.live_risk_manager = None
        mock_engine.sim_risk_manager = None
        r = TestClient(app).post("/start-live")
        assert r.status_code == 200
        guardian.reload_policy.assert_called_once_with({"max_daily_trades": 5})
    finally:
        app.dependency_overrides.clear()
