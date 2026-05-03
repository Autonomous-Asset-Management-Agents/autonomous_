# tests/unit/test_data_cache_gcs.py
# ML-1 Phase 6 — GCS persistent cache for Databento data
#
# Gherkin:
#   Given: GCS bucket is configured (DATABENTO_GCS_BUCKET set)
#   When:  get_bars() is called for a symbol already in GCS cache
#   Then:  Databento client is NOT called — data returned from cache
#
#   Given: Symbol not in GCS cache
#   When:  get_bars() is called
#   Then:  Databento client IS called, result written to GCS for next time
#
#   Given: GCS is unavailable or DATABENTO_GCS_BUCKET not set
#   When:  get_bars() is called
#   Then:  Falls back to direct Databento call (no crash)

from __future__ import annotations

import pandas as pd
import pytest
from datetime import datetime, date
from unittest.mock import MagicMock, patch, call


def _make_ohlcv(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. GCS cache hit — Databento never called
# ---------------------------------------------------------------------------


class TestGCSCacheHit:
    def test_databento_not_called_when_gcs_has_data(self):
        """When GCS already holds data for the symbol/range, Databento is skipped."""
        from core.data_cache_gcs import GCSDatabentoCache

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = _make_ohlcv()

        mock_gcs_client = MagicMock()
        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket="test-bucket",
            gcs_client=mock_gcs_client,
        )

        with patch.object(
            cache, "_read_from_gcs", return_value=_make_ohlcv()
        ) as mock_read:
            result = cache.get_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 2, 1))

        mock_read.assert_called_once()
        mock_databento.get_bars.assert_not_called()
        assert not result.empty

    def test_returns_dataframe_from_cache(self):
        """Data returned from GCS cache has correct OHLCV structure."""
        from core.data_cache_gcs import GCSDatabentoCache

        expected = _make_ohlcv(30)
        mock_databento = MagicMock()

        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket="test-bucket",
        )

        with patch.object(cache, "_read_from_gcs", return_value=expected):
            result = cache.get_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 2, 1))

        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        assert len(result) == 30


# ---------------------------------------------------------------------------
# 2. GCS cache miss — Databento called, result written to GCS
# ---------------------------------------------------------------------------


class TestGCSCacheMiss:
    def test_databento_called_on_cache_miss(self):
        """When GCS has no data, Databento is called to fetch it."""
        from core.data_cache_gcs import GCSDatabentoCache

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = _make_ohlcv()

        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket="test-bucket",
        )

        with (
            patch.object(cache, "_read_from_gcs", return_value=None),
            patch.object(cache, "_write_to_gcs") as mock_write,
        ):
            result = cache.get_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 2, 1))

        mock_databento.get_bars.assert_called_once_with(
            symbol="AAPL",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 2, 1),
        )
        mock_write.assert_called_once()
        assert not result.empty

    def test_result_written_to_gcs_after_databento_fetch(self):
        """After a Databento fetch, data is written to GCS so next call is a cache hit."""
        from core.data_cache_gcs import GCSDatabentoCache

        fetched_data = _make_ohlcv(20)
        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = fetched_data

        written = []
        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket="test-bucket",
        )

        def capture_write(key, df):
            written.append((key, df))

        with (
            patch.object(cache, "_read_from_gcs", return_value=None),
            patch.object(cache, "_write_to_gcs", side_effect=capture_write),
        ):
            cache.get_bars("MSFT", datetime(2024, 1, 1), datetime(2024, 2, 1))

        assert len(written) == 1
        key, df = written[0]
        assert "MSFT" in key
        assert len(df) == 20


# ---------------------------------------------------------------------------
# 3. Graceful fallback when GCS is unavailable or bucket not set
# ---------------------------------------------------------------------------


class TestGCSFallback:
    def test_falls_back_to_databento_when_gcs_read_fails(self):
        """GCS read error → fall back to Databento directly (no crash)."""
        from core.data_cache_gcs import GCSDatabentoCache

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = _make_ohlcv()

        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket="test-bucket",
        )

        with (
            patch.object(
                cache, "_read_from_gcs", side_effect=Exception("GCS unavailable")
            ),
            patch.object(cache, "_write_to_gcs"),
        ):
            result = cache.get_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 2, 1))

        mock_databento.get_bars.assert_called_once()
        assert not result.empty

    def test_no_bucket_disables_gcs(self):
        """When gcs_bucket is None/empty, GCS is skipped entirely — Databento called directly."""
        from core.data_cache_gcs import GCSDatabentoCache

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = _make_ohlcv()

        cache = GCSDatabentoCache(
            databento_client=mock_databento,
            gcs_bucket=None,
        )

        with patch.object(cache, "_read_from_gcs") as mock_read:
            result = cache.get_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 2, 1))

        mock_read.assert_not_called()
        mock_databento.get_bars.assert_called_once()
        assert not result.empty


# ---------------------------------------------------------------------------
# Helper — pickle bytes for blob mock
# ---------------------------------------------------------------------------


def _ohlcv_bytes() -> bytes:
    import pickle

    return pickle.dumps(_make_ohlcv())
