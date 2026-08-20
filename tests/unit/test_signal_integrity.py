# tests/unit/test_signal_integrity.py
# Epic 6.1 / Security — Signal Integrity Check (D6 Compliance Gap)
#
# AI Security Control: Statistical anomaly detection on agent vote distributions.
# Closes the gap identified in the security governance audit (2026-05-12):
# ComplianceGatekeeper only checks Execution Risk, not Signal Integrity.
#
# Check logic (Stufe A — stdlib only, no new dependencies):
#   - HIGH_CORRELATION: std_dev < STD_DEV_MIN_THRESHOLD when score is in
#     BUY or SELL territory → suspicious agent alignment
#   - QUORUM_FAILURE: fewer than MIN_VOTES valid votes → unreliable consensus
#
# ADR: MiFID II Art. 17 — pre-trade controls must cover signal integrity.
# Policy: CODING_POLICY.md §11.5 TDD — tests written BEFORE implementation.

from __future__ import annotations

import allure
import pytest

from core.round_table.base_agent import VoteResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_vote(
    agent_name: str,
    score: float,
    weight: float = 1.0,
    vetoed: bool = False,
) -> VoteResult:
    return VoteResult(
        agent_name=agent_name,
        symbol="AAPL",
        score=score,
        weight=weight,
        reasoning=f"Test reasoning for {agent_name}",
        vetoed=vetoed,
    )


# ---------------------------------------------------------------------------
# Tests: check_distribution() — exists and returns (bool, str)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSignalIntegrityInterface:
    """ConsensusEngine.check_distribution() must exist and return (bool, str)."""

    def test_method_exists(self):
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        assert hasattr(
            engine, "check_distribution"
        ), "ConsensusEngine must have check_distribution() method"

    def test_returns_tuple(self):
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote("A", 0.5), make_vote("B", 0.5)]
        result = engine.check_distribution(votes)
        assert (
            isinstance(result, tuple) and len(result) == 2
        ), "check_distribution must return (bool, str)"
        ok, reason = result
        assert isinstance(ok, bool)
        assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# Tests: Healthy distributions → PASS
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSignalIntegrityHealthy:
    """Normal, diverse vote distributions should always pass."""

    def test_diverse_buy_votes_pass(self):
        """Diverse opinions in BUY territory → legitimate consensus."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("MomentumAgent", 0.80),
            make_vote("TrendAgent", 0.72),
            make_vote("SentimentAgent", 0.68),
            make_vote("RiskAgent", 0.55),
            make_vote("LSTMAgent", 0.75),
            make_vote("RLAgent", 0.60),
        ]
        ok, reason = engine.check_distribution(votes)
        assert ok is True, f"Diverse BUY votes should pass, got reason={reason}"

    def test_diverse_sell_votes_pass(self):
        """Diverse opinions in SELL territory → legitimate consensus."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("MomentumAgent", 0.22),
            make_vote("TrendAgent", 0.30),
            make_vote("SentimentAgent", 0.18),
            make_vote("RiskAgent", 0.35),
            make_vote("LSTMAgent", 0.25),
        ]
        ok, reason = engine.check_distribution(votes)
        assert ok is True, f"Diverse SELL votes should pass, got reason={reason}"

    def test_mixed_opinion_passes(self):
        """Split opinions (some BUY, some SELL) → clearly not correlated."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            make_vote("BullAgent", 0.85),
            make_vote("BearAgent", 0.15),
            make_vote("NeutralAgent", 0.50),
            make_vote("RiskAgent", 0.60),
        ]
        ok, reason = engine.check_distribution(votes)
        assert ok is True, f"Mixed opinions should pass, got reason={reason}"

    def test_hold_zone_always_passes(self):
        """Perfect alignment in HOLD zone (0.35–0.65) is not suspicious."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote(f"Agent{i}", 0.50) for i in range(9)]
        ok, reason = engine.check_distribution(votes)
        assert ok is True, f"Perfect HOLD alignment should pass, got reason={reason}"

    def test_insufficient_votes_returns_pass_with_flag(self):
        """Fewer than 3 votes: cannot compute std_dev reliably → pass with note."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote("OnlyAgent", 0.95)]
        ok, reason = engine.check_distribution(votes)
        assert ok is True
        assert "insufficient" in reason.lower()

    def test_vetoed_votes_excluded_from_check(self):
        """Vetoed votes must NOT count toward the distribution check."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        # 7 vetoed votes all at 0.99 → if counted would trigger HIGH_CORRELATION
        # 3 active diverse votes → should pass
        votes = [
            make_vote("VetoedA", 0.99, vetoed=True),
            make_vote("VetoedB", 0.99, vetoed=True),
            make_vote("VetoedC", 0.99, vetoed=True),
            make_vote("VetoedD", 0.99, vetoed=True),
            make_vote("VetoedE", 0.99, vetoed=True),
            make_vote("VetoedF", 0.99, vetoed=True),
            make_vote("VetoedG", 0.99, vetoed=True),
            make_vote("ActiveA", 0.80),
            make_vote("ActiveB", 0.65),
            make_vote("ActiveC", 0.55),
        ]
        ok, reason = engine.check_distribution(votes)
        assert (
            ok is True
        ), f"Vetoed votes must be excluded from distribution check, reason={reason}"


# ---------------------------------------------------------------------------
# Tests: HIGH_CORRELATION anomaly → FAIL
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSignalIntegrityHighCorrelation:
    """Suspiciously uniform votes in extreme territory should be flagged."""

    def test_perfect_buy_alignment_flagged(self):
        """9 agents all voting ~0.99 is statistically impossible without data poisoning."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        # All 9 agents perfectly aligned in BUY territory: std_dev ≈ 0.0
        votes = [make_vote(f"Agent{i}", 0.99) for i in range(9)]
        ok, reason = engine.check_distribution(votes)
        assert ok is False, "Perfect BUY alignment must trigger HIGH_CORRELATION alert"
        assert "HIGH_CORRELATION" in reason

    def test_perfect_sell_alignment_flagged(self):
        """All agents voting 0.01 in SELL territory → correlated manipulation."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote(f"Agent{i}", 0.01) for i in range(9)]
        ok, reason = engine.check_distribution(votes)
        assert ok is False, "Perfect SELL alignment must trigger HIGH_CORRELATION alert"
        assert "HIGH_CORRELATION" in reason

    def test_near_perfect_buy_alignment_flagged(self):
        """Tiny variance (std < threshold) in BUY zone → flagged."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        # std_dev ≈ 0.003 — far below suspicious threshold
        votes = [
            make_vote("Agent1", 0.970),
            make_vote("Agent2", 0.971),
            make_vote("Agent3", 0.969),
            make_vote("Agent4", 0.972),
            make_vote("Agent5", 0.968),
            make_vote("Agent6", 0.970),
        ]
        ok, reason = engine.check_distribution(votes)
        assert (
            ok is False
        ), f"Near-perfect BUY alignment must be flagged, reason={reason}"
        assert "HIGH_CORRELATION" in reason

    def test_reason_contains_std_and_mean(self):
        """Reason string must include diagnostic values for audit trail."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote(f"Agent{i}", 0.95) for i in range(6)]
        ok, reason = engine.check_distribution(votes)
        assert ok is False
        # Reason must be audit-traceable
        assert "std=" in reason or "stddev=" in reason or "std_dev=" in reason
        assert "mean=" in reason

    def test_high_correlation_in_hold_zone_does_not_flag(self):
        """std_dev < threshold in HOLD zone (0.35–0.65) must NOT be flagged.

        If agents agree the signal is HOLD, that is a legitimate outcome —
        e.g. a sideways market. Only extreme BUY/SELL uniform agreement is suspicious.
        """
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        # All agents voting 0.50: perfect HOLD alignment — legitimate
        votes = [make_vote(f"Agent{i}", 0.500) for i in range(9)]
        ok, _ = engine.check_distribution(votes)
        assert ok is True

    def test_boundary_buy_threshold(self):
        """Score just above BUY threshold (0.65) with extreme uniformity → flagged."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote(f"Agent{i}", 0.70) for i in range(7)]
        ok, reason = engine.check_distribution(votes)
        assert (
            ok is False
        ), f"Uniform votes just above BUY threshold must be flagged: {reason}"

    def test_boundary_sell_threshold(self):
        """Score just below SELL threshold (0.35) with extreme uniformity → flagged."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote(f"Agent{i}", 0.30) for i in range(7)]
        ok, reason = engine.check_distribution(votes)
        assert (
            ok is False
        ), f"Uniform votes just below SELL threshold must be flagged: {reason}"


# ---------------------------------------------------------------------------
# Tests: Quorum (minimum 3 active votes required for reliable check)
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestSignalIntegrityQuorum:
    """Edge cases around minimum vote count."""

    def test_two_votes_returns_pass_with_insufficient_flag(self):
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [make_vote("A", 0.99), make_vote("B", 0.99)]
        ok, reason = engine.check_distribution(votes)
        # With only 2 votes: stdlib statistics.stdev requires >= 2 — passes but flags
        assert ok is True
        assert "insufficient" in reason.lower()

    def test_three_votes_is_checked(self):
        """3 votes is the minimum for a meaningful distribution check."""
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        # 3 perfectly aligned BUY votes → should still trigger
        votes = [make_vote(f"Agent{i}", 0.99) for i in range(3)]
        ok, reason = engine.check_distribution(votes)
        assert ok is False
        assert "HIGH_CORRELATION" in reason

    def test_empty_vote_list(self):
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        ok, reason = engine.check_distribution([])
        assert ok is True
        assert "insufficient" in reason.lower()
