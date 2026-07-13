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

import allure
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


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
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


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
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


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
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

    @pytest.mark.anyio
    async def test_sell_still_blocked_by_pdt(self):
        """#2031a scope-lock: PDT stays direction-agnostic (blocks SELL too) until
        #1994 provides a cross-day guard. A same-day round-trip is a FINRA day trade,
        so exempting SELL from PDT without cross-day info could breach the 3/5d rule."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(day_trades_last_5d=3)
        result = await gk.check("AAPL", 0.1, ctx)  # SELL — must STILL be blocked
        assert result.approved is False
        assert "PDTLimit" in result.reason


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
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
    async def test_buy_blocked_when_concentration_at_limit(self):
        """25% exakt = Grenzwert → VETOED (>= konsistent mit Sektorlimit)."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"NVDA": 0.25})
        result = await gk.check("NVDA", 0.8, ctx)
        assert (
            result.approved is False
        ), "BUY must be blocked when symbol is at concentration limit"
        assert "ConcentrationLimit" in result.reason


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
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

    @pytest.mark.anyio
    async def test_sell_passes_when_daily_limit_reached(self):
        """#2031: a risk-reducing SELL must NOT be blocked by DailyLimit — mirrors
        the concentration/sector/lock SELL-exemption. 50/50 + score 0.1 → APPROVED."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(current_daily_trades=50, max_daily_trades=50)
        result = await gk.check("AAPL", 0.1, ctx)  # 0.1 < 0.35 = SELL
        assert result.approved is True, (
            "SELL must pass even at the daily-trade limit — blocking a "
            "risk-reducing exit is the #2031 defect."
        )

    @pytest.mark.anyio
    async def test_hold_passes_when_daily_limit_reached(self):
        """HOLD (score in [0.35, 0.65]) must not be blocked by DailyLimit."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(current_daily_trades=50, max_daily_trades=50)
        result = await gk.check("AAPL", 0.5, ctx)
        assert result.approved is True


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGatekeeperPositionLock:
    @pytest.mark.anyio
    async def test_buy_blocked_when_position_locked(self):
        """
        Position Lock Guard blocks BUY signals.
        Given: position_locked=True AND score=0.9 (BUY)
        Then:  VETOED mit 'PositionLocked'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(position_locked=True)
        result = await gk.check("AAPL", 0.9, ctx)  # 0.9 > 0.65 = BUY
        assert result.approved is False
        assert "PositionLocked" in result.reason

    @pytest.mark.anyio
    async def test_sell_passes_when_position_locked(self):
        """
        SELL-Lockout regression test for position_locked.

        Given: position_locked=True (in-flight order)
        When:  SELL signal (score=0.1) — risk-reducing
        Then:  APPROVED — the lock must not prevent position reduction.

        Without this, a single partial fill permanently freezes the position.
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(position_locked=True)
        result = await gk.check("AAPL", 0.1, ctx)  # 0.1 < 0.35 = SELL
        assert result.approved is True, (
            "SELL must pass even when position is locked. "
            "A lock on BUY should never freeze a risk-reducing SELL."
        )


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGatekeeperSectorConcentrationLimit:
    """
    Sector concentration limit tests.

    Critical invariants — any regression in these means the bot cannot
    reduce its own positions (SELL-lockout) or silently accumulates
    unlimited exposure in unmapped symbols (Unknown sink).

    Threshold alignment (must match runner.py _score_to_signal):
        score > 0.65  → BUY  (sector limit applies)
        score < 0.35  → SELL (sector limit NEVER applies)
        0.35–0.65    → HOLD (sector limit NEVER applies)
    """

    @pytest.mark.anyio
    async def test_buy_blocked_when_sector_at_limit(self):
        """
        Given: Technology sector is already at 30% (= limit)
        When:  BUY signal (score=0.9) for AAPL (Technology)
        Then:  VETOED with 'SectorConcentrationLimit'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(
            sector_weights={"Technology": 0.30},
            symbol_sector_map={"AAPL": "Technology"},
        )
        result = await gk.check("AAPL", 0.9, ctx)  # 0.9 > 0.65 = BUY
        assert result.approved is False, "BUY must be blocked when sector is at limit"
        assert "SectorConcentrationLimit" in result.reason

    @pytest.mark.anyio
    async def test_sell_passes_when_sector_over_limit(self):
        """
        SELL-Lockout regression test — this is the critical case.

        Given: Technology sector is at 35% (over limit)
        When:  SELL signal (score=0.1) for AAPL (Technology)
        Then:  APPROVED — bot must be able to reduce its own risk

        A VETO here would make the position permanently illiquid.
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(
            sector_weights={"Technology": 0.35},
            symbol_sector_map={"AAPL": "Technology"},
        )
        result = await gk.check("AAPL", 0.1, ctx)  # 0.1 < 0.35 = SELL
        assert result.approved is True, (
            "SELL signal must always pass even when sector is over limit. "
            "Blocking SELL makes the position permanently illiquid."
        )

    @pytest.mark.anyio
    async def test_hold_passes_when_sector_over_limit(self):
        """HOLD signal (score ≈ 0.5) must not be blocked by sector limit."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(
            sector_weights={"Technology": 0.35},
            symbol_sector_map={"AAPL": "Technology"},
        )
        result = await gk.check("AAPL", 0.5, ctx)  # 0.5 in [0.35, 0.65] = HOLD
        assert result.approved is True, "HOLD must not be blocked by sector limit"

    @pytest.mark.anyio
    async def test_unknown_sector_is_capped(self):
        """
        Unknown sector must not be a free bypass.

        Given: 'Unknown' sector accumulated to 31% (unmapped symbols)
        When:  BUY signal for XYZ (no mapping in symbol_sector_map)
        Then:  VETOED — Unknown is treated as its own capped bucket

        Without this test, a misconfigured sector map allows unlimited
        accumulation of unmapped positions.
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(
            sector_weights={"Unknown": 0.31},
            symbol_sector_map={},  # No mapping → falls into "Unknown"
        )
        result = await gk.check("XYZ", 0.9, ctx)  # 0.9 > 0.65 = BUY
        assert (
            result.approved is False
        ), "BUY for unmapped symbol must be blocked when 'Unknown' bucket is full"
        assert "SectorConcentrationLimit" in result.reason

    @pytest.mark.anyio
    async def test_buy_passes_when_sector_below_limit(self):
        """Sector at 20% (below 30%) must allow BUY."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(
            sector_weights={"Healthcare": 0.20},
            symbol_sector_map={"JNJ": "Healthcare"},
        )
        result = await gk.check("JNJ", 0.8, ctx)  # 0.8 > 0.65 = BUY, sector at 20%
        assert result.approved is True


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGatekeeperSymbolConcentrationLimit:
    """
    Per-symbol concentration limit tests (gatekeeper.py:95-112).

    Mirrors TestGatekeeperSectorConcentrationLimit — both use the same
    score > 0.65 BUY guard. The reviewer (PR #840 deep-review §4) identified
    that the per-symbol check was SELL-deaf while the new sector check was
    correctly score-gated. Both must be consistent.
    """

    @pytest.mark.anyio
    async def test_buy_blocked_when_symbol_over_limit(self):
        """
        Given: AAPL is already at 30% of the portfolio (= CONCENTRATION_LIMIT)
        When:  BUY signal (score=0.9)
        Then:  VETOED with 'ConcentrationLimit'
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"AAPL": 0.30})
        result = await gk.check("AAPL", 0.9, ctx)  # 0.9 > 0.65 = BUY
        assert (
            result.approved is False
        ), "BUY must be blocked when symbol is at concentration limit"
        assert "ConcentrationLimit" in result.reason

    @pytest.mark.anyio
    async def test_sell_passes_when_symbol_over_limit(self):
        """
        Per-symbol SELL-Lockout regression test — the critical mirror of the sector test.

        Given: AAPL is at 35% of portfolio (over the 25% limit)
        When:  SELL signal (score=0.1)
        Then:  APPROVED — position must be reducible even when over limit

        A VETO here creates a permanent illiquid trap: the bot accumulates
        above the limit (via market moves), then cannot exit. This was the
        exact failure mode the per-symbol check created before the score-gate.
        """
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"AAPL": 0.35})
        result = await gk.check("AAPL", 0.1, ctx)  # 0.1 < 0.35 = SELL
        assert result.approved is True, (
            "SELL must always pass even when symbol is over concentration limit. "
            "Blocking SELL creates a permanent illiquid trap."
        )

    @pytest.mark.anyio
    async def test_hold_passes_when_symbol_over_limit(self):
        """HOLD signal must not be blocked by the per-symbol concentration check."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"AAPL": 0.35})
        result = await gk.check("AAPL", 0.5, ctx)  # 0.5 in [0.35, 0.65] = HOLD
        assert (
            result.approved is True
        ), "HOLD must not be blocked by concentration limit"

    @pytest.mark.anyio
    async def test_buy_passes_when_symbol_below_limit(self):
        """Symbol at 10% (below 25% limit) must allow BUY."""
        from core.round_table.gatekeeper import ComplianceGatekeeper

        gk = ComplianceGatekeeper()
        ctx = make_context(symbol_weights={"AAPL": 0.10})
        result = await gk.check("AAPL", 0.8, ctx)  # 0.8 > 0.65 = BUY, symbol at 10%
        assert result.approved is True
