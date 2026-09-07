# tests/unit/test_runner_decision_snapshot.py
# #2389 (Epic RTR #1958) — TDD Red→Green: real TA snapshot in the DecisionContext.
#
# runner._score_to_signal built the DecisionContext with only 5 fields, so every
# decision record carried the neutral DecisionRecord defaults (rsi_14=50,
# atr_14d=0, macd=0, bb_pct=0.5, volume_ratio=1.0 — MiFID II Art. 25 blindness).
# With #2389 the state["features"] scalars written by _compute_features_node are
# mapped into the snapshot; a missing/partial features dict falls open to today's
# defaults field-by-field (regression-safe when the flag is OFF).
#
# Gherkin (plan #2389 §7/§8):
#   R5  state["features"] present → ctx carries the REAL rsi/atr/macd/bb/volume
#   R6  no state["features"]      → ctx keeps today's defaults (regression)

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.round_table.runner import _score_to_signal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _votes():
    return [
        SimpleNamespace(
            agent_name="RLConfidenceAgent",
            score=0.5,
            weight=0.9,
            reasoning="RL HOLD conv=0.5",
            vetoed=False,
        ),
        SimpleNamespace(
            agent_name="LSTMSignalAgent",
            score=0.5,
            weight=0.7,
            reasoning="LSTM neutral",
            vetoed=False,
        ),
    ]


def _state(features=None, symbol="AAPL", close=151.0):
    state = {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 152.0,
            "low": 149.0,
            "close": close,
            "volume": 1000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-07-22T14:00:00+00:00",
        "signal": None,
        "error": None,
    }
    if features is not None:
        state["features"] = features
    return state


_REAL_FEATURES = {
    "rsi_14": 71.3,
    "atr_14d": 4.2,
    "macd_hist": 1.23,
    "bb_position": 0.8,
    "vol_anomaly": 2.4,
}


# ---------------------------------------------------------------------------
# R5: features present → real snapshot in the DecisionContext
# ---------------------------------------------------------------------------


def test_r5_features_land_in_decision_context():
    sig = _score_to_signal(_state(features=_REAL_FEATURES), 0.5, _votes())

    assert sig is not None
    ctx = sig.decision_context
    assert ctx.rsi_14 == pytest.approx(71.3)
    assert ctx.atr_14d == pytest.approx(4.2)
    # features.py exposes the MACD histogram (macd - signal line).
    assert ctx.macd == pytest.approx(1.23)
    # bb_position ∈ ~[-1, +1] (lower..upper band) → %B ∈ ~[0, 1].
    assert ctx.bb_pct == pytest.approx(0.5 + 0.8 / 2.0)
    assert ctx.volume_ratio == pytest.approx(2.4)


def test_r5b_partial_features_fall_open_per_field():
    """A partial dict (e.g. short history → NaN-dropped fields) keeps defaults
    for the missing fields only — never crashes, never zeroes real values."""
    sig = _score_to_signal(_state(features={"rsi_14": 33.0}), 0.5, _votes())

    ctx = sig.decision_context
    assert ctx.rsi_14 == pytest.approx(33.0)
    assert ctx.atr_14d == 0.0
    assert ctx.macd == 0.0
    assert ctx.bb_pct == 0.5
    assert ctx.volume_ratio == 1.0


def test_r5c_existing_fields_unchanged_by_snapshot():
    """The snapshot mapping must not disturb the 5 fields the runner already set."""
    sig = _score_to_signal(_state(features=_REAL_FEATURES), 0.5, _votes())

    ctx = sig.decision_context
    assert ctx.symbol == "AAPL"
    assert ctx.action == "HOLD"
    assert ctx.conviction_score == pytest.approx(0.5)
    assert ctx.current_price == pytest.approx(151.0)
    assert "RoundTableV2 consensus=0.500" in ctx.reasoning_summary


# ---------------------------------------------------------------------------
# R6: no features → today's defaults (regression, flag-off path)
# ---------------------------------------------------------------------------


def test_r6_no_features_keeps_default_snapshot():
    sig = _score_to_signal(_state(), 0.5, _votes())

    assert sig is not None
    ctx = sig.decision_context
    assert ctx.rsi_14 == 50.0
    assert ctx.atr_14d == 0.0
    assert ctx.macd == 0.0
    assert ctx.bb_pct == 0.5
    assert ctx.volume_ratio == 1.0


def test_r6b_features_none_keeps_default_snapshot():
    """state['features'] = None (fail-open path of the node) behaves like absent."""
    sig = _score_to_signal({**_state(), "features": None}, 0.5, _votes())

    ctx = sig.decision_context
    assert ctx.rsi_14 == 50.0
    assert ctx.atr_14d == 0.0
