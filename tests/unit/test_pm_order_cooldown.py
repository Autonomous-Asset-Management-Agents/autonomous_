# tests/unit/test_pm_order_cooldown.py
# Part C Increment 2 — per-symbol order cooldown + POLICY-01 timezone safety.
#
# Two coupled concerns land here:
#   1. Anti-churn cooldown: PortfolioManager must refuse to re-OPEN a symbol that
#      was traded within MIN_ORDER_INTERVAL_SEC. BUY-side only — exits are never
#      gated here (that would trap the exit-invariant the R1 flag protects).
#   2. POLICY-01: the cooldown compares `now` against timestamps in _trade_history.
#      If `now` is aware (UTC) and the stored stamps are naive, the subtraction
#      raises TypeError. Increment 2 migrates ALL PM datetimes to aware UTC and,
#      because a naive-local -> UTC shift moves the .date() boundary, anchors the
#      "trades today" count in ET (America/New_York) — the NY trading-day boundary.
#
# Deterministic: no network, no clock freezing. The ET boundary is proven against
# the pure _trading_day_et() helper with fixed instants (DST-safe: EST + EDT cases).

import importlib.util
import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import config
from core.portfolio_manager import OpportunityScore, PortfolioManager, _trading_day_et


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_cooldown", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pm(max_positions: int = 10) -> PortfolioManager:
    client = MagicMock()
    client.get_all_positions.return_value = []
    client.get_account.return_value = MagicMock(equity=100_000.0)
    return PortfolioManager(
        client=client, total_capital=100_000.0, max_positions=max_positions
    )


def _opp(symbol: str = "AAPL", score: float = 80.0) -> "OpportunityScore":
    return OpportunityScore(symbol=symbol, current_price=100.0, total_score=score)


# ===========================================================================
# 1. Config — MIN_ORDER_INTERVAL_SEC exists + BORA parity (both editions).
# ===========================================================================
def test_min_order_interval_sec_default_and_parity():
    assert config.get_config().MIN_ORDER_INTERVAL_SEC == 900
    assert config.MIN_ORDER_INTERVAL_SEC == 900  # PEP-562 module export
    oss = _load_config_oss()
    assert oss.MIN_ORDER_INTERVAL_SEC == 900
    assert oss.get_config().MIN_ORDER_INTERVAL_SEC == 900


# ===========================================================================
# 2. POLICY-01 — ET trading-day boundary is a pure, DST-safe function.
# ===========================================================================
def test_trading_day_et_winter_est_boundary():
    # 02:00 UTC on Jan 2 is 21:00 EST on Jan 1 (UTC-5) -> still the Jan-1 session.
    assert _trading_day_et(datetime(2026, 1, 2, 2, 0, tzinfo=timezone.utc)) == date(
        2026, 1, 1
    )
    # 12:00 UTC on Jan 2 is 07:00 EST on Jan 2.
    assert _trading_day_et(datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)) == date(
        2026, 1, 2
    )


def test_trading_day_et_summer_edt_boundary():
    # 03:00 UTC on Jul 2 is 23:00 EDT on Jul 1 (UTC-4).
    assert _trading_day_et(datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc)) == date(
        2026, 7, 1
    )


def test_trading_day_et_treats_naive_as_utc():
    # Defense-in-depth: a stray naive stamp must not throw and must be read as UTC.
    assert _trading_day_et(datetime(2026, 1, 2, 2, 0)) == date(2026, 1, 1)


# ===========================================================================
# 3. POLICY-01 — record_trade stores aware UTC; no naive/aware TypeError.
# ===========================================================================
def test_record_trade_stores_aware_utc():
    pm = _make_pm()
    pm.record_trade("AAPL", "buy")
    stamp = pm._trade_history["AAPL"][-1]
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() == timedelta(0)


def test_cooldown_check_tolerates_naive_history_without_typeerror():
    pm = _make_pm()
    # Legacy/naive stamp (e.g. a hot-reload survivor) must not raise.
    pm._trade_history["AAPL"] = [datetime.now() - timedelta(seconds=30)]
    assert pm._within_order_cooldown("AAPL") is True  # 30s < 900s


def test_cooldown_survives_mixed_naive_and_aware_history():
    """A naive survivor + a fresh aware record_trade must not build a heterogeneous
    [naive, aware] list whose max() raises a naive/aware TypeError. record_trade must
    normalise the retained history, and the cooldown read must tolerate a mix."""
    pm = _make_pm()
    pm._trade_history["AAPL"] = [datetime.now() - timedelta(seconds=120)]  # naive
    pm.record_trade("AAPL", "buy")  # appends aware -> would be [naive, aware]
    # Must not raise, and the fresh trade must still gate a re-open.
    assert pm._within_order_cooldown("AAPL") is True
    # Retained history is normalised to aware UTC (homogeneous).
    assert all(t.tzinfo is not None for t in pm._trade_history["AAPL"])


def test_can_sell_survives_mixed_naive_and_aware_history():
    """can_sell_position's max() over the history must tolerate a naive+aware mix."""
    pm = _make_pm()
    pm._min_hold_hours = 0.5
    pm._trade_history["AAPL"] = [
        datetime.now() - timedelta(minutes=5),  # naive
        datetime.now(timezone.utc) - timedelta(minutes=1),  # aware, most recent
    ]
    can_sell, _reason = pm.can_sell_position("AAPL")  # must not raise
    assert can_sell is False  # newest ~1min < 30min hold


def test_refresh_positions_survives_mixed_history_days_held():
    """refresh_positions' min() over the history (days_held) must tolerate a mix."""
    pm = _make_pm()
    pos = MagicMock()
    pos.symbol = "AAPL"
    pos.qty = "10"
    pos.avg_entry_price = "100"
    pos.current_price = "110"
    pos.market_value = "1100"
    pos.unrealized_pl = "100"
    pos.unrealized_plpc = "0.1"
    pm.client.get_all_positions.return_value = [pos]
    pm._trade_history["AAPL"] = [
        datetime.now() - timedelta(days=3),  # naive
        datetime.now(timezone.utc) - timedelta(days=1),  # aware
    ]
    scores = pm.refresh_positions()  # must not raise on min()
    assert "AAPL" in scores


# ===========================================================================
# 4. Cooldown behaviour (BUY-side; covers both the room and swap paths).
# ===========================================================================
def test_cooldown_blocks_reopen_within_interval():
    pm = _make_pm()
    pm._trade_history["AAPL"] = [datetime.now(timezone.utc) - timedelta(seconds=60)]
    should_open, reason, to_close = pm.should_open_new_position(_opp("AAPL"))
    assert should_open is False
    assert "cooldown" in reason.lower()
    assert to_close is None


def test_cooldown_allows_open_beyond_interval():
    pm = _make_pm()
    pm._trade_history["AAPL"] = [datetime.now(timezone.utc) - timedelta(seconds=1000)]
    should_open, _reason, _close = pm.should_open_new_position(_opp("AAPL"))
    assert should_open is True


def test_cooldown_is_per_symbol():
    pm = _make_pm()
    pm._trade_history["AAPL"] = [datetime.now(timezone.utc) - timedelta(seconds=60)]
    # MSFT has no recent trade -> not gated by AAPL's cooldown.
    should_open, _reason, _close = pm.should_open_new_position(_opp("MSFT"))
    assert should_open is True


def test_cooldown_is_isolated_per_portfolio_manager():
    pm1 = _make_pm()
    pm2 = _make_pm()
    pm1.record_trade("AAPL", "buy")
    # pm2 keeps its own history — no cross-tenant leakage.
    assert pm2._within_order_cooldown("AAPL") is False


def test_cooldown_disabled_when_interval_zero():
    pm = _make_pm()
    pm._min_order_interval_sec = 0
    pm._trade_history["AAPL"] = [datetime.now(timezone.utc) - timedelta(seconds=1)]
    should_open, _reason, _close = pm.should_open_new_position(_opp("AAPL"))
    assert should_open is True


def test_cooldown_never_gates_a_symbol_with_no_history():
    pm = _make_pm()
    assert pm._within_order_cooldown("NVDA") is False


# ===========================================================================
# 5. FINDING-01 (concurrency) — record_trade is atomic under thread contention.
#    The 30-day prune is a read-modify-write on the list; without a lock a
#    concurrent append can be dropped by another thread's rebuild.
# ===========================================================================
def test_record_trade_concurrent_appends_are_not_lost():
    pm = _make_pm()
    n = 200
    start = threading.Barrier(n)

    def worker():
        start.wait()  # release all threads together to maximise contention
        pm.record_trade("AAPL", "buy")

    # Force frequent thread preemption so the append + 30-day-prune read-modify-write
    # is actually interleaved. At the default 5ms switch interval the ~microsecond
    # critical section runs to completion between switches, so a lock-free regression
    # would pass unnoticed; at 1e-6 a missing lock reliably drops appends.
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)
    assert len(pm._trade_history["AAPL"]) == n
