"""Per-symbol forward-volatility model (HAR-RV) — real, non-LLM quant signal.

Volatility is the strongest persistent per-symbol signal in the universe
(walk-forward Spearman IC ~0.28 per symbol, +0.43..+0.55 cross-sectionally every
year 2021-2026 — far above the near-efficient return targets). Unlike a directional
alpha model it is NOT used to pick buy/sell; it powers **risk-aware sizing** (size
down names predicted to be volatile, up calm ones → constant risk contribution)
and a **vol-regime** read for the Guardian.

Design follows the "simple beats overfit ML on thin data" lesson: a classic
HAR-RV (Corsi 2009) linear model on log realized-vol — daily/weekly/monthly
components — fit per symbol on-the-fly. No training artifacts, no GPU, no deps
beyond numpy/pandas. Stateless and side-effect-free.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_HORIZON = 20  # forecast horizon (trading days)
_MIN_HISTORY = 180  # need enough history to fit HAR meaningfully
_EPS = 1e-8


def _log_returns(close: np.ndarray) -> np.ndarray:
    return np.diff(np.log(np.maximum(close, _EPS)), prepend=np.log(max(close[0], _EPS)))


def _rv(logret: np.ndarray, window: int) -> np.ndarray:
    """Trailing realized vol (daily stdev) over `window` days, as a series."""
    return pd.Series(logret).rolling(window).std().to_numpy()


def _extract_close(bars: pd.DataFrame) -> Optional[np.ndarray]:
    if bars is None or len(bars) < _MIN_HISTORY:
        return None
    col = (
        "close"
        if "close" in bars.columns
        else ("Close" if "Close" in bars.columns else None)
    )
    if col is None:
        return None
    c = pd.to_numeric(bars[col], errors="coerce").to_numpy(dtype="float64")
    c = c[~np.isnan(c)]
    return c if len(c) >= _MIN_HISTORY else None


def forecast_forward_vol(
    bars: pd.DataFrame, horizon: int = _HORIZON
) -> Optional[float]:
    """Predict the symbol's average daily realized vol over the next `horizon` days.

    HAR-RV: log(fwd_vol) ~ log(rv_d) + log(rv_w) + log(rv_m), OLS-fit on this
    symbol's own history. Returns a daily stdev (e.g. 0.018 = 1.8%/day), or None
    when there isn't enough history. Never raises.
    """
    try:
        close = _extract_close(bars)
        if close is None:
            return None
        lr = _log_returns(close)
        rv_d = _rv(lr, 1 + 1)  # ~daily (2d to avoid zero)
        rv_w = _rv(lr, 5)  # weekly
        rv_m = _rv(lr, 22)  # monthly
        # target: realized vol over the FORWARD horizon (shift by 1 to align t with t+1..t+horizon)
        fwd = pd.Series(lr[::-1]).rolling(horizon).std().to_numpy()[::-1]
        fwd = np.concatenate([fwd[1:], [np.nan] * 1])

        X = np.column_stack(
            [np.log(rv_d + _EPS), np.log(rv_w + _EPS), np.log(rv_m + _EPS)]
        )
        y = np.log(fwd + _EPS)
        mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
        if mask.sum() < _MIN_HISTORY // 2:
            return None
        Xf = np.column_stack([np.ones(mask.sum()), X[mask]])
        coef, *_ = np.linalg.lstsq(Xf, y[mask], rcond=None)

        # predict from the most recent (complete) feature row
        last = None
        for i in range(len(X) - 1, -1, -1):
            if np.isfinite(X[i]).all():
                last = X[i]
                break
        if last is None:
            return None
        pred_log = float(
            coef[0] + coef[1] * last[0] + coef[2] * last[1] + coef[3] * last[2]
        )
        vol = float(np.exp(pred_log))
        # sanity clamp: daily vol in (0.1%, 20%)
        return float(np.clip(vol, 0.001, 0.20))
    except Exception as exc:  # never break the caller
        logger.warning("forecast_forward_vol failed: %s", exc)
        return None


def vol_regime(bars: pd.DataFrame) -> Optional[str]:
    """Classify the symbol's CURRENT vol vs its own history: low / normal / high.

    Percentile of trailing 20d vol against the symbol's full distribution.
    """
    try:
        close = _extract_close(bars)
        if close is None:
            return None
        lr = _log_returns(close)
        rv20 = _rv(lr, 20)
        cur = rv20[~np.isnan(rv20)]
        if len(cur) < 60:
            return None
        pct = float((cur < cur[-1]).mean())
        return "high" if pct >= 0.80 else "low" if pct <= 0.20 else "normal"
    except Exception as exc:
        logger.warning("vol_regime failed: %s", exc)
        return None


def vol_size_scaler_from_forecast(
    forecast_vol: Optional[float],
    target_daily_vol: float = 0.015,
    lo: float = 0.5,
    hi: float = 1.5,
) -> float:
    """#1953: scalar risk-parity multiplier from an ALREADY-COMPUTED forecast.

    Same math as ``vol_size_scaler`` (clip(target/fv, lo, hi)) but takes the
    forward-vol SCALAR that is already plumbed through the DecisionContext —
    no bars re-fetch at the sizing call-site. Missing/invalid forecast (None,
    <=0, NaN/inf, non-numeric) returns 1.0: a fail-safe no-op, so an absent
    model can never change behaviour (and never silently over-size).
    """
    try:
        # STRICT numeric types only (bool excluded): the forecast arrives typed
        # (Optional[float] on the DecisionContext); anything else — str, list —
        # is a wiring bug upstream and must stay a no-op, never be coerced.
        if not isinstance(forecast_vol, (int, float)) or isinstance(forecast_vol, bool):
            return 1.0
        fv = float(forecast_vol)
        if not np.isfinite(fv) or fv <= 0:
            return 1.0
        return float(np.clip(target_daily_vol / fv, lo, hi))
    except (TypeError, ValueError):
        return 1.0


def vol_size_scaler(
    bars: pd.DataFrame,
    target_daily_vol: float = 0.015,
    lo: float = 0.5,
    hi: float = 1.5,
) -> float:
    """Risk-parity sizing multiplier from the forward-vol forecast.

    scaler = target_vol / forecast_vol, clamped to [lo, hi]. >1 for calm names
    (size up), <1 for volatile names (size down). Returns 1.0 (no-op) whenever a
    forecast isn't available — so a missing model can never change behaviour.
    Delegates to ``vol_size_scaler_from_forecast`` (#1953: single source of
    truth for the risk-parity math — the two variants can never drift apart).
    """
    fv = forecast_forward_vol(bars)
    return vol_size_scaler_from_forecast(fv, target_daily_vol, lo, hi)


def debiased_daily_vol_from_iv(
    iv_annual: Optional[float],
    vrp_factor: float = 0.85,
    trading_days: int = 252,
) -> Optional[float]:
    """#3094 (a): turn an ANNUALIZED implied vol into a DAILY forecast for the
    #1953 vol-targeting sizer, removing the Variance-Risk-Premium level bias.

    Implied vol sits above realized vol (a positive VRP). Feeding the raw
    ``iv_annual / sqrt(252)`` into the daily-calibrated scaler (target 0.015)
    would under-size — a chronic relative drag vs a ~fully-invested SPY.
    ``vrp_factor`` scales the level back while KEEPING the forward-looking timing.

    Default 0.85 is CALIBRATED, not assumed (corpus 7cf4c4b03bf0, 15,544
    point-in-time symbol/day pairs): it is the factor at which the mean IV vol
    scaler (0.848) matches the incumbent HAR-RV mean scaler (0.847), so swapping
    the estimator does not silently move the risk LEVEL. The separately measured
    forecast-neutral factor — median realized/implied over 31,056 pairs,
    2024-01..2026-07 — is 0.932; the empirical VRP is therefore only ~7%, far
    below the ~20% the original 0.80 placeholder implied (0.80 over-sizes by
    ~5.4%). Pass ``vrp_factor=1.0`` for the raw Option-A comparison arm.

    Returns ``None`` for missing/invalid IV (None, non-numeric, bool, <=0, NaN/inf) —
    the caller then falls back to the HAR-RV forecast (never fabricated, never zero).
    """
    if not isinstance(iv_annual, (int, float)) or isinstance(iv_annual, bool):
        return None
    iv = float(iv_annual)
    if not np.isfinite(iv) or iv <= 0:
        return None
    return round(iv / math.sqrt(trading_days) * float(vrp_factor), 6)
