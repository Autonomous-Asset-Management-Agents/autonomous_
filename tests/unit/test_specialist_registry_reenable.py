# tests/unit/test_specialist_registry_reenable.py
# RPAR-#1284 (G1b) - Flag-gated Re-Enablement der StockSpecialistRegistry.
#
# TDD Red-First. Deckt die bindenden Invarianten des Aktivierungs-Gates ab:
#   (1) Flag OFF (Default) -> byte-identisch: specialist_registry is None,
#       /specialist-reports-DTO inkl. exaktem `message`-Key, monitor_loop None-Gate.
#   (2) Flag ON -> StockSpecialistRegistry wird NACH der Universe-Befüllung
#       (start_live_strategy) mit dem ECHTEN, befüllten self.live_universe
#       konstruiert + .start() + _register_high_priority_symbols_at_startup +
#       _schedule_specialist_warmup_check. (Schlägt heute fehl - der Prod-Pfad
#       macht diese Calls nicht.)
#   (3) Decision-Path: non-None Registry mit Report -> SpecialistAlphaAgent votet
#       über den Report-Pfad (score>0.5, reasoning ohne "EXCLUDED") statt über den
#       Excluded-Pfad. Der P1-Decision-Path-delta.
#   (4) /specialist-reports Empty/Warming-Route-States.
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First (MiFID II / EU AI Act Art. 14).

from __future__ import annotations

from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

import config
from core.engine.base import BotEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_POPULATED_UNIVERSE: List[str] = [f"SYM{i:03d}" for i in range(503)]


def _make_engine() -> BotEngine:
    """Construct a BotEngine shell WITHOUT running the heavy __init__.

    `object.__new__` bypasses validate_dependencies / news-polling / health
    checks. We set exactly the attributes start_live_strategy touches up to -
    and just past - the universe-load + activation block, then stub the
    downstream thread/loop machinery so the method runs to completion without
    real threads, brokers or network.
    """
    import threading

    engine = object.__new__(BotEngine)

    # Attributes read in start_live_strategy before/around the activation block.
    engine._shutdown_event = threading.Event()
    engine.specialist_registry = None  # set by _init_specialist_registry in prod
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

    # Broker + data provider (mocked - no network).
    engine.api = MagicMock()
    engine.api.get_account.return_value = MagicMock(equity="100000.0")
    engine.data_provider = MagicMock()
    engine.data_provider.get_sp500_symbols.return_value = list(_POPULATED_UNIVERSE)

    # No-op the GUI/update plumbing + thread machinery so the method completes.
    engine._send_update_threadsafe = MagicMock()
    engine.stop_strategy = MagicMock()
    engine.run_strategy_async_wrapper = MagicMock()
    engine.run_strategy_monitor_loop = MagicMock()
    return engine


# ---------------------------------------------------------------------------
# (1) Flag OFF -> byte-identisch (bindendes Epic-#1262-Gherkin)
# ---------------------------------------------------------------------------


class TestRegistryDisabledByteIdentical:
    def _run_start(self, engine: BotEngine) -> None:
        # Drive the real start_live_strategy through the universe-load + (skipped)
        # activation block. RiskManager is patched so no real broker math runs.
        with patch("core.engine.base.config.ENVIRONMENT", "production"), patch(
            "core.engine.base.RiskManager"
        ):
            result = engine.start_live_strategy()
        assert result is True

    def test_flag_off_registry_stays_none(self):
        engine = _make_engine()
        assert (
            config.get_config().SPECIALIST_REGISTRY_ENABLED is False
        ), "Default-Posture muss OFF sein - sonst ist das Landing nicht dormant."
        self._run_start(engine)
        # The whole universe loaded (proves we ran past L555) ...
        assert engine.live_universe == _POPULATED_UNIVERSE
        # ... yet the registry is still the None set by _init_specialist_registry.
        assert engine.specialist_registry is None

    def test_flag_off_specialist_reports_dto_byte_identical(self):
        # The /specialist-reports unavailable-DTO must equal today's EXACT dict,
        # INCLUDING the `message` key. This is the byte-identity core invariant.
        from core.engine import api_routes

        engine = MagicMock()
        engine.specialist_registry = None
        with patch.object(api_routes, "engine", engine):
            import asyncio

            dto = asyncio.run(api_routes.get_specialist_reports(None))
        assert dto == {
            "status": "unavailable",
            "message": "StockSpecialistRegistry not running on this deployment.",
            "reports": [],
            "registry_status": {},
        }

    def test_flag_off_monitor_loop_does_not_inject(self):
        # The monitor_loop injection path is None-gated: with registry None it must
        # NOT call set_specialist_registry. We replay that exact guard.
        engine = _make_engine()
        self._run_start(engine)
        spec_reg = getattr(engine, "specialist_registry", None)
        called = {"n": 0}

        def _fake_set(_reg):
            called["n"] += 1

        with patch(
            "core.round_table.agents.set_specialist_registry", side_effect=_fake_set
        ):
            if spec_reg is not None:  # the literal monitor_loop None-gate
                from core.round_table.agents import set_specialist_registry

                set_specialist_registry(spec_reg)
        assert called["n"] == 0


# ---------------------------------------------------------------------------
# (2) Flag ON -> Registry NACH Universe-Load mit dem ECHTEN Universe aktiviert
# ---------------------------------------------------------------------------


class TestRegistryEnabledConstructsAndWires:
    def test_flag_on_constructs_with_populated_universe_and_wires(self):
        engine = _make_engine()
        fake_registry = MagicMock()

        with patch("core.engine.base.config.ENVIRONMENT", "production"), patch(
            "core.engine.base.RiskManager"
        ), patch("core.engine.base.config.get_config") as mock_get_config, patch(
            "core.engine.base.StockSpecialistRegistry", return_value=fake_registry
        ) as mock_registry_cls, patch.object(
            engine, "_register_high_priority_symbols_at_startup"
        ) as mock_register_prio, patch.object(
            engine, "_schedule_specialist_warmup_check"
        ) as mock_warmup:
            mock_get_config.return_value = MagicMock(SPECIALIST_REGISTRY_ENABLED=True)
            result = engine.start_live_strategy()

        assert result is True
        # Constructed exactly once ...
        assert mock_registry_cls.call_count == 1
        ctor_args, _ = mock_registry_cls.call_args
        # ... with the POPULATED universe (proves activation runs AFTER L555),
        # NOT [] / DEFAULT_SYMBOLS.
        assert ctor_args[0] == _POPULATED_UNIVERSE
        assert engine.live_universe == _POPULATED_UNIVERSE
        # .start() started, priority + warmup wired (closes the start-wiring gap).
        fake_registry.start.assert_called_once()
        mock_register_prio.assert_called_once_with(_POPULATED_UNIVERSE)
        mock_warmup.assert_called_once()
        assert engine.specialist_registry is fake_registry

    def test_flag_on_registry_init_failure_degrades_to_none(self):
        # Boot resilience (#1361): on the OSS edition the registry's LLM/model deps may
        # be absent (ollama, no Gemini key, no local model), so construction/start can
        # throw. The engine MUST degrade to specialist_registry=None and keep booting,
        # NOT crash -> SpecialistAlphaAgent then stays excluded (weight 0), as when OFF.
        engine = _make_engine()
        with patch("core.engine.base.config.ENVIRONMENT", "production"), patch(
            "core.engine.base.RiskManager"
        ), patch("core.engine.base.config.get_config") as mock_get_config, patch(
            "core.engine.base.StockSpecialistRegistry",
            side_effect=RuntimeError("LLM provider unavailable on the OSS edition"),
        ):
            mock_get_config.return_value = MagicMock(SPECIALIST_REGISTRY_ENABLED=True)
            result = engine.start_live_strategy()  # must NOT raise
        assert result is True  # boot continued despite the registry init failure
        assert engine.specialist_registry is None  # degraded gracefully


# ---------------------------------------------------------------------------
# (3) Decision-Path: non-None Registry -> Report-Pfad statt Excluded (P1-delta)
# ---------------------------------------------------------------------------


class TestDecisionPathInjection:
    def _state(self, symbol: str = "AAPL") -> Dict:
        return {
            "symbol": symbol,
            "ohlc": {"close": 150.0, "open": 148.0, "high": 151.0, "low": 147.0},
        }

    @pytest.mark.anyio
    async def test_report_present_uses_report_branch_not_excluded(self):
        from core.round_table.agents import SpecialistAlphaAgent

        agent = SpecialistAlphaAgent()
        report = MagicMock()
        report.sentiment_score = 75.0
        report.recommendation = "buy"
        report.escalate = False
        registry = MagicMock()
        registry.get_report.return_value = report
        with patch(
            "core.round_table.agents._specialist_registry_instance", new=registry
        ):
            result = await agent.vote(self._state())
        # Report-Pfad: buy + 75 sentiment -> score > 0.5, reasoning ohne "EXCLUDED".
        assert result.score > 0.5
        assert "EXCLUDED" not in result.reasoning
        assert result.weight == agent.weight

    @pytest.mark.anyio
    async def test_no_registry_excluded_branch(self):
        from core.round_table.agents import SpecialistAlphaAgent

        agent = SpecialistAlphaAgent()
        with patch("core.round_table.agents._specialist_registry_instance", new=None):
            result = await agent.vote(self._state())
        assert "EXCLUDED" in result.reasoning
        assert result.weight == 0.0


# ---------------------------------------------------------------------------
# (4) /specialist-reports Empty/Warming-Route-States
# ---------------------------------------------------------------------------


class TestSpecialistReportsRouteStates:
    def test_route_registry_none_unavailable(self):
        import asyncio

        from core.engine import api_routes

        engine = MagicMock()
        engine.specialist_registry = None
        with patch.object(api_routes, "engine", engine):
            dto = asyncio.run(api_routes.get_specialist_reports(None))
        assert dto["status"] == "unavailable"

    def test_route_registry_warming_empty_reports_ok(self):
        import asyncio

        from core.engine import api_routes

        registry = MagicMock()
        registry.get_all_reports.return_value = {}
        registry.get_escalations.return_value = []
        registry.get_status.return_value = {}
        engine = MagicMock()
        engine.specialist_registry = registry
        with patch.object(api_routes, "engine", engine):
            dto = asyncio.run(api_routes.get_specialist_reports(None))
        assert dto["status"] == "ok"
        assert dto["total"] == 0
        assert dto["reports"] == []
