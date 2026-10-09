"""O3-SAFETY: PortfolioManager._last_refresh_ok — the freshness signal that lets cash-aware sizing
(effective_free_slots) fail safe to the fixed book-cap divisor when the last position read did NOT
confirm the live set. refresh_positions() swallows a broker-read error and returns the un-pruned
_position_scores (a stale over-count); without this flag that stale count would collapse the sizing
divisor and oversize a buy.
"""

from unittest.mock import MagicMock

from core.portfolio_manager import PortfolioManager


def test_fresh_pm_starts_unconfirmed():
    """Before any successful refresh the flag is False → sizing stays conservative (fixed cap)."""
    pm = PortfolioManager(client=MagicMock(), total_capital=100_000.0)
    assert pm._last_refresh_ok is False


def test_successful_refresh_confirms():
    """get_all_positions succeeds → the map is rebuilt/pruned → flag True (held count trustworthy)."""
    client = MagicMock()
    client.get_all_positions.return_value = []
    pm = PortfolioManager(client=client, total_capital=100_000.0)
    pm.refresh_positions()
    assert pm._last_refresh_ok is True


def test_broker_read_failure_marks_stale():
    """get_all_positions raises → the map is STALE → flag False so sizing fails safe to the cap."""
    client = MagicMock()
    client.get_all_positions.side_effect = RuntimeError("transient APIError")
    pm = PortfolioManager(client=client, total_capital=100_000.0)
    # simulate a prior confirmed refresh
    pm._last_refresh_ok = True
    pm.refresh_positions()
    assert pm._last_refresh_ok is False


def test_equity_sync_failure_alone_does_not_mark_stale():
    """A get_account (equity-sync) failure does NOT invalidate the position map — only the position
    read matters. get_all_positions still succeeds → flag True."""
    client = MagicMock()
    client.get_account.side_effect = RuntimeError("equity flap")
    client.get_all_positions.return_value = []
    pm = PortfolioManager(client=client, total_capital=100_000.0)
    pm.refresh_positions()
    assert pm._last_refresh_ok is True


def test_sell_marks_positions_unconfirmed():
    """O3-SAFETY (recency): a recorded SELL flips the freshness flag to False — the held-count map
    still lists the sold name until the next refresh, so sizing must fail safe to the cap meanwhile.
    """
    client = MagicMock()
    client.get_all_positions.return_value = []
    pm = PortfolioManager(client=client, total_capital=100_000.0)
    pm.refresh_positions()
    assert pm._last_refresh_ok is True
    pm.record_trade("AAPL", "sell")
    assert pm._last_refresh_ok is False


def test_buy_does_not_mark_unconfirmed():
    """A recorded BUY does not touch the freshness flag (only a SELL removes a listed holding)."""
    client = MagicMock()
    client.get_all_positions.return_value = []
    pm = PortfolioManager(client=client, total_capital=100_000.0)
    pm.refresh_positions()
    assert pm._last_refresh_ok is True
    pm.record_trade("AAPL", "buy")
    assert pm._last_refresh_ok is True
