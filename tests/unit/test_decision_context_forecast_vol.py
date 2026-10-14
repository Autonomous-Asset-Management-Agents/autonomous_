# tests/unit/test_decision_context_forecast_vol.py
"""#1953 TRD-2 Inc 1 — forecast_vol context plumbing (T11-T12), closes RC-2.

The HAR-RV forward-vol is computed per symbol (stock_specialist.py) and copied
into the graph state (graph.py _attach_specialist_scalars -> state["ml"]), but
never reached the sizing layer: DecisionContext had no field for it. Inc 1 adds
the ADDITIVE `forecast_vol: Optional[float] = None` field and plumbs it in
runner._score_to_signal, where the RoundTableV2 DecisionContext is built.

Missing specialist report / registry / short history => state["ml"] is None
=> the context keeps its None default (fail-safe, byte-identical decision).
"""

from types import SimpleNamespace

import pytest

from core.cloud_logger import DecisionContext
from core.round_table.runner import _score_to_signal


class TestDecisionContextField:
    def test_t11_default_is_none(self):
        assert DecisionContext().forecast_vol is None

    def test_t11b_field_accepts_float(self):
        assert DecisionContext(forecast_vol=0.02).forecast_vol == 0.02

    def test_t11c_to_dict_carries_forecast_vol(self):
        """The decisions sink may see the field (the DB writer drops keys without
        a backing column — cloud_logger._dialect_insert_ignore); pinning it here
        makes the payload shape change EXPLICIT and reviewed."""
        assert DecisionContext().to_dict()["forecast_vol"] is None
        assert DecisionContext(forecast_vol=0.02).to_dict()["forecast_vol"] == 0.02


def _vote(score: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name="TestAgent",
        score=score,
        weight=1.0,
        reasoning="unit-test vote",
        vetoed=False,
    )


def _state(ml) -> dict:
    return {"symbol": "AAPL", "ohlc": {"close": 100.0}, "ml": ml}


class TestRunnerPlumbing:
    def test_t12_state_ml_forecast_vol_reaches_context(self):
        signal = _score_to_signal(_state({"forecast_vol": 0.02}), 0.5, [_vote()])
        assert signal is not None, "_score_to_signal must not fail on this state"
        assert signal.decision_context.forecast_vol == 0.02

    def test_t12b_ml_none_keeps_default(self):
        signal = _score_to_signal(_state(None), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.forecast_vol is None

    def test_t12c_ml_without_forecast_keeps_default(self):
        signal = _score_to_signal(_state({"tft_direction": "up"}), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.forecast_vol is None

    def test_t12d_non_dict_ml_is_safe(self):
        signal = _score_to_signal(_state("garbage"), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.forecast_vol is None
