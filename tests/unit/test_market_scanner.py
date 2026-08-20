# tests/unit/test_market_scanner.py
# TDD-First: Tests written BEFORE core/market_scanner.py exists.
# Defines the contract of AIMarketScanner extracted from ai_components.py.
# Epic 1.7 / PR-A

import asyncio
import threading
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pandas as pd
import pytest

# RED: will fail until core/market_scanner.py is created
from core.market_scanner import AIMarketScanner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def shutdown_event():
    return threading.Event()


@pytest.fixture()
def mock_signals():
    signals = MagicMock()
    signals.scanner_progress = MagicMock()
    signals.scanner_progress.emit = MagicMock()
    signals.scanner_complete = MagicMock()
    signals.scanner_complete.emit = MagicMock()
    return signals


@pytest.fixture()
def mock_data_provider():
    dp = MagicMock()
    dp.get_available_symbols.return_value = ["AAPL", "MSFT", "GOOG"]
    dp.get_data.return_value = pd.DataFrame()
    return dp


@pytest.fixture()
def mock_news_processor():
    np = MagicMock()
    np._is_simulation = False
    return np


@pytest.fixture()
def scanner(mock_signals, mock_data_provider, mock_news_processor, shutdown_event):
    with patch("core.gemini_client._gemini_instance", None):
        return AIMarketScanner(
            signals=mock_signals,
            data_provider=mock_data_provider,
            news_processor=mock_news_processor,
            shutdown_event=shutdown_event,
        )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAIMarketScannerInit:
    def test_initializes_without_error(self, scanner):
        assert scanner is not None

    def test_starts_not_running(self, scanner):
        assert scanner.running is False

    def test_simulation_mode_off_by_default(self, scanner):
        assert scanner._is_simulation is False


# ---------------------------------------------------------------------------
# set_simulation_mode
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestSetSimulationMode:
    def test_sets_simulation_mode(self, scanner, mock_news_processor):
        scanner.set_simulation_mode(True)
        assert scanner._is_simulation is True

    def test_propagates_to_news_processor(self, scanner, mock_news_processor):
        scanner.set_simulation_mode(True)
        assert mock_news_processor._is_simulation is True


# ---------------------------------------------------------------------------
# _is_valid_dataframe
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestIsValidDataframe:
    def test_none_is_invalid(self, scanner):
        assert scanner._is_valid_dataframe(None) is False

    def test_empty_df_is_invalid(self, scanner):
        assert scanner._is_valid_dataframe(pd.DataFrame()) is False

    def test_non_empty_df_is_valid(self, scanner):
        df = pd.DataFrame({"close": [1, 2, 3]})
        assert scanner._is_valid_dataframe(df) is True

    def test_non_dataframe_is_invalid(self, scanner):
        assert scanner._is_valid_dataframe([1, 2, 3]) is False


# ---------------------------------------------------------------------------
# scan_market — aborts when shutdown_event is set
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestScanMarketShutdown:
    @pytest.mark.anyio
    async def test_returns_none_when_shutdown(self, scanner, shutdown_event):
        shutdown_event.set()
        result = await scanner.scan_market(
            current_date=datetime(2024, 1, 15),
            market_regime={"regime": "Ranging", "confidence": 0.5},
        )
        assert result is None
        assert scanner.running is False


# ---------------------------------------------------------------------------
# scan_market — fallback (no Gemini, no sufficient data → default symbols)
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestScanMarketFallback:
    @pytest.mark.anyio
    async def test_returns_result_dict_on_success(self, scanner, mock_data_provider):
        # Provide enough OHLCV data for _calculate_score to succeed
        idx = pd.date_range("2023-06-01", periods=100, freq="D")
        df = pd.DataFrame(
            {
                "open": [100.0] * 100,
                "high": [105.0] * 100,
                "low": [95.0] * 100,
                "close": [102.0] * 100,
                "volume": [1_000_000] * 100,
            },
            index=idx,
        )
        mock_data_provider.get_data.return_value = df
        mock_data_provider.get_available_symbols.return_value = ["AAPL"]

        result = await scanner.scan_market(
            current_date=datetime(2024, 1, 15),
            market_regime={"regime": "Ranging", "confidence": 0.5},
        )
        assert result is not None
        assert "top_stocks" in result
        assert "recommended_strategy" in result

    @pytest.mark.anyio
    async def test_result_contains_recommended_strategy(
        self, scanner, mock_data_provider
    ):
        mock_data_provider.get_available_symbols.return_value = []
        result = await scanner.scan_market(
            current_date=datetime(2024, 1, 15),
            market_regime={"regime": "Ranging", "confidence": 0.5},
        )
        if result:
            assert result["recommended_strategy"] in ("RLAgent", "LSTMDynamic")


# ---------------------------------------------------------------------------
# scan_market — cross-loop (simulation scenario)
# ---------------------------------------------------------------------------
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestScanMarketCrossLoop:
    def test_scan_market_works_in_new_thread_event_loop(
        self, scanner, mock_data_provider
    ):
        """Regression: scanner must not raise 'Semaphore bound to a different event loop'
        when called from a thread that creates asyncio.new_event_loop() — the exact
        scenario used by simulation_runner.py."""
        idx = pd.date_range("2023-06-01", periods=100, freq="D")
        df = pd.DataFrame(
            {
                "open": [100.0] * 100,
                "high": [105.0] * 100,
                "low": [95.0] * 100,
                "close": [102.0] * 100,
                "volume": [1_000_000] * 100,
            },
            index=idx,
        )
        mock_data_provider.get_data.return_value = df
        mock_data_provider.get_available_symbols.return_value = ["AAPL"]

        result_box: list = []
        error_box: list = []

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    scanner.scan_market(
                        datetime(2024, 1, 15), {"regime": "Ranging", "confidence": 0.5}
                    )
                )
                result_box.append(result)
            except Exception as exc:
                error_box.append(exc)
            finally:
                loop.close()

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=15)

        assert not error_box, f"Scanner raised in thread: {error_box[0]}"
        assert len(result_box) == 1, "Thread timed out or did not return a result"
        assert result_box[0] is not None
        assert "top_stocks" in result_box[0]
