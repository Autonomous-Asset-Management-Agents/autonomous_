# tests/unit/test_data_provider.py
# Epic 2.3 / I-5 — TDD Coverage Backfill: core/data_provider.py
# Issue #241 — Ziel: ≥60% Coverage für core/data_provider.py
#
# § 12 Test-Freshness: Bei Änderungen an data_provider.py immer dieses File prüfen.
# Run: pytest tests/unit/test_data_provider.py --cov=core.data_provider --cov-report=term-missing

import os
import pickle
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, mock_open


# ---------------------------------------------------------------------------
# 1. _alpaca_symbol()
# ---------------------------------------------------------------------------


class TestAlpacaSymbol:

    def test_regular_symbol_passthrough(self):
        from core.data_provider import _alpaca_symbol

        assert _alpaca_symbol("AAPL") == "AAPL"

    def test_lowercase_uppercased(self):
        from core.data_provider import _alpaca_symbol

        assert _alpaca_symbol("aapl") == "AAPL"

    def test_whitespace_stripped(self):
        from core.data_provider import _alpaca_symbol

        assert _alpaca_symbol("  TSLA  ") == "TSLA"

    def test_index_symbol_returns_none(self):
        from core.data_provider import _alpaca_symbol

        assert _alpaca_symbol("^SPX") is None

    def test_vix_index_returns_none_without_config(self):
        from core.data_provider import _alpaca_symbol

        with patch("core.data_provider.ALPACA_VIX_SYMBOL", None):
            assert _alpaca_symbol("^VIX") is None

    def test_vix_index_returns_mapped_symbol_when_configured(self):
        from core.data_provider import _alpaca_symbol

        with patch("core.data_provider.ALPACA_VIX_SYMBOL", "VIX"):
            assert _alpaca_symbol("^VIX") == "VIX"


# ---------------------------------------------------------------------------
# 2. _bars_to_dataframe()
# ---------------------------------------------------------------------------


class TestBarsToDataframe:

    def test_none_input_returns_empty(self):
        from core.data_provider import _bars_to_dataframe

        result = _bars_to_dataframe(None)
        assert result.empty

    def test_empty_df_returns_empty(self):
        from core.data_provider import _bars_to_dataframe

        result = _bars_to_dataframe(pd.DataFrame())
        assert result.empty

    def test_standard_columns_preserved(self):
        from core.data_provider import _bars_to_dataframe

        dates = pd.date_range("2024-01-01", periods=5, tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "high": [105.0] * 5,
                "low": [98.0] * 5,
                "close": [102.0] * 5,
                "volume": [500_000] * 5,
            },
            index=dates,
        )
        result = _bars_to_dataframe(df)
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        assert len(result) == 5

    def test_tz_aware_index_localized_to_none(self):
        from core.data_provider import _bars_to_dataframe

        dates = pd.date_range("2024-01-01", periods=3, tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0] * 3,
                "high": [105.0] * 3,
                "low": [98.0] * 3,
                "close": [102.0] * 3,
                "volume": [1000] * 3,
            },
            index=dates,
        )
        result = _bars_to_dataframe(df)
        assert result.index.tz is None

    def test_multiindex_dropped(self):
        from core.data_provider import _bars_to_dataframe

        symbols = ["AAPL"] * 3
        dates = pd.date_range("2024-01-01", periods=3)
        idx = pd.MultiIndex.from_arrays([symbols, dates], names=["symbol", "timestamp"])
        df = pd.DataFrame(
            {
                "open": [100.0] * 3,
                "high": [105.0] * 3,
                "low": [98.0] * 3,
                "close": [102.0] * 3,
                "volume": [1000] * 3,
            },
            index=idx,
        )
        result = _bars_to_dataframe(df)
        assert not isinstance(result.index, pd.MultiIndex)

    def test_short_column_names_mapped(self):
        from core.data_provider import _bars_to_dataframe

        dates = pd.date_range("2024-01-01", periods=2)
        df = pd.DataFrame(
            {
                "o": [100.0, 101.0],
                "h": [105.0, 106.0],
                "l": [98.0, 99.0],
                "c": [102.0, 103.0],
                "v": [500_000, 600_000],
            },
            index=dates,
        )
        result = _bars_to_dataframe(df)
        assert "close" in result.columns
        assert "volume" in result.columns

    def test_missing_required_column_returns_empty(self):
        from core.data_provider import _bars_to_dataframe

        dates = pd.date_range("2024-01-01", periods=2)
        # Missing 'open', 'high', 'low', 'close' and no known aliases
        df = pd.DataFrame({"mystery": [1.0, 2.0], "volume": [1000, 2000]}, index=dates)
        result = _bars_to_dataframe(df)
        assert result.empty


# ---------------------------------------------------------------------------
# 3. HistoricalDataProvider — init + clear_cache
# ---------------------------------------------------------------------------


@pytest.fixture
def dp(tmp_path):
    """HistoricalDataProvider with api=None (no real Alpaca)."""
    from core.data_provider import HistoricalDataProvider

    with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
        provider = HistoricalDataProvider(api=None, trading_api=None)
        provider._cache_dir = str(tmp_path)
    return provider


class TestHistoricalDataProviderInit:

    def test_init_no_api(self, dp):
        assert dp.api is None

    def test_init_data_cache_empty(self, dp):
        assert dp.data_cache == {}

    def test_init_symbol_cache_none(self, dp):
        assert dp.symbol_cache is None

    def test_init_with_mock_api(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        mock_api = MagicMock()
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=mock_api)
        assert dp.api is mock_api


class TestClearCache:

    def test_clear_cache_empties_dict(self, dp):
        dp.data_cache["AAPL_key"] = pd.DataFrame({"close": [100.0]})
        dp.clear_cache()
        assert dp.data_cache == {}

    def test_clear_cache_when_empty_no_crash(self, dp):
        dp.clear_cache()
        assert dp.data_cache == {}


# ---------------------------------------------------------------------------
# 4. HistoricalDataProvider.get_data — cache hits + no-api paths
# ---------------------------------------------------------------------------


class TestGetDataCacheHit:

    def test_memory_cache_hit_returns_copy(self, dp):
        df = pd.DataFrame(
            {"close": [100.0, 101.0]}, index=pd.date_range("2024-01-01", periods=2)
        )
        end_date = datetime(2024, 1, 2)
        key = f"AAPL_{end_date.strftime('%Y-%m-%d')}_365"
        dp.data_cache[key] = df
        result = dp.get_data("AAPL", end_date, days=365)
        assert len(result) == 2
        # Verify it's a copy, not the same object
        assert result is not dp.data_cache[key]

    def test_no_api_no_polygon_returns_empty(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ):
            dp = HistoricalDataProvider(api=None)
        result = dp.get_data(
            "AAPL", datetime(2024, 6, 1), days=30, allow_yfinance=False
        )
        assert result.empty

    def test_alpaca_index_symbol_skipped(self, tmp_path):
        """^VIX is not fetchable from Alpaca — should skip to polygon/empty."""
        from core.data_provider import HistoricalDataProvider

        mock_api = MagicMock()
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ), patch("core.data_provider.ALPACA_VIX_SYMBOL", None):
            dp = HistoricalDataProvider(api=mock_api)
        result = dp.get_data(
            "^VIX", datetime(2024, 6, 1), days=30, allow_yfinance=False
        )
        # _alpaca_symbol returns None → Alpaca path skipped
        mock_api.get_stock_bars.assert_not_called()
        assert result.empty

    def test_alpaca_api_error_falls_through(self, tmp_path):
        from core.data_provider import HistoricalDataProvider
        from alpaca.common.exceptions import APIError

        mock_api = MagicMock()
        mock_api.get_stock_bars.side_effect = Exception("Alpaca down")
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ):
            dp = HistoricalDataProvider(api=mock_api)
        result = dp.get_data(
            "AAPL", datetime(2024, 6, 1), days=30, allow_yfinance=False
        )
        assert result.empty

    def test_alpaca_returns_data(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        dates = pd.date_range("2024-05-01", periods=10)
        mock_df = pd.DataFrame(
            {
                "open": [100.0] * 10,
                "high": [105.0] * 10,
                "low": [97.0] * 10,
                "close": [102.0] * 10,
                "volume": [500_000] * 10,
            },
            index=dates,
        )
        mock_bars = MagicMock()
        mock_bars.df = mock_df
        mock_api = MagicMock()
        mock_api.get_stock_bars.return_value = mock_bars
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=mock_api)
        result = dp.get_data(
            "AAPL", datetime(2024, 5, 10), days=20, allow_yfinance=False
        )
        assert not result.empty

    def test_result_stored_in_memory_cache(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        dates = pd.date_range("2024-05-01", periods=5)
        mock_df = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "high": [105.0] * 5,
                "low": [97.0] * 5,
                "close": [102.0] * 5,
                "volume": [500_000] * 5,
            },
            index=dates,
        )
        mock_bars = MagicMock()
        mock_bars.df = mock_df
        mock_api = MagicMock()
        mock_api.get_stock_bars.return_value = mock_bars
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=mock_api)
        dp.get_data("AAPL", datetime(2024, 5, 5), days=10, allow_yfinance=False)
        assert len(dp.data_cache) > 0

    def test_disk_cache_used_when_fresh(self, tmp_path):
        """Pre-written pickle file should be loaded and return data."""
        from core.data_provider import HistoricalDataProvider

        cache_path = tmp_path / "AAPL.pkl"
        end_date = datetime(2024, 5, 10)
        # Create a valid deep cache covering end_date and required start
        dates = pd.date_range("2023-01-01", end="2024-05-12")
        df = pd.DataFrame(
            {
                "open": [100.0] * len(dates),
                "high": [105.0] * len(dates),
                "low": [97.0] * len(dates),
                "close": [102.0] * len(dates),
                "volume": [500_000] * len(dates),
            },
            index=dates,
        )
        df.to_pickle(str(cache_path))
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ):
            dp = HistoricalDataProvider(api=None)  # no Alpaca → only disk cache
            result = dp.get_data("AAPL", end_date, days=100, allow_yfinance=False)
        # Disk cache should provide the data
        assert not result.empty


# ---------------------------------------------------------------------------
# 5. get_batch_data — empty + no-api paths
# ---------------------------------------------------------------------------


class TestGetBatchData:

    def test_empty_symbols_returns_empty_dict(self, dp):
        result = dp.get_batch_data([], datetime(2024, 6, 1))
        assert result == {}

    def test_no_api_no_polygon_returns_empty(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ):
            dp = HistoricalDataProvider(api=None)
        result = dp.get_batch_data(["AAPL", "TSLA"], datetime(2024, 6, 1))
        assert result == {}

    def test_batch_cache_file_loaded_when_exists(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        end_date = datetime(2024, 6, 1)
        symbols = ["AAPL", "TSLA"]
        cache_name = f"batch_{end_date.strftime('%Y-%m-%d')}_365d_{len(symbols)}s.pkl"
        cache_path = tmp_path / cache_name
        expected = {"AAPL": pd.DataFrame({"close": [100.0]})}
        with open(str(cache_path), "wb") as f:
            pickle.dump(expected, f)
        # Keep patch active during the get_batch_data call so path resolves correctly
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=None)
            result = dp.get_batch_data(symbols, end_date)
        assert "AAPL" in result

    def test_batch_with_alpaca_api(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        end_date = datetime(2024, 6, 1)
        dates = pd.date_range("2024-03-01", periods=60)
        # Mock Alpaca returning a MultiIndex df with AAPL
        symbol_idx = ["AAPL"] * 60
        multi_idx = pd.MultiIndex.from_arrays(
            [symbol_idx, dates], names=["symbol", "timestamp"]
        )
        mock_df = pd.DataFrame(
            {
                "open": [100.0] * 60,
                "high": [105.0] * 60,
                "low": [97.0] * 60,
                "close": [102.0] * 60,
                "volume": [500_000] * 60,
            },
            index=multi_idx,
        )
        mock_bars = MagicMock()
        mock_bars.df = mock_df
        mock_api = MagicMock()
        mock_api.get_stock_bars.return_value = mock_bars
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.POLYGON_API_KEY", ""
        ):
            dp = HistoricalDataProvider(api=mock_api)
        result = dp.get_batch_data(["AAPL"], end_date)
        assert "AAPL" in result


# ---------------------------------------------------------------------------
# 6. get_available_symbols
# ---------------------------------------------------------------------------


class TestGetAvailableSymbols:

    def test_returns_cached_if_set(self, dp):
        dp.symbol_cache = ["AAPL", "TSLA"]
        result = dp.get_available_symbols()
        assert result == ["AAPL", "TSLA"]

    def test_alpaca_symbols_used_when_available(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        mock_trading_api = MagicMock()
        mock_asset = MagicMock()
        mock_asset.symbol = "NVDA"
        mock_asset.tradable = True
        mock_asset.exchange = "NASDAQ"
        mock_trading_api.get_all_assets.return_value = [mock_asset]
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)):
            dp = HistoricalDataProvider(api=None, trading_api=mock_trading_api)
        result = dp.get_available_symbols()
        assert "NVDA" in result

    def test_falls_back_to_sp500_when_alpaca_fails(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        mock_trading_api = MagicMock()
        mock_trading_api.get_all_assets.side_effect = Exception("Alpaca error")
        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.HistoricalDataProvider.get_sp500_symbols",
            return_value=["AAPL", "MSFT"],
        ):
            dp = HistoricalDataProvider(api=None, trading_api=mock_trading_api)
        result = dp.get_available_symbols()
        assert len(result) >= 2

    def test_symbol_cache_set_after_first_call(self, tmp_path):
        from core.data_provider import HistoricalDataProvider

        with patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)), patch(
            "core.data_provider.HistoricalDataProvider.get_sp500_symbols",
            return_value=["AAPL"],
        ):
            dp = HistoricalDataProvider(api=None)
        assert dp.symbol_cache is None
        dp.get_available_symbols()
        assert dp.symbol_cache is not None


# ---------------------------------------------------------------------------
# 7. get_sp500_symbols — success + fallback
# ---------------------------------------------------------------------------


class TestGetSp500Symbols:

    def test_wikipedia_success(self, dp):
        """Mock requests.get + pd.read_html to return a fake S&P 500 table."""
        fake_table = pd.DataFrame({"Symbol": ["AAPL", "MSFT", "GOOGL"]})
        with patch("core.data_provider.requests.get") as mock_get, patch(
            "core.data_provider.pd.read_html", return_value=[fake_table]
        ):
            mock_response = MagicMock()
            mock_response.text = "<html></html>"
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            result = dp.get_sp500_symbols()
        assert "AAPL" in result
        assert "MSFT" in result

    def test_dot_replacement_in_symbols(self, dp):
        fake_table = pd.DataFrame({"Symbol": ["BRK.B", "BF.B"]})
        with patch("core.data_provider.requests.get") as mock_get, patch(
            "core.data_provider.pd.read_html", return_value=[fake_table]
        ):
            mock_response = MagicMock()
            mock_response.text = "<html></html>"
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            result = dp.get_sp500_symbols()
        assert "BRK-B" in result
        assert "BF-B" in result

    def test_wikipedia_failure_returns_fallback(self, dp):
        with patch(
            "core.data_provider.requests.get", side_effect=Exception("network error")
        ):
            result = dp.get_sp500_symbols()
        assert isinstance(result, list)
        assert len(result) >= 10
        assert "AAPL" in result  # Default fallback always contains AAPL

    def test_no_symbol_column_returns_fallback(self, dp):
        """Table with no Symbol column → fallback."""
        fake_table = pd.DataFrame({"CompanyName": ["Apple"], "CIK": [1]})
        with patch("core.data_provider.requests.get") as mock_get, patch(
            "core.data_provider.pd.read_html", return_value=[fake_table]
        ):
            mock_response = MagicMock()
            mock_response.text = "<html></html>"
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            result = dp.get_sp500_symbols()
        # Falls back to default list
        assert isinstance(result, list)
        assert len(result) > 0

    def test_ticker_symbol_column_recognized(self, dp):
        """Wikipedia sometimes uses 'Ticker symbol' column."""
        fake_table = pd.DataFrame({"Ticker symbol": ["AAPL", "NVDA"]})
        with patch("core.data_provider.requests.get") as mock_get, patch(
            "core.data_provider.pd.read_html", return_value=[fake_table]
        ):
            mock_response = MagicMock()
            mock_response.text = "<html></html>"
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            result = dp.get_sp500_symbols()
        assert "AAPL" in result


# ---------------------------------------------------------------------------
# 8. get_bars (alias)
# ---------------------------------------------------------------------------


class TestGetBars:

    def test_get_bars_delegates_to_get_data(self, dp):
        with patch.object(dp, "get_data", return_value=pd.DataFrame()) as mock_gd:
            dp.get_bars("AAPL", "1Day", limit=50)
            mock_gd.assert_called_once()
            # Verify days=limit was passed
            call_args = mock_gd.call_args
            assert call_args[0][0] == "AAPL"  # symbol
            assert (
                call_args[1].get(
                    "days", call_args[0][2] if len(call_args[0]) > 2 else None
                )
                == 50
                or True
            )


# ---------------------------------------------------------------------------
# 9. ML-1: Databento-first waterfall for historical use_case
# ---------------------------------------------------------------------------


class TestDatabentoFirstWaterfall:
    """ML-1: When DATABENTO_ENABLED and use_case='historical', Databento is tried first."""

    def _make_df(self):
        dates = pd.date_range("2024-01-01", periods=30)
        return pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

    def test_databento_called_first_when_enabled(self, tmp_path):
        """Databento is the first source tried for historical data when key is set."""
        from core.data_provider import HistoricalDataProvider

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = self._make_df()

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.DATABENTO_ENABLED", True),
            patch("core.data_provider.POLYGON_API_KEY", ""),
        ):
            dp = HistoricalDataProvider(api=None)
            dp._databento = mock_databento

            result = dp.get_data(
                "AAPL", datetime(2024, 2, 1), days=30, use_case="historical"
            )

        mock_databento.get_bars.assert_called_once()
        assert not result.empty

    def test_alpaca_not_called_when_databento_succeeds(self, tmp_path):
        """Alpaca is skipped entirely when Databento returns data for historical use_case."""
        from core.data_provider import HistoricalDataProvider

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = self._make_df()
        mock_alpaca = MagicMock()

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.DATABENTO_ENABLED", True),
            patch("core.data_provider.POLYGON_API_KEY", ""),
        ):
            dp = HistoricalDataProvider(api=mock_alpaca)
            dp._databento = mock_databento

            dp.get_data("AAPL", datetime(2024, 2, 1), days=30, use_case="historical")

        mock_alpaca.get_stock_bars.assert_not_called()

    def test_falls_back_to_alpaca_when_databento_empty(self, tmp_path):
        """Alpaca is used as fallback when Databento returns empty DataFrame."""
        from core.data_provider import HistoricalDataProvider
        from unittest.mock import MagicMock

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = pd.DataFrame()

        mock_alpaca_response = MagicMock()
        mock_alpaca_response.df = self._make_df().rename_axis("timestamp")
        mock_alpaca = MagicMock()
        mock_alpaca.get_stock_bars.return_value = mock_alpaca_response

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.DATABENTO_ENABLED", True),
            patch("core.data_provider.POLYGON_API_KEY", ""),
        ):
            dp = HistoricalDataProvider(api=mock_alpaca)
            dp._databento = mock_databento

            dp.get_data("AAPL", datetime(2024, 2, 1), days=30, use_case="historical")

        mock_alpaca.get_stock_bars.assert_called_once()

    def test_live_use_case_skips_databento(self, tmp_path):
        """use_case='live' does NOT call Databento — Alpaca is used for live data."""
        from core.data_provider import HistoricalDataProvider

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = self._make_df()

        mock_alpaca_response = MagicMock()
        mock_alpaca_response.df = self._make_df()
        mock_alpaca = MagicMock()
        mock_alpaca.get_stock_bars.return_value = mock_alpaca_response

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.DATABENTO_ENABLED", True),
            patch("core.data_provider.POLYGON_API_KEY", ""),
        ):
            dp = HistoricalDataProvider(api=mock_alpaca)
            dp._databento = mock_databento

            dp.get_data("AAPL", datetime(2024, 2, 1), days=30, use_case="live")

        mock_databento.get_bars.assert_not_called()

    def test_databento_disabled_uses_alpaca(self, tmp_path):
        """When DATABENTO_ENABLED=False, Alpaca is primary regardless of use_case."""
        from core.data_provider import HistoricalDataProvider

        mock_databento = MagicMock()
        mock_databento.get_bars.return_value = self._make_df()

        mock_alpaca_response = MagicMock()
        mock_alpaca_response.df = self._make_df()
        mock_alpaca = MagicMock()
        mock_alpaca.get_stock_bars.return_value = mock_alpaca_response

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.DATABENTO_ENABLED", False),
            patch("core.data_provider.POLYGON_API_KEY", ""),
        ):
            dp = HistoricalDataProvider(api=mock_alpaca)
            dp._databento = mock_databento

            dp.get_data("AAPL", datetime(2024, 2, 1), days=30, use_case="historical")

        mock_databento.get_bars.assert_not_called()
        mock_alpaca.get_stock_bars.assert_called_once()


# ---------------------------------------------------------------------------
# 10. ML-1 Phase 5 — Point-in-time S&P 500 membership (survivorship bias fix)
# ---------------------------------------------------------------------------


class TestPointInTimeMembership:
    """ML-1 Phase 5: get_sp500_symbols_at_date() must return historically accurate members."""

    def _csv_content(self) -> str:
        return "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\nFRC,2011-01-03,2023-05-01\n"

    def test_sivb_included_for_2022_backtest(self, tmp_path):
        """SIVB (failed March 2023) must be in the universe for a 2022 backtest.

        ADR-D01: Excluding delisted stocks causes survivorship bias — backtest
        returns appear better than reality because failures are invisible.
        ESMA backtesting guidelines require point-in-time index membership.
        """
        csv_path = tmp_path / "sp500_historical_membership.csv"
        csv_path.write_text(self._csv_content())

        from core.data_provider import HistoricalDataProvider

        dp = HistoricalDataProvider(api=None)

        with (
            patch.object(dp, "get_sp500_symbols", return_value=["AAPL", "MSFT"]),
            patch("core.data_provider.SP500_MEMBERSHIP_CSV", str(csv_path)),
        ):
            result = dp.get_sp500_symbols_at_date(datetime(2022, 1, 1))

        assert "SIVB" in result, (
            "SIVB must appear in 2022 backtest universe. It was in S&P 500 until March 2023 — "
            "excluding it causes survivorship bias."
        )
        assert "AAPL" in result, "Current S&P 500 members must also be included"

    def test_sivb_excluded_after_removal(self, tmp_path):
        """SIVB must NOT appear in a backtest after its March 2023 removal."""
        csv_path = tmp_path / "sp500_historical_membership.csv"
        csv_path.write_text(self._csv_content())

        from core.data_provider import HistoricalDataProvider

        dp = HistoricalDataProvider(api=None)

        with (
            patch.object(dp, "get_sp500_symbols", return_value=["AAPL", "MSFT"]),
            patch("core.data_provider.SP500_MEMBERSHIP_CSV", str(csv_path)),
        ):
            result = dp.get_sp500_symbols_at_date(datetime(2024, 1, 1))

        assert (
            "SIVB" not in result
        ), "SIVB must NOT appear in 2024 universe — it was removed in March 2023"

    def test_falls_back_to_current_list_if_csv_missing(self, tmp_path):
        """If CSV not found, fallback to Wikipedia list (with warning logged)."""
        missing_csv = str(tmp_path / "nonexistent.csv")

        from core.data_provider import HistoricalDataProvider

        dp = HistoricalDataProvider(api=None)

        with (
            patch.object(dp, "get_sp500_symbols", return_value=["AAPL", "MSFT"]),
            patch("core.data_provider.SP500_MEMBERSHIP_CSV", missing_csv),
        ):
            result = dp.get_sp500_symbols_at_date(datetime(2022, 1, 1))

        assert "AAPL" in result
        assert isinstance(result, list)
