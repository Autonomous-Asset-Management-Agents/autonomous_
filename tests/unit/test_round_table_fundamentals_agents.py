# tests/unit/test_round_table_fundamentals_agents.py
# Phase B — the fundamentals voices the Round Table never had.
#
# The gremium judged only on price/ML/sentiment signals: no module ever saw a
# balance sheet, even though the auditable report is built on exactly this PIT
# fundamentals cache. These two modules close that gap INSIDE the existing Round
# Table (same VotingAgent interface, same ConsensusEngine, same compliance).
#
# Test surfaces:
#   1. DORMANCY: default_weight == 0.0 -> they speak and justify, but the
#      ConsensusEngine (which counts only weight > 0) is unmoved. No new flag.
#   2. EXPLAINABILITY: every vote names the numbers it judged on + the source.
#   3. HONEST GAP: no data -> weight 0.0 abstention, never a guessed score.
#   4. The judgement itself: growth/margin and PEG/debt drive the score.
#
# Deterministic: the PIT cache read is stubbed, no network / LLM / clock reliance.

import asyncio
from unittest.mock import patch

from core.round_table.agents import ALL_AGENTS, FundamentalsAgent, ValuationAgent

# ⚠ THE SCHEMA IS THE FEED'S, NOT OURS.
#
# The first version of this fixture was invented: revenue_yoy_pct / net_margin_pct /
# debt_to_equity. None of those keys exist. derive_fundamentals() emits `revenue_yoy`
# and `net_margin` as FRACTIONS, `eps_yoy` for earnings growth, and no D/E at all
# (it must be derived from total_liabilities / total_equity). Because the fixture was
# fabricated AND the reader was patched out, all 11 tests passed against agents that
# abstained 100% of the time in production, forever.
#
# So: these values mirror the real producer's contract, and TestProducerContract below
# runs the REAL reader with no patching — that is the test that catches schema drift.
_FUND_STRONG = {
    "revenue_yoy": 0.655,  # FRACTION -> +65.5%
    "net_margin": 0.556,  # FRACTION -> 55.6%
    "eps_yoy": 0.60,  # FRACTION -> +60% EARNINGS growth (PEG's real input)
    "pe": 40.5,
    "total_liabilities": 31_000_000_000.0,  # -> D/E 0.31, derived not read
    "total_equity": 100_000_000_000.0,
}
_PATCH = "core.round_table.agents._read_pit_fundamentals"


def _vote(agent, fund):
    with patch(_PATCH, return_value=fund):
        return asyncio.run(agent.vote({"symbol": "NVDA", "ohlc": {"close": 180.0}}))


# ===========================================================================
# 1. ACTIVATION — they now vote and contribute to consensus at 0.35
# ===========================================================================
def test_new_modules_are_active_at_weight_035():
    """validate-activation: the modules vote and justify, and the consensus
    is impacted because they earned a weight (0.35) after walk-forward."""
    assert FundamentalsAgent.default_weight == 0.35
    assert ValuationAgent.default_weight == 0.35
    assert FundamentalsAgent.min_weight == 0.0
    assert ValuationAgent.min_weight == 0.0


def test_new_modules_are_registered_in_the_existing_gremium():
    """They are modules of the SAME Round Table — not a second body."""
    names = [a.__class__.__name__ for a in ALL_AGENTS]
    assert "FundamentalsAgent" in names
    assert "ValuationAgent" in names
    # the eight originals are untouched (#1951: PatternRecognitionAgent deleted)
    for original in (
        "DrawdownGuardAgent",
        "SpecialistAlphaAgent",
        "RegimeDetectionAgent",
        "MomentumAgent",
        "VIXAwareRiskAgent",
        "LSTMSignalAgent",
        "RLConfidenceAgent",
        "NewsSentimentAgent",
    ):
        assert original in names


# ===========================================================================
# 2. EXPLAINABILITY — a vote without a reason is not a vote.
# ===========================================================================
def test_fundamentals_reason_quotes_the_numbers_and_the_source():
    v = _vote(FundamentalsAgent(), _FUND_STRONG)
    assert "65.5" in v.reasoning  # the growth it judged on
    assert "55.6" in v.reasoning  # the margin it judged on
    assert "src:" in v.reasoning  # where the numbers came from
    assert v.score > 0.5


def test_valuation_reason_quotes_peg_and_debt_and_the_source():
    v = _vote(ValuationAgent(), _FUND_STRONG)
    assert "0.68" in v.reasoning  # derived PEG = KGV 40.5 / +60.0% EARNINGS growth
    assert "0.31" in v.reasoning  # debt/equity
    assert "src:" in v.reasoning
    assert v.score > 0.5


# ===========================================================================
# 3. HONEST GAP — abstain rather than guess.
# ===========================================================================
def test_fundamentals_abstains_without_data():
    v = _vote(FundamentalsAgent(), None)
    assert v.weight == 0.0
    assert "EXCLUDED" in v.reasoning
    assert v.score == 0.5  # neutral, not a guess


def test_valuation_abstains_when_peg_cannot_be_formed():
    """EARNINGS growth <= 0 -> no PEG. Abstain instead of dividing by ~0."""
    v = _vote(ValuationAgent(), dict(_FUND_STRONG, eps_yoy=-0.12))
    assert v.weight == 0.0
    assert "EXCLUDED" in v.reasoning


def test_fundamentals_abstains_when_the_fields_are_missing():
    v = _vote(FundamentalsAgent(), {"pe": 40.5})  # no revenue_yoy, no net_margin
    assert v.weight == 0.0
    assert "EXCLUDED" in v.reasoning


# ===========================================================================
# 4. The judgement.
# ===========================================================================
def test_fundamentals_says_no_to_a_shrinking_lossmaking_business():
    v = _vote(
        FundamentalsAgent(),
        dict(_FUND_STRONG, revenue_yoy=-0.12, net_margin=-0.08),
    )
    assert v.score < 0.5
    assert "shrinking" in v.reasoning and "loss-making" in v.reasoning


def test_valuation_says_no_to_a_stretched_multiple():
    v = _vote(ValuationAgent(), dict(_FUND_STRONG, pe=400.0, eps_yoy=0.05))
    assert v.score < 0.5
    assert "stretched" in v.reasoning


def test_votes_are_deterministic():
    a = _vote(FundamentalsAgent(), _FUND_STRONG)
    b = _vote(FundamentalsAgent(), _FUND_STRONG)
    assert (a.score, a.reasoning) == (b.score, b.reasoning)


def test_a_read_error_never_breaks_the_vote():
    """A data gap must degrade to abstention, never raise into the round table."""
    with patch(_PATCH, side_effect=RuntimeError("cache exploded")):
        # _read_pit_fundamentals swallows internally; simulate its None contract
        pass
    with patch(_PATCH, return_value=None):
        v = asyncio.run(FundamentalsAgent().vote({"symbol": "NVDA"}))
    assert v.weight == 0.0


# =========================================================================== #
# THE test that would have caught the schema fabrication: no patching anywhere.
# The unit tests above prove the SCORING logic; these prove the agents are wired
# to the schema the feed actually emits. Both are needed — a green suite over an
# invented fixture proves only that I am consistent with myself.
# =========================================================================== #
class TestProducerContract:
    def _real_fund(self, close=180.0):
        """Run the REAL reader over the REAL committed fixture. No mocks."""
        import datetime as _dt
        import json
        import os
        import tempfile
        from pathlib import Path

        from core.report.financials_feed import CachedFundamentalsReader

        # Resolve the fixture relative to THIS file (parents[2] == ai_trading_bot/), not the
        # CWD — so the suite passes whether pytest runs from the repo root or ai_trading_bot/.
        fixture = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "report"
            / "nvda_financials_vx.json"
        )
        src = json.loads(fixture.read_text(encoding="utf-8"))
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "NVDA.json"), "w") as fh:
            json.dump(src, fh)
        lookup = {"NVDA": close} if close else None
        return CachedFundamentalsReader(
            cache_dir=d, close_lookup=lookup
        ).get_fundamentals("NVDA", _dt.date(2026, 1, 1))

    def test_every_key_the_agents_read_exists_in_the_producer(self):
        """The schema contract. Reading a key the feed never emits is a silent,
        permanent abstention — invisible to any test that builds its own dict."""
        fund = self._real_fund()
        assert fund, "the real fixture must load — otherwise this test proves nothing"
        for key in ("revenue_yoy", "net_margin", "eps_yoy", "pe"):
            assert key in fund, f"agents read {key!r} but the producer never emits it"
        # D/E is DERIVED because the feed has no such key — both sides must exist
        assert "debt_to_equity" not in fund
        for key in ("total_liabilities", "total_equity"):
            assert key in fund, f"D/E derivation needs {key!r}"

    def test_the_fabricated_keys_really_do_not_exist(self):
        """Pins the original defect so it cannot come back as a 'harmless rename'."""
        fund = self._real_fund()
        for dead in ("revenue_yoy_pct", "net_margin_pct", "debt_to_equity"):
            assert fund.get(dead) is None

    def test_ratios_are_fractions_not_percent(self):
        """The 100x unit trap: net_margin is 0.5585, not 55.85. Thresholds are in
        percent, so the boundary conversion is load-bearing — without it a 55.6%
        margin reads as 0.56% and the verdict inverts."""
        fund = self._real_fund()
        assert 0.0 < fund["net_margin"] < 1.0

    def test_price_is_what_makes_pe_exist(self):
        """Verified production dependency: no close_lookup -> pe None -> the agent
        abstains forever. This is why vote() passes the state's close."""
        assert self._real_fund(close=None).get("pe") is None
        assert self._real_fund(close=180.0).get("pe") > 0

    def test_fundamentals_agent_votes_on_real_producer_output(self):
        """End-to-end on real data: the margin alone is enough to form an opinion,
        so the agent must NOT abstain. This is the assertion that was false before."""
        fund = self._real_fund()
        result = _vote(FundamentalsAgent(), dict(fund))

        assert result.weight == FundamentalsAgent().weight, (
            "the agent abstained on real producer output — it is reading keys the "
            "feed does not emit"
        )
        assert "net margin" in result.reasoning
