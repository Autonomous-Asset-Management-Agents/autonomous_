"""MLR-9 (#1909): Deflated/Probabilistic Sharpe utility — pure-math reference tests.

Bailey & Lopez de Prado ("The Deflated Sharpe Ratio", 2014; "The Sharpe Ratio
Efficient Frontier", 2012):

  PSR(SR*) = Phi( (SR - SR*) * sqrt(n_obs - 1)
                  / sqrt(1 - skew*SR + (kurt - 1)/4 * SR^2) )

  E[max SR | N trials, null SR=0]
           = sqrt(V) * ((1-gamma) * PhiInv(1 - 1/N) + gamma * PhiInv(1 - 1/(N*e)))
    (gamma = Euler-Mascheroni)

  DSR      = PSR evaluated at SR* = E[max SR]  (a probability in [0, 1])

The gate-facing ``deflated_sharpe`` metadata field is the SHARPE-SCALE deflation
margin ``SR - E[max SR]`` (can be <= 0 for overfit models, floor 0.0 per plan
Gherkin) — exposed here as ``deflated_sharpe_excess``.

Reference fixtures generated once with scipy.stats.norm (scipy==1.17.1,
requirements-ci) at authoring time; the shipped module itself is scipy-free
(math/statistics only — OSS/public-api dependency parity). Tolerance 1e-3 per
plan §7.

Pure math — no torch, runs in CI.
"""

import math

import pytest

from core.ml.deflated_sharpe import (
    deflated_sharpe_excess,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_moments,
)

TOL = 1e-3


# --- U1: PSR / DSR against scipy-generated reference values --------------------------
def test_psr_normal_case_reference():
    # sr=0.1, n_obs=101, skew=0, kurt=3 → z = 0.1*10/sqrt(1.005) → Phi = 0.840741
    assert probabilistic_sharpe_ratio(0.1, 101, 0.0, 3.0) == pytest.approx(
        0.840741, abs=TOL
    )


def test_psr_nonnormal_reference():
    # skew=-3, kurt=10 widen the SR standard error → PSR drops to 0.807731
    assert probabilistic_sharpe_ratio(0.1, 101, -3.0, 10.0) == pytest.approx(
        0.807731, abs=TOL
    )


def test_expected_max_sharpe_reference():
    # N=10, V=0.01 → 0.157460 ; N=100 → 0.253060 (PhiInv via published quantiles)
    assert expected_max_sharpe(10, 0.01) == pytest.approx(0.157460, abs=TOL)
    assert expected_max_sharpe(100, 0.01) == pytest.approx(0.253060, abs=TOL)


def test_dsr_full_reference():
    # sr=0.1, n_obs=1001, normal, N=10 trials, V=0.01 → DSR = 0.034953
    assert deflated_sharpe_ratio(0.1, 1001, 0.0, 3.0, 10, 0.01) == pytest.approx(
        0.034953, abs=TOL
    )


def test_dsr_bailey_lopez_de_prado_style_example():
    # Paper-style worked example: daily SR = 2.5/sqrt(250), 1250 obs (5y), skew -3,
    # kurt 10, best of N=100 trials with trial variance 0.005 → DSR = 0.275942.
    sr_daily = 2.5 / math.sqrt(250)
    assert deflated_sharpe_ratio(sr_daily, 1250, -3.0, 10.0, 100, 0.005) == (
        pytest.approx(0.275942, abs=TOL)
    )


# --- U2: DSR monotonically decreasing in n_trials ------------------------------------
def test_dsr_monotone_decreasing_in_n_trials():
    d10 = deflated_sharpe_ratio(0.1, 1001, 0.0, 3.0, 10, 0.01)
    d50 = deflated_sharpe_ratio(0.1, 1001, 0.0, 3.0, 50, 0.01)
    d200 = deflated_sharpe_ratio(0.1, 1001, 0.0, 3.0, 200, 0.01)
    assert d10 > d50 > d200


def test_excess_monotone_decreasing_and_signed():
    e10 = deflated_sharpe_excess(0.1, 10, 0.01)
    e200 = deflated_sharpe_excess(0.1, 200, 0.01)
    assert e10 > e200
    # 0.1 - 0.157460 < 0 → overfit under 10 trials at this trial variance
    assert e10 == pytest.approx(0.1 - 0.157460, abs=TOL)
    assert e10 < 0


def test_dsr_single_trial_equals_psr():
    # N<=1 → no selection → E[max SR]=0 → DSR == PSR(benchmark 0)
    assert deflated_sharpe_ratio(0.1, 101, 0.0, 3.0, 1, 0.01) == pytest.approx(
        probabilistic_sharpe_ratio(0.1, 101, 0.0, 3.0), abs=1e-12
    )
    assert deflated_sharpe_excess(0.1, 1, 0.01) == pytest.approx(0.1, abs=1e-12)


# --- U3: non-normality lowers PSR for a positive SR ----------------------------------
def test_negative_skew_lowers_psr():
    assert probabilistic_sharpe_ratio(0.1, 101, -3.0, 3.0) < (
        probabilistic_sharpe_ratio(0.1, 101, 0.0, 3.0)
    )


def test_fat_tails_lower_psr():
    assert probabilistic_sharpe_ratio(0.1, 101, 0.0, 10.0) < (
        probabilistic_sharpe_ratio(0.1, 101, 0.0, 3.0)
    )


# --- U4: degenerate inputs → NaN, never a crash --------------------------------------
def test_psr_degenerate_n_obs():
    assert math.isnan(probabilistic_sharpe_ratio(0.1, 1, 0.0, 3.0))
    assert math.isnan(probabilistic_sharpe_ratio(0.1, 0, 0.0, 3.0))


def test_psr_degenerate_variance_correction():
    # Pathological skew/kurt can push the Mertens variance term <= 0 → NaN, no crash.
    assert math.isnan(probabilistic_sharpe_ratio(2.0, 101, 5.0, 1.0))


def test_expected_max_sharpe_degenerate():
    assert expected_max_sharpe(0, 0.01) == 0.0
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert expected_max_sharpe(100, 0.0) == 0.0
    assert expected_max_sharpe(100, -1.0) == 0.0


def test_sharpe_moments_degenerate():
    assert sharpe_moments([]) is None
    assert sharpe_moments([0.01]) is None
    assert sharpe_moments([0.01, 0.01, 0.01]) is None  # zero std


def test_sharpe_moments_known_series():
    # Alternating +1%/-1% → mean 0, per-period SR 0, platykurtic.
    m = sharpe_moments([0.01, -0.01] * 50)
    assert m is not None
    assert m["sr"] == pytest.approx(0.0, abs=1e-12)
    assert m["n_obs"] == 100
    assert m["skew"] == pytest.approx(0.0, abs=1e-9)
    assert m["kurtosis"] == pytest.approx(1.0, abs=0.1)  # two-point distribution


# --- U5: PBO on constructed OOS ranks ------------------------------------------------
def test_pbo_mixed_ranks():
    # Relative OOS ranks of the IS-best candidate; rank <= 0.5 ⇒ logit lambda <= 0.
    assert probability_of_backtest_overfitting([0.2, 0.3, 0.6, 0.7]) == (
        pytest.approx(0.5, abs=1e-12)
    )


def test_pbo_extremes():
    assert probability_of_backtest_overfitting([0.9, 0.8]) == 0.0
    assert probability_of_backtest_overfitting([0.1, 0.2]) == 1.0


def test_pbo_empty_is_nan():
    assert math.isnan(probability_of_backtest_overfitting([]))
