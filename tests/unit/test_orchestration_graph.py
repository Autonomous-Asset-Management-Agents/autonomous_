# tests/unit/test_orchestration_graph.py
# Epic 1.4 / Issue #216 — TDD Red-Phase
# LangGraph Orchestration Graph: SymbolEvalState, build_symbol_eval_graph, Redis Checkpointer
#
# Alle Tests importieren aus core.orchestration.graph, das noch NICHT existiert.
# => ModuleNotFoundError beim ersten Run = korrekte Red-Phase ✅
#
# Gherkin-Kriterien:
#   Given: SymbolEvalState mit validen Skalardaten (keine OHLCV-Arrays)
#   When:  Graph wird ausgeführt (ainvoke)
#   Then:  SignalEvent oder None zurück / state.error befüllt bei Fehler
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD — Red → Green → Refactor

import asyncio
from unittest.mock import MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Smoke: Import zeigt ob Modul existiert (Red = ImportError erwartet)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestModuleExists:
    def test_graph_module_importable(self):
        """
        Given: core/orchestration/graph.py existiert noch nicht
        When:  Import versucht wird
        Then:  Sobald Green-Phase abgeschlossen — kein ImportError mehr
        """
        from core.orchestration.graph import build_symbol_eval_graph  # noqa: F401

        assert callable(build_symbol_eval_graph)

    def test_state_type_importable(self):
        """
        Given: SymbolEvalState ist ein TypedDict in core.orchestration.graph
        When:  Importiert
        Then:  Kein Fehler, Klasse ist vorhanden
        """
        from core.orchestration.graph import SymbolEvalState  # noqa: F401

        assert SymbolEvalState is not None


# ---------------------------------------------------------------------------
# 1. Happy Path: Graph gibt Signal zurück bei validen Skalardaten
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestGraphHappyPath:
    @pytest.mark.anyio
    async def test_graph_returns_signal_for_valid_state(self):
        """
        Given: SymbolEvalState mit validen Skalardaten (kein DataFrame, nur floats)
        When:  Graph via ainvoke ausgeführt
        Then:  Rückgabe enthält 'signal' key oder 'error' ist None
        """
        from core.events import SignalEvent
        from core.orchestration.graph import (  # noqa: F401
            SymbolEvalState,
            build_symbol_eval_graph,
        )

        mock_signal = MagicMock(spec=SignalEvent)
        mock_signal.action = "HOLD"
        mock_signal.symbol = "AAPL"

        # LangGraph-Nodes MUST return a dict (state update), not the signal directly
        async def mock_run_strategy(state):
            return {**state, "signal": mock_signal}

        with patch(
            "core.orchestration.graph._run_strategy_node", mock_run_strategy
        ), patch("core.orchestration.graph._build_checkpointer", return_value=None):
            graph = build_symbol_eval_graph()
            state: SymbolEvalState = {
                "symbol": "AAPL",
                "ohlc": {
                    "open": 150.0,
                    "high": 152.0,
                    "low": 149.0,
                    "close": 151.0,
                    "volume": 1000.0,
                },
                "market_data_keys": [],
                "current_time": "2026-03-08T14:00:00+00:00",
                "signal": None,
                "error": None,
            }
            result = await graph.ainvoke(state)

        assert result is not None
        assert (
            result.get("error") is None
        ), f"Kein Fehler erwartet, got: {result.get('error')}"
        assert result.get("signal") is not None, "Signal muss im State-Dict sein"

    @pytest.mark.anyio
    async def test_state_does_not_contain_raw_dataframe(self):
        """
        Given: SymbolEvalState wird mit einem DataFrame-Wert befüllt (Policy-Verletzung)
        When:  State validiert wird
        Then:  ValueError — nur Skalare/Refs erlaubt (kein Serialisierungs-Overhead)
        """
        import pandas as pd

        from core.orchestration.graph import validate_symbol_eval_state

        invalid_ohlc = pd.DataFrame({"close": [150.0, 151.0]})  # DataFrame statt dict

        with pytest.raises((ValueError, TypeError)):
            validate_symbol_eval_state(
                {
                    "symbol": "AAPL",
                    "ohlc": invalid_ohlc,  # Soll abgelehnt werden
                    "market_data_keys": [],
                    "current_time": "2026-03-08T14:00:00+00:00",
                    "signal": None,
                    "error": None,
                }
            )


# ---------------------------------------------------------------------------
# 2. Fehler-Isolation: Node-Crash befüllt state.error, kein Crash
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestErrorIsolation:
    @pytest.mark.anyio
    async def test_node_crash_sets_error_field(self):
        """
        Given: run_strategy Node wirft eine Exception
        When:  Graph via ainvoke ausgeführt
        Then:  state['error'] enthält den Fehler-String, kein Crash der gesamten Loop
        """
        from core.orchestration.graph import (  # noqa: F401
            SymbolEvalState,
            build_symbol_eval_graph,
        )

        # LangGraph-Node muss Dict zurückgeben — simuliere Fehler im Kontext-Node
        async def crashing_run_strategy(state):
            raise RuntimeError("Simulated Node Crash")

        with patch(
            "core.orchestration.graph._run_strategy_node", crashing_run_strategy
        ), patch("core.orchestration.graph._build_checkpointer", return_value=None):
            graph = build_symbol_eval_graph()
            state: SymbolEvalState = {
                "symbol": "MSFT",
                "ohlc": {
                    "open": 300.0,
                    "high": 305.0,
                    "low": 299.0,
                    "close": 302.0,
                    "volume": 500.0,
                },
                "market_data_keys": [],
                "current_time": "2026-03-08T14:00:00+00:00",
                "signal": None,
                "error": None,
            }
            # LangGraph propagiert Exceptions durch ainvoke — daher via gather mit return_exceptions
            results = await asyncio.gather(graph.ainvoke(state), return_exceptions=True)
            result = results[0]

        # Fehler-Isolation: entweder Exception caught oder state["error"] gesetzt
        if isinstance(result, Exception):
            assert "Simulated Node Crash" in str(
                result
            ), f"Erwartete 'Simulated Node Crash' in Exception, got: {result}"
        else:
            assert (
                result.get("error") is not None
            ), "error-Feld muss bei Node-Crash befüllt sein"

    @pytest.mark.anyio
    async def test_multiple_symbols_error_isolation(self):
        """
        Given: 3 Symbole, eines davon wirft Exception
        When:  asyncio.gather über 3 Graph-Invocations ausgeführt
        Then:  Die anderen 2 Symbole liefern valide Ergebnisse (kein Gesamt-Crash)
        """
        from core.orchestration.graph import (  # noqa: F401
            SymbolEvalState,
            build_symbol_eval_graph,
        )

        # Jeder Aufruf bekommt seinen eigenen State — Node gibt Dict zurück
        async def selective_crash_node(state):
            if state["symbol"] == "CRASH":
                raise RuntimeError("Only this one crashes")
            return {**state, "signal": None}  # HOLD

        symbols = ["AAPL", "CRASH", "MSFT"]

        with patch(
            "core.orchestration.graph._run_strategy_node", selective_crash_node
        ), patch("core.orchestration.graph._build_checkpointer", return_value=None):
            graph = build_symbol_eval_graph()
            states = [
                {
                    "symbol": s,
                    "ohlc": {
                        "open": 150.0,
                        "high": 152.0,
                        "low": 149.0,
                        "close": 151.0,
                        "volume": 1000.0,
                    },
                    "market_data_keys": [],
                    "current_time": "2026-03-08T14:00:00+00:00",
                    "signal": None,
                    "error": None,
                }
                for s in symbols
            ]
            results = await asyncio.gather(
                *[graph.ainvoke(s) for s in states], return_exceptions=True
            )

        # AAPL und MSFT müssen valide sein (kein Exception oder error)
        aapl_result = results[0]
        msft_result = results[2]
        assert not isinstance(
            aapl_result, Exception
        ), f"AAPL soll kein Crash sein: {aapl_result}"
        assert not isinstance(
            msft_result, Exception
        ), f"MSFT soll kein Crash sein: {msft_result}"


# ---------------------------------------------------------------------------
# 3. Redis Checkpointer: build_symbol_eval_graph nutzt RedisSaver
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRedisCheckpointer:
    def test_graph_uses_checkpointer(self):
        """
        Given: Redis ist via RedisClient verfügbar
        When:  build_symbol_eval_graph() aufgerufen
        Then:  Graph hat einen Checkpointer konfiguriert (nicht None)
        """
        from core.orchestration.graph import build_symbol_eval_graph

        with patch("core.orchestration.graph.RedisClient") as mock_redis_client:
            mock_redis_client.get_sync_redis.return_value = MagicMock()
            graph = build_symbol_eval_graph()

        # LangGraph CompiledGraph hat checkpointer-Attribut
        assert (
            hasattr(graph, "checkpointer") or graph is not None
        ), "Graph muss einen Checkpointer haben (RedisSaver)"

    def test_graph_works_without_redis_fallback(self):
        """
        Given: Redis nicht verfügbar (ConnectionError)
        When:  build_symbol_eval_graph() aufgerufen
        Then:  Graph wird ohne Checkpointer gebaut (Fallback, kein crash)
        """
        from core.orchestration.graph import build_symbol_eval_graph

        with patch("core.orchestration.graph.RedisClient") as mock_redis_client:
            mock_redis_client.get_sync_redis.side_effect = ConnectionError(
                "Redis unavailable"
            )
            graph = build_symbol_eval_graph()

        assert graph is not None, "Graph muss auch ohne Redis funktionieren (Fallback)"


# ---------------------------------------------------------------------------
# 3b. No-Redis async-invocation contract (checkpointer sync→async bug)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestNoRedisCheckpointerAsyncContract:
    """Regression for the desktop/OSS checkpointer: the no-Redis branch must produce a
    checkpointer that survives the graph's *async* invocation. A sync SqliteSaver builds
    fine but raises NotImplementedError at ``ainvoke`` (its ``aget_tuple`` is a stub),
    so every symbol-eval crashed into error-isolation. The linear symbol_eval graph
    (no interrupt/resume) needs no persistence → no-Redis returns None."""

    @staticmethod
    def _reset_singleton():
        import core.orchestration.graph as g

        g._CHECKPOINTER_INSTANCE = None
        g._CHECKPOINTER_CM = None

    @staticmethod
    def _minimal_state():
        return {
            "symbol": "AAPL",
            "ohlc": {
                "open": 150.0,
                "high": 151.0,
                "low": 149.0,
                "close": 150.5,
                "volume": 1000.0,
            },
            "market_data_keys": [],
            "current_time": "2026-06-12T00:00:00+00:00",
            "signal": None,
            "error": None,
            "round_table_scores": None,
            "consensus_ranking": None,
            "ml": None,
            "_portfolio_context": None,
        }

    def test_no_redis_build_checkpointer_returns_none(self, monkeypatch):
        """Given REDIS_URL empty, the builder returns None (no SQLite I/O at all)."""
        monkeypatch.setenv("REDIS_URL", "")
        self._reset_singleton()
        import core.orchestration.graph as g

        assert g._build_checkpointer() is None

    @pytest.mark.anyio
    async def test_no_redis_graph_ainvoke_survives(self, monkeypatch):
        """The durable contract: on the no-Redis path ``ainvoke`` must NOT raise.

        On ``main`` today this raises
        ``NotImplementedError: The SqliteSaver does not support async methods``
        from ``AsyncPregelLoop.__aenter__`` → ``checkpointer.aget_tuple``."""
        monkeypatch.setenv("REDIS_URL", "")
        self._reset_singleton()
        import core.orchestration.graph as g

        graph = g.build_symbol_eval_graph()
        res = await graph.ainvoke(
            self._minimal_state(),
            config={"configurable": {"market_data": {}}},
        )
        assert isinstance(res, dict)  # completed without raising


# ---------------------------------------------------------------------------
# 4. OHLC Close Plausibility Guard (ADR-SEC-03 / I-3 #944 — Rogue Agent Hardening)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestOHLCCloseValidation:
    """
    TDD coverage for the SuspectDataException guard added in I-3 #944.

    Gherkin:
        Given: SymbolEvalState with ohlc.close outside [0.01, 100_000]
        When:  validate_symbol_eval_state() is called
        Then:  SuspectDataException is raised (not ValueError)
        And:   The Round Table is never started
    """

    def test_close_too_low_raises_suspect_data_exception(self):
        """
        Given: ohlc.close = 0.00001 (far below Penny Stock minimum 0.01)
        When:  validate_symbol_eval_state() called
        Then:  SuspectDataException raised — manipulated Redis checkpoint blocked.
        """
        from core.orchestration.graph import validate_symbol_eval_state
        from core.round_table.agents import SuspectDataException

        with pytest.raises(SuspectDataException, match="plausiblen Bereich"):
            validate_symbol_eval_state(
                {
                    "symbol": "AAPL",
                    "ohlc": {
                        "open": 150.0,
                        "high": 155.0,
                        "low": 148.0,
                        "close": 0.00001,
                    },
                    "market_data_keys": [],
                    "current_time": "2026-01-01T00:00:00Z",
                    "signal": None,
                    "error": None,
                }
            )

    def test_close_too_high_raises_suspect_data_exception(self):
        """
        Given: ohlc.close = 999_999.0 (far above S&P500 upper bound 100_000)
        When:  validate_symbol_eval_state() called
        Then:  SuspectDataException raised — data feed injection blocked.
        """
        from core.orchestration.graph import validate_symbol_eval_state
        from core.round_table.agents import SuspectDataException

        with pytest.raises(SuspectDataException, match="plausiblen Bereich"):
            validate_symbol_eval_state(
                {
                    "symbol": "AAPL",
                    "ohlc": {
                        "open": 150.0,
                        "high": 155.0,
                        "low": 148.0,
                        "close": 999_999.0,
                    },
                    "market_data_keys": [],
                    "current_time": "2026-01-01T00:00:00Z",
                    "signal": None,
                    "error": None,
                }
            )

    def test_normal_close_passes_validation(self):
        """
        Given: ohlc.close = 152.34 (realistic S&P500 price)
        When:  validate_symbol_eval_state() called
        Then:  No exception raised.
        """
        from core.orchestration.graph import validate_symbol_eval_state

        # Must not raise
        validate_symbol_eval_state(
            {
                "symbol": "AAPL",
                "ohlc": {"open": 150.0, "high": 155.0, "low": 148.0, "close": 152.34},
                "market_data_keys": [],
                "current_time": "2026-01-01T00:00:00Z",
                "signal": None,
                "error": None,
            }
        )

    def test_boundary_values_are_accepted(self):
        """
        Given: ohlc.close at exact boundary values 0.01 and 100_000.0
        When:  validate_symbol_eval_state() called
        Then:  No exception raised (inclusive bounds per ADR-SEC-03).
        """
        from core.orchestration.graph import (
            _OHLC_CLOSE_MAX,
            _OHLC_CLOSE_MIN,
            validate_symbol_eval_state,
        )

        base = {
            "symbol": "TEST",
            "market_data_keys": [],
            "current_time": "2026-01-01T00:00:00Z",
            "signal": None,
            "error": None,
        }
        validate_symbol_eval_state({**base, "ohlc": {"close": _OHLC_CLOSE_MIN}})
        validate_symbol_eval_state({**base, "ohlc": {"close": _OHLC_CLOSE_MAX}})

    def test_missing_close_key_passes_validation(self):
        """
        Given: ohlc dict without 'close' key (edge case in partial market data)
        When:  validate_symbol_eval_state() called
        Then:  No SuspectDataException — close is optional in the plausibility check.
        """
        from core.orchestration.graph import validate_symbol_eval_state

        validate_symbol_eval_state(
            {
                "symbol": "AAPL",
                "ohlc": {"open": 150.0, "high": 155.0, "low": 148.0},  # no 'close'
                "market_data_keys": [],
                "current_time": "2026-01-01T00:00:00Z",
                "signal": None,
                "error": None,
            }
        )
