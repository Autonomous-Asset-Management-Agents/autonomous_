# tests/unit/test_audit_placeholder_3251.py
# Issue #3251 — MiFID-Audit-Integrität.
#
# Two audit-integrity defects in the decision record:
#   (1) adx_14 / volatility_20d carried hard placeholder defaults (25.0 / 0.02)
#       that the live RoundTableV2 path never overrides — they landed in the
#       MiFID decision log as if they were REAL measurements ("security theatre").
#   (2) reasoning_trace inherited the [:200]-truncated top-3 reasoning_summary,
#       so only ~2 of the voting agents' MiFID reasonings survived; the rest of
#       the pre-trade decision rationale was silently lost.
#
# TDD Red→Green: these tests fail on main and pass after the fix.

import os
import sys

import allure
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.cloud_logger import DecisionContext  # noqa: E402
from core.round_table.base_agent import VoteResult  # noqa: E402


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("MiFID Audit Integrity (#3251)")
class TestPlaceholderNotLoggedAsMeasurement:
    """(1) An unset adx_14 / volatility_20d must NOT serialize as a fabricated
    25.0 / 0.02 measurement — it must be null/omitted so an auditor can tell the
    value was never observed."""

    def test_unset_adx_and_vol_serialize_as_null(self):
        ctx = DecisionContext(symbol="AAPL", action="HOLD")
        d = ctx.to_dict()

        assert (
            d["adx_14"] is None
        ), "unset adx_14 must serialize as null, not the 25.0 placeholder"
        assert (
            d["volatility_20d"] is None
        ), "unset volatility_20d must serialize as null, not the 0.02 placeholder"

    def test_real_measurements_pass_through_unchanged(self):
        ctx = DecisionContext(
            symbol="AAPL", action="BUY", adx_14=31.5, volatility_20d=0.047
        )
        d = ctx.to_dict()

        assert d["adx_14"] == 31.5
        assert d["volatility_20d"] == 0.047

    def test_build_reasoning_summary_none_safe_on_hold(self):
        # The ADX weak-trend guard must not crash when adx_14 is unset (None).
        ctx = DecisionContext(symbol="MSFT", action="HOLD", lstm_prediction=0.1)
        summary = ctx.build_reasoning_summary()
        assert "HELD" in summary  # no exception, ADX reason simply omitted


def _vote(name: str, reasoning: str, weight: float) -> VoteResult:
    return VoteResult(
        agent_name=name,
        symbol="AAPL",
        score=0.5,
        weight=weight,
        reasoning=reasoning,
        vetoed=False,
    )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("MiFID Audit Integrity (#3251)")
class TestReasoningTraceCapturesAllAgents:
    """(2) reasoning_trace must capture EVERY voting agent's reasoning, not just
    the ~2 that fit under the old [:200] top-3 summary cap."""

    def test_reasoning_trace_not_capped_below_agent_count(self):
        from core.round_table.runner import _score_to_signal

        # Five distinct agents, each with a sentinel token in its reasoning. The
        # old top-3 + [:200] truncation dropped agents 4 and 5 entirely.
        votes = [
            _vote("MomentumAgent", "MOMENTUM_SENTINEL momentum trend intact", 1.0),
            _vote("LSTMSignalAgent", "LSTM_SENTINEL model bullish forecast", 0.9),
            _vote("VIXAwareAgent", "VIX_SENTINEL volatility regime calm", 0.8),
            _vote("RegimeDetectionAgent", "REGIME_SENTINEL bull regime detected", 0.7),
            _vote("DrawdownGuardAgent", "DRAWDOWN_SENTINEL no drawdown stress", 0.6),
        ]
        state = {
            "symbol": "AAPL",
            "ohlc": {"close": 150.0},
            "features": {},
            "ml": None,
        }

        signal = _score_to_signal(state, 0.5, votes)  # 0.5 → HOLD (no damping)
        assert signal is not None, "_score_to_signal returned None unexpectedly"
        trace = signal.decision_context.reasoning_trace or ""

        for token in (
            "MOMENTUM_SENTINEL",
            "LSTM_SENTINEL",
            "VIX_SENTINEL",
            "REGIME_SENTINEL",
            "DRAWDOWN_SENTINEL",
        ):
            assert (
                token in trace
            ), f"{token} missing — trace truncated below agent count"
