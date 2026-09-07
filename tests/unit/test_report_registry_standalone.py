from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.engine.base import BotEngine

_UNIVERSE = [f"SYM{i:03d}" for i in range(503)]


def _boot_shell(api=None):
    """Minimal BotEngine shell for _boot_report_registry_standalone (no heavy __init__)."""
    engine = object.__new__(BotEngine)
    engine.api = api  # None => Alpaca not connected (the live failure scenario)
    engine.specialist_registry = None
    engine.data_provider = MagicMock()
    engine.data_provider.get_sp500_symbols.return_value = list(_UNIVERSE)
    engine._schedule_specialist_warmup_check = MagicMock()
    return engine


class TestStandaloneReportBoot:
    def test_flag_off_no_boot_byte_identical(self):
        engine = _boot_shell(api=None)
        with patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry"
        ) as cls:
            gc.return_value = MagicMock(REPORT_REGISTRY_STANDALONE_ENABLED=False)
            engine._boot_report_registry_standalone()
        cls.assert_not_called()
        assert engine.specialist_registry is None

    def test_flag_on_boots_rich_report_WITHOUT_api(self):
        from core.engine.base import _CARD_REGISTRY_STOCKS

        engine = _boot_shell(api=None)  # self.api is None throughout
        fake = MagicMock()
        with patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry", return_value=fake
        ) as cls, patch("core.engine.base.config.get_secret_str", return_value=""):
            gc.return_value = MagicMock(REPORT_REGISTRY_STANDALONE_ENABLED=True)
            engine._boot_report_registry_standalone()
        assert engine.api is None  # proves independence from Alpaca
        assert cls.call_count == 1
        args, kwargs = cls.call_args
        assert args[0] == list(
            _CARD_REGISTRY_STOCKS
        )  # curated distinctive-ticker stocks
        assert (
            kwargs.get("report_only") is False
        )  # rich research()+synthesis path (the card)
        fake.start.assert_called_once()
        assert engine.specialist_registry is fake

    def test_idempotent_when_already_booted(self):
        engine = _boot_shell(api=None)
        sentinel = MagicMock()
        engine.specialist_registry = sentinel
        with patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry"
        ) as cls:
            gc.return_value = MagicMock(REPORT_REGISTRY_STANDALONE_ENABLED=True)
            engine._boot_report_registry_standalone()
        cls.assert_not_called()
        assert engine.specialist_registry is sentinel

    def test_boot_failure_degrades_to_none(self):
        engine = _boot_shell(api=None)
        with patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry",
            side_effect=RuntimeError("no llm/model on OSS edition"),
        ), patch("core.engine.base.config.get_secret_str", return_value=""):
            gc.return_value = MagicMock(REPORT_REGISTRY_STANDALONE_ENABLED=True)
            engine._boot_report_registry_standalone()  # must NOT raise
        assert engine.specialist_registry is None


class TestCardSetIsStocksOnly:
    def test_card_set_is_curated_distinctive_stocks(self):
        """#2249: the standalone card set is a curated list of distinctive-ticker
        household S&P 500 stocks — NO index ETFs (SPY/QQQ/IWM/DIA collide with
        everyday entities, e.g. DIA -> Dia Art Foundation) and NO ambiguous short
        tickers (A/ALL/KEY/ON/IT/KO) that fabricate off-subject prose the runtime
        grounding cannot yet disambiguate. Display-only; config.DEFAULT_SYMBOLS (the
        trading-universe fallback) is UNCHANGED.
        RED: point registry_universe back at config.DEFAULT_SYMBOLS."""
        from core.engine.base import _CARD_REGISTRY_STOCKS

        engine = _boot_shell(api=None)
        fake = MagicMock()
        with patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry", return_value=fake
        ) as cls, patch("core.engine.base.config.get_secret_str", return_value=""):
            gc.return_value = MagicMock(REPORT_REGISTRY_STANDALONE_ENABLED=True)
            engine._boot_report_registry_standalone()
        passed = cls.call_args[0][0]
        assert passed == list(_CARD_REGISTRY_STOCKS)
        for name in ("AAPL", "MSFT", "GOOGL", "NVDA", "JPM", "WMT"):
            assert name in passed  # household names present
        for bad in ("SPY", "QQQ", "IWM", "DIA", "A", "ALL", "KEY", "ON", "IT", "KO"):
            assert bad not in passed  # no ETFs, no ambiguous short tickers


class TestNoDoubleActivationGuard:
    def _make_start_engine(self):
        import threading

        engine = object.__new__(BotEngine)
        engine._shutdown_event = threading.Event()
        engine.specialist_registry = None
        engine.live_universe = []
        engine.current_market_data = {}
        engine.is_simulation = False
        engine.compliance_guardian = None
        engine.live_risk_manager = None
        engine.strategy_running = threading.Event()
        engine.monitor_running = threading.Event()
        engine.strategy_thread = None
        engine.monitor_thread = None
        engine._cycle_watchdog = None
        engine.api = MagicMock()
        engine.api.get_account.return_value = MagicMock(equity="100000.0")
        engine.data_provider = MagicMock()
        engine.data_provider.get_sp500_symbols.return_value = list(_UNIVERSE)
        engine._send_update_threadsafe = MagicMock()
        engine.stop_strategy = MagicMock()
        engine.run_strategy_async_wrapper = MagicMock()
        engine.run_strategy_monitor_loop = MagicMock()
        return engine

    def test_start_live_strategy_skips_reconstruction_when_standalone_on(self):
        # STANDALONE ON + a registry already booted (the init-boot one) -> the guard
        # dedups: start_live_strategy keeps the standalone registry, does not rebuild.
        engine = self._make_start_engine()
        sentinel = MagicMock()
        engine.specialist_registry = sentinel  # e.g. the standalone init-boot registry
        with patch("core.engine.base.config.ENVIRONMENT", "production"), patch(
            "core.engine.base.RiskManager"
        ), patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry"
        ) as cls:
            gc.return_value = MagicMock(
                SPECIALIST_REGISTRY_ENABLED=True,
                REPORT_REGISTRY_STANDALONE_ENABLED=True,
            )
            result = engine.start_live_strategy()
        assert result is True
        cls.assert_not_called()  # guard prevented double-construction
        assert engine.specialist_registry is sentinel

    def test_start_live_strategy_rebuilds_when_standalone_off(self):
        # STANDALONE OFF (default): the guard MUST fall back to today's behaviour — a
        # 2nd operator start rebuilds the registry (fresh cache), byte-identical to the
        # pre-fix engine. Regression for the refute blocker (unconditional guard would
        # have wrongly skipped this rebuild even with the standalone feature OFF).
        engine = self._make_start_engine()
        sentinel = MagicMock()
        engine.specialist_registry = sentinel
        with patch("core.engine.base.config.ENVIRONMENT", "production"), patch(
            "core.engine.base.RiskManager"
        ), patch("core.engine.base.config.get_config") as gc, patch(
            "core.engine.base.StockSpecialistRegistry"
        ) as cls:
            gc.return_value = MagicMock(
                SPECIALIST_REGISTRY_ENABLED=True,
                REPORT_REGISTRY_STANDALONE_ENABLED=False,
            )
            result = engine.start_live_strategy()
        assert result is True
        cls.assert_called_once()  # STANDALONE off -> original rebuild, not skipped


class TestReportOnlyNeverFeedsConsensus:
    @pytest.mark.anyio
    async def test_specialist_alpha_excluded_at_zero_weight_even_with_report(self):
        from core.round_table.agents import SpecialistAlphaAgent

        # #2839: default weight is now 0.40 (desktop-master profile) — the
        # dormancy path (0.0 => excluded) stays tested by forcing it explicitly.
        with patch.object(SpecialistAlphaAgent, "default_weight", 0.0), patch.object(
            SpecialistAlphaAgent, "max_weight", 0.0
        ):
            agent = SpecialistAlphaAgent()
            # Forced dormant posture -> max_weight 0.0 -> weight clamped 0.0.
            assert agent.weight == 0.0
            report = MagicMock(
                sentiment_score=80.0, recommendation="buy", escalate=False
            )
            registry = MagicMock()
            registry.get_report.return_value = report
            state = {
                "symbol": "AAPL",
                "ohlc": {"close": 150.0, "open": 148.0, "high": 151.0, "low": 147.0},
            }
            with patch(
                "core.round_table.agents._specialist_registry_instance", new=registry
            ):
                result = await agent.vote(state)
            # Even a bullish report yields weight 0.0 -> ConsensusEngine filters
            # weight>0 -> excluded.
            assert result.weight == 0.0
