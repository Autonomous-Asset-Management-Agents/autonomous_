# tests/unit/test_consensus_regime_conditioning.py
# #1949 (RTR-2) — ConsensusEngine: Momentum-only ×0.5 → gradual risk-off scaling
# of ALL directional voters, driven by the RegimeDetection conditioner score.
#
# Today (consensus.py): regime score < 0.45 ⇒ MomentumAgent weight ×0.5 — binary,
# hardcoded, Momentum-only. Behind REGIME_CONDITIONER_ENABLED (default OFF =
# byte-identical) the block becomes gradual:
#   severity = clamp((0.5 - regime_score) / 0.5, 0, 1)
#   factor   = 1 - REGIME_RISKOFF_DAMP_MAX * severity
# #3618 (2026-09-24): the damping is no longer a WEIGHT scale of a 5-name list (a
# weight scale of ALL voters cancels out of the mean) but an upward-only SHRINK of the
# direction consensus toward neutral: consensus' = 0.5 + factor * (consensus - 0.5),
# applied to every voter that remains in the mean. Weights are untouched.
# RegimeDetection + DrawdownGuard stay EXCLUDED from the mean (conditioners, never
# directional votes) — flag on or off.
#
# Plan §8: R6 flag-off regression · R7 gradual shrink · R8 monotone gradation ·
# R9 exclusion invariant.

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


# The _arm() stub carries no IMPLIED_VOL_FORECAST_ENABLED (=> off): VIXAware stays in the mean.
_EXCLUDED = ("RegimeDetectionAgent", "DrawdownGuardAgent")


def _undamped(votes: list[VoteResult]) -> float:
    active = [v for v in votes if v.agent_name not in _EXCLUDED and v.weight > 0]
    return sum(v.score * v.weight for v in active) / sum(v.weight for v in active)


def _shrunk(votes: list[VoteResult], regime_score: float, damp_max: float) -> float:
    severity = max(0.0, min(1.0, (0.5 - regime_score) / 0.5))
    factor = 1.0 - damp_max * severity
    u = _undamped(votes)
    return 0.5 + factor * (u - 0.5) if u > 0.5 else u


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


def test_r7_flag_on_deep_riskoff_shrinks_the_whole_mean(monkeypatch):
    _arm(monkeypatch, enabled=True, damp_max=0.5)
    votes = _panel(regime_score=0.10)
    result = ConsensusEngine().aggregate(votes)
    severity = (0.5 - 0.10) / 0.5  # 0.8
    factor = 1.0 - 0.5 * severity  # 0.6
    assert result == pytest.approx(0.5 + factor * (_undamped(votes) - 0.5))
    # #3618: weights are untouched — the shrink acts on the consensus, for every
    # remaining voter alike (News included), never by tilting the mean.
    w = _weights_after(votes)
    for name, weight in (
        ("MomentumAgent", 0.45),
        ("LSTMSignalAgent", 0.40),
        ("RLConfidenceAgent", 0.40),
        ("SpecialistAlphaAgent", 0.55),
        ("VIXAwareRiskAgent", 0.45),
        ("NewsSentimentAgent", 0.35),
    ):
        assert w[name] == pytest.approx(weight)


def test_r7b_flag_on_neutral_or_bullish_regime_no_damping(monkeypatch):
    _arm(monkeypatch, enabled=True)
    for regime_score in (0.5, 0.75, 1.0):
        votes = _panel(regime_score=regime_score)
        result = ConsensusEngine().aggregate(votes)
        assert result == pytest.approx(_undamped(votes)), regime_score


def test_r7c_flag_on_no_regime_vote_no_damping(monkeypatch):
    _arm(monkeypatch, enabled=True)
    votes = [v for v in _panel(0.1) if v.agent_name != "RegimeDetectionAgent"]
    result = ConsensusEngine().aggregate(votes)
    assert result == pytest.approx(_undamped(votes))


def test_r7d_flag_on_regime_abstain_no_damping(monkeypatch):
    """An abstaining conditioner (score 0.5, weight 0.0 — the R4 fail-open) must
    condition NOTHING: the abstention is filtered before the damping block."""
    _arm(monkeypatch, enabled=True)
    votes = _panel(regime_score=0.1)
    for v in votes:
        if v.agent_name == "RegimeDetectionAgent":
            v.score = 0.5
            v.weight = 0.0
    result = ConsensusEngine().aggregate(votes)
    assert result == pytest.approx(_undamped(votes))


# ---------------------------------------------------------------------------
# R8 — gradual, not binary: consensus(0.45) > consensus(0.25) > consensus(0.05)
# ---------------------------------------------------------------------------


def test_r8_gradual_damping_monotone(monkeypatch):
    _arm(monkeypatch, enabled=True)
    results = []
    for regime_score in (0.45, 0.25, 0.05):
        votes = _panel(regime_score=regime_score)
        results.append(ConsensusEngine().aggregate(votes))
    undamped = _undamped(_panel(0.45))
    assert results[0] > results[1] > results[2]
    assert results[0] < undamped  # 0.45 regime is already (mildly) risk-off
    # never below the 1-damp_max shrink of the undamped distance to neutral
    assert results[2] >= 0.5 + (1.0 - 0.5) * (undamped - 0.5) - 1e-9


def test_r8b_damp_max_configurable(monkeypatch):
    _arm(monkeypatch, enabled=True, damp_max=1.0)
    votes = _panel(regime_score=0.0)  # full risk-off, severity 1.0
    result = ConsensusEngine().aggregate(votes)
    # factor = 1 - 1.0*1.0 = 0.0 → a BUY-leaning consensus is fully neutralised
    assert result == pytest.approx(0.5)
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
    # never enter the mean. Flag ON: the risk-off shrink (#3618) applies to that
    # mean (severity 1.0, factor 0.5 → 0.65); flag OFF: the plain mean 0.8.
    assert result == pytest.approx(0.65 if enabled else 0.8)
