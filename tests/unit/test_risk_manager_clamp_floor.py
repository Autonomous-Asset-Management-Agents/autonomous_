# tests/unit/test_risk_manager_clamp_floor.py
"""#2960 — the total-exposure clamp must not buy headroom residues as dust.

The cap CLAMPS an order to the remaining headroom (risk_manager 5b). Residues
between Alpaca's $1 technical minimum (MIN_ORDER_VALUE_USD, #2784 — untouched
here) and a few dollars were bought as standalone dust positions (live: DD
0.020727 shares ≈ $2.90, EL 0.014587 ≈ $1.27).

ADR-R12 contract pinned here:
- the floor applies ONLY inside the clamp branch — a small account's normal
  cash-/pct-bound order never reaches it (exactly the #2784 objection to a
  flat MIN_ORDER_VALUE_USD raise)
- default 0.0 = OFF = today's behavior byte-identical
"""

from unittest.mock import MagicMock, patch

import allure
import pytest

from core.risk_manager import RiskManager


def _position(market_value: float):
    p = MagicMock()
    p.market_value = market_value
    return p


@pytest.fixture()
def rm_with_headroom():
    """total_capital 100k, MAX_TOTAL_EXPOSURE_PCT 0.9 → cap $90,000.
    Book at $89,997 → $3 headroom: any BUY gets clamped to a $3 residue."""
    client = MagicMock()
    client.get_all_positions.return_value = [_position(89_997.0)]
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(client=client, total_capital=100_000.0)


def _size(rm, floor, price=100.0, cash=1_000_000.0, exposure_pct=0.9):
    trace: dict = {}
    with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 1e12), patch(
        "config.MAX_TOTAL_EXPOSURE_PCT", exposure_pct
    ), patch("config.EXPOSURE_CLAMP_MIN_NOTIONAL_USD", floor):
        size = rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            market_data={"vix": 15.0},
            current_price=price,
            account_cash=cash,
            conviction_score=0.5,
            sizing_trace=trace,
        )
    return size, trace


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestExposureClampEntryFloor:
    def test_clamp_residue_below_floor_is_zeroed(self, rm_with_headroom):
        size, trace = _size(rm_with_headroom, floor=5.0)
        assert size == 0.0, "a $3 clamp residue must not become a dust position"
        assert trace.get("zero_reason") == "exposure_clamp_entry_floor"

    def test_floor_zero_keeps_todays_behavior(self, rm_with_headroom):
        # Default OFF: the residue is still bought — pins the rollback story.
        size, trace = _size(rm_with_headroom, floor=0.0)
        assert size == pytest.approx(
            0.03, rel=1e-6
        ), "floor=0.0 must keep today's behavior byte-identical"
        assert trace.get("zero_reason") is None

    def test_clamp_residue_above_floor_still_buys(self):
        # $60 headroom, floor $50 → clamped order survives (0.6 shares).
        client = MagicMock()
        client.get_all_positions.return_value = [_position(89_940.0)]
        with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
            rm = RiskManager(client=client, total_capital=100_000.0)
        size, trace = _size(rm, floor=50.0)
        assert size == pytest.approx(0.6, rel=1e-6)
        assert trace.get("zero_reason") is None

    def test_floor_never_touches_unclamped_small_account_orders(self):
        # The #2784 scenario: an empty book (cap does not bind) and a
        # cash-bound ~$30 order on a funded small account. Even a $50 floor
        # must NOT zero it — the floor lives ONLY in the clamp branch.
        client = MagicMock()
        client.get_all_positions.return_value = []
        with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
            rm = RiskManager(client=client, total_capital=300.0)
        size, trace = _size(rm, floor=50.0, cash=300.0)
        assert size > 0.0, (
            "a normal small-account order must never be zeroed by the "
            "clamp floor — that was the #2721/#2784 failure mode"
        )
        assert trace.get("zero_reason") is None


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestFallbackLogging:
    def test_garbage_config_warns_and_disables_floor(self, rm_with_headroom, caplog):
        """Review apeldorn (PR #2961): the config-read fallback must log at
        WARNING, never swallow silently (CLAUDE.md §5.6). Garbage config →
        floor OFF (today's behavior) + a visible WARNING."""
        import logging as _logging

        with caplog.at_level(_logging.WARNING):
            size, trace = _size(rm_with_headroom, floor="not-a-number")
        assert size == pytest.approx(
            0.03, rel=1e-6
        ), "unreadable floor must fail-safe to OFF (residue bought as today)"
        assert any(
            r.levelno == _logging.WARNING
            and "EXPOSURE_CLAMP_MIN_NOTIONAL_USD" in r.getMessage()
            for r in caplog.records
        ), "the fallback must be visible at WARNING (§5.6)"
