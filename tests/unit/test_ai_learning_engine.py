# tests/unit/test_ai_learning_engine.py
# Epic 1.7 / PR-D — TDD Red-Phase
# Tests für AILearningEngine (wird nach core/learning/engine.py extrahiert)
#
# Gherkin-Kriterien:
#   Given: AILearningEngine mit gemockten Abhängigkeiten
#   When:  Methoden der Engine aufgerufen werden
#   Then:  Korrektes Verhalten für CSV-Laden, Prompt-Bau, Gemini-Query, Learning-Analysis

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine():
    """Erstellt eine AILearningEngine-Instanz mit gemockten Abhängigkeiten."""
    from core.learning.engine import AILearningEngine

    signals = MagicMock()
    signals.ai_learning_update = MagicMock()
    signals.ai_learning_update.emit = MagicMock()
    signals.ai_learning_complete = MagicMock()
    signals.ai_learning_complete.emit = MagicMock()
    signals.error_message = MagicMock()
    signals.error_message.emit = MagicMock()

    with patch("core.gemini_client._gemini_instance", None):
        engine = AILearningEngine(signals)
    return engine, signals


# ---------------------------------------------------------------------------
# 1. CSV-Laden
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestLoadSimulationData:
    def test_file_not_found_returns_none_triple(self):
        """
        Given: simulation_trades.csv existiert nicht
        When:  _load_simulation_data() aufgerufen
        Then:  (None, None, None) zurückgegeben, error_message emittiert
        """
        engine, signals = _make_engine()
        with patch("pandas.read_csv", side_effect=FileNotFoundError("not found")):
            result = engine._load_simulation_data()
        assert result == (None, None, None)
        signals.error_message.emit.assert_called_once()

    def test_empty_dataframe_returns_none_triple(self):
        """
        Given: CSV vorhanden aber leer
        When:  _load_simulation_data() aufgerufen
        Then:  (None, None, None) zurückgegeben
        """
        import pandas as pd

        engine, signals = _make_engine()
        empty_df = pd.DataFrame()
        with patch("pandas.read_csv", return_value=empty_df):
            result = engine._load_simulation_data()
        assert result == (None, None, None)


# ---------------------------------------------------------------------------
# 2. Prompt-Bau
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestBuildPrompts:
    def test_learning_prompt_contains_trade_samples(self):
        """
        Given: Trade-Samples und News-Samples
        When:  _build_gemini_learning_prompt() aufgerufen
        Then:  Prompt enthält serialisierte Trade-Samples als JSON
        """
        engine, _ = _make_engine()
        trade_samples = [{"Symbol": "AAPL", "Side": "BUY", "daily_pnl": -50.0}]
        news_samples = [{"headline": "Market down"}]
        prompt = engine._build_gemini_learning_prompt(trade_samples, news_samples)
        assert "AAPL" in prompt
        assert "LOSING" in prompt or "losing" in prompt.lower()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_opportunity_prompt_contains_win_samples(self):
        """
        Given: Gewinn-Trade-Samples und News-Samples
        When:  _build_gemini_opportunity_prompt() aufgerufen
        Then:  Prompt enthält WINNING/WIN-Kontext
        """
        engine, _ = _make_engine()
        trade_samples = [{"Symbol": "MSFT", "Side": "BUY", "daily_pnl": 120.0}]
        news_samples = []
        prompt = engine._build_gemini_opportunity_prompt(trade_samples, news_samples)
        assert "MSFT" in prompt
        assert "WINNING" in prompt or "winning" in prompt.lower()
        assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# 3. Gemini-Query
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestRunGeminiQuery:
    @pytest.mark.anyio
    async def test_returns_parsed_json_on_success(self):
        """
        Given: Gemini gibt gültiges JSON zurück
        When:  _run_gemini_query() aufgerufen
        Then:  Geparste Dict zurückgegeben, kein Error
        """
        engine, _ = _make_engine()
        gemini_mock = MagicMock()
        gemini_mock.generate_content.return_value = json.dumps(
            {"analysis_summary": "test", "learned_rules": []}
        )
        engine.gemini_model = gemini_mock
        shutdown = threading.Event()
        result, error = await engine._run_gemini_query("test prompt", shutdown)
        assert error is None
        assert result == {"analysis_summary": "test", "learned_rules": []}

    @pytest.mark.anyio
    async def test_aborts_immediately_if_shutdown_set(self):
        """
        Given: shutdown_event bereits gesetzt
        When:  _run_gemini_query() aufgerufen
        Then:  Sofort (None, 'Aborted') zurückgegeben, kein Gemini-Aufruf
        """
        engine, _ = _make_engine()
        shutdown = threading.Event()
        shutdown.set()
        result, error = await engine._run_gemini_query("test prompt", shutdown)
        assert result is None
        assert error == "Aborted"

    @pytest.mark.anyio
    async def test_retries_on_failure_and_returns_error(self):
        """
        Given: Gemini wirft immer Exception
        When:  _run_gemini_query() aufgerufen (3 Retries)
        Then:  (None, str) nach Retries, generate_content 3x aufgerufen
        """
        engine, _ = _make_engine()
        gemini_mock = MagicMock()
        gemini_mock.generate_content.side_effect = Exception("API Error")
        engine.gemini_model = gemini_mock
        shutdown = threading.Event()

        with patch("asyncio.sleep", return_value=None):
            result, error = await engine._run_gemini_query("test prompt", shutdown)

        assert result is None
        assert error is not None
        assert gemini_mock.generate_content.call_count == 3


# ---------------------------------------------------------------------------
# 4. run_learning_analysis — Early Return ohne Gemini
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestRunLearningAnalysis:
    @pytest.mark.anyio
    async def test_emits_complete_if_no_gemini_model(self):
        """
        Given: Kein Gemini-Model verfügbar (gemini_model=None)
              und valide Simulation-CSV
        When:  run_learning_analysis() aufgerufen
        Then:  ai_learning_complete.emit({}) aufgerufen (kein Crash)
        """
        import pandas as pd

        engine, signals = _make_engine()
        engine.gemini_model = None

        trades_df = pd.DataFrame(
            {
                "Timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                "Symbol": ["AAPL", "MSFT"],
                "Side": ["BUY", "SELL"],
                "Qty": [10, 5],
                "Price": [150.0, 300.0],
                # TradeContext als JSON-String (so wie es aus CSV.read_csv käme)
                "TradeContext": ["{}", "{}"],
            }
        )
        equity_df = pd.DataFrame(
            {
                "Timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                "Equity": [100000.0, 101000.0],
            }
        )

        shutdown = threading.Event()
        historical_mock = MagicMock()
        historical_mock.get_data.return_value = pd.DataFrame()
        news_mock = MagicMock()
        news_mock.get_historical_news.return_value = []

        with patch("pandas.read_csv", side_effect=[trades_df, equity_df]), patch(
            "core.learning.engine.get_llm_provider", return_value=None
        ):
            await engine.run_learning_analysis(historical_mock, news_mock, shutdown)

        signals.ai_learning_complete.emit.assert_called_once_with({})

    @pytest.mark.anyio
    async def test_aborts_immediately_if_no_simulation_data(self):
        """
        Given: Simulation-CSVs nicht vorhanden (FileNotFoundError)
        When:  run_learning_analysis() aufgerufen
        Then:  ai_learning_update emittiert Fehler, kein ai_learning_complete-Aufruf
        """
        engine, signals = _make_engine()
        shutdown = threading.Event()
        historical_mock = MagicMock()
        news_mock = MagicMock()

        with patch("pandas.read_csv", side_effect=FileNotFoundError("not found")):
            await engine.run_learning_analysis(historical_mock, news_mock, shutdown)

        signals.ai_learning_complete.emit.assert_not_called()
        signals.ai_learning_update.emit.assert_called()
