# tests/unit/test_entry_time_reconcile.py
# TDD (#1994): durable entry-time via Alpaca fill reconcile.
# _entry_time_from_fills reconstructs the CURRENT open position's entry-time from the
# broker fill history. Pins the three AUD-2039-1 requirements:
#   §1 descending aggregation (closed-then-reopened must NOT pick the old, closed BUY)
#   §2 tz-normalisation (offset-aware Alpaca filled_at -> offset-naive local; no TypeError)
#   §3 fallback when history is exhausted (oldest BUY found; no BUY -> None)

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.engine.entry_time_reconcile import (
    _collect_fills,
    _entry_time_from_fills,
    _to_naive_local,
    reconcile_entry_time_from_alpaca,
)

BASE = datetime(2026, 7, 1, 15, 0, 0, tzinfo=timezone.utc)


def _fill(days_ago, side, qty):
    t = BASE - timedelta(days=days_ago)
    return SimpleNamespace(
        side=side, filled_at=t, submitted_at=t, filled_qty=str(qty), symbol="HOOD"
    )


# ── §1: descending aggregation (closed → reopened) ───────────────────────────
def test_descending_picks_current_open_not_old_closed():
    # BUY 50 (T-10), SELL 50 (T-5, flat), BUY 50 (T-3) → current qty 50
    fills = [_fill(10, "buy", 50), _fill(5, "sell", 50), _fill(3, "buy", 50)]
    entry = _entry_time_from_fills(fills, 50)
    assert entry == (BASE - timedelta(days=3)).astimezone().replace(tzinfo=None)


def test_partial_fills_use_oldest_of_accumulated_subset():
    # BUY 30 (T-7) + BUY 25 (T-3), never sold → qty 55 → entry = T-7
    fills = [_fill(7, "buy", 30), _fill(3, "buy", 25)]
    entry = _entry_time_from_fills(fills, 55)
    assert entry == (BASE - timedelta(days=7)).astimezone().replace(tzinfo=None)


# ── §2: timezone normalisation ───────────────────────────────────────────────
def test_entry_is_offset_naive_and_subtracts_cleanly():
    entry = _entry_time_from_fills([_fill(4, "buy", 10)], 10)
    assert entry is not None and entry.tzinfo is None
    _ = datetime.now() - entry  # must NOT raise TypeError (offset-naive)


def test_to_naive_local_strips_tzinfo():
    aware = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    naive = _to_naive_local(aware)
    assert naive.tzinfo is None
    assert _to_naive_local(None) is None


# ── §3: fallback for exhausted / missing history ─────────────────────────────
def test_exhausted_history_returns_oldest_buy_found():
    # only one small BUY, position qty far larger → oldest found (T-2)
    fills = [_fill(2, "buy", 10)]
    entry = _entry_time_from_fills(fills, 500)
    assert entry == (BASE - timedelta(days=2)).astimezone().replace(tzinfo=None)


def test_no_buy_fill_returns_none():
    assert _entry_time_from_fills([_fill(1, "sell", 10)], 10) is None
    assert _entry_time_from_fills([], 10) is None


# ── Orchestrator: reconcile_entry_time_from_alpaca ────────────────────────────
import asyncio  # noqa: E402


def test_reconcile_populates_trade_history_from_broker():
    pos = SimpleNamespace(symbol="HOOD", qty="50")
    orders = [_fill(10, "buy", 50), _fill(5, "sell", 50), _fill(3, "buy", 50)]
    client = SimpleNamespace(
        get_all_positions=lambda: [pos],
        get_orders=lambda req: orders,
    )
    pm = SimpleNamespace(client=client, _trade_history={})
    asyncio.run(reconcile_entry_time_from_alpaca(pm))
    assert "HOOD" in pm._trade_history
    assert pm._trade_history["HOOD"][0] == (
        (BASE - timedelta(days=3)).astimezone().replace(tzinfo=None)
    )


def test_reconcile_skips_symbols_already_durable():
    pos = SimpleNamespace(symbol="HOOD", qty="50")
    calls = {"orders": 0}

    def _get_orders(req):
        calls["orders"] += 1
        return []

    client = SimpleNamespace(get_all_positions=lambda: [pos], get_orders=_get_orders)
    existing = datetime(2026, 6, 1, 12, 0)
    pm = SimpleNamespace(client=client, _trade_history={"HOOD": [existing]})
    asyncio.run(reconcile_entry_time_from_alpaca(pm))
    assert pm._trade_history["HOOD"] == [existing]  # untouched
    assert calls["orders"] == 0  # no broker order-fetch when nothing to reconcile


def test_collect_fills_paginates_beyond_one_window():
    # page1 (full) has an insufficient recent BUY; the qty-completing BUY is on page2 —
    # a single window would miss it and under-estimate the hold (AUD-2039-1 §3).
    page1 = [_fill(3, "buy", 20)]  # 20 < 50 → must page
    page2 = [_fill(9, "buy", 30)]  # +30 = 50 → covered, entry = T-9
    page3: list = []
    pages = iter([page1, page2, page3])
    client = SimpleNamespace(get_orders=lambda req: next(pages))
    fills = asyncio.run(_collect_fills(client, {"HOOD": 50}, page_size=1))
    assert len(fills["HOOD"]) == 2  # both pages collected
    entry = _entry_time_from_fills(fills["HOOD"], 50)
    assert entry == (BASE - timedelta(days=9)).astimezone().replace(tzinfo=None)
