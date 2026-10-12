# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deflated / Probabilistic Sharpe Ratio — pure-math utility (MLR-9, #1909).

ADR-ML-GATE-03: Bailey & Lopez de Prado, "The Deflated Sharpe Ratio: Correcting
for Selection Bias, Backtest Overfitting and Non-Normality" (2014) and "The
Sharpe Ratio Efficient Frontier" (2012).

  PSR(SR*) = Phi( (SR - SR*) * sqrt(n_obs - 1)
                  / sqrt(1 - skew*SR + (kurt - 1)/4 * SR^2) )

  E[max SR | N trials, null SR = 0]
           = sqrt(V) * ((1-gamma)*PhiInv(1 - 1/N) + gamma*PhiInv(1 - 1/(N*e)))
    where V is the cross-trial variance of the SR estimates and gamma is the
    Euler-Mascheroni constant.

  DSR      = PSR evaluated at SR* = E[max SR]   (probability in [0, 1])

Two output scales, both provided (plan §5.1 + §7 Gherkin reconciliation):
  * :func:`deflated_sharpe_ratio`  — the canonical PROBABILITY-scale DSR.
  * :func:`deflated_sharpe_excess` — the SHARPE-SCALE deflation margin
    ``SR - E[max SR]`` (negative ⇒ the observed Sharpe does not clear the
    expected maximum of N zero-skill trials ⇒ backtest overfitting). This is
    what the Layer-5 producer annualizes and stamps as the ``deflated_sharpe``
    metadata field the serving gate compares against ``TFT_DSR_FLOOR`` (0.0).

All inputs are PER-PERIOD (non-annualized) Sharpe unless stated otherwise;
``kurtosis`` is the raw (non-excess) fourth moment ratio (normal = 3).

Edition-neutral by construction: only ``math``/``statistics`` — no scipy, no
torch, no config/env reads (CODING_POLICY §2.10) → identical in OSS and
Enterprise, importable in every CI lane. Degenerate inputs return ``nan``
(functions) or ``None`` (:func:`sharpe_moments`) — never raise.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

# Euler-Mascheroni constant (gamma) — E[max] of N iid standard normals term.
EULER_MASCHERONI = 0.5772156649015329

__all__ = [
    "EULER_MASCHERONI",
    "probabilistic_sharpe_ratio",
    "expected_max_sharpe",
    "deflated_sharpe_ratio",
    "deflated_sharpe_excess",
    "probability_of_backtest_overfitting",
    "sharpe_moments",
]


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (double-precision exact)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's rational approximation for the inverse standard normal CDF.
# Peter Acklam (2003), relative error < 1.15e-9 over (0, 1) — far inside the
# 1e-3 test tolerance and dependency-free (no scipy in OSS/public-api, §12.4).
_PPF_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_PPF_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_PPF_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_PPF_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_PPF_P_LOW = 0.02425


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam). p outside (0, 1) → nan/±inf."""
    if math.isnan(p) or p < 0.0 or p > 1.0:
        return float("nan")
    if p == 0.0:
        return float("-inf")
    if p == 1.0:
        return float("inf")
    a, b, c, d = _PPF_A, _PPF_B, _PPF_C, _PPF_D
    if p < _PPF_P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= 1.0 - _PPF_P_LOW:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def probabilistic_sharpe_ratio(
    sr: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
    sr_benchmark: float = 0.0,
) -> float:
    """PSR: P[true SR > ``sr_benchmark``] given the observed per-period ``sr``.

    Uses the Mertens/Lo standard error of the SR estimator corrected for
    skewness and (raw) kurtosis. Degenerate inputs (``n_obs`` < 2, or a
    non-positive variance correction from pathological skew/kurt) → ``nan``.
    """
    if n_obs is None or n_obs < 2:
        return float("nan")
    variance_term = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr
    if not math.isfinite(variance_term) or variance_term <= 0.0:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1.0) / math.sqrt(variance_term)
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] of ``n_trials`` zero-skill trials with SR-estimate variance V.

    ``n_trials`` <= 1 (no selection) or ``sr_variance`` <= 0 → 0.0 (no
    deflation), so a single honest backtest is never penalized.
    """
    if n_trials is None or n_trials <= 1:
        return 0.0
    if sr_variance is None or sr_variance <= 0.0 or not math.isfinite(sr_variance):
        return 0.0
    g = EULER_MASCHERONI
    return math.sqrt(sr_variance) * (
        (1.0 - g) * _norm_ppf(1.0 - 1.0 / n_trials)
        + g * _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    sr_variance: float,
) -> float:
    """DSR (probability scale): PSR evaluated at ``SR* = E[max SR]``.

    Monotonically decreasing in ``n_trials`` — the more trials the selection
    ran over, the higher the bar the observed Sharpe must clear.
    """
    benchmark = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, benchmark)


def deflated_sharpe_excess(sr: float, n_trials: int, sr_variance: float) -> float:
    """Sharpe-scale deflation margin ``SR - E[max SR]`` (per-period).

    Negative ⇒ overfit: the observed Sharpe is below what the best of
    ``n_trials`` zero-skill trials would show by chance. The Layer-5 producer
    annualizes this and stamps it as the gate-facing ``deflated_sharpe`` field
    (floor ``TFT_DSR_FLOOR`` = 0.0, exclusive — plan §7 Gherkin).
    """
    if sr is None or not math.isfinite(sr):
        return float("nan")
    return sr - expected_max_sharpe(n_trials, sr_variance)


def probability_of_backtest_overfitting(oos_ranks: Sequence[float]) -> float:
    """CSCV-style PBO from the OOS relative ranks of the IS-best candidate.

    ``oos_ranks``: per-split relative rank omega in (0, 1) of the in-sample
    winner within the out-of-sample candidate ranking (Lopez de Prado CSCV).
    PBO = P[logit(omega) <= 0] = fraction of splits with omega <= 0.5.
    Empty input → ``nan``. Report-only — never a gate input (plan §5.1).
    """
    ranks = [r for r in oos_ranks if r is not None and math.isfinite(r)]
    if not ranks:
        return float("nan")
    return sum(1 for r in ranks if r <= 0.5) / len(ranks)


def sharpe_moments(returns: Sequence[float]) -> Optional[dict]:
    """Per-period SR + higher moments of a return series (for PSR/DSR inputs).

    Returns ``{"sr", "n_obs", "skew", "kurtosis", "mean", "std"}`` or ``None``
    when the series is degenerate (< 2 observations or zero variance).
    ``kurtosis`` is raw (normal = 3) as the PSR formula expects. Population
    moments (ddof=0) for skew/kurtosis, sample std (ddof=1) for the SR —
    matching the Bailey/Lopez de Prado estimator conventions.
    """
    xs = [float(r) for r in returns if r is not None and math.isfinite(float(r))]
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    m2 = sum((x - mean) ** 2 for x in xs) / n
    if m2 <= 0.0:
        return None
    sample_var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    std = math.sqrt(sample_var)
    if std <= 0.0:
        return None
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    skew = m3 / (m2**1.5)
    kurtosis = m4 / (m2**2)
    return {
        "sr": mean / std,
        "n_obs": n,
        "skew": skew,
        "kurtosis": kurtosis,
        "mean": mean,
        "std": std,
    }
