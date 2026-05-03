# tests/unit/test_databento_cache_normalization.py
# ML-1 Phase 6b — GCS cache key normalization for cost optimisation.
#
# Problem: exact-date cache keys cause a full Databento re-fetch every day
# because start=T-565 shifts by 1 day daily → cache miss → wasted API calls.
#
# Fix: normalize start to month boundary, end to T-7 (stable week boundary).
# Same key is reused for ~30 days → Databento called ~monthly, not daily.
#
# Gherkin:
#   Given: two get_bars() calls one day apart for the same symbol
#   When:  normalized cache keys are used
#   Then:  second call is a cache HIT (Databento called only once)
#
#   Given: end_date is today
#   When:  _normalize_end() is called
#   Then:  result is at least 7 days in the past (stable historical data)
#
#   Given: start_date is mid-month
#   When:  _normalize_start() is called
#   Then:  result is the first day of that month

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _make_ohlcv(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. Cache key normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizeStart:
    def test_rounds_down_to_first_of_month(self):
        from core.data_cache_gcs import _normalize_start

        dt = datetime(2024, 10, 17)
        assert _normalize_start(dt) == datetime(2024, 10, 1)

    def test_first_of_month_unchanged(self):
        from core.data_cache_gcs import _normalize_start

        dt = datetime(2024, 10, 1)
        assert _normalize_start(dt) == datetime(2024, 10, 1)

    def test_last_of_month_rounds_to_first(self):
        from core.data_cache_gcs import _normalize_start

        dt = datetime(2024, 10, 31)
        assert _normalize_start(dt) == datetime(2024, 10, 1)


class TestNormalizeEnd:
    def test_end_is_at_least_7_days_in_past(self):
        from core.data_cache_gcs import _normalize_end

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = _normalize_end(today)
        assert result <= today - timedelta(days=7)

    def test_old_end_date_not_moved_forward(self):
        """An end date already well in the past stays where it is (rounded to Monday)."""
        from core.data_cache_gcs import _normalize_end

        old_date = datetime(2023, 6, 15)  # well in the past
        result = _normalize_end(old_date)
        assert result <= old_date

    def test_recent_end_capped_to_t_minus_7(self):
        """End date of today gets capped — not served from cache as unstable."""
        from core.data_cache_gcs import _normalize_end

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = _normalize_end(today)
        assert result <= today - timedelta(days=7)


# ---------------------------------------------------------------------------
# 2. Stable keys across consecutive days
# ---------------------------------------------------------------------------


class TestCacheKeyStability:
    def test_same_key_on_consecutive_days(self):
        """Two calls one day apart in the same month produce the same cache key."""
        from core.data_cache_gcs import GCSDatabentoCache

        cache = GCSDatabentoCache(
            databento_client=MagicMock(), gcs_bucket="test-bucket"
        )

        # Two calls where start shifts by 1 day (typical daily production pattern)
        key1 = cache._cache_key("AAPL", datetime(2024, 10, 13), datetime(2025, 3, 10))
        key2 = cache._cache_key("AAPL", datetime(2024, 10, 14), datetime(2025, 3, 10))

        assert (
            key1 == key2
        ), "Cache key must be stable across consecutive days in the same month"

    def test_different_symbols_different_keys(self):
        from core.data_cache_gcs import GCSDatabentoCache

        cache = GCSDatabentoCache(
            databento_client=MagicMock(), gcs_bucket="test-bucket"
        )
        key_aapl = cache._cache_key(
            "AAPL", datetime(2024, 10, 13), datetime(2025, 3, 10)
        )
        key_msft = cache._cache_key(
            "MSFT", datetime(2024, 10, 13), datetime(2025, 3, 10)
        )
        assert key_aapl != key_msft

    def test_key_changes_on_new_month(self):
        """When start crosses a month boundary, a new cache key is generated."""
        from core.data_cache_gcs import GCSDatabentoCache

        cache = GCSDatabentoCache(
            databento_client=MagicMock(), gcs_bucket="test-bucket"
        )
        key_oct = cache._cache_key(
            "AAPL", datetime(2024, 10, 28), datetime(2025, 3, 10)
        )
        key_nov = cache._cache_key("AAPL", datetime(2024, 11, 1), datetime(2025, 3, 10))
        assert key_oct != key_nov


# ---------------------------------------------------------------------------
# 3. Cache hit on second daily call (Databento called only once)
# ---------------------------------------------------------------------------


class TestDatabentoCostOptimisation:
    def test_databento_called_once_for_two_consecutive_day_requests(self):
        """Same effective date range on consecutive days → only one Databento call."""
        from core.data_cache_gcs import GCSDatabentoCache

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = _make_ohlcv(30)

        # Simulate GCS: first call misses, second call hits
        gcs_store: dict = {}

        def read_from_gcs(key):
            return gcs_store.get(key)

        def write_to_gcs(key, df):
            gcs_store[key] = df

        cache = GCSDatabentoCache(
            databento_client=mock_databento, gcs_bucket="test-bucket"
        )

        with (
            patch.object(cache, "_read_from_gcs", side_effect=read_from_gcs),
            patch.object(cache, "_write_to_gcs", side_effect=write_to_gcs),
        ):
            # Day 1 — cache miss, Databento called
            cache.get_bars("AAPL", datetime(2024, 10, 13), datetime(2025, 3, 10))
            # Day 2 — same normalized key → cache HIT, Databento NOT called again
            cache.get_bars("AAPL", datetime(2024, 10, 14), datetime(2025, 3, 10))

        assert (
            mock_databento.get_bars.call_count == 1
        ), "Databento must only be called once — second call should hit GCS cache"
