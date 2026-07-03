"""TFT serving correctness (M1 units + M3a step-0), gated by TFT_SERVING_FIX.

These exercise ``TFTInferenceEngine._postprocess_quantiles`` DIRECTLY with mock quantile
arrays — pure numpy, no model and no pytorch_forecasting — so the numeric contract is
verified in CI (unlike the model-load path, which is pf-gated). Kept in a separate file
from test_tft_inference.py to avoid a merge collision with the in-flight D3 PR (#1164).

Adversarially-verified findings ported from the bundle:
  * M1 (units): v2 models output DECIMAL log-returns (~0.002–0.02). Read as percent they
    sit inside the ±0.3 dead-band → direction is ALWAYS "neutral" and confidence fake-
    saturates ≈1.0. The trainer's own scoring multiplies by 100
    (train_tft_per_symbol._score_fold) — so serving must too.
  * M3a (step alignment): the gate's walkforward_ic was validated on DECODER STEP 0, not
    the 5-step horizon mean. Serving must read step-0 to match what the gate certified.

The fix is DORMANT behind TFT_SERVING_FIX (default False): OFF reproduces the historical
mean+no-scale behaviour byte-for-byte (the dormancy test below proves it).
"""

from unittest.mock import patch

import numpy as np
import pytest

from config import RuntimeConfigState
from core.ml.tft_inference import TFTInferenceEngine


def _engine(tmp_path):
    # __init__ only stores symbol/model_dir — no load, no torch, no pf.
    return TFTInferenceEngine("AAPL", tmp_path)


def _raw(steps):
    """Build a raw [N=1, H, Q=3] quantile array from a list of [bear, base, bull] steps."""
    return np.asarray([steps], dtype=float)


def _fix_on():
    return patch(
        "core.ml.tft_inference.get_config",
        return_value=RuntimeConfigState(TFT_SERVING_FIX=True),
    )


def _fix_off():
    return patch(
        "core.ml.tft_inference.get_config",
        return_value=RuntimeConfigState(TFT_SERVING_FIX=False),
    )


# --- a) M1: decimal → percent (×100) -------------------------------------------------
def test_predict_applies_100x_scaling(tmp_path):
    eng = _engine(tmp_path)
    raw = _raw([[-0.01, 0.02, 0.045]])  # H=1 → isolates the ×100 from step selection
    with _fix_on():
        pred = eng._postprocess_quantiles(raw)
    assert pred is not None
    assert pred.base_return_pct == pytest.approx(2.0)  # 2.0, not 0.02
    assert pred.direction == "up"  # was "neutral" (0.02 < 0.3 dead-band)


# --- b) M1: negative signal scales + points down -------------------------------------
def test_predict_direction_down_for_negative_signal(tmp_path):
    eng = _engine(tmp_path)
    raw = _raw([[-0.02, -0.008, 0.004]])
    with _fix_on():
        pred = eng._postprocess_quantiles(raw)
    assert pred.base_return_pct == pytest.approx(-0.8)  # -0.8
    assert pred.direction == "down"  # was "neutral"


# --- c) M3a: direction follows decoder-step-0, not the horizon mean ------------------
def test_predict_uses_step0_not_mean(tmp_path):
    eng = _engine(tmp_path)
    # Step-0 bullish (+1.8% base); steps 1-4 strongly bearish. The 5-step MEAN base is
    # negative → old path says "neutral"/"down"; step-0 says "up".
    raw = _raw(
        [
            [-0.012, 0.018, 0.045],  # step 0 — bullish
            [-0.03, -0.02, -0.005],  # step 1
            [-0.03, -0.02, -0.005],  # step 2
            [-0.03, -0.02, -0.005],  # step 3
            [-0.03, -0.02, -0.005],  # step 4
        ]
    )
    with _fix_on():
        pred = eng._postprocess_quantiles(raw)
    assert pred.direction == "up"  # step-0 drives it; mean would dilute to non-up
    assert pred.base_return_pct == pytest.approx(1.8)  # 1.8 (step-0 base, scaled)


# --- d) confidence is real after scaling (not fake-saturated ≈1.0) -------------------
def test_confidence_scales_correctly_after_fix(tmp_path):
    eng = _engine(tmp_path)
    raw = _raw([[-0.02, 0.0, 0.02]])  # spread 0.04 decimal → 4.0 after ×100
    with _fix_on():
        pred = eng._postprocess_quantiles(raw)
    # spread = bull-bear = 4.0; confidence = 1 - 4.0/(2*4.0) = 0.5
    assert pred.confidence == pytest.approx(0.5)


# --- e) DORMANCY: flag OFF reproduces the historical behaviour byte-for-byte ---------
def test_default_off_is_byte_identical_old_behaviour(tmp_path):
    eng = _engine(tmp_path)
    raw = _raw(
        [
            [-0.012, 0.018, 0.045],
            [-0.03, -0.02, -0.005],
            [-0.03, -0.02, -0.005],
            [-0.03, -0.02, -0.005],
            [-0.03, -0.02, -0.005],
        ]
    )
    with _fix_off():
        pred = eng._postprocess_quantiles(raw)
    # OLD path: mean across H, no ×100. base = mean of bases = (0.018-0.08)/5 = -0.0124.
    assert pred.base_return_pct == pytest.approx(-0.0124)
    assert (
        pred.direction == "neutral"
    )  # |0.0124| < 0.3 → the historical (broken) result
