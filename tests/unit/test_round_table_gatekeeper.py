# tests/unit/test_round_table_gatekeeper.py
# Epic 2.5 / Issue I-2 — TDD Red-Phase
# ComplianceGatekeeper: PDT, Konzentration, Tageslimit, PositionLock
#
# Gherkin (Architect Blueprint):
#   Given: symbol already occupying 26% of portfolio
#   When:  ComplianceGatekeeper.check()
#   Then:  result must be VETOED with reason 'ConcentrationLimit'
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import pytest


def make_context(**kwargs) -> dict:
    """Standard portfolio_context mit sicheren Default-Werten."""
    defaults = {
        "day_trades_last_5d": 0,
        "max_daily_trades": 50,
        "current_daily_trades": 5,
        "symbol_weights": {"AAPL": 0.10},
        "position_locked": False,
    }
    defaults.update(kwargs)
    return defaults


class TestGatekeeperImports:
    def test_gatekeeper_importable(self):
        from core.round_table.gatekeeper import ComplianceGatekeeper  # noqa: F401

        assert ComplianceGatekeeper is not None

    def test_decision_importable(self):
        from core.round_table.gatekeeper import GatekeeperDecision  # noqa: F401

        assert GatekeeperDecision is not None

    def test_decision_has_slots(self):
        from core.round_table.gatekeeper import GatekeeperDecision

        assert hasattr(GatekeeperDecision, "__slots__")


class TestGatekeeperHappyPath:
    @pytest.mark.anyio
    async def test_all_checks_pass(self):
        """
        Given: Sauberes Portfolio (wenige Trades, geringe Konzentration, kein Lock)
        When:  check()
        Then:  approved=True
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context()
        result = await gk.check("AAPL", 0.7, ctx)
        assert result.approved is True
        assert result.symbol == "AAPL"


class TestGatekeeperPDTVeto:
    @pytest.mark.anyio
    async def test_pdt_limit_vetoed(self):
        """
        Given: 3 Day Trades in 5 Tagen (PDT-Limit erreicht)
        When:  check()
        Then:  approved=False, reason enthält 'PDTLimit'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(day_trades_last_5d=3)
        result = await gk.check("AAPL", 0.7, ctx)
        assert result.approved is False
        assert "PDTLimit" in result.reason, f"Erwartet 'PDTLimit' in: {result.reason}"

    @pytest.mark.anyio
    async def test_pdt_below_limit_passes(self):
        """2 Day Trades → noch unter Limit → approved."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(day_trades_last_5d=2)
        result = await gk.check("MSFT", 0.6, ctx)
        assert result.approved is True


class TestGatekeeperConcentrationVeto:
    @pytest.mark.anyio
    async def test_concentration_limit_vetoed(self):
        """
        Gherkin (Architect):
          Given: symbol already occupying 26% of portfolio
          When:  ComplianceGatekeeper.check()
          Then:  VETOED with reason 'ConcentrationLimit'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"TSLA": 0.26})  # > 25% Limit
        result = await gk.check("TSLA", 0.8, ctx)
        assert result.approved is False
        assert (
            "ConcentrationLimit" in result.reason
        ), f"Erwartet 'ConcentrationLimit' in: {result.reason}"

    @pytest.mark.anyio
    async def test_concentration_at_boundary_passes(self):
        """25% exakt = Grenzwert → passes (>, nicht >=)."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"NVDA": 0.25})
        result = await gk.check("NVDA", 0.8, ctx)
        assert result.approved is True


class TestGatekeeperDailyLimitVeto:
    @pytest.mark.anyio
    async def test_daily_limit_vetoed(self):
        """50/50 Trades → DailyLimit VETO."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(current_daily_trades=50, max_daily_trades=50)
        result = await gk.check("AAPL", 0.7, ctx)
        assert result.approved is False
        assert "DailyLimit" in result.reason


class TestGatekeeperPositionLock:
    @pytest.mark.anyio
    async def test_position_locked_vetoed(self):
        """
        Partial Fill / Position Lock Guard.
        Given: position_locked=True (laufender Swap oder offene Position)
        Then:  VETOED mit 'PositionLocked'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(position_locked=True)
        result = await gk.check("AAPL", 0.9, ctx)
        assert result.approved is False
        assert "PositionLocked" in result.reason
