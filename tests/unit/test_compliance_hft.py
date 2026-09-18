"""Gate 5 — HFT Throttle (ADR-C07 / GTM-1 #1802) unit + concurrency tests.

This module closes the test-coverage gap for the HFT throttle gate of
``ComplianceGuardian.check_order`` (compliance.py Gate 5) and proves its
thread-safety (the shared-state gap flagged in the #1835 review).

Design notes
------------
* Orders MUST clear the earlier gates to reach Gate 5, so every fixture order
  uses a valid US-equity ticker (AAPL/MSFT/...), complete MiFID fields, a value
  well under ``COMPLIANCE_MAX_ORDER_VALUE`` and side ``buy`` — all-buys means the
  wash-trade gate (Gate 3) never fires.
* The throttle window is wall-clock (``time.time``). To keep the timing tests
  deterministic (never flaky under CI load) we freeze/inject ``time.time`` via a
  monkeypatched clock rather than relying on real elapsed time.
* Config defaults asserted explicitly (per the TDD brief): per-symbol cap = 2,
  aggregate cap = 10.
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

    Mirrors tests/unit/test_compliance.py::guardian.
    """
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_get_logger.return_value = MagicMock()
        g = ComplianceGuardian()
        g._recent_trades = []
        g._hft_recent_orders = []
        return g


def _valid_order(symbol="AAPL", user_id="user_a", timestamp=None):
    """A Gate-5-reachable order: valid US equity, complete MiFID fields, buy side,
    value (5 * 100 = 500) far below COMPLIANCE_MAX_ORDER_VALUE."""
    return {
        "symbol": symbol,
        "side": "buy",
        "quantity": 5,
        "price": 100.0,
        "strategy_id": "test_strat",
        "timestamp": timestamp if timestamp is not None else time.time(),
        "user_id": user_id,
    }


# ── 0. Config-default characterization ───────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_hft_default_thresholds(guardian):
    """Documented thresholds: per-symbol = 2, aggregate = 10 orders/sec."""
    assert guardian.hft_max_orders_per_sec_symbol == 2
    assert guardian.hft_max_orders_per_sec_aggregate == 10


# ── 1. Under both limits → approved (no false positive) ──────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_under_limits_all_approved(guardian):
    """A handful of orders spread under both caps all pass — no false positive.

    2 symbols x 2 orders = 4 aggregate (< 10) and 2 per symbol (== symbol cap of
    2 approved, which is allowed; the 3rd for a symbol would reject). All within
    the same frozen second.
    """
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert guardian.check_order(_valid_order("MSFT")) is True
        assert guardian.check_order(_valid_order("MSFT")) is True
    assert len(guardian._hft_recent_orders) == 4


# ── 2. Per-symbol cap ────────────────────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_per_symbol_cap_rejects_third_same_symbol(guardian):
    """(symbol_limit + 1)-th order for the SAME symbol within 1s → hft_throttle.

    symbol cap = 2 → orders #1 and #2 approve, #3 rejects.
    """
    from core.compliance import get_compliance_counters, reset_compliance_counters

    reset_compliance_counters()
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        assert guardian.check_order(_valid_order("AAPL")) is True  # #1
        assert guardian.check_order(_valid_order("AAPL")) is True  # #2
        assert guardian.check_order(_valid_order("AAPL")) is False  # #3 → throttled
    assert get_compliance_counters()["reject_reasons"].get("hft_throttle") == 1


# ── 3. Aggregate cap ─────────────────────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_aggregate_cap_rejects_eleventh_across_symbols(guardian):
    """(aggregate_limit + 1)-th order ACROSS symbols within 1s → hft_throttle.

    aggregate cap = 10. Spread across distinct symbols (2 per symbol so the
    per-symbol gate never fires first): 5 symbols x 2 = 10 approved, the 11th
    (a 6th symbol) trips the aggregate gate.
    """
    from core.compliance import get_compliance_counters, reset_compliance_counters

    reset_compliance_counters()
    symbols = ["AAPL", "MSFT", "TSLA", "GOOG", "AMZN"]  # 5 symbols x 2 = 10
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        for sym in symbols:
            assert guardian.check_order(_valid_order(sym)) is True
            assert guardian.check_order(_valid_order(sym)) is True
        # 10 approved now buffered; an 11th (fresh symbol, so NOT per-symbol
        # limited) must be rejected by the aggregate gate.
        assert guardian.check_order(_valid_order("NVDA")) is False
    assert get_compliance_counters()["reject_reasons"].get("hft_throttle") == 1
    assert len(guardian._hft_recent_orders) == 10  # the reject is NOT buffered


# ── 4. Boundary: exactly at limit allowed, one over rejects ──────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_per_symbol_boundary_exact_then_over(guardian):
    """Per-symbol: exactly `symbol cap` (2) approved orders allowed; +1 rejects."""
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        # exactly at the limit: 2 approved
        for _ in range(guardian.hft_max_orders_per_sec_symbol):
            assert guardian.check_order(_valid_order("AAPL")) is True
        # one over the limit → reject
        assert guardian.check_order(_valid_order("AAPL")) is False


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_aggregate_boundary_exact_then_over(guardian):
    """Aggregate: exactly `aggregate cap` (10) approved allowed; +1 rejects."""
    symbols = ["AAPL", "MSFT", "TSLA", "GOOG", "AMZN"]
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        approved = 0
        for sym in symbols:
            for _ in range(2):
                assert guardian.check_order(_valid_order(sym)) is True
                approved += 1
        assert approved == guardian.hft_max_orders_per_sec_aggregate  # exactly 10
        # one over the aggregate limit → reject
        assert guardian.check_order(_valid_order("NVDA")) is False


# ── 5. 1-second window housekeeping ──────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_window_housekeeping_drops_stale_orders(guardian):
    """Orders older than 1.0s are dropped from the buffer, so a later order that
    would otherwise be over the per-symbol cap is NOT throttled.

    We advance the injected clock past the 1.0s window between bursts.
    """
    t0 = 1_000_000.0
    # Two AAPL orders at t0 — fills the per-symbol cap (2).
    with patch("core.compliance.time.time", return_value=t0):
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert guardian.check_order(_valid_order("AAPL")) is True
        # A 3rd right now WOULD be throttled — sanity check the setup.
        assert guardian.check_order(_valid_order("AAPL")) is False

    # Advance > 1.0s: the two t0 orders are now stale and must be housekept out.
    t1 = t0 + 1.5
    with patch("core.compliance.time.time", return_value=t1):
        assert guardian.check_order(_valid_order("AAPL")) is True
        # Buffer holds only the single fresh order (the 2 stale ones were dropped).
        assert len(guardian._hft_recent_orders) == 1


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_window_boundary_exactly_one_second_still_stale(guardian):
    """Housekeeping uses a strict `now - ts < 1.0`, so an order at exactly 1.0s
    old is dropped (it is NOT strictly younger than the window)."""
    t0 = 1_000_000.0
    with patch("core.compliance.time.time", return_value=t0):
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert guardian.check_order(_valid_order("AAPL")) is True

    # Exactly 1.0s later: now - ts == 1.0, which is NOT < 1.0 → both dropped.
    with patch("core.compliance.time.time", return_value=t0 + 1.0):
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert len(guardian._hft_recent_orders) == 1


# ── 6. Per-user isolation ────────────────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_per_user_isolation_symbol_cap(guardian):
    """User A's per-symbol burst does not throttle user B for the same symbol."""
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        # User A fills & overshoots the AAPL per-symbol cap.
        assert guardian.check_order(_valid_order("AAPL", user_id="user_a")) is True
        assert guardian.check_order(_valid_order("AAPL", user_id="user_a")) is True
        assert guardian.check_order(_valid_order("AAPL", user_id="user_a")) is False
        # User B is unaffected — their own AAPL count is still zero.
        assert guardian.check_order(_valid_order("AAPL", user_id="user_b")) is True
        assert guardian.check_order(_valid_order("AAPL", user_id="user_b")) is True


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_per_user_isolation_aggregate_cap(guardian):
    """User A's aggregate burst does not throttle user B."""
    symbols = ["AAPL", "MSFT", "TSLA", "GOOG", "AMZN"]
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        for sym in symbols:
            for _ in range(2):
                assert guardian.check_order(_valid_order(sym, user_id="user_a")) is True
        # User A is now at the aggregate cap; their next order rejects.
        assert guardian.check_order(_valid_order("NVDA", user_id="user_a")) is False
        # User B, with an empty per-user window, is approved.
        assert guardian.check_order(_valid_order("NVDA", user_id="user_b")) is True


# ── 7. Reject code + single audit ────────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_hft_reject_code_and_single_audit(guardian):
    """A throttled order returns False, carries reason_code `hft_throttle`, and is
    audited EXACTLY once (BUG-AI-101 / #1237 single-audit invariant)."""
    from core.compliance import get_compliance_counters, reset_compliance_counters

    reset_compliance_counters()
    now = 1_000_000.0
    with patch("core.compliance.time.time", return_value=now):
        assert guardian.check_order(_valid_order("AAPL")) is True
        assert guardian.check_order(_valid_order("AAPL")) is True
        # Reset the audit mock so we count ONLY the rejected order's audit.
        guardian.cloud_logger.log_compliance_event.reset_mock()
        assert guardian.check_order(_valid_order("AAPL")) is False

    # Exactly one audit entry for the rejected order.
    assert guardian.cloud_logger.log_compliance_event.call_count == 1
    _, kwargs = guardian.cloud_logger.log_compliance_event.call_args
    assert kwargs.get("approved") is False
    # Machine reason code recorded once.
    assert get_compliance_counters()["reject_reasons"].get("hft_throttle") == 1


# ── B. Thread-safety / concurrency ───────────────────────────────────────────


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("HFT Throttle (ADR-C07)")
def test_concurrent_check_order_no_corruption(guardian):
    """#1835: N threads hammer check_order concurrently with valid orders →
    no exception and no torn / lost state in `_hft_recent_orders`.

    The shared-state gap is the housekeep -> read -> append read-modify-write on
    ``_hft_recent_orders`` (a filtered-list *reassignment* followed by an
    ``.append``). Without a lock, thread B's append can be stomped by thread A's
    reassignment → a LOST UPDATE (fewer buffered records than were approved).

    To make that corruption *deterministically observable* we:
      * freeze the wall clock to a single instant, so every approved order is
        in-window and MUST remain buffered — the buffer length is then a hard
        conservation invariant (== number of approved orders), and
      * raise the caps so the throttle itself never rejects — every call is on
        the shared-buffer write path, maximising contention.

    Corruption signatures asserted:
      1. no worker raised (a list mutated mid-iteration, or a torn record),
      2. every buffered entry is a well-formed record (no torn dicts),
      3. CONSERVATION: buffered count == total approved (no lost append).
    """
    # Disable the throttle for this stress test so every call reaches the buffer
    # write path; the caps' correctness is covered by the deterministic tests.
    guardian.hft_max_orders_per_sec_symbol = 10**9
    guardian.hft_max_orders_per_sec_aggregate = 10**9

    n_threads = 16
    per_thread = 200
    errors = []
    approved_total = [0] * n_threads
    barrier = threading.Barrier(n_threads)
    frozen_now = 1_000_000.0

    def _thread_symbol(idx):
        # A UNIQUE, digit-free, letter-only US-equity ticker per thread. Digits
        # would trip Gate 1b (spot-US-equity shape) BEFORE Gate 5, so the order
        # would never reach the buffer write path we are stress-testing.
        return "Q" + chr(ord("A") + idx // 26) + chr(ord("A") + idx % 26)

    def worker(idx):
        try:
            barrier.wait()  # release all threads together for max contention
            sym = _thread_symbol(idx)  # unique letter-only equity ticker
            uid = f"user_{idx}"
            local_ok = 0
            for _ in range(per_thread):
                order = _valid_order(sym, user_id=uid, timestamp=frozen_now)
                if guardian.check_order(order) is True:
                    local_ok += 1
            approved_total[idx] = local_ok
        except Exception as exc:  # noqa: BLE001 — any raise IS the corruption signal
            errors.append(exc)

    # Freeze the clock so no approved order is ever housekept out mid-run: the
    # buffer length becomes an exact conservation invariant.
    with patch("core.compliance.time.time", return_value=frozen_now):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    # 1. No worker raised.
    assert not errors, f"concurrent check_order raised: {errors!r}"
    # 2. Every buffered entry is a well-formed record (no torn dicts).
    for rec in list(guardian._hft_recent_orders):
        assert {"symbol", "side", "timestamp", "user_id"} <= set(rec.keys())
    # 3. CONSERVATION — every approved order is still in the frozen window, so the
    #    buffer MUST hold exactly as many records as were approved. A shortfall is
    #    a lost update (torn shared state) — the #1835 corruption signature.
    assert len(guardian._hft_recent_orders) == sum(approved_total), (
        "lost update in _hft_recent_orders: approved="
        f"{sum(approved_total)} buffered={len(guardian._hft_recent_orders)}"
    )
