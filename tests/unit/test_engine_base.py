# tests/unit/test_engine_base.py
# TDD Red → Green für Epic 1: Fail-Fast Initialisierung
#
# Gherkin:
#   Given: GEMINI_API_KEY fehlt
#   When:  BotEngine instanziiert wird
#   Then:  RuntimeError mit "CRITICAL DEPENDENCY MISSING" geworfen
#
#   Given: GEMINI_API_KEY vorhanden
#   When:  BotEngine instanziiert wird
#   Then:  Engine startet ohne Fehler
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD - Red → Green → Refactor

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestBotEngineValidateDependencies:
    def test_missing_gemini_key_raises(self, monkeypatch):
        """
        TDD GREEN: Fehlender GEMINI_API_KEY muss harten RuntimeError auslösen.
        Kein stiller Fallback (Watermelon-Prevention).
        """
        import config as cfg

        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "")

        # Heavy deps mocken, um import-Seiteneffekte zu vermeiden
        with patch(
            "core.engine.base.HistoricalDataProvider", return_value=MagicMock()
        ), patch("core.engine.base.NewsProcessor", return_value=MagicMock()), patch(
            "core.engine.base.MarketRegimeModel", return_value=MagicMock()
        ), patch(
            "core.engine.base.AIMarketScanner", return_value=MagicMock()
        ), patch(
            "core.engine.base.AILearningEngine", return_value=MagicMock()
        ), patch(
            "core.engine.base.AgentRegistry", return_value=MagicMock()
        ), patch(
            "core.engine.base.set_global_registry"
        ):

            from core.engine.base import BotEngine

            with pytest.raises(
                RuntimeError, match="CRITICAL DEPENDENCY MISSING.*GEMINI_API_KEY"
            ):
                BotEngine(trading_client=MagicMock(), data_client=MagicMock())

    def test_valid_gemini_key_does_not_raise(self, monkeypatch):
        """
        TDD GREEN: Mit vorhandenem GEMINI_API_KEY darf kein RuntimeError kommen.
        """
        import config as cfg

        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "valid-test-key")

        with patch(
            "core.engine.base.HistoricalDataProvider", return_value=MagicMock()
        ), patch("core.engine.base.NewsProcessor", return_value=MagicMock()), patch(
            "core.engine.base.MarketRegimeModel", return_value=MagicMock()
        ), patch(
            "core.engine.base.AIMarketScanner", return_value=MagicMock()
        ), patch(
            "core.engine.base.AILearningEngine", return_value=MagicMock()
        ), patch(
            "core.engine.base.AgentRegistry", return_value=MagicMock()
        ), patch(
            "core.engine.base.set_global_registry"
        ):

            from core.engine.base import BotEngine

            # Kein Crash erwartet
            engine = BotEngine(trading_client=MagicMock(), data_client=MagicMock())
            assert engine is not None
