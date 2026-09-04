# tests/unit/test_decision_context_skew_percentile.py
"""#3199 — skew_percentile context plumbing (mirrors #1953 forecast_vol / T11-T12).

The 25Δ RR skew percentile is derived per symbol from the completed-cycle options
channels (`risk_reversal` + `risk_reversal_reference`, the SAME channels the
UpsideSkewAgent reads) and copied into the DecisionContext in
runner._score_to_signal, so it reaches the flag-gated skew size tilt in the sizer.

Missing RR / reference (< 2 distinct values) => rr_percentile is None => the
context keeps its None default (fail-safe, byte-identical decision, fail-open tilt).
"""

from types import SimpleNamespace

from core.cloud_logger import DecisionContext
from core.options_skew import rr_percentile
from core.round_table.runner import _score_to_signal

_REF = [-1.5, -0.5, 0.0, 0.5, 1.5]  # 5 distinct completed-cycle RRs


class TestDecisionContextField:
    def test_default_is_none(self):
        assert DecisionContext().skew_percentile is None

    def test_field_accepts_float(self):
        assert DecisionContext(skew_percentile=0.6).skew_percentile == 0.6

    def test_to_dict_carries_skew_percentile(self):
        """The DB writer drops keys without a backing column (forecast_vol
        precedent) — pinning to_dict makes the payload shape change explicit."""
        assert DecisionContext().to_dict()["skew_percentile"] is None
        assert DecisionContext(skew_percentile=0.6).to_dict()["skew_percentile"] == 0.6


def _vote(score: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name="TestAgent",
        score=score,
        weight=1.0,
        reasoning="unit-test vote",
        vetoed=False,
    )


def _state(**extra) -> dict:
    base = {"symbol": "AAPL", "ohlc": {"close": 100.0}, "ml": None}
    base.update(extra)
    return base


class TestRunnerPlumbing:
    def test_rr_reaches_context_matches_helper(self):
        rr = 0.8
        signal = _score_to_signal(
            _state(risk_reversal=rr, risk_reversal_reference=_REF), 0.5, [_vote()]
        )
        assert signal is not None
        expected = rr_percentile(rr, _REF)  # plumbing must match the pure helper
        assert expected is not None
        assert signal.decision_context.skew_percentile == expected

    def test_bullish_skew_ranks_above_bearish(self):
        bull = _score_to_signal(
            _state(risk_reversal=1.4, risk_reversal_reference=_REF), 0.5, [_vote()]
        ).decision_context.skew_percentile
        bear = _score_to_signal(
            _state(risk_reversal=-1.4, risk_reversal_reference=_REF), 0.5, [_vote()]
        ).decision_context.skew_percentile
        assert bull > bear

    def test_no_risk_reversal_keeps_default(self):
        signal = _score_to_signal(_state(), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.skew_percentile is None

    def test_no_reference_keeps_default(self):
        signal = _score_to_signal(_state(risk_reversal=0.8), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.skew_percentile is None

    def test_degenerate_reference_keeps_default(self):
        # < 2 distinct usable values => rr_percentile None => context None
        signal = _score_to_signal(
            _state(risk_reversal=0.8, risk_reversal_reference=[1.0, 1.0]),
            0.5,
            [_vote()],
        )
        assert signal is not None
        assert signal.decision_context.skew_percentile is None
