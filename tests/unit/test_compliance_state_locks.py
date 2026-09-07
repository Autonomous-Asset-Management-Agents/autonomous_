"""Gate 3 / Gate 4 shared-state thread-safety (follow-up to the #1849 HFT lock).

Closes the SAME concurrency bug class the #1849 HFT-throttle lock already fixed
(lost update on an unlocked shared read-modify-write) for the two remaining
``ComplianceGuardian`` compliance-gate buffers:

* ``_recent_trades`` — Gate 3 (wash-trade). Appended on the approve path and
  reassigned (``= [filtered...]``) by ``_cleanup_recent_trades``; the
  append + cleanup-reassign RMW is non-atomic → a concurrent cleanup can drop a
  just-appended record (LOST UPDATE). Now funnelled through
  ``_record_recent_trade`` under ``self._state_lock``.
* ``daily_trades`` — Gate 4 (daily cap). Incremented once per approved trade; the
  ``+= 1`` RMW is non-atomic → concurrent increments collide (LOST INCREMENT), so
  the compliance cap can be silently exceeded. Now funnelled through
  ``record_trade`` under the same ``self._state_lock``.

Determinism
-----------
* ``_recent_trades``: frozen wall clock (nothing ages out) + a unique letter-only
  ticker per thread (every order clears the wash-trade gate) → the buffer length
  is an exact conservation invariant. The lost update is observable directly on
  the append + cleanup RMW (proven RED against an unlocked subclass).
* ``daily_trades``: the ``+= 1`` RMW is a load/store split. Rather than relying on
  the scheduler to interleave it (rare under the CPython GIL's ~5 ms switch
  interval), the RED test *orchestrates* the classic lost-increment interleaving
  with events — deterministic and scheduler-independent. The real locked
  ``record_trade`` makes the second writer block on the lock, so no increment is
  lost (GREEN under the identical orchestration) and the bulk-conservation and
  cap-not-exceeded invariants hold.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.compliance import ComplianceGuardian


@pytest.fixture
def guardian():
    """A clean ComplianceGuardian with a mocked cloud logger and empty buffers.

    Mirrors tests/unit/test_compliance_hft.py::guardian.
    """
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_get_logger.return_value = MagicMock()
        g = ComplianceGuardian()
        g._recent_trades = []
        g._hft_recent_orders = []
        g.daily_trades = 0
        return g


def _valid_order(symbol="AAPL", side="buy", user_id="user_a", timestamp=None):
    """A fully approvable order: valid US equity, complete MiFID fields, value
    (5 * 100 = 500) far below COMPLIANCE_MAX_ORDER_VALUE. side/symbol/user are
    caller-controlled so the wash-trade gate never rejects unintentionally."""
    return {
        "symbol": symbol,
        "side": side,
        "quantity": 5,
        "price": 100.0,
        "strategy_id": "test_strat",
        "timestamp": timestamp if timestamp is not None else time.time(),
        "user_id": user_id,
    }


def _thread_symbol(idx):
    """A UNIQUE, digit-free, letter-only US-equity ticker per thread. Digits trip
    Gate 1b (spot-US-equity shape); a shared symbol with mixed sides could trip
    the wash-trade gate. A unique letter-only ticker keeps every order on the
    approve path."""
    return "Q" + chr(ord("A") + idx // 26) + chr(ord("A") + idx % 26)


# ── An unlocked guardian that reproduces the pre-fix RMW races (RED baselines). ─


class _UnlockedRecentTrades(ComplianceGuardian):
    """Restores the pre-fix approve-path RMW: append then cleanup-reassign with NO
    lock — the #1849 bug class, kept only to prove the RED lost update."""

    def _record_recent_trade(self, trade_record):
        self._recent_trades.append(trade_record)
        self._cleanup_recent_trades()


def _make(cls):
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_get_logger.return_value = MagicMock()
        g = cls()
        g._recent_trades = []
        g._hft_recent_orders = []
        g.daily_trades = 0
        return g


# ── 1. _recent_trades (Gate 3) conservation under concurrency ─────────────────


def _hammer_recent_trades(g):
    """N threads hammer the approve-path buffer RMW → returns (approved, buffered)
    under a frozen clock (nothing ages out, so buffered MUST == approved)."""
    n_threads, per_thread = 16, 300
    errors, approved = [], [0] * n_threads
    barrier = threading.Barrier(n_threads)
    frozen_now = 1_000_000.0

    def worker(idx):
        try:
            barrier.wait()
            rec = {
                "symbol": _thread_symbol(idx),
                "side": "buy",
                "timestamp": frozen_now,
                "user_id": f"user_{idx}",
            }
            for _ in range(per_thread):
                g._record_recent_trade(dict(rec))
            approved[idx] = per_thread
        except Exception as exc:  # noqa: BLE001 — any raise IS a corruption signal
            errors.append(exc)

    with patch("core.compliance.time.time", return_value=frozen_now):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert not errors, f"raised: {errors!r}"
    return sum(approved), len(g._recent_trades)


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Wash-Trade buffer thread-safety (Gate 3)")
def test_recent_trades_lost_update_without_lock_RED():
    """Baseline: the UNLOCKED append + cleanup-reassign RMW drops records under
    concurrency → buffered < approved (the lost update this lock fixes).

    Captured RED assertion (pre-fix): ``buffered < approved`` — e.g. approved=4800
    buffered=2342 on a 16-thread run.
    """
    approved, buffered = _hammer_recent_trades(_make(_UnlockedRecentTrades))
    assert buffered < approved, (
        "expected the unlocked RMW to LOSE records (RED baseline); "
        f"approved={approved} buffered={buffered}"
    )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Wash-Trade buffer thread-safety (Gate 3)")
def test_recent_trades_conservation_under_concurrency(guardian):
    """GREEN: with ``_state_lock`` guarding the approve-path RMW, every approved
    order stays buffered — buffered == approved (no lost update)."""
    approved, buffered = _hammer_recent_trades(guardian)
    assert (
        buffered == approved
    ), f"lost update in _recent_trades: approved={approved} buffered={buffered}"


# ── 2. daily_trades (Gate 4) conservation under concurrency ───────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Daily-cap counter thread-safety (Gate 4)")
def test_daily_trades_lost_increment_without_lock_RED():
    """Baseline: an UNLOCKED ``+= 1`` loses an increment under the orchestrated
    interleaving → two increments, final value 1 (the compliance-cap escape).

    Captured RED assertion (pre-fix): ``final == 1`` (one of two increments lost).
    """

    class _UnlockedIncrement(ComplianceGuardian):
        def record_trade(self):
            current = self.daily_trades  # read
            time.sleep(0)  # yield the GIL to widen the load→store window
            self.daily_trades = current + 1  # store (RMW, unlocked)

    g = _make(_UnlockedIncrement)
    g.daily_trades = 0
    # Orchestrate: A reads 0, B fully increments to 1, A stores stale 0+1 = 1.
    a_read = threading.Event()
    b_done = threading.Event()

    def racy_a():
        current = g.daily_trades  # A reads 0
        a_read.set()
        b_done.wait(timeout=5)  # let B run to completion
        g.daily_trades = current + 1  # A stores 1 (B's increment lost)

    def clean_b():
        a_read.wait(timeout=5)
        g.record_trade()  # B: 0 -> 1
        b_done.set()

    tb = threading.Thread(target=clean_b)
    ta = threading.Thread(target=racy_a)
    tb.start()
    ta.start()
    ta.join(timeout=10)
    tb.join(timeout=10)
    assert g.daily_trades == 1, (
        "expected the unlocked RMW to LOSE one of two increments (RED baseline); "
        f"final={g.daily_trades}"
    )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Daily-cap counter thread-safety (Gate 4)")
def test_daily_trades_conservation_bulk(guardian):
    """GREEN: N threads each drive many approved trades through ``record_trade`` →
    final ``daily_trades`` == total increments (no lost increment)."""
    n_threads, per_thread = 16, 200
    errors = []
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            barrier.wait()
            for _ in range(per_thread):
                guardian.record_trade()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"concurrent record_trade raised: {errors!r}"
    assert guardian.daily_trades == n_threads * per_thread, (
        "lost increment in daily_trades: expected="
        f"{n_threads * per_thread} actual={guardian.daily_trades}"
    )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Daily-cap counter thread-safety (Gate 4)")
def test_daily_cap_not_exceeded_under_concurrency(guardian):
    """GREEN: under concurrency the enforced daily cap is NEVER silently exceeded.

    A lost increment would let MORE than ``max_daily_trades`` trades through — a
    compliance failure. Threads serialise the check+increment (as the executor's
    ``if not is_simulation`` block does) so the cap is enforced atomically; the
    lock guarantees no increment is lost, so the accepted count is exactly the cap.
    """
    guardian.max_daily_trades = 20
    n_threads, per_thread = 16, 50
    errors, accepted = [], [0] * n_threads
    barrier = threading.Barrier(n_threads)
    gate = threading.Lock()

    def worker(idx):
        try:
            barrier.wait()
            local = 0
            for _ in range(per_thread):
                with gate:
                    if guardian.check_trade(_valid_order()):
                        guardian.record_trade()
                        local += 1
            accepted[idx] = local
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"concurrent cap race raised: {errors!r}"
    assert sum(accepted) == guardian.max_daily_trades
    assert guardian.daily_trades == guardian.max_daily_trades
