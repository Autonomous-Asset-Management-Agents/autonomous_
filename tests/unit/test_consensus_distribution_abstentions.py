# tests/unit/test_consensus_distribution_abstentions.py
# ADR-SEC-01 — the signal-integrity control must judge OPINIONS, not abstentions.
#
# check_distribution() flags "suspiciously uniform votes in extreme territory" as
# possible correlated data poisoning (MiFID II Art. 17 pre-trade control). It measured
# the standard deviation across ALL non-vetoed votes — including abstentions, which
# are score=0.5/weight=0.0 by contract and carry no opinion at all.
#
# Why that is a defect: an abstention parked at 0.5 is a CONSTANT. Every constant in
# the sample raises the standard deviation of any extreme cluster, because reaching
# extreme territory (mean > 0.65 / < 0.35) requires the real votes to pull AWAY from
# 0.5 — which only widens the spread. So each abstention pushes the control further
# from firing, and enough of them make it mathematically unreachable.
#
# This was already fragile: the existing agents abstain on data gaps, so a gap could
# mask a poisoning attempt. It became unconditional when two permanently-abstaining
# modules joined the gremium — verified: 9 poisoned votes fire the alert, the same 9
# plus two abstentions return "ok".
#
# aggregate() has always filtered `v.weight > 0.0` (consensus.py:180). This makes the
# integrity control agree with the aggregator: a vote nobody weighs is not evidence.

from __future__ import annotations

import pytest

from core.round_table.base_agent import VoteResult
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    STD_DEV_UNIFORMITY_THRESHOLD,
    ConsensusEngine,
)


def _vote(name: str, score: float, weight: float = 0.4, vetoed: bool = False):
    return VoteResult(
        agent_name=name,
        symbol="NVDA",
        score=score,
        weight=weight,
        reasoning="",
        vetoed=vetoed,
    )


def _abstention(name: str):
    """The abstention contract: no data -> score 0.5 at weight 0."""
    return _vote(name, 0.5, weight=0.0)


def _poisoned(n: int = 9, score: float = 0.70):
    """A correlated attack: n agents suspiciously unanimous in extreme territory."""
    return [_vote(f"Agent{i}", score) for i in range(n)]


class TestAbstentionsDoNotMaskPoisoning:
    def test_poisoned_cluster_fires_the_alert(self):
        """Baseline — the control works on real votes."""
        ok, reason = ConsensusEngine().check_distribution(_poisoned())
        assert ok is False
        assert "HIGH_CORRELATION" in reason

    def test_abstentions_do_not_silence_the_alert(self):
        """THE regression test. Two abstentions must not turn a poisoning alert into
        'ok' — they are not counter-evidence, they are the absence of evidence."""
        votes = _poisoned() + [_abstention("Fundamentals"), _abstention("Valuation")]

        ok, reason = ConsensusEngine().check_distribution(votes)

        assert ok is False, (
            "abstentions (score 0.5, weight 0) must not enter the distribution — "
            "each one is a constant that pushes an extreme cluster's std_dev up and "
            "the integrity control further from ever firing"
        )
        assert "HIGH_CORRELATION" in reason

    def test_many_abstentions_cannot_make_the_control_unreachable(self):
        """The failure mode at its limit: enough parked 0.5s and no cluster, however
        unanimous, can ever look uniform."""
        votes = _poisoned() + [_abstention(f"Dormant{i}") for i in range(8)]

        ok, _ = ConsensusEngine().check_distribution(votes)

        assert ok is False

    def test_abstention_count_does_not_change_the_verdict(self):
        """Adding modules that cannot vote must never move a signal-integrity call."""
        engine = ConsensusEngine()
        base, _ = engine.check_distribution(_poisoned())
        for extra in range(1, 5):
            with_abstentions, _ = engine.check_distribution(
                _poisoned() + [_abstention(f"D{i}") for i in range(extra)]
            )
            assert with_abstentions == base, f"{extra} abstention(s) moved the verdict"


class TestRealVotesAreStillJudged:
    """The filter must remove abstentions ONLY — never real, weighted disagreement."""

    def test_genuine_disagreement_still_passes(self):
        votes = [
            _vote("A", 0.80),
            _vote("B", 0.30),
            _vote("C", 0.55),
            _vote("D", 0.20),
        ]
        ok, reason = ConsensusEngine().check_distribution(votes)
        assert ok is True and reason == "ok"

    def test_a_weighted_vote_at_exactly_0_5_still_counts(self):
        """0.5 is only meaningless when it is an ABSTENTION. A module that weighs in
        at neutral with real weight is a genuine opinion and must stay in the sample."""
        votes = _poisoned(n=8) + [_vote("Neutral", 0.5, weight=0.4)]

        ok, _ = ConsensusEngine().check_distribution(votes)

        assert ok is True, "a weighted neutral vote is real dissent from the cluster"

    def test_vetoed_votes_remain_excluded(self):
        """Pre-existing behaviour must be untouched by the new filter."""
        votes = _poisoned() + [_vote("Guard", 0.05, weight=0.4, vetoed=True)]
        ok, reason = ConsensusEngine().check_distribution(votes)
        assert ok is False and "HIGH_CORRELATION" in reason

    def test_quorum_counts_only_opinions(self):
        """Below quorum the control cannot judge. Abstentions must not pad the count
        into a false quorum over what is really a single opinion."""
        votes = [_vote("A", 0.70)] + [_abstention(f"D{i}") for i in range(4)]

        ok, reason = ConsensusEngine().check_distribution(votes)

        assert ok is True
        assert (
            "insufficient" in reason
        ), "4 abstentions + 1 opinion is 1 opinion, not a 5-vote quorum"


class TestControlContract:
    def test_thresholds_are_untouched(self):
        """The fix changes WHO is measured, never the calibration."""
        assert STD_DEV_UNIFORMITY_THRESHOLD == 0.03
        assert SIGNAL_BUY_THRESHOLD == 0.65

    def test_distribution_filter_mirrors_the_aggregator(self):
        """aggregate() has always ignored weight-0 votes. The integrity control must
        agree with it — a vote nobody weighs is not evidence either way."""
        import inspect

        from core.round_table import consensus

        src = inspect.getsource(consensus.ConsensusEngine.check_distribution)
        assert "v.weight > 0.0" in src


class TestAbstentionIsNotMalformed:
    """An abstention is a designed outcome, not a broken vote. It must be EXCLUDED
    (unchanged) but not reported as a defect — at ~500 symbols/cycle several
    legitimately-abstaining modules generate thousands of routine WARNINGs, and real
    ones drown in them."""

    def test_abstention_is_excluded_but_not_warned_about(self, caplog):
        import logging

        engine = ConsensusEngine()
        with caplog.at_level(logging.WARNING):
            valid = engine._validate_vote(_abstention("Fundamentals"))

        assert valid is False, "an abstention must still be excluded from consensus"
        assert not [
            r for r in caplog.records if r.levelno >= logging.WARNING
        ], "a routine abstention must not be logged as an invalid vote"

    def test_a_genuinely_malformed_vote_still_warns(self, caplog):
        """The guard must not become a blanket silencer — a real defect stays loud."""
        import logging

        engine = ConsensusEngine()
        with caplog.at_level(logging.WARNING):
            valid = engine._validate_vote(_vote("Broken", 1.7, weight=0.4))

        assert valid is False
        assert any(
            r.levelno >= logging.WARNING for r in caplog.records
        ), "an out-of-range score is a real defect and must still warn"

    def test_a_zero_weight_vote_with_a_real_opinion_is_not_an_abstention(self):
        """Zero-weight votes (abstained or dormant modules under validate-before-activate)
        are excluded cleanly without producing loud WARNING logs in consensus validation.
        """
        import logging

        engine = ConsensusEngine()
        with caplog_at(logging.WARNING) as records:
            valid = engine._validate_vote(_vote("Odd", 0.9, weight=0.0))
        assert valid is False
        assert (
            not records
        ), "zero-weight votes (abstained/dormant) must not spam WARNING logs"


import contextlib  # noqa: E402


@contextlib.contextmanager
def caplog_at(level):
    import logging

    records = []

    class _H(logging.Handler):
        def emit(self, record):
            if record.levelno >= level:
                records.append(record)

    h = _H()
    lg = logging.getLogger("core.round_table.consensus")
    lg.addHandler(h)
    try:
        yield records
    finally:
        lg.removeHandler(h)
