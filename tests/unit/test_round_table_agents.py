# tests/unit/test_round_table_agents.py
# Epic 2.5 / Issue I-1 — TDD Red-Phase
# Round Table V2: VotingAgent Basisklasse + 9 spezialisierte Agents
#
# Gherkin-Kriterien (Architect Blueprint):
#   Given: SymbolEvalState mit validen OHLC-Skalaren
#   When:  agent.vote(state) aufgerufen
#   Then:  VoteResult mit score in [0.0, 1.0] + reasoning non-empty
#
#   Given: VIX-Proxy (Volume-Inverse) > Threshold
#   When:  VIXAwareRiskAgent.vote(state)
#   Then:  score < 0.3 (Strong Avoid)
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD - Red → Green → Refactor

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

try:
    import sb3_contrib  # noqa: F401

    HAS_SB3 = True
except ImportError:
    HAS_SB3 = False

# ---------------------------------------------------------------------------
# TDD Test: Epic 3 — SuspectDataException importierbar + verhalten korrekt
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestSuspectDataGuard:
    def test_suspect_data_exception_importable(self):
        """Epic 3: SuspectDataException muss aus agents importierbar sein."""
        from core.round_table.agents import SuspectDataException

        assert issubclass(SuspectDataException, ValueError)

    def test_suspect_data_exception_message(self):
        """Epic 3: Exception sollte Symbol und Preis im Message haben."""
        from core.round_table.agents import SuspectDataException

        exc = SuspectDataException("[AAPL] Flat-Candle mit vol=1000")
        assert "AAPL" in str(exc)


# ---------------------------------------------------------------------------
# Helper: Standard SymbolEvalState Fixture
# ---------------------------------------------------------------------------


def make_state(
    symbol: str = "AAPL",
    open_: float = 150.0,
    high: float = 155.0,
    low: float = 148.0,
    close: float = 152.0,
    volume: float = 1_000_000.0,
) -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


# ---------------------------------------------------------------------------
# 1. Smoke: VoteResult + VotingAgent importierbar
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestImports:
    def test_vote_result_importable(self):
        """VoteResult muss aus core.round_table.base_agent importierbar sein."""
        from core.round_table.base_agent import VoteResult  # noqa: F401

        assert VoteResult is not None

    def test_voting_agent_importable(self):
        """VotingAgent Basisklasse muss importierbar sein."""
        from core.round_table.base_agent import VotingAgent  # noqa: F401

        assert VotingAgent is not None

    def test_vote_result_has_slots(self):
        """VoteResult muss __slots__ nutzen (Serialisierungs-Optimierung)."""
        from core.round_table.base_agent import VoteResult

        assert hasattr(
            VoteResult, "__slots__"
        ), "VoteResult muss @dataclass(slots=True) nutzen"

    def test_vote_result_fields(self):
        """VoteResult muss alle Pflichtfelder haben."""
        from core.round_table.base_agent import VoteResult

        vr = VoteResult(
            agent_name="TestAgent",
            symbol="AAPL",
            score=0.7,
            weight=0.5,
            reasoning="Test reason",
            vetoed=False,
        )
        assert vr.agent_name == "TestAgent"
        assert vr.symbol == "AAPL"
        assert vr.score == 0.7
        assert vr.weight == 0.5
        assert vr.reasoning == "Test reason"
        assert vr.vetoed is False

    def test_all_agents_importable(self):
        """Alle 9 Agents müssen aus core.round_table.agents importierbar sein."""
        from core.round_table.agents import (  # noqa: F401
            DrawdownGuardAgent,
            LSTMSignalAgent,
            MomentumAgent,
            NewsSentimentAgent,
            PatternRecognitionAgent,
            RegimeDetectionAgent,
            RLConfidenceAgent,
            SpecialistAlphaAgent,
            VIXAwareRiskAgent,
        )


# ---------------------------------------------------------------------------
# 2. Agent-Gewichte prüfen
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestAgentWeights:
    def test_agent_weights_correct(self):
        """Alle Agents müssen die korrekten Gewichte haben."""
        from core.round_table.agents import (
            DrawdownGuardAgent,
            LSTMSignalAgent,
            MomentumAgent,
            NewsSentimentAgent,
            PatternRecognitionAgent,
            RegimeDetectionAgent,
            RLConfidenceAgent,
            SpecialistAlphaAgent,
            VIXAwareRiskAgent,
        )

        expected = {
            DrawdownGuardAgent: 0.60,
            SpecialistAlphaAgent: 0.0,
            RegimeDetectionAgent: 0.50,
            MomentumAgent: 0.45,
            VIXAwareRiskAgent: 0.45,
            LSTMSignalAgent: 0.40,
            RLConfidenceAgent: 0.40,
            NewsSentimentAgent: 0.35,
            PatternRecognitionAgent: 0.30,
        }
        for cls, expected_weight in expected.items():
            assert (
                cls.default_weight == expected_weight
            ), f"{cls.__name__}.default_weight = {cls.default_weight}, erwartet {expected_weight}"


# ---------------------------------------------------------------------------
# 3. Einzelne Agents: Happy Path (score in [0,1], reasoning non-empty)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestDrawdownGuardAgent:
    @pytest.mark.anyio
    async def test_vote_returns_valid_result(self):
        from core.round_table.agents import DrawdownGuardAgent

        agent = DrawdownGuardAgent()
        state = make_state(
            high=155.0, low=145.0, close=152.0
        )  # drawdown = 10/155 ~ 6.4%
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert result.reasoning
        assert result.agent_name == "DrawdownGuardAgent"
        assert result.weight == 0.60

    @pytest.mark.anyio
    async def test_high_drawdown_lowers_score(self):
        from core.round_table.agents import DrawdownGuardAgent

        agent = DrawdownGuardAgent()
        state = make_state(high=200.0, low=100.0, close=105.0)  # extreme drawdown
        result = await agent.vote(state)
        assert result.score < 0.5, "Hoher Drawdown soll niedrigen Score erzeugen"


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestSpecialistAlphaAgent:
    @pytest.mark.anyio
    async def test_vote_returns_stub_score(self):
        from core.round_table.agents import (
            SpecialistAlphaAgent,
            set_specialist_registry,
        )

        # Reset global registry state — earlier tests may leave a mock that changes score
        set_specialist_registry(None)
        agent = SpecialistAlphaAgent()
        state = make_state()
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert result.agent_name == "SpecialistAlphaAgent"
        # Stub: 0.5 wenn kein Registry aktiv (Epic 3.3 nicht aktiv)
        assert result.score == 0.5


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRegimeDetectionAgent:
    @pytest.mark.anyio
    async def test_bullish_regime_high_score(self):
        from core.round_table.agents import RegimeDetectionAgent

        agent = RegimeDetectionAgent()
        state = make_state(open_=100.0, close=115.0)  # +15% (bullish)
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert result.score > 0.5, "Bullisches Regime soll hohen Score geben"

    @pytest.mark.anyio
    async def test_bearish_regime_low_score(self):
        from core.round_table.agents import RegimeDetectionAgent

        agent = RegimeDetectionAgent()
        state = make_state(open_=100.0, close=85.0)  # -15% (bearish)
        result = await agent.vote(state)
        assert result.score < 0.5, "Bärisches Regime soll niedrigen Score geben"


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestMomentumAgent:
    @pytest.mark.anyio
    async def test_positive_momentum(self):
        from core.round_table.agents import MomentumAgent

        agent = MomentumAgent()
        state = make_state(open_=100.0, close=110.0)  # +10%
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert result.score > 0.5


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestVIXAwareRiskAgent:
    @pytest.mark.anyio
    async def test_high_volume_proxy_low_score(self):
        """
        Gherkin (Architect):
          Given: VIX-Proxy (Volume-Inverse) sehr hoch (extremes Volumen = hohe Volatilität)
          When:  VIXAwareRiskAgent.vote(state)
          Then:  score < 0.3 (Strong Avoid)
        """
        from core.round_table.agents import VIXAwareRiskAgent

        agent = VIXAwareRiskAgent()
        # Extrem hohes Volumen = VIX-Proxy-Stress → score soll < 0.3
        state = make_state(volume=50_000_000.0)  # 50x normales Volumen
        result = await agent.vote(state)
        assert (
            result.score < 0.3
        ), f"Hohes Volumen (VIX-Proxy) soll score < 0.3 erzeugen, got {result.score}"

    @pytest.mark.anyio
    async def test_normal_volume_neutral_score(self):
        from core.round_table.agents import VIXAwareRiskAgent

        agent = VIXAwareRiskAgent()
        state = make_state(volume=1_000_000.0)
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestLSTMSignalAgent:
    @pytest.mark.anyio
    async def test_vote_no_registry_triggers_watchdog_and_dropout(self):
        """TRD-7: Ohne aktive Registry wird der MLWatchdog getriggert und weight=0.0 (Dropout) zurückgegeben."""
        from core.round_table.agents import LSTMSignalAgent

        with (
            patch("core.round_table.agents.get_global_registry", return_value=None),
            patch("core.ml_watchdog.ml_watchdog.record_error") as mock_record,
        ):
            agent = LSTMSignalAgent()
            state = make_state()
            result = await agent.vote(state)

            assert (
                result.weight == 0.0
            ), "Bei Fehler muss weight auf 0 gesetzt werden (Dropout)"
            mock_record.assert_called_once()

    @pytest.mark.anyio
    async def test_vote_strategy_returns_none_abstains(self):
        """If active strategy returns None, agent must abstain with weight=0.0 and update reasoning."""
        from core.round_table.agents import LSTMSignalAgent

        mock_registry = MagicMock()
        mock_strategy = MagicMock()
        mock_strategy.run_for_symbol = AsyncMock(return_value=None)
        mock_registry.get_active.return_value = mock_strategy

        with patch(
            "core.round_table.agents.get_global_registry", return_value=mock_registry
        ):
            agent = LSTMSignalAgent()
            state = make_state()
            result = await agent.vote(state)

            assert result.score == 0.5
            assert result.weight == 0.0
            assert "strategy returned none" in result.reasoning.lower()


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRLConfidenceAgent:
    @pytest.mark.anyio
    async def test_vote_no_registry_triggers_watchdog_and_dropout(self):
        """TRD-7: Ohne aktive Registry wird der MLWatchdog getriggert und weight=0.0 (Dropout) zurückgegeben."""
        from core.round_table.agents import RLConfidenceAgent

        with (
            patch("core.round_table.agents.get_global_registry", return_value=None),
            patch("core.ml_watchdog.ml_watchdog.record_error") as mock_record,
        ):
            agent = RLConfidenceAgent()
            state = make_state()
            result = await agent.vote(state)

            assert (
                result.weight == 0.0
            ), "Bei Fehler muss weight auf 0 gesetzt werden (Dropout)"
            mock_record.assert_called_once()

    @pytest.mark.anyio
    async def test_vote_strategy_returns_none_abstains(self):
        """If active strategy returns None, agent must abstain with weight=0.0 and update reasoning."""
        from core.round_table.agents import RLConfidenceAgent

        mock_registry = MagicMock()
        mock_strategy = MagicMock()
        mock_strategy.run_for_symbol = AsyncMock(return_value=None)
        mock_registry.get_active.return_value = mock_strategy

        with patch(
            "core.round_table.agents.get_global_registry", return_value=mock_registry
        ):
            agent = RLConfidenceAgent()
            state = make_state()
            result = await agent.vote(state)

            assert result.score == 0.5
            assert result.weight == 0.0
            assert "strategy returned none" in result.reasoning.lower()

    @pytest.mark.anyio
    async def test_vote_reflects_prediction_regression_656(self):
        """Regression (reverses #656 / f5cc27da): the RL vote must read the ALWAYS-set
        `decision_context.lstm_prediction`, not the BUY-only `conviction_score`. Under #656
        every HOLD/SELL collapsed to a dead neutral 0.5 — the RL effectively stopped voting.
        A strong directional prediction must move the vote off 0.5 even when action==HOLD.
        """
        from core.cloud_logger import DecisionContext
        from core.events import SignalEvent
        from core.round_table.agents import RLConfidenceAgent

        def _signal(action, pred):
            return SignalEvent(
                symbol="AAPL",
                action=action,
                decision_context=DecisionContext(
                    symbol="AAPL", action=action, lstm_prediction=pred
                ),
            )

        mock_registry = MagicMock()
        mock_strategy = MagicMock()
        mock_registry.get_active.return_value = mock_strategy

        with patch(
            "core.round_table.agents.get_global_registry", return_value=mock_registry
        ):
            agent = RLConfidenceAgent()

            # Strong bullish lean while HOLDing → vote > 0.5 (was a dead 0.5 under #656)
            mock_strategy.run_for_symbol = AsyncMock(return_value=_signal("HOLD", 2.0))
            bull = await agent.vote(make_state())
            assert (
                bull.score > 0.5
            ), f"bullish HOLD must lift the vote, got {bull.score}"

            # Strong bearish lean while HOLDing → vote < 0.5
            mock_strategy.run_for_symbol = AsyncMock(return_value=_signal("HOLD", -2.0))
            bear = await agent.vote(make_state())
            assert (
                bear.score < 0.5
            ), f"bearish HOLD must lower the vote, got {bear.score}"

            # A BUY with strong conviction scores clearly bullish
            mock_strategy.run_for_symbol = AsyncMock(return_value=_signal("BUY", 2.0))
            buy = await agent.vote(make_state())
            assert (
                buy.score > 0.7
            ), f"BUY with strong pred must score high, got {buy.score}"


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestNewsSentimentAgent:
    @pytest.mark.anyio
    async def test_vote_gemini_unavailable_returns_fallback(self):
        """Wenn Gemini nicht erreichbar: Fallback 0.5 (kein Crash)."""
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        state = make_state()
        # Gemini-Client wird nicht aufgerufen (kein API-Key in Test) → Fallback
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert not result.vetoed


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestPatternRecognitionAgent:
    @pytest.mark.anyio
    async def test_bullish_candle_pattern(self):
        from core.round_table.agents import PatternRecognitionAgent

        agent = PatternRecognitionAgent()
        # Bullish Engulfing Proxy: close >> open, low near open
        state = make_state(open_=100.0, high=120.0, low=99.0, close=118.0)
        result = await agent.vote(state)
        assert 0.0 <= result.score <= 1.0
        assert result.agent_name == "PatternRecognitionAgent"

    @pytest.mark.anyio
    async def test_ohlc_not_flat_daily_bar_simulation(self):
        """
        TDD Test: Prüft dass echte O/H/L/C Kerzen (nicht O=H=L=C via latest_trade)
        korrekt verarbeitet werden und zu Varianzen führen.
        """
        from core.round_table.agents import PatternRecognitionAgent

        agent = PatternRecognitionAgent()

        # Test 1: Flat OHLC (wie vorher fehlerhaft via latest_trade) -> Neutral 0.5
        flat_state = make_state(open_=150.0, high=150.0, low=150.0, close=150.0)
        flat_result = await agent.vote(flat_state)

        # Test 2: Echte OHLC Varianz Bullish Marubozu (Körper > 60% der Range) -> 0.75
        real_state = make_state(open_=145.0, high=160.0, low=145.0, close=158.0)
        real_result = await agent.vote(real_state)

        assert 0.0 <= flat_result.score <= 1.0
        assert 0.0 <= real_result.score <= 1.0

        # Sicherstellen, dass die Scores nicht identisch sind bei unterschiedlichen Kerzen
        assert (
            flat_result.score != real_result.score
        ), "Echte OHLC Daten müssen zu unterschiedlichen Pattern-Scores führen"
        assert flat_result.score == 0.5
        assert real_result.score > 0.7


# ---------------------------------------------------------------------------
# Regression: Monitor Loop Strategy Switch Stability (Bug 2026-04-15)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SB3, reason="Requires ML dependencies like sb3_contrib")
@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestMonitorLoopStrategySwitchStability:
    """
    Regression test for the endless strategy-switch bug.

    Root cause: monitor_loop compared active_strategy.strategy_name ("RLAgent")
    against TargetStrategyClass.__name__ ("RLStrategy") — these never match,
    causing a new RLStrategy to be instantiated every 30-min monitor cycle,
    wiping the AgentRegistry state and making LSTMSignalAgent + RLConfidenceAgent
    permanently blind (score=0.5). Result: Round Table consensus stuck below 0.65
    BUY threshold → zero trades executed.

    Fix: compare against target_strategy_name (config value) not __name__.
    This test ensures the comparison is stable after the first cycle.
    """

    def test_strategy_name_matches_config_active_strategy(self):
        """
        RLStrategy.strategy_name must equal config.ACTIVE_STRATEGY ('RLAgent').

        If this fails: monitor_loop will trigger STRATEGY SWITCH every cycle,
        wiping the AgentRegistry and blocking all trades.
        """
        from unittest.mock import MagicMock, patch

        mock_client = MagicMock()
        mock_client.__class__.__name__ = "TradingClient"

        with (
            patch("core.strategies.rl_strategy.os.path.exists", return_value=False),
            patch(
                "core.strategies.rl_strategy.get_trade_intelligence",
                return_value=MagicMock(),
            ),
            patch(
                "core.strategies.rl_strategy.HistoricalDataProvider",
                return_value=MagicMock(),
            ),
        ):
            from core.risk_manager import RiskManager
            from core.strategies.rl_strategy import RLStrategy

            mock_rm = MagicMock(spec=RiskManager)

            strategy = RLStrategy(
                client=mock_client,
                symbols=["AAPL"],
                running_event=MagicMock(),
                total_capital=100_000.0,
                risk_manager=mock_rm,
                data_provider=MagicMock(),
            )

        # The name set in __init__ must match what monitor_loop reads from config
        import config

        active_strategy_config = getattr(config, "ACTIVE_STRATEGY", "RLAgent")

        assert strategy.strategy_name == active_strategy_config, (
            f"RLStrategy.strategy_name='{strategy.strategy_name}' does not match "
            f"config.ACTIVE_STRATEGY='{active_strategy_config}'. "
            f"This causes monitor_loop to trigger STRATEGY SWITCH every 30 minutes, "
            f"wiping AgentRegistry and blocking all trades. "
            f"Fix: ensure strategy_name matches the config key used in STRATEGY_CLASSES."
        )

    def test_strategy_classes_key_matches_strategy_name(self):
        """
        STRATEGY_CLASSES key must equal the strategy_name set in __init__.

        Monitor loop does:
          target_name = config.ACTIVE_STRATEGY           # e.g. "RLAgent"
          cls = STRATEGY_CLASSES[target_name]            # → RLStrategy class
          current = active_strategy.strategy_name        # e.g. "RLAgent"
          if current != target_name: SWITCH              # ← must be stable

        If STRATEGY_CLASSES["RLAgent"] maps to a class whose strategy_name
        attribute is NOT "RLAgent", the switch will fire every cycle.
        """
        from unittest.mock import MagicMock, patch

        import config
        from core.strategies import STRATEGY_CLASSES

        active_strategy_name = getattr(config, "ACTIVE_STRATEGY", "RLAgent")

        assert active_strategy_name in STRATEGY_CLASSES, (
            f"config.ACTIVE_STRATEGY='{active_strategy_name}' not found in "
            f"STRATEGY_CLASSES keys: {list(STRATEGY_CLASSES.keys())}. "
            f"Add the mapping to core/strategies/__init__.py."
        )

        target_cls = STRATEGY_CLASSES[active_strategy_name]

        # The class must set strategy_name = active_strategy_name in __init__
        # We check via a lightweight mock (no ML models loaded)
        mock_client = MagicMock()
        with (
            patch("core.strategies.rl_strategy.os.path.exists", return_value=False),
            patch(
                "core.strategies.rl_strategy.get_trade_intelligence",
                return_value=MagicMock(),
            ),
            patch(
                "core.strategies.rl_strategy.HistoricalDataProvider",
                return_value=MagicMock(),
            ),
        ):
            instance = target_cls(
                client=mock_client,
                symbols=["AAPL"],
                running_event=MagicMock(),
                total_capital=100_000.0,
                risk_manager=MagicMock(),
                data_provider=MagicMock(),
            )

        assert instance.strategy_name == active_strategy_name, (
            f"STRATEGY_CLASSES['{active_strategy_name}'] instantiates class "
            f"'{target_cls.__name__}' but its strategy_name='{instance.strategy_name}' "
            f"!= '{active_strategy_name}'. monitor_loop will SWITCH every cycle. "
            f"Fix: set self.strategy_name = '{active_strategy_name}' in {target_cls.__name__}.__init__."
        )


# ---------------------------------------------------------------------------
# TestRedisWeightInjectionDetection (I-1 #942 — Rogue Agent Hardening)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRedisWeightInjectionDetection:
    """
    TDD coverage for the Redis weight manipulation WARNING log added in I-1 #942.

    Gherkin:
        Given: Redis contains agent_weights_v2[AgentName] = 99999 (out-of-bounds)
        When:  agent.weight is accessed
        Then:  A SECURITY WARNING is emitted in the logs
        And:   The returned weight is clamped to max_weight (not 99999)
    """

    def test_out_of_bounds_redis_weight_triggers_security_warning(self, caplog):
        """
        Given: Redis returns 99999.0 for NewsSentimentAgent (max_weight=1.50)
        When:  agent.weight property is accessed
        Then:  SECURITY WARNING log is emitted with 'rogue' or 'manipulation' context
        And:   Returned weight == max_weight (1.50), NOT 99999.0
        """
        import logging
        from unittest.mock import MagicMock, patch

        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        mock_redis = MagicMock()
        mock_redis.hget.return_value = b"99999.0"  # Redis returns bytes

        with (
            patch("core.round_table.base_agent.RedisClient") as mock_client,
            caplog.at_level(logging.WARNING, logger="core.round_table.base_agent"),
        ):
            mock_client.get_sync_redis.return_value = mock_redis
            actual_weight = agent.weight

        # Weight must be clamped to max_weight
        assert actual_weight == agent.max_weight, (
            f"Out-of-bounds Redis weight 99999.0 should be clamped to max_weight={agent.max_weight}, "
            f"got {actual_weight}."
        )
        # SECURITY WARNING must be emitted
        assert any("SECURITY" in r.message for r in caplog.records), (
            "Expected a SECURITY WARNING when Redis weight is out of bounds. "
            "Rogue weight injection must be visible in production logs."
        )

    def test_out_of_bounds_low_redis_weight_triggers_security_warning(self, caplog):
        """
        Given: Redis returns -5.0 for LSTMSignalAgent (min_weight=0.15)
        When:  agent.weight property is accessed
        Then:  SECURITY WARNING log is emitted
        And:   Returned weight == min_weight (0.15), NOT -5.0
        """
        import logging
        from unittest.mock import MagicMock, patch

        from core.round_table.agents import LSTMSignalAgent

        agent = LSTMSignalAgent()
        mock_redis = MagicMock()
        mock_redis.hget.return_value = b"-5.0"

        with (
            patch("core.round_table.base_agent.RedisClient") as mock_client,
            caplog.at_level(logging.WARNING, logger="core.round_table.base_agent"),
        ):
            mock_client.get_sync_redis.return_value = mock_redis
            actual_weight = agent.weight

        assert actual_weight == agent.min_weight, (
            f"Sub-minimum Redis weight -5.0 should be clamped to min_weight={agent.min_weight}, "
            f"got {actual_weight}."
        )
        assert any(
            "SECURITY" in r.message for r in caplog.records
        ), "SECURITY WARNING must be emitted for sub-minimum weight injection."

    def test_in_bounds_redis_weight_no_warning(self, caplog):
        """
        Given: Redis returns 1.0 for LSTMSignalAgent (bounds: [0.15, 1.50])
        When:  agent.weight property is accessed
        Then:  No SECURITY WARNING emitted, returned weight == 1.0
        """
        import logging
        from unittest.mock import MagicMock, patch

        from core.round_table.agents import LSTMSignalAgent

        agent = LSTMSignalAgent()
        mock_redis = MagicMock()
        mock_redis.hget.return_value = b"1.0"

        with (
            patch("core.round_table.base_agent.RedisClient") as mock_client,
            caplog.at_level(logging.WARNING, logger="core.round_table.base_agent"),
        ):
            mock_client.get_sync_redis.return_value = mock_redis
            actual_weight = agent.weight

        assert (
            actual_weight == 1.0
        ), f"In-bounds weight 1.0 should pass through, got {actual_weight}"
        security_warnings = [r for r in caplog.records if "SECURITY" in r.message]
        assert (
            len(security_warnings) == 0
        ), f"No SECURITY WARNING expected for in-bounds weight, got: {security_warnings}"

    def test_redis_unavailable_falls_back_to_default_no_warning(self, caplog):
        """
        Given: Redis connection fails
        When:  agent.weight property is accessed
        Then:  Returned weight == default_weight, no SECURITY WARNING (only DEBUG)
        """
        import logging
        from unittest.mock import patch

        from core.round_table.agents import RegimeDetectionAgent

        agent = RegimeDetectionAgent()

        with (
            patch("core.round_table.base_agent.RedisClient") as mock_client,
            caplog.at_level(logging.DEBUG, logger="core.round_table.base_agent"),
        ):
            mock_client.get_sync_redis.side_effect = ConnectionError(
                "Redis unavailable"
            )
            actual_weight = agent.weight

        assert actual_weight == agent.default_weight, (
            f"Redis failure should fall back to default_weight={agent.default_weight}, "
            f"got {actual_weight}."
        )
        security_warnings = [r for r in caplog.records if "SECURITY" in r.message]
        assert len(security_warnings) == 0, (
            "Redis connection failure should NOT emit SECURITY WARNING (only DEBUG). "
            "Alert fatigue would mask real security events."
        )
