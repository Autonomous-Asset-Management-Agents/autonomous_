# tests/unit/test_data_provider_databento.py
# TDD für DatabentoHistoricalClient — Epic 2.7
# yfinance eliminated; Databento ist institutioneller Ersatz
#
# Skip entire module if databento is not installed (optional dependency, not in CI requirements)
import allure
import pytest

databento = pytest.importorskip(
    "databento", reason="databento not installed — skipping (Epic 2.7 deferred)"
)

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from core.data_provider_databento import (
    DatabentoHistoricalClient,
    _databento_to_dataframe,
)

# ---------------------------------------------------------------------------
# Helper: build a fake Databento record
# ---------------------------------------------------------------------------


def _fake_record(open_price=100.0, close_price=101.0, high=102.0, low=99.0, vol=1000):
    """Create a minimal mock Databento record."""
    r = MagicMock()
    r.open = int(open_price * 1e9)
    r.high = int(high * 1e9)
    r.low = int(low * 1e9)
    r.close = int(close_price * 1e9)
    r.volume = vol
    r.ts_event = int(datetime(2025, 1, 2).timestamp() * 1e9)
    r.symbol = "AAPL"
    return r


# ---------------------------------------------------------------------------
# _databento_to_dataframe
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestDatabentoToDataframe:
    def test_empty_records_returns_empty_df(self):
        df = _databento_to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_returns_ohlcv_columns(self):
        record = _fake_record()
        df = _databento_to_dataframe([record])
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

    def test_price_conversion_from_fixed_point(self):
        record = _fake_record(open_price=150.0, close_price=155.0)
        df = _databento_to_dataframe([record])
        assert abs(df["open"].iloc[0] - 150.0) < 0.01
        assert abs(df["close"].iloc[0] - 155.0) < 0.01

    def test_index_is_datetime(self):
        record = _fake_record()
        df = _databento_to_dataframe([record])
        assert isinstance(df.index, pd.DatetimeIndex)


# ---------------------------------------------------------------------------
# DatabentoHistoricalClient
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestDatabentoHistoricalClient:
    """Unit tests with mocked Databento SDK — no real API calls."""

    def test_raises_without_api_key(self):
        """Client should fail loudly if no API key is set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABENTO_API_KEY", None)
            with pytest.raises(ValueError, match="DATABENTO_API_KEY"):
                DatabentoHistoricalClient(api_key=None)

    def test_get_bars_returns_dataframe(self):
        """get_bars returns a non-empty OHLCV DataFrame on success."""
        client = DatabentoHistoricalClient(api_key="test-key")
        mock_historical = MagicMock()
        mock_historical.timeseries.get_range.return_value = [_fake_record()]

        with patch("databento.Historical", return_value=mock_historical):
            client._client = mock_historical
            df = client.get_bars(
                symbol="AAPL",
                start=datetime(2025, 1, 1),
                end=datetime(2025, 1, 31),
            )

        assert not df.empty
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_get_bars_empty_response_returns_empty_df(self):
        """get_bars returns an empty DataFrame when API returns no records."""
        client = DatabentoHistoricalClient(api_key="test-key")
        mock_historical = MagicMock()
        mock_historical.timeseries.get_range.return_value = []

        with patch("databento.Historical", return_value=mock_historical):
            client._client = mock_historical
            df = client.get_bars(
                symbol="AAPL",
                start=datetime(2025, 1, 1),
                end=datetime(2025, 1, 31),
            )

        assert df.empty

    def test_get_bars_api_error_returns_empty_df(self):
        """get_bars swallows Databento API errors and returns empty DataFrame."""
        client = DatabentoHistoricalClient(api_key="test-key")
        mock_historical = MagicMock()
        mock_historical.timeseries.get_range.side_effect = RuntimeError("API Error")

        with patch("databento.Historical", return_value=mock_historical):
            client._client = mock_historical
            df = client.get_bars(
                symbol="AAPL",
                start=datetime(2025, 1, 1),
                end=datetime(2025, 1, 31),
            )

        assert df.empty

    def test_get_batch_bars_returns_dict(self):
        """get_batch_bars returns a dict mapping symbol → DataFrame."""
        client = DatabentoHistoricalClient(api_key="test-key")
        mock_historical = MagicMock()
        record = _fake_record()
        record.symbol = "AAPL"
        mock_historical.timeseries.get_range.return_value = [record]

        with patch("databento.Historical", return_value=mock_historical):
            client._client = mock_historical
            result = client.get_batch_bars(
                symbols=["AAPL"],
                start=datetime(2025, 1, 1),
                end=datetime(2025, 1, 31),
            )

        assert "AAPL" in result
        assert not result["AAPL"].empty

    def test_get_batch_bars_empty_symbols_returns_empty_dict(self):
        """get_batch_bars with empty symbol list returns empty dict."""
        client = DatabentoHistoricalClient(api_key="test-key")
        result = client.get_batch_bars([], datetime(2025, 1, 1), datetime(2025, 1, 31))
        assert result == {}
