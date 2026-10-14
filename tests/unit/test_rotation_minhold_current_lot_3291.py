"""#3291 — the rotation min-hold must measure the CURRENT lot, not the oldest buy.

Live churn 2026-09-08: `PortfolioManager.refresh_positions` set
``days_held = now - min(_trade_history[symbol])``. Because ``_trade_history`` keeps 30 days
of buys and is NOT reset on a full exit, a name re-entered within 30 days (sell→rebuy) looked
20+ days old, so the rotation min-hold gate (``days_held >= SMART_EXIT_MIN_HOLD_DAYS``) passed
and the FRESH lot was force-sold at a loss.

The fix (dark flag ``ROTATION_MIN_HOLD_CURRENT_LOT``) stamps a witnessed absent→present entry
and resets it on full exit, so a re-entry's ``days_held`` reflects the current holding. Flag
OFF is byte-identical to the legacy ``min(_trade_history)`` age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import PortfolioManager

_NOW = datetime.now(timezone.utc)


def _pos():
    return [
        {
            "symbol": "AAPL",
            "qty": 10.0,
            "avg_entry_price": 100.0,
            "current_price": 101.0,
            "market_value": 1010.0,
            "unrealized_pl": 10.0,
        }
    ]


def _pm(monkeypatch, *, current_lot: bool):
    import config

    monkeypatch.setattr(
        config.get_config(), "ROTATION_MIN_HOLD_CURRENT_LOT", current_lot, raising=False
    )
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(
        equity=100000.0, account_number="acct-1"
    )
    pm = PortfolioManager(client=client, total_capital=100000.0)
    # An OLD buy sits in the 30-day history — min() would date the position to 25 days.
    pm._trade_history["AAPL"] = [_NOW - timedelta(days=25)]
    return pm, client


def _reenter(pm, client):
    """refresh present → absent (full exit) → present (re-entry)."""
    client.get_all_positions.return_value = _pos()
    pm.refresh_positions()  # 1: first refresh (rehydrate → history age)
    first_age = pm._position_scores["AAPL"].days_held
    client.get_all_positions.return_value = []
    pm.refresh_positions()  # 2: full exit
    client.get_all_positions.return_value = _pos()
    pm.refresh_positions()  # 3: re-entry
    return first_age, pm._position_scores["AAPL"].days_held


def test_reentry_uses_current_lot_age_when_flag_on(monkeypatch):
    pm, client = _pm(monkeypatch, current_lot=True)
    first_age, reentry_age = _reenter(pm, client)
    # First refresh rehydrates from history (25d); the RE-ENTRY resets to the current lot (0d).
    assert first_age == 25
    assert reentry_age == 0
    assert "AAPL" in pm._position_opened_at


def test_reentry_stays_inflated_when_flag_off(monkeypatch):
    """Flag OFF → byte-identical legacy behaviour: the re-entry still reads the 25-day age."""
    pm, client = _pm(monkeypatch, current_lot=False)
    first_age, reentry_age = _reenter(pm, client)
    assert first_age == 25
    assert reentry_age == 25
    assert pm._position_opened_at == {}


def test_full_exit_clears_the_clock(monkeypatch):
    pm, client = _pm(monkeypatch, current_lot=True)
    # present → absent → present: the re-entry is a witnessed transition → stamped.
    client.get_all_positions.return_value = _pos()
    pm.refresh_positions()
    client.get_all_positions.return_value = []
    pm.refresh_positions()
    client.get_all_positions.return_value = _pos()
    pm.refresh_positions()
    assert "AAPL" in pm._position_opened_at
    # a further full exit clears the clock again.
    client.get_all_positions.return_value = []
    pm.refresh_positions()
    assert "AAPL" not in pm._position_opened_at
    assert "AAPL" not in pm._position_scores
