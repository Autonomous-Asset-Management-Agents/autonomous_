"""
Unit tests for Anti-Churn Redis Persistence (Rev 4).

Tests restore_pm_state_from_redis() and persist_pm_state_to_redis()
as module-level functions — no OrderExecutor fixture required (NB-1 fix).

References:
  - AGENTS.md §0 (Zero-Guessing), §1 (TDD)
  - Implementation plan Rev 4 (BORA-approved 2026-06-13)
  - order_executor.py: restore_pm_state_from_redis, persist_pm_state_to_redis
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine.order_executor import (
    persist_pm_state_to_redis,
    restore_pm_state_from_redis,
)
from core.portfolio_manager import PortfolioManager

# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_pm(user_id: str = "oss-single", symbols: list | None = None) -> MagicMock:
    """Minimal PM mock with real dicts for state (no mock-magic on _trade_history)."""
    pm = MagicMock(
        spec=["user_id", "_trade_history", "_consecutive_sell_signals", "client"]
    )
    pm.user_id = user_id
    pm._trade_history = {}
    pm._consecutive_sell_signals = {}
    positions = [MagicMock(symbol=s, spec=["symbol"]) for s in (symbols or [])]
    pm.client = MagicMock()
    pm.client.get_all_positions = MagicMock(return_value=positions)
    return pm


# ── restore_pm_state_from_redis ──────────────────────────────────────────────


class TestRestorePmStateFromRedis:
    """Tests for restore_pm_state_from_redis() module-level function."""

    @pytest.mark.asyncio
    async def test_noop_when_already_restored(self):
        """If user_id is in pm_restored set, no Redis call is made."""
        pm = _make_pm()
        r = AsyncMock()
        pm_restored = {"oss-single"}  # Already restored this session

        await restore_pm_state_from_redis(pm, r, pm_restored)

        r.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_r_is_none_flag_not_set(self):
        """r=None → early return without setting pm_restored (enables retry)."""
        pm = _make_pm()
        pm_restored = set()

        await restore_pm_state_from_redis(pm, None, pm_restored)

        # NB-2 fix: flag must NOT be set so next call can retry
        assert "oss-single" not in pm_restored

    @pytest.mark.asyncio
    async def test_noop_when_r_has_no_get(self):
        """r without .get() attr → early return, flag not set."""
        pm = _make_pm()
        pm_restored = set()
        r_bad = object()  # No .get() method

        await restore_pm_state_from_redis(pm, r_bad, pm_restored)

        assert "oss-single" not in pm_restored

    @pytest.mark.asyncio
    async def test_noop_and_flag_set_when_no_open_positions(self):
        """No open positions → no Redis read, but user_id IS added to pm_restored."""
        pm = _make_pm(symbols=[])
        r = AsyncMock()
        pm_restored = set()

        await restore_pm_state_from_redis(pm, r, pm_restored)

        r.get.assert_not_called()
        assert "oss-single" in pm_restored

    @pytest.mark.asyncio
    async def test_restores_trade_history_from_redis(self):
        """Redis has trade_history → pm._trade_history is populated."""
        pm = _make_pm(symbols=["AAPL", "NVDA"])
        ts = (datetime.now() - timedelta(minutes=10)).isoformat()
        r = AsyncMock()
        r.get.return_value = json.dumps(
            [ts]
        )  # Both trade_history and sell_signals return same
        pm_restored = set()

        await restore_pm_state_from_redis(pm, r, pm_restored)

        assert "AAPL" in pm._trade_history
        assert len(pm._trade_history["AAPL"]) == 1
        assert "NVDA" in pm._trade_history
        assert "oss-single" in pm_restored

    @pytest.mark.asyncio
    async def test_flag_set_after_successful_restore(self):
        """pm_restored.add() called only after r validation — not before."""
        pm = _make_pm(symbols=["AAPL"])
        r = AsyncMock()
        r.get.return_value = None  # No data in Redis
        pm_restored = set()

        await restore_pm_state_from_redis(pm, r, pm_restored)

        assert "oss-single" in pm_restored  # Flag set even when no data found

    @pytest.mark.asyncio
    async def test_sell_blocked_after_restore(self):
        """Core regression: fresh PM after restart, Redis has recent BUY → SELL is blocked."""
        client = MagicMock()
        ts = (datetime.now() - timedelta(minutes=5)).isoformat()
        client.get_all_positions = MagicMock(
            return_value=[MagicMock(symbol="AAPL", spec=["symbol"])]
        )
        pm = PortfolioManager(
            client=client, total_capital=100_000.0, user_id="oss-single"
        )
        pm._min_hold_hours = 0.5  # 30-minute threshold

        r = AsyncMock()
        r.get.side_effect = lambda key: (
            json.dumps([ts]) if "trade_history" in key else "0"
        )
        pm_restored = set()

        await restore_pm_state_from_redis(pm, r, pm_restored)

        can_sell, reason = pm.can_sell_position("AAPL")
        assert can_sell is False
        assert "Minimum hold period not met" in reason

    @pytest.mark.asyncio
    async def test_corrupt_redis_data_is_skipped_gracefully(self):
        """Corrupt JSON in Redis → warning logged, symbol skipped, no crash."""
        pm = _make_pm(symbols=["AAPL"])
        r = AsyncMock()
        r.get.return_value = "NOT_VALID_JSON{{{{"
        pm_restored = set()

        await restore_pm_state_from_redis(pm, r, pm_restored)  # Must not raise

        assert "AAPL" not in pm._trade_history  # Corrupt data not loaded
        assert "oss-single" in pm_restored


# ── persist_pm_state_to_redis ────────────────────────────────────────────────


class TestPersistPmStateToRedis:
    """Tests for persist_pm_state_to_redis() module-level function."""

    @pytest.mark.asyncio
    async def test_writes_exactly_two_keys(self):
        """Persist writes exactly 2 Redis keys — no tracked_symbols index."""
        pm = _make_pm()
        pm._trade_history["AAPL"] = [datetime.now()]
        r = AsyncMock()

        await persist_pm_state_to_redis(pm, "AAPL", r)

        assert r.set.call_count == 2
        keys = [c.args[0] for c in r.set.call_args_list]
        assert "pm:trade_history:oss-single:AAPL" in keys
        assert "pm:sell_signals:oss-single:AAPL" in keys
        assert not any("tracked_symbols" in k for k in keys)  # NF-1: no index key

    @pytest.mark.asyncio
    async def test_ttl_26h_on_both_keys(self):
        """Both keys have 26h TTL (px = 26 * 3600 * 1000 ms)."""
        pm = _make_pm()
        r = AsyncMock()

        await persist_pm_state_to_redis(pm, "AAPL", r)

        expected_ttl = 26 * 60 * 60 * 1000
        for call in r.set.call_args_list:
            assert call.kwargs.get("px") == expected_ttl

    @pytest.mark.asyncio
    async def test_silent_on_none_redis(self):
        """r=None → silent return, no exception raised."""
        pm = _make_pm()
        await persist_pm_state_to_redis(pm, "AAPL", None)  # Must not raise

    @pytest.mark.asyncio
    async def test_silent_when_r_has_no_set(self):
        """r without .set() attr → silent return."""
        pm = _make_pm()
        await persist_pm_state_to_redis(pm, "AAPL", object())  # Must not raise

    @pytest.mark.asyncio
    async def test_trade_history_serialized_as_iso_strings(self):
        """Timestamps are written as ISO-format strings."""
        pm = _make_pm()
        t = datetime(2026, 6, 13, 20, 45, 28)
        pm._trade_history["AAPL"] = [t]
        r = AsyncMock()

        await persist_pm_state_to_redis(pm, "AAPL", r)

        history_call = next(
            c for c in r.set.call_args_list if "trade_history" in c.args[0]
        )
        payload = json.loads(history_call.args[1])
        assert payload == ["2026-06-13T20:45:28"]

    @pytest.mark.asyncio
    async def test_sell_signals_written_as_string_int(self):
        """Consecutive sell count is written as str(int)."""
        pm = _make_pm()
        pm._consecutive_sell_signals["AAPL"] = 3
        r = AsyncMock()

        await persist_pm_state_to_redis(pm, "AAPL", r)

        sells_call = next(
            c for c in r.set.call_args_list if "sell_signals" in c.args[0]
        )
        assert sells_call.args[1] == "3"
