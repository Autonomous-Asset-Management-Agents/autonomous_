# tests/unit/test_lstm_serve_scale.py
# Issue #1878 (MLA-4) — Fix 2: scaler_y.inverse_transform + behavior-preserving
# threshold rescale; Fix 4: vix/market_news_sentiment as a real time-series.
#
# Fix 2 — the loaded scaler_y is a StandardScaler; without inverse_transform the
# "predicted 5-day return" is a unitless z-score and every comparison threshold
# (LSTM_DYNAMIC_MIN_PRED_BUY, the VIX-adaptive lstm_buy/sell staffel, ...) lives
# on that z-scale. We convert BOTH the prediction and every threshold via
#   return = z * scaler_y.scale_ + scaler_y.mean_   (monotone; scale_ > 0)
# so the buy/sell DECISION is byte-identical (behavior-preserving) while the
# reported value and the thresholds are now real 5-day returns.
#
# Gherkin (Fix 2):
#   Given a StandardScaler scaler_y with mean m and scale s
#   When the strategy converts a z-score prediction/threshold
#   Then the result equals z*s + m
#   And ordering (and thus every buy/sell decision) is preserved for all z.

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_lstm_strategy():
    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    s = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    s.torch_model = MagicMock()
    s.scaler_x = MagicMock()
    s.scaler_y = None
    s.features_list = ["close", "volume", "rsi_14"]
    s._initialized = True
    s.device = "cpu"
    s.client = MagicMock()
    s.data_provider = MagicMock()
    return s


def _standard_scaler(mean: float, scale: float):
    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler()
    sc.mean_ = np.array([mean], dtype=np.float64)
    sc.scale_ = np.array([scale], dtype=np.float64)
    sc.var_ = np.array([scale * scale], dtype=np.float64)
    sc.n_features_in_ = 1
    return sc


class TestFix2ScalerYInverse:
    def test_z_to_return_uses_scaler_y_affine(self):
        s = _make_lstm_strategy()
        s.scaler_y = _standard_scaler(mean=0.008, scale=0.04)
        # z = 0.2  ->  0.2 * 0.04 + 0.008 = 0.016
        assert s._z_to_return(0.2) == pytest.approx(0.016)
        assert s._z_to_return(0.0) == pytest.approx(0.008)
        assert s._z_to_return(-0.4) == pytest.approx(-0.4 * 0.04 + 0.008)

    def test_z_to_return_is_monotone_ordering_preserved(self):
        # The behavior-preservation guarantee: scale_ > 0 => strictly increasing,
        # so z_pred > z_threshold  <=>  return(z_pred) > return(z_threshold).
        s = _make_lstm_strategy()
        s.scaler_y = _standard_scaler(mean=-0.01, scale=0.05)
        zs = [-1.0, -0.4, -0.2, 0.0, 0.2, 0.5, 1.0, 1.3]
        rets = [s._z_to_return(z) for z in zs]
        assert rets == sorted(rets)
        for a, b in zip(zs, zs[1:]):
            assert (a < b) == (s._z_to_return(a) < s._z_to_return(b))

    def test_z_to_return_identity_when_scaler_missing(self):
        # No scaler_y (older bundle) -> degrade to identity + WARNING, never crash.
        s = _make_lstm_strategy()
        s.scaler_y = None
        assert s._z_to_return(0.2) == pytest.approx(0.2)


class TestFix2BehaviorPreserving:
    def test_buy_threshold_decision_identical_across_scale(self):
        # For raw z-scores spanning the buy threshold, the (rescaled) decision
        # must equal the pure z-space decision — no trade flips.
        from core.strategies.lstm_strategy import LSTM_DYNAMIC_MIN_PRED_BUY

        s = _make_lstm_strategy()
        s.scaler_y = _standard_scaler(mean=0.01, scale=0.03)
        thr_real = s._z_to_return(LSTM_DYNAMIC_MIN_PRED_BUY)
        for z in (-0.5, 0.0, 0.19, 0.2, 0.21, 0.5, 1.0):
            z_space_buy = z >= LSTM_DYNAMIC_MIN_PRED_BUY
            real_space_buy = s._z_to_return(z) >= thr_real
            assert z_space_buy == real_space_buy, f"decision flipped at z={z}"


class TestFix2SharedHelper:
    def test_module_z_to_return_affine_and_identity(self):
        from models.torch_model import z_to_return

        sy = _standard_scaler(mean=0.005, scale=0.02)
        assert z_to_return(sy, 0.25) == pytest.approx(0.25 * 0.02 + 0.005)
        assert z_to_return(None, 0.25) == pytest.approx(0.25)  # identity degrade

    def test_negative_threshold_converts_the_zscore_not_the_return(self):
        # RLStrategy strong-sell: z=-0.6 must map via the affine, NOT -(convert(0.6)),
        # otherwise (mean != 0) the sell boundary shifts and decisions flip.
        from models.torch_model import z_to_return

        sy = _standard_scaler(mean=-0.01, scale=0.03)
        assert z_to_return(sy, -0.6) == pytest.approx(-0.6 * 0.03 - 0.01)
        assert z_to_return(sy, -0.6) != pytest.approx(-z_to_return(sy, 0.6))


class TestFix4VixSentimentTimeseries:
    def test_serve_preserves_real_vix_sentiment_series(self):
        # Fix 4: the serve path must NOT unconditionally overwrite hist["vix"] /
        # hist["market_news_sentiment"] with the current scalar — a constant across
        # the window breaks a retrained 34-feature model. Both serve paths must guard
        # the scalar behind an "absent column" check (real series preserved if present).
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in ("core/strategies/lstm_strategy.py", "core/strategies/rl_signal.py"):
            src = (root / rel).read_text(encoding="utf-8")
            assert (
                'if "vix" not in hist.columns' in src
            ), f"{rel}: vix scalar not guarded"
            assert (
                'if "market_news_sentiment" not in hist.columns' in src
            ), f"{rel}: sentiment scalar not guarded"
