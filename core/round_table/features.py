# core/round_table/features.py
# Epic 4.4 — Feature Engineering Layer
#
# Shared feature computation used by the LightGBM training pipeline (Epic 4.5)
# and inference-time Round Table agents (Epic 4.7).
# Keeping features in sync between training and inference is critical for model validity.
#
# Feature tiers:
#   Technical:   computed from OHLCV data (always available)
#   SPY/Market:  computed from SPY OHLCV (always available)
#   Specialist:  computed from alt-data reports (live SpecialistReport objects)
#
# Usage:
#   from core.round_table.features import (
#       compute_technical_features,
#       compute_spy_features,
#       compute_specialist_features_from_report,
#       AGENT_FEATURE_SETS,
#   )

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
# Technical Features (per-stock OHLCV)
# ─────────────────────────────────────────────────────────────


def compute_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-stock technical features from OHLCV data.

    Args:
        df: DataFrame with columns: Open, High, Low, Close, Volume.
            Index should be a DatetimeIndex.

    Returns:
        DataFrame with feature columns, same index as input.
        Last row can be extracted as a dict for inference:
            features = compute_technical_features(df).iloc[-1].to_dict()
    """
    feat = pd.DataFrame(index=df.index)
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    # RSI (14-day)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    feat["rsi_14"] = 100 - 100 / (1 + rs)

    # Moving average ratios
    feat["price_ma50"] = close / close.rolling(50).mean()
    feat["price_ma200"] = close / close.rolling(200).mean()

    # Volume ratio (5-day avg / 20-day avg)
    feat["vol_ratio_5_20"] = volume.rolling(5).mean() / volume.rolling(
        20
    ).mean().replace(0, np.nan)

    # MACD histogram
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    feat["macd_hist"] = macd - macd_signal

    # ATR as % of price
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    feat["atr_pct"] = tr.rolling(14).mean() / close

    # Bollinger Band position (-1 = lower band, +1 = upper band)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    feat["bb_position"] = (close - bb_mid) / (2 * bb_std.replace(0, np.nan))

    # Momentum (% returns)
    feat["momentum_5d"] = close.pct_change(5)
    feat["momentum_20d"] = close.pct_change(20)

    # Distance from 52-week extremes
    feat["dist_52w_high"] = close / close.rolling(252, min_periods=50).max() - 1
    feat["dist_52w_low"] = close / close.rolling(252, min_periods=50).min() - 1

    # Consecutive up days
    up = (close.diff() > 0).astype(float)
    feat["consec_up"] = up.groupby((up != up.shift()).cumsum()).cumsum()

    # Volume anomaly (today vs 20-day avg)
    feat["vol_anomaly"] = volume / volume.rolling(20).mean().replace(0, np.nan)

    # Range expansion (today's range vs ATR)
    feat["range_expansion"] = (high - low) / tr.rolling(14).mean().replace(0, np.nan)

    # Lagged returns (temporal context)
    feat["return_1d"] = close.pct_change(1)
    feat["return_10d"] = close.pct_change(10)

    # Volatility regime (realized vol vs its own history)
    realized_vol_20d = close.pct_change().rolling(20).std() * np.sqrt(252)
    realized_vol_60d = close.pct_change().rolling(60).std() * np.sqrt(252)
    feat["vol_regime"] = realized_vol_20d / realized_vol_60d.replace(0, np.nan)
    feat["realized_vol_20d"] = realized_vol_20d

    # On-Balance Volume trend
    obv = (np.sign(close.diff()) * volume).cumsum()
    obv_ma20 = obv.rolling(20).mean()
    feat["obv_trend"] = (obv - obv_ma20) / obv_ma20.abs().replace(0, np.nan)

    # Stochastic %K (14-day)
    low_14 = low.rolling(14).min()
    high_14 = high.rolling(14).max()
    feat["stochastic_k"] = (
        (close - low_14) / (high_14 - low_14).replace(0, np.nan) * 100
    )

    # Price acceleration (momentum of momentum)
    feat["price_accel"] = feat["momentum_5d"] - feat["momentum_5d"].shift(5)

    # Mean reversion signal (distance from 10-day mean)
    feat["mean_rev_10d"] = close / close.rolling(10).mean() - 1

    # Volume-price divergence (price up + volume down = weak)
    feat["vol_price_div"] = feat["momentum_5d"] * np.log1p(
        feat["vol_ratio_5_20"].fillna(1)
    )

    return feat


# ─────────────────────────────────────────────────────────────
# SPY / Market Features
# ─────────────────────────────────────────────────────────────


def compute_spy_features(spy_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute market-level features from SPY OHLCV.

    Args:
        spy_df: DataFrame with columns: Open, High, Low, Close, Volume.

    Returns:
        DataFrame with SPY feature columns, same index as input.
    """
    feat = pd.DataFrame(index=spy_df.index)
    close = spy_df["Close"].astype(float)

    feat["spy_ma50"] = close / close.rolling(50).mean()
    feat["spy_ma200"] = close / close.rolling(200).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    feat["spy_rsi"] = 100 - 100 / (1 + rs)

    feat["spy_momentum_5d"] = close.pct_change(5)
    feat["spy_momentum_20d"] = close.pct_change(20)
    feat["spy_vol_ratio"] = spy_df["Volume"].astype(float).rolling(5).mean() / spy_df[
        "Volume"
    ].astype(float).rolling(20).mean().replace(0, np.nan)

    spy_vol_20d = close.pct_change().rolling(20).std() * np.sqrt(252)
    spy_vol_60d = close.pct_change().rolling(60).std() * np.sqrt(252)
    feat["spy_vol_regime"] = spy_vol_20d / spy_vol_60d.replace(0, np.nan)
    feat["spy_return_10d"] = close.pct_change(10)
    feat["spy_bb_position"] = (close - close.rolling(20).mean()) / (
        2 * close.rolling(20).std().replace(0, np.nan)
    )

    return feat


# ─────────────────────────────────────────────────────────────
# Specialist (alt-data) Features — inference-time
# ─────────────────────────────────────────────────────────────


def compute_specialist_features_from_report(report: Any) -> Dict[str, float]:
    """
    Convert a live SpecialistReport into the specialist feature dict.
    Used at inference time with real-time specialist data.

    Args:
        report: SpecialistReport object (or any object with the expected attributes).

    Returns:
        Dict mapping specialist feature names to float values.
    """
    insider_count = len(getattr(report, "insider_trades", []))
    material_count = len(getattr(report, "material_events", []))
    activist_flag = 1.0 if getattr(report, "activist_stakes", []) else 0.0

    wiki_spike = getattr(report, "wiki_spike", False)
    trend_score = getattr(report, "google_trend_score", None) or 0

    # wiki_spike=True means >2.5x normal → approximate z-score ~2.5
    wiki_zscore = 2.5 if wiki_spike else 0.0
    wiki_ratio = 2.5 if wiki_spike else 1.0
    # Google trend: normalize 0-100 to approximate z-score
    trend_zscore = (trend_score - 50) / 25.0 if trend_score else 0.0

    return {
        "insider_filing_count_45d": float(insider_count),
        "material_event_count_30d": float(material_count),
        "activist_filing_flag": activist_flag,
        "wiki_views_zscore": wiki_zscore,
        "wiki_spike_ratio": wiki_ratio,
        "google_trend_zscore": trend_zscore,
    }


# ─────────────────────────────────────────────────────────────
# Feature Sets per Agent
# MUST match training exactly — any change here requires retraining
# ─────────────────────────────────────────────────────────────

SPECIALIST_FEATURES: List[str] = [
    "insider_filing_count_45d",
    "material_event_count_30d",
    "activist_filing_flag",
    "wiki_views_zscore",
    "wiki_spike_ratio",
    "google_trend_zscore",
]

AGENT_FEATURE_SETS: Dict[str, List[str]] = {
    "regime": [
        "spy_ma50",
        "spy_ma200",
        "spy_rsi",
        "spy_momentum_5d",
        "spy_momentum_20d",
        "spy_vol_ratio",
        "spy_vol_regime",
        "spy_return_10d",
        "spy_bb_position",
    ],
    "momentum": [
        "rsi_14",
        "price_ma50",
        "price_ma200",
        "vol_ratio_5_20",
        "macd_hist",
        "atr_pct",
        "bb_position",
        "momentum_5d",
        "momentum_20d",
        "return_1d",
        "return_10d",
        "price_accel",
        "obv_trend",
        "stochastic_k",
        "spy_momentum_5d",
        "spy_vol_regime",
    ],
    "drawdown": [
        "rsi_14",
        "momentum_5d",
        "momentum_20d",
        "vol_ratio_5_20",
        "dist_52w_high",
        "atr_pct",
        "bb_position",
        "vol_anomaly",
        "vol_regime",
        "realized_vol_20d",
        "return_1d",
        "mean_rev_10d",
        "spy_vol_regime",
        "spy_momentum_5d",
    ],
    "squeeze": [
        "momentum_5d",
        "momentum_20d",
        "vol_ratio_5_20",
        "rsi_14",
        "dist_52w_low",
        "bb_position",
        "vol_anomaly",
        "range_expansion",
        "obv_trend",
        "price_accel",
        "vol_price_div",
        "stochastic_k",
        "wiki_spike_ratio",
        "google_trend_zscore",
    ],
    "catalyst": [
        "vol_anomaly",
        "momentum_5d",
        "atr_pct",
        "range_expansion",
        "bb_position",
        "dist_52w_high",
        "obv_trend",
        "vol_price_div",
        "return_1d",
        "material_event_count_30d",
        "insider_filing_count_45d",
        "activist_filing_flag",
    ],
    "specialist": [
        "vol_anomaly",
        "price_ma50",
        "momentum_5d",
        "momentum_20d",
        "vol_ratio_5_20",
        "bb_position",
        "atr_pct",
        "return_10d",
        "vol_regime",
        "obv_trend",
        "insider_filing_count_45d",
        "material_event_count_30d",
        "activist_filing_flag",
        "wiki_views_zscore",
        "wiki_spike_ratio",
        "google_trend_zscore",
    ],
    "contrary": [
        "rsi_14",
        "bb_position",
        "consec_up",
        "vol_anomaly",
        "momentum_5d",
        "dist_52w_high",
        "price_ma50",
        "mean_rev_10d",
        "stochastic_k",
        "vol_regime",
        "insider_filing_count_45d",
        "wiki_spike_ratio",
        "google_trend_zscore",
    ],
    "construction": [
        "rsi_14",
        "atr_pct",
        "vol_ratio_5_20",
        "momentum_20d",
        "price_ma50",
        "bb_position",
        "dist_52w_high",
        "realized_vol_20d",
        "vol_regime",
        "return_10d",
        "mean_rev_10d",
        "spy_vol_regime",
        "spy_momentum_20d",
    ],
}

# All 8 expected agent keys
AGENT_KEYS: List[str] = list(AGENT_FEATURE_SETS.keys())
