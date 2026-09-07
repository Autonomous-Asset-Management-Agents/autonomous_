"""#3096 (Hebel 5) — benchmark-relative Metriken vs SPY: Active Return, Tracking Error,
Information Ratio. Referenzwerte unabhängig mit numpy gerechnet (verify-before-assert)."""

import math

import numpy as np
import pytest

from core.performance_metrics import (
    active_return_series,
    information_ratio,
    tracking_error,
)

SQRT252 = math.sqrt(252.0)


def _ref_te(active):
    a = np.asarray(active, dtype=float)
    return round(float(np.std(a, ddof=1) * SQRT252), 6)


def _ref_ir(active):
    a = np.asarray(active, dtype=float)
    sd = float(np.std(a, ddof=1))
    if sd == 0.0:
        return None
    return round(float(np.mean(a) * SQRT252 / sd), 6)


def test_active_return_series_elementwise():
    strat = [0.01, 0.02, -0.01, 0.03]
    bench = [0.005, 0.01, 0.0, 0.02]
    got = active_return_series(strat, bench)
    assert got == pytest.approx([0.005, 0.01, -0.01, 0.01])


def test_active_return_series_length_mismatch_is_none():
    assert active_return_series([0.01, 0.02], [0.01]) is None


def test_active_return_series_empty_is_none():
    assert active_return_series([], []) is None
    assert active_return_series(None, [0.01]) is None


def test_tracking_error_matches_numpy_reference():
    active = [0.005, 0.01, -0.01, 0.01, -0.002]
    assert tracking_error(active) == pytest.approx(_ref_te(active), abs=1e-6)


def test_information_ratio_matches_numpy_reference():
    active = [0.005, 0.01, -0.01, 0.01, -0.002]
    assert information_ratio(active) == pytest.approx(_ref_ir(active), abs=1e-6)


def test_tracking_error_needs_two_points():
    assert tracking_error([0.01]) is None
    assert tracking_error([]) is None
    assert tracking_error(None) is None


def test_information_ratio_zero_std_is_none_not_divzero():
    # constant active return -> std 0 -> IR undefined, must be None (no ZeroDivisionError)
    assert information_ratio([0.01, 0.01, 0.01]) is None
    assert tracking_error([0.01, 0.01, 0.01]) == pytest.approx(0.0, abs=1e-9)


def test_information_ratio_needs_two_points():
    assert information_ratio([0.01]) is None
    assert information_ratio(None) is None


def test_positive_active_gives_positive_ir():
    # strategy strictly beats benchmark every day -> IR > 0
    active = active_return_series([0.02, 0.03, 0.01], [0.01, 0.01, 0.005])
    assert information_ratio(active) > 0.0
