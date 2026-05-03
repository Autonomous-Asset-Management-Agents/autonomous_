# tests/unit/test_sp500_universe.py
# Epic 4.2 — Universe Port: tests for core.round_table.sp500_universe
#
# Gherkin:
#   Given: The sp500_universe module is imported
#   When:  Public functions are called
#   Then:  They return non-empty lists of strings with no delisted symbols

from __future__ import annotations

from unittest.mock import patch


class TestSp500UniverseImports:
    def test_module_importable(self):
        from core.round_table.sp500_universe import (  # noqa: F401
            get_sp500_symbols,
            get_sp500_symbols_live,
            get_universe_symbols,
            get_universe_batches,
        )

    def test_sp500_symbols_constant_exists(self):
        from core.round_table.sp500_universe import SP500_SYMBOLS

        assert isinstance(SP500_SYMBOLS, list)
        assert len(SP500_SYMBOLS) > 400


class TestGetSp500Symbols:
    def test_returns_list_of_strings(self):
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = get_sp500_symbols()
        assert isinstance(symbols, list)
        assert all(isinstance(s, str) for s in symbols)

    def test_returns_non_empty_list(self):
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = get_sp500_symbols()
        assert len(symbols) > 400

    def test_excludes_delisted_symbols(self):
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = get_sp500_symbols()
        delisted = {"ATVI", "FRC", "SIVB", "SBNY", "BF.B", "BRK.B"}
        for ticker in delisted:
            assert (
                ticker not in symbols
            ), f"Delisted ticker {ticker} should not be in universe"

    def test_contains_core_symbols(self):
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = get_sp500_symbols()
        core = {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA"}
        for ticker in core:
            assert ticker in symbols, f"Core symbol {ticker} missing from universe"

    def test_contains_etfs(self):
        from core.round_table.sp500_universe import get_sp500_symbols

        symbols = get_sp500_symbols()
        etfs = {"SPY", "QQQ", "IWM", "DIA"}
        for etf in etfs:
            assert etf in symbols


class TestGetSp500SymbolsLive:
    def test_returns_none_on_network_failure(self):
        from core.round_table.sp500_universe import get_sp500_symbols_live

        with patch("pandas.read_html", side_effect=Exception("network error")):
            result = get_sp500_symbols_live()
        assert result is None

    def test_returns_none_on_small_result(self):
        """If the live fetch returns < 400 symbols, treat as failure."""
        from core.round_table.sp500_universe import get_sp500_symbols_live
        import pandas as pd

        tiny_df = pd.DataFrame({"Symbol": ["AAPL", "MSFT"]})
        with patch("pandas.read_html", return_value=[tiny_df]):
            result = get_sp500_symbols_live()
        assert result is None


class TestGetUniverseSymbols:
    def test_returns_list_of_strings(self):
        from core.round_table.sp500_universe import get_universe_symbols

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            symbols = get_universe_symbols()
        assert isinstance(symbols, list)
        assert all(isinstance(s, str) for s in symbols)

    def test_max_symbols_respected(self):
        from core.round_table.sp500_universe import get_universe_symbols

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            symbols = get_universe_symbols(max_symbols=50)
        assert len(symbols) <= 50

    def test_exclude_etfs(self):
        from core.round_table.sp500_universe import get_universe_symbols

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            symbols = get_universe_symbols(include_etfs=False)
        etfs = {"SPY", "QQQ", "IWM", "DIA"}
        for etf in etfs:
            assert etf not in symbols

    def test_include_etfs_by_default(self):
        from core.round_table.sp500_universe import get_universe_symbols

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            symbols = get_universe_symbols()
        assert "SPY" in symbols


class TestGetUniverseBatches:
    def test_returns_list_of_lists(self):
        from core.round_table.sp500_universe import get_universe_batches

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            batches = get_universe_batches(batch_size=50)
        assert isinstance(batches, list)
        assert all(isinstance(b, list) for b in batches)

    def test_batch_size_respected(self):
        from core.round_table.sp500_universe import get_universe_batches

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            batches = get_universe_batches(batch_size=20)
        for b in batches[:-1]:  # last batch may be smaller
            assert len(b) == 20

    def test_all_symbols_covered(self):
        from core.round_table.sp500_universe import (
            get_universe_batches,
            get_sp500_symbols,
        )

        with patch(
            "core.round_table.sp500_universe.get_sp500_symbols_live", return_value=None
        ):
            batches = get_universe_batches(batch_size=50)
            all_batched = [s for batch in batches for s in batch]

        static = get_sp500_symbols()
        assert set(all_batched) == set(static)
