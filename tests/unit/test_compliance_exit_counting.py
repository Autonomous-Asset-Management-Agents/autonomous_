# tests/unit/test_compliance_exit_counting.py
# Fix B — a risk-reducing exit must not CONSUME the daily buy budget.
#
# check_trade() already EXEMPTS a risk-reducing exit from being BLOCKED by the daily cap
# when COMPLIANCE_ALLOW_RISK_REDUCING_EXITS is on (compliance.py:478-489). But record_trade()
# was side-agnostic and always incremented, so an exempt exit still spent a buy slot. Live
# (2026-07-28): a rotation flushed 8 held names → 8 of the 10 daily slots consumed → after 2
# re-buys the cap was hit → every further BUY blocked:daily_limit → book frozen in cash.
#
# This makes record_trade() honour the SAME predicate the block already uses — the counter
# and the block now agree. Symmetric with test_compliance_exit_exemption.py (the BLOCK side).
from __future__ import annotations

import pytest

from core.compliance import ComplianceGuardian


def _order(side="sell", qty=10.0, held=10.0, price=100.0):
    o = {
        "symbol": "NVDA",
        "side": side,
        "quantity": qty,
        "price": price,
        "asset_class": "us_equity",
    }
    if held is not None:
        o["held_qty"] = held
    return o


@pytest.fixture
def guardian():
    g = ComplianceGuardian()
    g.daily_trades = 4  # some spent budget, well below the cap
    return g


def _flag(monkeypatch, value):
    import config

    monkeypatch.setattr(
        config.get_config(),
        "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
        value,
        raising=False,
    )


class TestExitDoesNotConsumeBudget:
    def test_risk_reducing_exit_does_not_increment(self, guardian, monkeypatch):
        """flag ON: a held-bounded SELL is exempt from the block AND from the counter."""
        _flag(monkeypatch, True)
        before = guardian.daily_trades
        guardian.record_trade(_order(side="sell", qty=10.0, held=10.0))
        assert guardian.daily_trades == before  # the exit did NOT spend a buy slot

    def test_partial_exit_does_not_increment(self, guardian, monkeypatch):
        """A partial trim (qty < held) is still risk-reducing → not counted."""
        _flag(monkeypatch, True)
        before = guardian.daily_trades
        guardian.record_trade(_order(side="sell", qty=3.0, held=10.0))
        assert guardian.daily_trades == before


class TestRiskAddingStillCounts:
    def test_buy_still_increments(self, guardian, monkeypatch):
        _flag(monkeypatch, True)
        before = guardian.daily_trades
        guardian.record_trade(_order(side="buy", qty=5.0, held=0.0))
        assert guardian.daily_trades == before + 1

    def test_short_flip_still_increments(self, guardian, monkeypatch):
        """Selling MORE than held flips toward short → ADDS risk → must still count."""
        _flag(monkeypatch, True)
        before = guardian.daily_trades
        guardian.record_trade(_order(side="sell", qty=25.0, held=10.0))
        assert guardian.daily_trades == before + 1

    def test_no_order_still_increments(self, guardian):
        """Backward-compatible: record_trade() with no order → increments (unchanged)."""
        before = guardian.daily_trades
        guardian.record_trade()
        assert guardian.daily_trades == before + 1


class TestFailClosed:
    def test_flag_off_exit_still_increments(self, guardian, monkeypatch):
        """Rollback: flag OFF → the exit consumes a slot, exactly as before this change."""
        _flag(monkeypatch, False)
        before = guardian.daily_trades
        guardian.record_trade(_order(side="sell", qty=10.0, held=10.0))
        assert guardian.daily_trades == before + 1

    def test_malformed_order_still_increments(self, guardian, monkeypatch):
        """Fail-closed: an order missing held_qty is NOT treated as an exempt exit."""
        _flag(monkeypatch, True)
        before = guardian.daily_trades
        guardian.record_trade({"side": "sell", "quantity": 10.0})  # no held_qty
        assert guardian.daily_trades == before + 1
