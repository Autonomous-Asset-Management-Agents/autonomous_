"""#3618 — regime damping consistent for ALL directional voters (score shrink).

Weight scaling of every voter in a weighted mean is a no-op (the factor cancels), so
"consistent for all" forces a different mechanism: the consensus is shrunk toward the
neutral 0.5 by the risk-off factor — ``consensus' = 0.5 + factor * (consensus - 0.5)`` —
which is exactly a shrink of every remaining directional score by the same factor with
the weights untouched. The set of damped voters is DERIVED (everything that stays in
the mean after ``consensus_exclusions``); no hand-kept name list.

Owner-open question of the plan (symmetric vs. upward-only): this implementation shrinks
UPWARD only — a BUY-leaning consensus loses conviction in risk-off, a SELL-leaning
consensus is left alone (capital preservation: exits must not get rarer under stress).
Flag off or severity 0 stay byte-identical.
"""

import inspect
import os
from types import SimpleNamespace

import pytest

import config as _config
import core.round_table.consensus as consensus_mod
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    ConsensusEngine,
    consensus_exclusions,
)

pytestmark = pytest.mark.vc1  # Stufen-Marker (#3396): Round Table = VC-1

HERE = os.path.dirname(inspect.getfile(_config))


def _vote(agent, score, weight=0.45, vetoed=False):
    return VoteResult(
        agent_name=agent,
        symbol="AAPL",
        score=score,
        weight=weight,
        reasoning="t",
        vetoed=vetoed,
    )


def _arm(monkeypatch, enabled=True, damp_max=0.5, iv_on=True):
    monkeypatch.setattr(
        _config,
        "get_config",
        lambda: SimpleNamespace(
            REGIME_CONDITIONER_ENABLED=enabled,
            REGIME_RISKOFF_DAMP_MAX=damp_max,
            IMPLIED_VOL_FORECAST_ENABLED=iv_on,
        ),
    )


def _mean(votes, excluded):
    active = [v for v in votes if v.agent_name not in excluded and v.weight > 0]
    return sum(v.score * v.weight for v in active) / sum(v.weight for v in active)


def _panel(regime_score):
    return [
        _vote("RegimeDetectionAgent", regime_score, 0.50),
        _vote("DrawdownGuardAgent", 0.4, 0.60),
        _vote("MomentumAgent", 0.80, 0.45),
        _vote("LSTMSignalAgent", 0.70, 0.40),
        _vote("FundamentalsAgent", 0.75, 0.35),
        _vote("ValuationAgent", 0.65, 0.35),
        _vote("NewsSentimentAgent", 0.60, 0.35),
        _vote("VIXAwareRiskAgent", 0.20, 0.45),
    ]


# --- Szenario: alle Richtungs-Voter werden gleich behandelt ------------------------------


def test_every_remaining_voter_is_shrunk_by_the_same_factor(monkeypatch):
    _arm(monkeypatch, damp_max=0.5)
    votes = _panel(regime_score=0.0)  # severity 1.0 -> factor 0.5
    excluded = consensus_exclusions(True)
    undamped = _mean(votes, excluded)
    result = ConsensusEngine().aggregate(votes)
    # per-score shrink with unchanged weights == mean shrink
    shrunk = [
        _vote(v.agent_name, 0.5 + 0.5 * (v.score - 0.5), v.weight)
        for v in votes
        if v.agent_name not in excluded
    ]
    assert result == pytest.approx(_mean(shrunk, ()))
    assert result == pytest.approx(0.5 + 0.5 * (undamped - 0.5))
    assert abs(result - 0.5) < abs(undamped - 0.5)
    # weights are NOT touched any more (the old mechanism)
    assert {v.agent_name: v.weight for v in votes}["MomentumAgent"] == 0.45


# --- Szenario: die Daempfung wirkt -----------------------------------------------------


def test_a_buy_consensus_of_072_becomes_061_and_no_trade(monkeypatch):
    _arm(monkeypatch, damp_max=0.5)
    votes = [
        _vote("RegimeDetectionAgent", 0.0, 0.50),
        _vote("MomentumAgent", 0.72, 1.0),
        _vote("LSTMSignalAgent", 0.72, 1.0),
    ]
    result = ConsensusEngine().aggregate(votes)
    assert result == pytest.approx(0.61)
    assert 0.72 > SIGNAL_BUY_THRESHOLD > result  # BUY before, NO-TRADE after


# --- Szenario: keine Namensliste -------------------------------------------------------


def test_no_hand_kept_name_list_a_new_agent_is_damped_too(monkeypatch):
    assert not hasattr(consensus_mod, "_DIRECTIONAL_VOTER_NAMES")
    src = inspect.getsource(consensus_mod)
    assert "_DIRECTIONAL_VOTER_NAMES" not in src
    _arm(monkeypatch, damp_max=0.5)
    votes = [
        _vote("RegimeDetectionAgent", 0.0, 0.50),
        _vote("BrandNewDirectionalAgent", 0.9, 1.0),
    ]
    assert ConsensusEngine().aggregate(votes) == pytest.approx(0.5 + 0.5 * (0.9 - 0.5))


# --- Szenario: nicht-direktionale Instanzen bleiben unberuehrt --------------------------


def test_veto_and_size_input_are_untouched(monkeypatch):
    _arm(monkeypatch, damp_max=0.5, iv_on=True)
    votes = _panel(regime_score=0.0)
    for v in votes:
        if v.agent_name == "DrawdownGuardAgent":
            v.vetoed = True
    ConsensusEngine().aggregate(votes)
    by = {v.agent_name: v for v in votes}
    assert (
        by["DrawdownGuardAgent"].vetoed is True
        and by["DrawdownGuardAgent"].weight == 0.60
    )
    assert (
        by["VIXAwareRiskAgent"].score == 0.20 and by["VIXAwareRiskAgent"].weight == 0.45
    )
    # VIXAware (size input) never entered the mean: its 0.20 did not pull the result
    excluded = consensus_exclusions(True)
    assert "VIXAwareRiskAgent" in excluded


# --- Szenario: neutrales Regime und Flag aus sind byte-identisch --------------------------


@pytest.mark.parametrize("regime_score", [0.5, 0.75, 1.0])
def test_severity_zero_is_the_plain_weighted_mean(monkeypatch, regime_score):
    _arm(monkeypatch, damp_max=0.5)
    votes = _panel(regime_score)
    assert ConsensusEngine().aggregate(votes) == pytest.approx(
        _mean(votes, consensus_exclusions(True))
    )


def test_flag_off_keeps_the_old_momentum_only_block(monkeypatch):
    _arm(monkeypatch, enabled=False)
    votes = _panel(regime_score=0.30)
    result = ConsensusEngine().aggregate(votes)
    by = {v.agent_name: v for v in votes}
    assert by["MomentumAgent"].weight == pytest.approx(0.225)
    assert result == pytest.approx(_mean(votes, consensus_exclusions(True)))


# --- upward-only (owner-open question of the plan, decided conservatively) ----------------


def test_a_sell_leaning_consensus_is_not_shrunk(monkeypatch):
    _arm(monkeypatch, damp_max=0.5)
    votes = [
        _vote("RegimeDetectionAgent", 0.0, 0.50),
        _vote("MomentumAgent", 0.20, 1.0),
        _vote("LSTMSignalAgent", 0.30, 1.0),
    ]
    assert ConsensusEngine().aggregate(votes) == pytest.approx(0.25)


# --- Szenario: Wirkung ist auditierbar -------------------------------------------------


def test_engine_exposes_factor_and_undamped_consensus(monkeypatch):
    _arm(monkeypatch, damp_max=0.5)
    eng = ConsensusEngine()
    votes = [
        _vote("RegimeDetectionAgent", 0.0, 0.50),
        _vote("MomentumAgent", 0.72, 1.0),
    ]
    result = eng.aggregate(votes)
    c = eng.last_conditioning
    assert c["factor"] == pytest.approx(0.5) and c["severity"] == pytest.approx(1.0)
    assert c["undamped_consensus"] == pytest.approx(0.72)
    assert c["consensus"] == pytest.approx(result)
    eng.aggregate([_vote("MomentumAgent", 0.72, 1.0)])  # no regime vote
    assert eng.last_conditioning is None


def test_decision_record_carries_the_damping():
    from core.cloud_logger import DecisionContext
    from core.database.models import DecisionOutcome
    from core.decision_capture.capture import build_outcome_row

    ctx = DecisionContext(
        symbol="AAPL", regime_damp_factor=0.5, consensus_undamped=0.72
    )
    row = build_outcome_row(ctx)
    assert row["regime_damp_factor"] == pytest.approx(0.5)
    assert row["consensus_undamped"] == pytest.approx(0.72)
    assert "regime_damp_factor" in DecisionOutcome.__table__.columns
    assert "consensus_undamped" in DecisionOutcome.__table__.columns
    assert os.path.exists(
        os.path.join(HERE, "alembic", "versions", "0017_add_outcome_regime_damping.py")
    )


def test_runner_hands_the_conditioning_to_the_signal_context():
    import core.round_table.runner as runner

    src = inspect.getsource(runner)
    assert "regime_conditioning" in src
    assert "regime_damp_factor=" in src and "consensus_undamped=" in src
