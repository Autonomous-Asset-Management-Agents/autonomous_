# tests/integration/test_min_hold_after_restart.py
# E2E (#1994 / Board #2039, plan §8.2 — Archon AUD 2026-07-22 §2): the min-hold
# anti-churn gate must survive a process restart on the desktop (no Redis).
#
# Exercises the REAL order_executor seam (order_executor.py:541→554):
#     restore_pm_state_from_redis(pm, r, set())   # no-op — LocalStateClient is empty
#     await reconcile_entry_time_from_alpaca(pm)  # broker = source of truth
# against a REAL PortfolioManager whose _trade_history is empty (simulated
# restart) and a fake Alpaca client that serves the fill history.
#
# Core invariant pinned here: after a restart the gate computes hours_held from
# the ORIGINAL broker entry-time, NOT from the restart time — a weeks-old
# position is sellable, a fresh one stays blocked. The control assertion in
# test_fresh_position_blocked_after_restart proves the counterfactual: WITHOUT
# the reconcile the gate fails open ("No trade history - can sell") — churn.
#
# Mock posture (CODING_POLICY §12.5 / CLAUDE.md §5.2): the broker client is a
# SYNC interface (called via asyncio.to_thread), so plain SimpleNamespace
# lambdas are correct here — same pattern as tests/unit/test_entry_time_reconcile.py.
# The state client is a real (empty) LocalStateClient — the desktop reality.
#
# Timezone robustness: reconciled entries are stored offset-naive LOCAL
# (_to_naive_local) and re-stamped as UTC by _ensure_aware_utc, shifting
# hours_held by the host's UTC offset (plan §12.1, max ±14 h). Every threshold
# below keeps a margin far wider than any possible offset, so the assertions
# hold on any CI/dev host timezone.

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.engine.entry_time_reconcile import reconcile_entry_time_from_alpaca
from core.engine.order_executor import restore_pm_state_from_redis
from core.local_state_client import LocalStateClient
from core.portfolio_manager import PortfolioManager

SYMBOL = "HOOD"


def _fill(hours_ago: float, side: str, qty: float) -> SimpleNamespace:
    """One Alpaca filled order, offset-aware UTC (as the real API returns it)."""
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return SimpleNamespace(
        side=side, filled_at=t, submitted_at=t, filled_qty=str(qty), symbol=SYMBOL
    )


def _naive_local(dt: datetime) -> datetime:
    """Expected stored form: _to_naive_local (offset-naive local wall clock)."""
    return dt.astimezone().replace(tzinfo=None)


def _fake_alpaca(orders: list, qty: str = "50") -> SimpleNamespace:
    """Sync broker stub: one open HOOD position + a served fill history."""
    pos = SimpleNamespace(symbol=SYMBOL, qty=qty)
    return SimpleNamespace(
        get_all_positions=lambda: [pos],
        get_orders=lambda req: list(orders),
    )


def _restarted_pm(client: SimpleNamespace, min_hold_hours: float) -> PortfolioManager:
    """A PM as it exists right after a desktop process restart: empty in-memory
    anti-churn state (no Redis persisted anything)."""
    pm = PortfolioManager(client=client, total_capital=100_000, user_id="oss-single")
    pm._min_hold_hours = min_hold_hours  # explicit — independent of config default
    assert pm._trade_history == {}  # precondition: restart == empty state
    return pm


async def _run_restart_seam(pm: PortfolioManager) -> None:
    """The exact startup sequence of the order_executor seam
    (core/engine/order_executor.py:541→554): Redis restore first (no-op on the
    desktop — LocalStateClient is empty), then the Alpaca entry-time reconcile
    fills only the gaps Redis did not restore."""
    await restore_pm_state_from_redis(pm, LocalStateClient(), set())
    await reconcile_entry_time_from_alpaca(pm)


# ── §8.2 case 1: weeks-old position is sellable from ORIGINAL entry ──────────
@pytest.mark.asyncio
async def test_weeks_old_position_is_sellable_from_original_entry():
    buy = _fill(8 * 24, "buy", 50)  # BUY 50 @ T-8d — far beyond min-hold
    pm = _restarted_pm(_fake_alpaca([buy]), min_hold_hours=4.0)

    await _run_restart_seam(pm)

    # Entry-time == the broker's ORIGINAL fill time, not the restart time.
    assert pm._trade_history[SYMBOL] == [_naive_local(buy.filled_at)]
    assert pm.can_sell_position(SYMBOL) == (True, "Hold period satisfied")


# ── §8.2 case 2: fresh position stays blocked (+ fail-open control) ──────────
@pytest.mark.asyncio
async def test_fresh_position_blocked_after_restart():
    buy = _fill(1, "buy", 50)  # BUY 50 @ T-1h — far below min-hold (48 h)
    pm = _restarted_pm(_fake_alpaca([buy]), min_hold_hours=48.0)

    # CONTROL (the actual win of #1994): WITHOUT the reconcile a restart wipes
    # the history and the gate fails open — instant churn permission.
    assert pm.can_sell_position(SYMBOL) == (True, "No trade history - can sell")

    await _run_restart_seam(pm)

    can_sell, reason = pm.can_sell_position(SYMBOL)
    assert can_sell is False
    assert reason.startswith("Minimum hold period not met")


# ── §8.2 case 3: gate measures from entry-time, NOT from restart time ────────
@pytest.mark.asyncio
async def test_min_hold_measured_from_entry_not_restart():
    buy = _fill(8 * 24, "buy", 50)  # BUY 50 @ T-8d
    pm = _restarted_pm(_fake_alpaca([buy]), min_hold_hours=24.0)

    await _run_restart_seam(pm)
    reconciled = list(pm._trade_history[SYMBOL])

    # Counterfactual: were the entry stamped at RESTART time, the same gate
    # would block (|hours_held| < 24 h on any host timezone).
    pm._trade_history[SYMBOL] = [datetime.now()]
    assert pm.can_sell_position(SYMBOL)[0] is False

    # Reality after reconcile: measured from the ORIGINAL T-8d entry → sellable.
    pm._trade_history[SYMBOL] = reconciled
    assert pm.can_sell_position(SYMBOL) == (True, "Hold period satisfied")


# ── §8.2 case 4: closed-then-reopened — no restart bypass (AUD-2039-1 §1) ────
@pytest.mark.asyncio
async def test_closed_reopened_blocks_after_restart():
    reopen = _fill(3 * 24, "buy", 50)  # the CURRENT open position (T-3d)
    orders = [
        _fill(10 * 24, "buy", 50),  # old, already-closed BUY (T-10d)
        _fill(5 * 24, "sell", 50),  # flat @ T-5d
        reopen,
    ]
    pm = _restarted_pm(_fake_alpaca(orders), min_hold_hours=96.0)  # 4 days

    await _run_restart_seam(pm)

    # Descending aggregation picks T-3d (the reopen), NOT T-10d: 72 h < 96 h
    # blocks; the old T-10d entry (240 h) would have slipped through.
    assert pm._trade_history[SYMBOL] == [_naive_local(reopen.filled_at)]
    can_sell, reason = pm.can_sell_position(SYMBOL)
    assert can_sell is False
    assert reason.startswith("Minimum hold period not met")
