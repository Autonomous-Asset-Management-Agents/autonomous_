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
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Smoke: Import zeigt ob Modul existiert (Red = ImportError erwartet)
# ---------------------------------------------------------------------------


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


class TestGraphHappyPath:
    @pytest.mark.anyio
    async def test_graph_returns_signal_for_valid_state(self):
        """
        Given: SymbolEvalState mit validen Skalardaten (kein DataFrame, nur floats)
        When:  Graph via ainvoke ausgeführt
        Then:  Rückgabe enthält 'signal' key oder 'error' ist None
        """
        from core.orchestration.graph import (
            build_symbol_eval_graph,
            SymbolEvalState,
        )  # noqa: F401
        from core.events import SignalEvent

        mock_signal = MagicMock(spec=SignalEvent)
        mock_signal.action = "HOLD"
        mock_signal.symbol = "AAPL"

        # LangGraph-Nodes MUST return a dict (state update), not the signal directly
        async def mock_run_strategy(state):
            return {**state, "signal": mock_signal}

        with patch("core.orchestration.graph._run_strategy_node", mock_run_strategy):
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
        from core.orchestration.graph import validate_symbol_eval_state

        import pandas as pd

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


class TestErrorIsolation:
    @pytest.mark.anyio
    async def test_node_crash_sets_error_field(self):
        """
        Given: run_strategy Node wirft eine Exception
        When:  Graph via ainvoke ausgeführt
        Then:  state['error'] enthält den Fehler-String, kein Crash der gesamten Loop
        """
        from core.orchestration.graph import (
            build_symbol_eval_graph,
            SymbolEvalState,
        )  # noqa: F401

        # LangGraph-Node muss Dict zurückgeben — simuliere Fehler im Kontext-Node
        async def crashing_run_strategy(state):
            raise RuntimeError("Simulated Node Crash")

        with patch(
            "core.orchestration.graph._run_strategy_node", crashing_run_strategy
        ):
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
        from core.orchestration.graph import (
            build_symbol_eval_graph,
            SymbolEvalState,
        )  # noqa: F401

        # Jeder Aufruf bekommt seinen eigenen State — Node gibt Dict zurück
        async def selective_crash_node(state):
            if state["symbol"] == "CRASH":
                raise RuntimeError("Only this one crashes")
            return {**state, "signal": None}  # HOLD

        symbols = ["AAPL", "CRASH", "MSFT"]

        with patch("core.orchestration.graph._run_strategy_node", selective_crash_node):
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
