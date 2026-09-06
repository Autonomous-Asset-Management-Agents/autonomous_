# tests/unit/test_heartbeat_status.py
# TDD Red -> Green for #1806 (GTM-1): machine-only periodic heartbeat.
#
# The periodic heartbeat must NOT leak equity/PnL to Slack by default
# (GTM / multi-instance operation). Equity is included ONLY on explicit
# opt-in via config.HEARTBEAT_INCLUDE_EQUITY.
#
# Gherkin:
#   Given: HEARTBEAT_INCLUDE_EQUITY is False (default)
#   When:  the heartbeat status message is built
#   Then:  it reports ACTIVE + Strategy state (+ version) and contains
#          NO equity ("Equity" / "$") and makes NO api.get_account() call
#
#   Given: HEARTBEAT_INCLUDE_EQUITY is True and an account is available
#   When:  the heartbeat status message is built
#   Then:  it contains the equity line
#
# Policy Ref: docs/5_engineering_and_devops/CODING_POLICY.md 5.1 TDD.

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import allure

from core.engine.base import BotEngine


def _make_engine(strategy_running: bool, api) -> BotEngine:
    """Build a bare BotEngine without its heavy __init__.

    _build_heartbeat_status only needs ``self.strategy_running`` and
    ``self.api`` — so we bypass the real constructor (broker clients, ML
    models, agent registry) and inject just those two attributes.
    """
    engine = BotEngine.__new__(BotEngine)
    event = threading.Event()
    if strategy_running:
        event.set()
    engine.strategy_running = event
    engine.api = api
    return engine


@allure.feature("VC-3 Trading & Execution")
@allure.story("Machine-only heartbeat (#1806)")
class TestBuildHeartbeatStatus:
    def test_default_is_machine_only_no_equity(self, monkeypatch):
        """Default mode: ACTIVE + Strategy state, but NO equity/PnL."""
        import config as cfg

        monkeypatch.setattr(cfg, "HEARTBEAT_INCLUDE_EQUITY", False, raising=False)

        api = MagicMock()
        engine = _make_engine(strategy_running=True, api=api)

        status_msg = engine._build_heartbeat_status()

        assert "ACTIVE" in status_msg
        assert "Strategy:" in status_msg
        assert "RUNNING" in status_msg
        # Machine-only: no equity/PnL may leak to Slack.
        assert "Equity" not in status_msg
        assert "$" not in status_msg

    def test_default_mode_makes_no_account_call(self, monkeypatch):
        """Default mode must NOT call api.get_account() (no equity fetch)."""
        import config as cfg

        monkeypatch.setattr(cfg, "HEARTBEAT_INCLUDE_EQUITY", False, raising=False)

        api = MagicMock()
        api.get_account.side_effect = AssertionError(
            "get_account() must not be called in machine-only heartbeat mode"
        )
        engine = _make_engine(strategy_running=False, api=api)

        status_msg = engine._build_heartbeat_status()

        assert "STOPPED" in status_msg
        api.get_account.assert_not_called()

    def test_opt_in_includes_equity(self, monkeypatch):
        """With HEARTBEAT_INCLUDE_EQUITY=True, the equity line is present."""
        import config as cfg

        monkeypatch.setattr(cfg, "HEARTBEAT_INCLUDE_EQUITY", True, raising=False)

        api = MagicMock()
        api.get_account.return_value = MagicMock(equity="12345.67")
        engine = _make_engine(strategy_running=True, api=api)

        status_msg = engine._build_heartbeat_status()

        assert "Equity" in status_msg
        assert "12,345.67" in status_msg
        api.get_account.assert_called_once()
