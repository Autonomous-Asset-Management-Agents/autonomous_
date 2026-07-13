"""RQ-1 (#1516) — per-decision execution-outcome store + serve-time join.

Display-only observability: the order executor records the FINAL gate result per
symbol (Iron-Dome / risk / kill-switch), and `/round-table-decisions` joins it
onto the matching decision so the console + demo can show a badge. NEVER on the
trading path. BORA: process-local in-memory, identical Desktop/Enterprise.
"""

from datetime import datetime, timezone

import pytest

from core.round_table import execution_outcomes as eo
from core.round_table import recent_decisions as rd


@pytest.fixture(autouse=True)
def _clean():
    rd.clear_recent_round_table_decisions()
    eo.clear_execution_outcomes()
    yield
    rd.clear_recent_round_table_decisions()
    eo.clear_execution_outcomes()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _seed_decision(symbol: str, action: str, epoch: float) -> None:
    rd.record_round_table_decision(
        {
            "symbol": symbol,
            "signal_action": action,
            "timestamp": _iso(epoch),
            "consensus_score": 0.6,
            "gatekeeper_approved": True,
        }
    )


class TestRecordGet:
    def test_record_and_get_roundtrips_and_uppercases_symbol(self):
        eo.record_execution_outcome(
            "aapl", eo.BLOCKED_ORDER_VALUE, "Order value limit exceeded.", ts=1000.0
        )
        rec = eo.get_execution_outcome("AAPL")
        assert rec["outcome"] == "blocked:order_value"
        assert rec["reason"] == "Order value limit exceeded."
        assert rec["ts"] == 1000.0

    def test_get_missing_is_none(self):
        assert eo.get_execution_outcome("NVDA") is None

    def test_blank_symbol_or_code_is_noop(self):
        eo.record_execution_outcome("", eo.EXECUTED)
        eo.record_execution_outcome("TSLA", "")
        assert eo.get_execution_outcome("TSLA") is None

    def test_record_never_raises(self):
        # a display-store failure must never bubble into the trading path
        eo.record_execution_outcome(None, None)  # type: ignore[arg-type]
        assert eo.get_execution_outcome("whatever") is None


class TestServeTimeJoin:
    def test_actionable_decision_gets_matching_outcome(self):
        e = 1_700_000_000.0
        _seed_decision("AAPL", "BUY", e)
        eo.record_execution_outcome(
            "AAPL", eo.BLOCKED_ORDER_VALUE, "Order value limit exceeded.", ts=e + 0.4
        )
        (d,) = rd.get_recent_round_table_decisions()
        assert d["execution_outcome"] == "blocked:order_value"
        assert d["execution_outcome_reason"] == "Order value limit exceeded."

    def test_executed_outcome_attaches(self):
        e = 1_700_000_000.0
        _seed_decision("NVDA", "BUY", e)
        eo.record_execution_outcome("NVDA", eo.EXECUTED, ts=e + 0.2)
        (d,) = rd.get_recent_round_table_decisions()
        assert d["execution_outcome"] == "executed"

    def test_hold_decision_has_no_execution_outcome(self):
        # HOLD never reaches the order executor → not "pending", explicitly None
        _seed_decision("AAPL", "HOLD", 1_700_000_000.0)
        (d,) = rd.get_recent_round_table_decisions()
        assert d["execution_outcome"] is None

    def test_actionable_without_outcome_is_pending(self):
        _seed_decision("AAPL", "SELL", 1_700_000_000.0)
        (d,) = rd.get_recent_round_table_decisions()
        assert d["execution_outcome"] == "pending"

    def test_stale_cross_cycle_outcome_is_not_attached(self):
        # an outcome recorded an hour after the decision belongs to another cycle
        e = 1_700_000_000.0
        _seed_decision("AAPL", "BUY", e)
        eo.record_execution_outcome("AAPL", eo.EXECUTED, ts=e + 3600)
        (d,) = rd.get_recent_round_table_decisions()
        assert d["execution_outcome"] == "pending"

    def test_join_does_not_mutate_the_stored_decision(self):
        e = 1_700_000_000.0
        _seed_decision("AAPL", "BUY", e)
        eo.record_execution_outcome("AAPL", eo.EXECUTED, ts=e)
        rd.get_recent_round_table_decisions()  # serve once
        raw = rd.get_round_table_decision("AAPL")
        assert "execution_outcome" not in raw  # store stays a pure record
