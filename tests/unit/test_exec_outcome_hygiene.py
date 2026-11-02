"""TDD — Execution-Outcome-Hygiene (#2118, plan v3, PR #2117).

Covers the compliance-side + outcome-code changes:
* ``ComplianceGuardian.refund_trade()`` — Reserve-and-Reclaim: a reserved daily
  slot is released when the broker deterministically rejects the order (4xx).
* ``execution_outcomes.CLOSED`` — a flat-position SELL (Alpaca 404/40410000) is a
  clean "closed", not an "error".

The order_executor wiring (submit-wrap refund, 404→closed on both paths) is covered
separately in the executor tests. (The daily-cap default 50 + desktop anti-churn ride
in the separate churn PR — the cap must not go live without the churn brake.)
"""

from unittest.mock import MagicMock, patch

import pytest

from core.compliance import ComplianceGuardian


@pytest.fixture
def guardian():
    """A clean ComplianceGuardian with a mocked cloud logger (mirrors
    tests/unit/test_compliance_state_locks.py::guardian)."""
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_get_logger.return_value = MagicMock()
        g = ComplianceGuardian()
        g.daily_trades = 0
        return g


# ── refund_trade() — Reserve-and-Reclaim ─────────────────────────────────────


def test_refund_trade_decrements(guardian):
    """A reserved slot is given back on refund (2 reserved − 1 refund == 1)."""
    guardian.record_trade()
    guardian.record_trade()
    guardian.refund_trade()
    assert guardian.daily_trades == 1


def test_refund_trade_never_goes_negative(guardian):
    """Refund at 0 is a no-op (guard) — the counter must never underflow."""
    assert guardian.daily_trades == 0
    guardian.refund_trade()
    assert guardian.daily_trades == 0


def test_reserve_then_reject_frees_the_slot(guardian):
    """The core Reserve-and-Reclaim invariant: a broker-rejected reservation
    releases its slot, so a rejected order does not burn daily budget."""
    guardian.record_trade()  # reserve → 1/N
    guardian.refund_trade()  # deterministic 4xx reject → 0/N
    assert guardian.daily_trades == 0


# ── CLOSED outcome code ──────────────────────────────────────────────────────


def test_closed_outcome_code_exists():
    from core.round_table import execution_outcomes as eo

    assert eo.CLOSED == "closed"
    assert "CLOSED" in eo.__all__
