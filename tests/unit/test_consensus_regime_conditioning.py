# tests/unit/test_consensus_regime_conditioning.py
# #1949 (RTR-2) — ConsensusEngine: Momentum-only ×0.5 → gradual risk-off scaling
# of ALL directional voters, driven by the RegimeDetection conditioner score.
#
# Today (consensus.py): regime score < 0.45 ⇒ MomentumAgent weight ×0.5 — binary,
# hardcoded, Momentum-only. Behind REGIME_CONDITIONER_ENABLED (default OFF =
# byte-identical) the block becomes gradual:
#   severity = clamp((0.5 - regime_score) / 0.5, 0, 1)
#   factor   = 1 - REGIME_RISKOFF_DAMP_MAX * severity
# applied to the weights of the directional voters that remain in the mean
# (Momentum / LSTMSignal / RLConfidence / SpecialistAlpha) BEFORE weighted_sum.
# RegimeDetection + DrawdownGuard stay EXCLUDED from the mean (conditioners, never
# directional votes) — flag on or off.
#
# Plan §8: R6 flag-off regression · R7 gradual all-directional scaling ·
# R8 monotone gradation · R9 exclusion invariant.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.round_table.base_agent import VoteResult
from core.round_table.consensus import ConsensusEngine


def _vote(agent: str, score: float = 0.7, weight: float = 0.45) -> VoteResult:
    return VoteResult(
        agent_name=agent,
        symbol="AAPL",
        score=score,
        weight=weight,
        reasoning="test",
    )


def _panel(regime_score: float) -> list[VoteResult]:
    """A full round-table panel: conditioners + directional + non-directional."""
    return [
        _vote("RegimeDetectionAgent", score=regime_score, weight=0.50),
        _vote("DrawdownGuardAgent", score=0.4, weight=0.60),
        _vote("MomentumAgent", score=0.8, weight=0.45),
        _vote("LSTMSignalAgent", score=0.7, weight=0.40),
        _vote("RLConfidenceAgent", score=0.6, weight=0.40),
        _vote("SpecialistAlphaAgent", score=0.7, weight=0.55),
        _vote("VIXAwareRiskAgent", score=0.6, weight=0.45),
        _vote("NewsSentimentAgent", score=0.55, weight=0.35),
    ]


def _arm(monkeypatch, enabled: bool, damp_max: float = 0.5) -> None:
    """Pin the flag via config.get_config (the finance-core's only config seam)."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            REGIME_CONDITIONER_ENABLED=enabled,
            REGIME_RISKOFF_DAMP_MAX=damp_max,
        ),
    )


def _weights_after(votes: list[VoteResult]) -> dict[str, float]:
    return {v.agent_name: v.weight for v in votes}


# ---------------------------------------------------------------------------
# R6 — flag OFF (default): ONLY MomentumAgent ×0.5, old binary block (BORA)
# ---------------------------------------------------------------------------


def test_r6_flag_off_keeps_momentum_only_halving(monkeypatch):
    _arm(monkeypatch, enabled=False)
    votes = _panel(regime_score=0.30)  # bearish (< 0.45) → old block fires
    ConsensusEngine().aggregate(votes)
    w = _weights_after(votes)
    assert w["MomentumAgent"] == pytest.approx(0.225)  # 0.45 × 0.5
    assert w["LSTMSignalAgent"] == pytest.approx(0.40)  # untouched
    assert w["RLConfidenceAgent"] == pytest.approx(0.40)  # untouched
    assert w["SpecialistAlphaAgent"] == pytest.approx(0.55)  # untouched
    assert w["VIXAwareRiskAgent"] == pytest.approx(0.45)  # untouched


def test_r6b_flag_off_neutral_regime_no_damping(monkeypatch):
    _arm(monkeypatch, enabled=False)
    votes = _panel(regime_score=0.46)  # ≥ 0.45 → old block does nothing
    ConsensusEngine().aggregate(votes)
    assert _weights_after(votes)["MomentumAgent"] == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# R7 — flag ON: deep risk-off scales ALL directional voters gradually
# ---------------------------------------------------------------------------


def test_r7_flag_on_deep_riskoff_scales_all_directional(monkeypatch):
    _arm(monkeypatch, enabled=True, damp_max=0.5)
    votes = _panel(regime_score=0.10)
    ConsensusEngine().aggregate(votes)
    severity = (0.5 - 0.10) / 0.5  # 0.8
    factor = 1.0 - 0.5 * severity  # 0.6
    w = _weights_after(votes)
    assert w["MomentumAgent"] == pytest.approx(0.45 * factor)
    assert w["LSTMSignalAgent"] == pytest.approx(0.40 * factor)
    assert w["RLConfidenceAgent"] == pytest.approx(0.40 * factor)
    assert w["SpecialistAlphaAgent"] == pytest.approx(0.55 * factor)
    # Non-directional voters keep their weight (no blanket de-risking of the mean)
    assert w["VIXAwareRiskAgent"] == pytest.approx(0.45)
    assert w["NewsSentimentAgent"] == pytest.approx(0.35)


def test_r7b_flag_on_neutral_or_bullish_regime_no_damping(monkeypatch):
    _arm(monkeypatch, enabled=True)
    for regime_score in (0.5, 0.75, 1.0):
        votes = _panel(regime_score=regime_score)
        ConsensusEngine().aggregate(votes)
        w = _weights_after(votes)
        assert w["MomentumAgent"] == pytest.approx(0.45), regime_score
        assert w["LSTMSignalAgent"] == pytest.approx(0.40), regime_score


def test_r7c_flag_on_no_regime_vote_no_damping(monkeypatch):
    _arm(monkeypatch, enabled=True)
    votes = [v for v in _panel(0.1) if v.agent_name != "RegimeDetectionAgent"]
    ConsensusEngine().aggregate(votes)
    assert _weights_after(votes)["MomentumAgent"] == pytest.approx(0.45)


def test_r7d_flag_on_regime_abstain_no_damping(monkeypatch):
    """An abstaining conditioner (score 0.5, weight 0.0 — the R4 fail-open) must
    condition NOTHING: the abstention is filtered before the damping block."""
    _arm(monkeypatch, enabled=True)
    votes = _panel(regime_score=0.1)
    for v in votes:
        if v.agent_name == "RegimeDetectionAgent":
            v.score = 0.5
            v.weight = 0.0
    ConsensusEngine().aggregate(votes)
    assert _weights_after(votes)["MomentumAgent"] == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# R8 — gradual, not binary: factor(0.45) > factor(0.25) > factor(0.05)
# ---------------------------------------------------------------------------


def test_r8_gradual_damping_monotone(monkeypatch):
    _arm(monkeypatch, enabled=True)
    momentum_weights = []
    for regime_score in (0.45, 0.25, 0.05):
        votes = _panel(regime_score=regime_score)
        ConsensusEngine().aggregate(votes)
        momentum_weights.append(_weights_after(votes)["MomentumAgent"])
    assert momentum_weights[0] > momentum_weights[1] > momentum_weights[2]
    # Bounds: severity ∈ (0,1] ⇒ factor ∈ [1-damp_max, 1)
    assert momentum_weights[0] < 0.45  # 0.45 regime is already (mildly) risk-off
    assert momentum_weights[2] >= 0.45 * (1.0 - 0.5)  # never below 1-damp_max


def test_r8b_damp_max_configurable(monkeypatch):
    _arm(monkeypatch, enabled=True, damp_max=1.0)
    votes = _panel(regime_score=0.0)  # full risk-off, severity 1.0
    ConsensusEngine().aggregate(votes)
    # factor = 1 - 1.0*1.0 = 0.0 → directional voters fully muted
    assert _weights_after(votes)["MomentumAgent"] == pytest.approx(0.0)
    assert _weights_after(votes)["VIXAwareRiskAgent"] == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# R9 — conditioners stay EXCLUDED from the weighted mean (flag on AND off)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [False, True])
def test_r9_conditioners_never_enter_the_mean(monkeypatch, enabled):
    _arm(monkeypatch, enabled=enabled)
    votes = [
        _vote("RegimeDetectionAgent", score=0.0, weight=0.50),  # extreme risk-off
        _vote("DrawdownGuardAgent", score=0.0, weight=0.60),
        _vote("MomentumAgent", score=0.8, weight=0.45),
        _vote("LSTMSignalAgent", score=0.8, weight=0.40),
    ]
    result = ConsensusEngine().aggregate(votes)
    # Mean over the two 0.8 directional votes only — the 0.0 conditioner scores
    # never pull the mean down directionally (they only scale weights).
    assert result == pytest.approx(0.8)
