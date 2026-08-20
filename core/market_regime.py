# core/market_regime.py
# Epic 1.7 / PR-A — Extracted from ai_components.py
# Contains: MarketRegimeModel (VIX → regime classification with SPY fallback).

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd

from config import USE_SPY_VOLATILITY_FALLBACK
from core.data_provider import HistoricalDataProvider

# ADR-OBS-01 / PR E: VIX/regime freshness observation (PURE OBSERVATION). The mark
# below is fail-safe at the module boundary AND wrapped again here so a counter
# failure can never raise into — or alter — the regime computation.
from core.data_provider_telemetry import mark_regime_update as _mark_regime_update


def _obs_regime(vix_present: bool) -> None:
    """Fail-safe call-site guard around the VIX/regime freshness mark (PR E)."""
    try:
        _mark_regime_update(vix_present)
    except Exception:  # noqa: BLE001 — a broken counter must never break the regime
        pass


class MarketRegimeModel:
    """Classifies current market regime based on VIX data with SPY volatility fallback."""

    def __init__(self, data_provider: HistoricalDataProvider):
        self.data_provider = data_provider
        self.vix_thresholds = {"low": 15, "normal": 25, "high": 35}
        self.vix_cache: Dict[str, Any] = {}
        self.cache_duration = timedelta(days=1)
        # A genuine live-VIX (or SPY-fallback) regime is valid for the trading day.
        # A FAILURE result (Default/Error, value=None) must NOT poison the whole
        # day: it is cached only briefly so a transient VIX/SPY outage self-heals
        # within the session instead of leaving VIXAwareRiskAgent EXCLUDED until an
        # engine restart (live finding 2026-07-23). value=None marks a failure.
        self.failure_cache_duration = timedelta(minutes=5)
        logging.info(
            "Market Regime Model initialized (using VIX thresholds with SPY fallback)."
        )

    def _calculate_spy_volatility(
        self, current_date: datetime, sim_client: Optional[Any] = None
    ) -> Optional[float]:
        if not USE_SPY_VOLATILITY_FALLBACK:
            return None

        try:
            spy_data = None
            if sim_client:
                spy_data = sim_client.get_bars("SPY", "1d", limit=60)
            else:
                spy_data = self.data_provider.get_data("SPY", current_date, days=60)

            if spy_data is None or spy_data.empty or "close" not in spy_data.columns:
                logging.warning(
                    "SPY fallback: No data for SPY up to %s",
                    current_date.strftime("%Y-%m-%d"),
                )
                return None

            returns = spy_data["close"].pct_change().dropna()

            if len(returns) < 20:
                logging.warning(
                    "SPY fallback: Insufficient returns (%d < 20)", len(returns)
                )
                return None

            implied_vix = returns.std() * (252**0.5) * 100
            logging.info(
                "SPY-derived volatility: %.2f (from %d returns)",
                implied_vix,
                len(returns),
            )
            return implied_vix

        except Exception as e:
            logging.error("Error calculating SPY volatility: %s", e, exc_info=True)
            return None

    def _regime_from_value(
        self, vix_value: Optional[float], source: Optional[str] = None
    ) -> Dict[str, Any]:
        if vix_value is None or pd.isna(vix_value):
            return {
                "regime": "Ranging",
                "confidence": 0.1,
                "indicator": "Default",
                "value": None,
            }

        if vix_value < self.vix_thresholds["low"]:
            regime, conf = "Low Volatility", 0.7
        elif vix_value < self.vix_thresholds["normal"]:
            regime, conf = "Ranging", 0.75
        elif vix_value < self.vix_thresholds["high"]:
            regime, conf = "Trending", 0.7
        else:
            regime, conf = "High Volatility", 0.8

        # #2958: the caller KNOWS which feed produced the number — trust it.
        # The old <10 value-heuristic never fired for real-world realized vol
        # (~12-30 annualized), so SPY-derived readings shipped labeled "VIX".
        indicator = source or ("SPY_VOL" if vix_value and vix_value < 10 else "VIX")

        return {
            "regime": regime,
            "confidence": conf,
            "indicator": indicator,
            "value": round(vix_value, 2),
        }

    def get_market_regime(
        self, current_date: datetime, sim_client: Optional[Any] = None
    ) -> Dict[str, Any]:
        if current_date is None:
            current_date = datetime.now()
        cache_key = current_date.strftime("%Y-%m-%d")
        if not sim_client:
            if cache_key in self.vix_cache:
                cached_data, cached_time = self.vix_cache[cache_key]
                # Failure results (value is None) get the short TTL so a transient
                # outage is retried within minutes; genuine regimes keep the day TTL.
                ttl = (
                    self.failure_cache_duration
                    if cached_data.get("value") is None
                    else self.cache_duration
                )
                if datetime.now() - cached_time < ttl:
                    return cached_data

        try:
            vix_data = None
            if sim_client:
                vix_data = sim_client.get_bars("^VIX", "1d", limit=100)
            else:
                vix_data = self.data_provider.get_data("^VIX", current_date, days=100)

            if (
                vix_data is not None
                and not vix_data.empty
                and "close" in vix_data.columns
            ):
                close_series = vix_data["close"].dropna()

                if not close_series.empty:
                    current_vix = close_series.iloc[-1]
                    logging.debug(
                        "VIX data found: %.2f for %s",
                        current_vix,
                        close_series.index[-1].strftime("%Y-%m-%d"),
                    )

                    result = self._regime_from_value(current_vix, source="VIX")
                    if not sim_client:
                        self.vix_cache[cache_key] = (result, datetime.now())
                        _obs_regime(vix_present=True)  # PR E: real VIX backed regime
                    return result

            # #2958 / §5.6: a fallback substitution logs at WARNING, never
            # DEBUG — operators must see that the regime runs on realized SPY
            # vol, not implied vol. The per-day vix_cache bounds the frequency.
            logging.warning(
                "VIX unavailable for %s, trying SPY fallback (realized vol)",
                current_date.strftime("%Y-%m-%d"),
            )
            spy_vix = self._calculate_spy_volatility(current_date, sim_client)

            if spy_vix is not None:
                result = self._regime_from_value(spy_vix, source="SPY_VOL")
                if not sim_client:
                    self.vix_cache[cache_key] = (result, datetime.now())
                    _obs_regime(vix_present=False)  # PR E: SPY-fallback (no live VIX)
                return result

            logging.warning(
                "Both VIX and SPY unavailable for %s, using default",
                current_date.strftime("%Y-%m-%d"),
            )
            result = {
                "regime": "Ranging",
                "confidence": 0.1,
                "indicator": "Default",
                "value": None,
            }
            if not sim_client:
                self.vix_cache[cache_key] = (result, datetime.now())
                _obs_regime(vix_present=False)  # PR E: neither VIX nor SPY available
            return result

        except Exception as e:
            logging.error("Error determining market regime: %s", e, exc_info=True)
            result = {
                "regime": "Ranging",
                "confidence": 0.1,
                "indicator": "Error",
                "value": None,
            }
            if not sim_client:
                self.vix_cache[cache_key] = (result, datetime.now())
                _obs_regime(vix_present=False)  # PR E: regime computation errored
            return result
