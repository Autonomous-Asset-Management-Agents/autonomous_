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

from unittest.mock import MagicMock, patch

import allure
import pytest


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
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
        ), patch(
            "core.engine.base.resolved_provider_name", return_value="gemini"
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

    def _build_engine(self, monkeypatch, provider):
        """Construct a BotEngine with all heavy deps mocked (proven pattern)."""
        import config as cfg

        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "valid-test-key")
        with patch(
            "core.engine.base.HistoricalDataProvider", return_value=provider
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

            return BotEngine(trading_client=MagicMock(), data_client=MagicMock())

    def test_live_universe_is_alpaca_normalised(self, monkeypatch):
        """INC (0-decisions outage): _resolve_live_universe must NEVER leave the
        internal hyphen form for class shares in ``live_universe``. Alpaca's
        snapshot API rejects the ENTIRE batch atomically on the first ``BRK-B``
        (HTTP 400), so the universe must be Alpaca-native dot form and index
        symbols Alpaca cannot serve must be dropped. Pins the base.py wiring."""
        import config as cfg

        # Provider returns the internal HYPHEN form + an index symbol.
        provider = MagicMock()
        provider.get_sp500_symbols.return_value = ["AAPL", "BRK-B", "BF-B", "^SPX"]
        engine = self._build_engine(monkeypatch, provider)

        # FULL_UNIVERSE branch (desktop-real path): universe comes from the
        # provider, not the already-clean DEFAULT_SYMBOLS.
        monkeypatch.setattr(
            cfg.get_config(), "FULL_UNIVERSE_TRADING_ENABLED", True, raising=False
        )
        engine._resolve_live_universe()

        # Hyphen class shares -> dot form; ^SPX (Alpaca cannot serve) dropped.
        assert engine.live_universe == ["AAPL", "BRK.B", "BF.B"]
        assert not any("-" in s for s in engine.live_universe)

    def test_live_universe_default_symbols_branch_is_unchanged(self, monkeypatch):
        """Guard: normalisation must be a no-op on the clean DEFAULT_SYMBOLS path
        (no index/hyphen symbols) — no silent drops on the local-dev default."""
        import config as cfg

        provider = MagicMock()
        engine = self._build_engine(monkeypatch, provider)

        monkeypatch.setattr(
            cfg.get_config(), "FULL_UNIVERSE_TRADING_ENABLED", False, raising=False
        )
        engine._resolve_live_universe()

        assert engine.live_universe == list(cfg.DEFAULT_SYMBOLS)
        # The provider must NOT be scraped on the DEFAULT_SYMBOLS branch.
        provider.get_sp500_symbols.assert_not_called()


# ---------------------------------------------------------------------------
# TestComplianceGuardianReset (ADR-C04 / Fix #945 — Rogue Agent Hardening)
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestComplianceGuardianReset:
    """
    TDD coverage for the ComplianceGuardian.reset_daily_limit() call in
    BotEngine.start_live_strategy() (ADR-C04).

    Gherkin:
        Given: BotEngine with ComplianceGuardian active
        When:  start_live_strategy() is called (new trading day / container restart)
        Then:  compliance_guardian.reset_daily_limit() is called exactly once
        And:   Trade counter is reset — blocking trades on day 2+ in long-running
               containers (min-instances=1) is prevented.

    Root Cause (without fix):
        ComplianceGuardian.daily_trades persists across calendar days in
        long-running Cloud Run containers. Without reset, max_daily_trades=10
        blocks all trades from day 2 onward. Fix: reset in start_live_strategy().
    """

    _COMMON_PATCHES = [
        "core.engine.base.HistoricalDataProvider",
        "core.engine.base.NewsProcessor",
        "core.engine.base.MarketRegimeModel",
        "core.engine.base.AIMarketScanner",
        "core.engine.base.AILearningEngine",
        "core.engine.base.AgentRegistry",
        "core.engine.base.set_global_registry",
    ]

    def _make_engine(self, monkeypatch):
        """Construct a minimally-mocked BotEngine for start_live_strategy tests."""
        import config as cfg
        from core.engine.base import BotEngine

        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "test-key-compliance")

        mock_api = MagicMock()
        mock_api.get_account.return_value = MagicMock(equity="50000.0")
        mock_api.get_all_positions.return_value = []

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
            engine = BotEngine(trading_client=mock_api, data_client=MagicMock())

        return engine

    def test_reset_daily_limit_called_when_guardian_active(self, monkeypatch):
        """
        Given: compliance_guardian is not None
        When:  start_live_strategy() is called
        Then:  compliance_guardian.reset_daily_limit() is called exactly once.

        This prevents the day-2 trade blackout in long-running containers.
        """
        engine = self._make_engine(monkeypatch)

        mock_guardian = MagicMock()
        engine.compliance_guardian = mock_guardian

        with patch.object(engine, "stop_strategy"), patch.object(
            engine, "data_provider"
        ) as mock_dp, patch.object(engine, "_start_alpaca_news_polling"), patch(
            "core.engine.base.RiskManager"
        ) as mock_rm_cls, patch(
            "threading.Thread"
        ), patch(
            "core.engine.base.send_slack_alert"
        ):
            mock_dp.get_sp500_symbols.return_value = ["AAPL", "MSFT"]
            mock_rm_cls.return_value = MagicMock()

            engine.start_live_strategy()

        mock_guardian.reset_daily_limit.assert_called_once()
        # ADR-C04: compliance_guardian.reset_daily_limit() must be called in
        # start_live_strategy() to prevent day-2 trade blackout in long-running containers.

    def test_no_reset_when_guardian_is_none(self, monkeypatch):
        """
        Given: compliance_guardian is None (ENABLE_COMPLIANCE_GUARDIAN=False)
        When:  start_live_strategy() is called
        Then:  No AttributeError raised — None guard is respected.
        """
        engine = self._make_engine(monkeypatch)
        engine.compliance_guardian = None  # Explicitly disable

        with patch.object(engine, "stop_strategy"), patch.object(
            engine, "data_provider"
        ) as mock_dp, patch.object(engine, "_start_alpaca_news_polling"), patch(
            "core.engine.base.RiskManager"
        ) as mock_rm_cls, patch(
            "threading.Thread"
        ), patch(
            "core.engine.base.send_slack_alert"
        ):
            mock_dp.get_sp500_symbols.return_value = ["AAPL"]
            mock_rm_cls.return_value = MagicMock()

            # Must not raise AttributeError: 'NoneType' has no attribute 'reset_daily_limit'
            result = engine.start_live_strategy()

        # Engine should start normally without compliance guardian
        assert result is not False or result is None  # Returns True on success or None
