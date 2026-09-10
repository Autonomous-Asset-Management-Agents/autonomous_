"""
feature_builder.py
------------------
Converts raw OHLCV bar data into a rich feature matrix for the
Temporal Fusion Transformer (TFT) specialist layer.

Dependencies: pandas, numpy only.
"""

from __future__ import annotations

import warnings
from typing import List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Sector encoding map (GICS-aligned)
# ---------------------------------------------------------------------------
SECTOR_MAP: dict[str, int] = {
    "technology": 1,
    "healthcare": 2,
    "financials": 3,
    "consumerdiscretionary": 4,
    "consumer discretionary": 4,
    "consumerstaples": 5,
    "consumer staples": 5,
    "energy": 6,
    "industrials": 7,
    "materials": 8,
    "realestate": 9,
    "real estate": 9,
    "utilities": 10,
    "communicationservices": 11,
    "communication services": 11,
}

MARKET_CAP_MAP: dict[str, int] = {
    "small": 0,
    "mid": 1,
    "large": 2,
}

# Columns produced by build() — used for the completeness check
FEATURE_COLUMNS = [
    "ret_1d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "vol_10d",
    "vol_20d",
    "vol_60d",
    "rsi_14",
    "rsi_28",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "price_vs_20ma",
    "price_vs_50ma",
    "price_vs_200ma",
    "bb_width",
    "atr_14_norm",
    "obv_trend_5d",
    "volume_zscore_20d",
    "volume_vs_20ma",
    "time_idx",
    "day_of_week",
    "month",
    "quarter",
    "sector_encoded",
    "market_cap_bucket",
    "earnings_in_next_5d",
    "ex_div_in_next_5d",
]

MIN_ROWS = 60
EPS = 1e-10  # guard against division by zero


class FeatureBuilder:
    """
    Builds a TFT-ready feature DataFrame from raw OHLCV bars.

    Usage
    -----
    fb = FeatureBuilder()
    df = fb.build(bars, symbol="AAPL", sector="Technology", ...)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        bars,
        symbol: str,
        sector: Optional[str] = None,
        market_cap_bucket: Optional[str] = None,
        earnings_dates: Optional[List] = None,
        ex_div_dates: Optional[List] = None,
    ) -> pd.DataFrame:
        """
        Parameters
        ----------
        bars : list[dict] | pd.DataFrame
            Raw OHLCV bars. Accepts both short keys (o/h/l/c/v/t) and
            long keys (open/high/low/close/volume/timestamp or index).
        symbol : str
            Ticker symbol (stored for reference, not as a feature column).
        sector : str | None
            GICS sector name. None → 0.
        market_cap_bucket : str | None
            'small' | 'mid' | 'large'. None → 1 (mid).
        earnings_dates : list[datetime] | None
            Known earnings announcement dates.
        ex_div_dates : list[datetime] | None
            Known ex-dividend dates.

        Returns
        -------
        pd.DataFrame
            All FEATURE_COLUMNS present. Empty if fewer than MIN_ROWS
            survive NaN-drop.
        """
        df = self._normalize_bars(bars)
        if df.empty:
            return pd.DataFrame(columns=FEATURE_COLUMNS)

        # Extract shared intermediates once to prevent silent divergence
        log_close = np.log(df["close"].clip(lower=EPS))
        daily_ret = log_close.diff()

        df = self._add_returns(df, log_close=log_close)
        df = self._add_realized_vol(df, daily_ret=daily_ret)
        df = self._add_rsi(df, period=14, col="rsi_14")
        df = self._add_rsi(df, period=28, col="rsi_28")
        df = self._add_macd(df)
        df = self._add_trend_ratios(df)
        df = self._add_bollinger(df)
        df = self._add_atr(df)
        df = self._add_obv(df)
        df = self._add_volume_features(df)
        df = self._add_calendar(df)

        # Static covariates
        df["sector_encoded"] = self._encode_sector(sector)
        df["market_cap_bucket"] = self._encode_market_cap(market_cap_bucket)

        # Known-future flags
        df = self._add_event_flags(df, earnings_dates, "earnings_in_next_5d")
        df = self._add_event_flags(df, ex_div_dates, "ex_div_in_next_5d")

        # Drop rows where any core feature is NaN
        core_cols = [
            c
            for c in FEATURE_COLUMNS
            if c
            not in (
                "time_idx",
                "sector_encoded",
                "market_cap_bucket",
                "earnings_in_next_5d",
                "ex_div_in_next_5d",
            )
        ]
        # 2026-05-08: Diagnostic — when result < MIN_ROWS, log per-column NaN
        # counts so we can see which feature is zeroing out the result.
        rows_before = len(df)
        nan_counts_pre = {
            c: int(df[c].isna().sum()) for c in core_cols if c in df.columns
        }
        df = df.dropna(subset=core_cols).reset_index(drop=True)

        if len(df) < MIN_ROWS:
            try:
                import logging as _lg

                _lg.getLogger(__name__).warning(
                    "FeatureBuilder[%s]: dropna %d → %d (< MIN_ROWS=%d). "
                    "Per-column NaN counts pre-dropna: %s",
                    symbol,
                    rows_before,
                    len(df),
                    MIN_ROWS,
                    nan_counts_pre,
                )
            except Exception:
                pass
            return pd.DataFrame(columns=FEATURE_COLUMNS)

        # Assign time_idx starting from 0 after NaN drop
        df["time_idx"] = np.arange(len(df), dtype=np.int64)

        return df[FEATURE_COLUMNS].copy()

    # ------------------------------------------------------------------
    # Step 1: normalise bar input
    # ------------------------------------------------------------------

    def _normalize_bars(self, bars) -> pd.DataFrame:
        """Accept dict-list or DataFrame; normalize column names."""
        if isinstance(bars, pd.DataFrame):
            df = bars.copy()
        else:
            df = pd.DataFrame(bars)

        if df.empty:
            return df

        # Map short → long column names
        rename = {}
        col_set = set(df.columns)
        for short, long in [
            ("o", "open"),
            ("h", "high"),
            ("l", "low"),
            ("c", "close"),
            ("v", "volume"),
        ]:
            if short in col_set and long not in col_set:
                rename[short] = long
        # Timestamp column
        for ts_name in ("t", "timestamp"):
            if ts_name in col_set and "date" not in col_set:
                rename[ts_name] = "date"
                break

        if rename:
            df = df.rename(columns=rename)

        # If the DataFrame's index is datetime-like, use it as 'date'
        if "date" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df["date"] = df.index
            else:
                try:
                    df["date"] = pd.to_datetime(df.index)
                except Exception as exc:
                    warnings.warn(
                        f"FeatureBuilder: could not parse date index: {exc}. Calendar features will be zero.",
                        stacklevel=3,
                    )

        # Ensure numeric OHLCV
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Ensure date column is datetime
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=False, errors="coerce")
            df["date"] = (
                df["date"].dt.tz_localize(None)
                if df["date"].dt.tz is not None
                else df["date"]
            )

        df = df.sort_values("date").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Step 2: log-returns
    # ------------------------------------------------------------------

    def _add_returns(
        self, df: pd.DataFrame, log_close: Optional[pd.Series] = None
    ) -> pd.DataFrame:
        if log_close is None:
            log_close = np.log(df["close"].clip(lower=EPS))
        for period, col in [
            (1, "ret_1d"),
            (5, "ret_5d"),
            (10, "ret_10d"),
            (20, "ret_20d"),
            (60, "ret_60d"),
        ]:
            df[col] = log_close - log_close.shift(period)
        return df

    # ------------------------------------------------------------------
    # Step 3: realized volatility
    # ------------------------------------------------------------------

    def _add_realized_vol(
        self, df: pd.DataFrame, daily_ret: Optional[pd.Series] = None
    ) -> pd.DataFrame:
        if daily_ret is None:
            daily_ret = np.log(df["close"].clip(lower=EPS)) - np.log(
                df["close"].clip(lower=EPS).shift(1)
            )
        for period, col in [(10, "vol_10d"), (20, "vol_20d"), (60, "vol_60d")]:
            df[col] = daily_ret.rolling(period).std()
        return df

    # ------------------------------------------------------------------
    # Step 4: RSI with Wilder's EMA smoothing
    # ------------------------------------------------------------------

    def _add_rsi(self, df: pd.DataFrame, period: int, col: str) -> pd.DataFrame:
        delta = df["close"].diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)

        # Wilder's smoothing: alpha = 1/period (same as EMA with span=period)
        alpha = 1.0 / period
        avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
        avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()

        rs = avg_gain / (avg_loss + EPS)
        df[col] = 100.0 - (100.0 / (1.0 + rs))
        return df

    # ------------------------------------------------------------------
    # Step 5: MACD (12/26/9 standard)
    # ------------------------------------------------------------------

    def _add_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd_line"] = ema12 - ema26
        df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd_line"] - df["macd_signal"]
        return df

    # ------------------------------------------------------------------
    # Step 6: price vs. moving average (ratio)
    # ------------------------------------------------------------------

    def _add_trend_ratios(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"].clip(lower=EPS)
        for period, col in [
            (20, "price_vs_20ma"),
            (50, "price_vs_50ma"),
            (200, "price_vs_200ma"),
        ]:
            ma = close.rolling(period).mean()
            df[col] = close / (ma + EPS)
        return df

    # ------------------------------------------------------------------
    # Step 7: Bollinger Band width
    # ------------------------------------------------------------------

    def _add_bollinger(self, df: pd.DataFrame) -> pd.DataFrame:
        period = 20
        mid = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        upper = mid + 2.0 * std
        lower = mid - 2.0 * std
        df["bb_width"] = (upper - lower) / (mid + EPS)
        return df

    # ------------------------------------------------------------------
    # Step 8: ATR-14 normalized
    # ------------------------------------------------------------------

    def _add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
        df["atr_14_norm"] = atr / (df["close"] + EPS)
        return df

    # ------------------------------------------------------------------
    # Step 9: OBV + 5-day slope (normalized)
    # ------------------------------------------------------------------

    def _add_obv(self, df: pd.DataFrame) -> pd.DataFrame:
        direction = np.sign(df["close"].diff().fillna(0.0))
        obv = (direction * df["volume"]).cumsum()

        # 5-period rolling slope via linear regression coefficient
        def rolling_slope(series: pd.Series, window: int) -> pd.Series:
            x = np.arange(window, dtype=float)
            x -= x.mean()
            x_norm = x / (np.dot(x, x) + EPS)
            return series.rolling(window).apply(
                lambda y: np.dot(x_norm, y - y.mean()), raw=True
            )

        slope = rolling_slope(obv, 5)
        mean_obv_abs = obv.abs().rolling(20).mean()
        df["obv_trend_5d"] = (slope / (mean_obv_abs + EPS)).clip(-1e3, 1e3)
        return df

    # ------------------------------------------------------------------
    # Step 10: volume features
    # ------------------------------------------------------------------

    def _add_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        vol = df["volume"]
        roll_mean = vol.rolling(20).mean()
        roll_std = vol.rolling(20).std()

        df["volume_vs_20ma"] = vol / (roll_mean + EPS)
        df["volume_zscore_20d"] = (vol - roll_mean) / (roll_std + EPS)
        return df

    # ------------------------------------------------------------------
    # Step 11: calendar features
    # ------------------------------------------------------------------

    def _add_calendar(self, df: pd.DataFrame) -> pd.DataFrame:
        if "date" in df.columns:
            dt = pd.to_datetime(df["date"])
            df["day_of_week"] = dt.dt.dayofweek  # 0=Mon … 4=Fri
            df["month"] = dt.dt.month  # 1–12
            df["quarter"] = dt.dt.quarter  # 1–4
        else:
            df["day_of_week"] = 0
            df["month"] = 1
            df["quarter"] = 1

        # Note: time_idx will be set in build() after NaN drop, not here
        return df

    # ------------------------------------------------------------------
    # Step 12: known-future event flags
    # ------------------------------------------------------------------

    def _add_event_flags(
        self,
        df: pd.DataFrame,
        event_dates,
        col: str,
    ) -> pd.DataFrame:
        df[col] = 0

        if not event_dates or "date" not in df.columns:
            return df

        # Normalize event dates to tz-naive date objects
        norm_events = []
        for d in event_dates:
            try:
                ts = pd.Timestamp(d)
                if ts.tzinfo is not None:
                    ts = ts.tz_localize(None)
                norm_events.append(ts.normalize())  # midnight
            except Exception:
                continue

        if not norm_events:
            return df

        # Use Python date objects (day-level) to avoid ns/us unit mismatch
        event_days = np.array(
            [e.date() for e in norm_events], dtype="datetime64[D]"
        ).astype(np.int64)
        window_days = 5  # 5 calendar days

        bar_dates = pd.to_datetime(df["date"])
        if bar_dates.dt.tz is not None:
            bar_dates = bar_dates.dt.tz_convert("UTC").dt.tz_localize(None)
        bar_days = bar_dates.values.astype("datetime64[D]").astype(np.int64)

        flags = np.zeros(len(df), dtype=np.int8)
        for ev_day in event_days:
            diff = ev_day - bar_days  # difference in days
            # Event is strictly within next 1-5 calendar days (excluding event day itself)
            flags[(diff >= 1) & (diff <= window_days)] = 1

        df[col] = flags
        return df

    # ------------------------------------------------------------------
    # Static covariate encoders
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_sector(sector: Optional[str]) -> int:
        if sector is None:
            return 0
        key = sector.strip().lower().replace(" ", "")
        # Try exact match first, then with spaces stripped
        for k, v in SECTOR_MAP.items():
            if k.replace(" ", "") == key:
                return v
        return 0

    @staticmethod
    def _encode_market_cap(bucket: Optional[str]) -> int:
        if bucket is None:
            return 1  # default: mid
        return MARKET_CAP_MAP.get(bucket.strip().lower(), 1)
