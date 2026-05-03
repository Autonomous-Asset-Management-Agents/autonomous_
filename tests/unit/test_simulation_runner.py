# tests/unit/test_simulation_runner.py
# Epic 1.7 / PR-C — TDD Red-Phase
# Tests für SimulationRunner (wird nach core/engine/simulation_runner.py extrahiert)
#
# Gherkin-Kriterien:
#   Given: Engine mit gemockter RealisticSimulationClient
#   When:  Simulation/Benchmark gestartet
#   Then:  Korrektes Universum, Flags, Redis-Schlüssel, Ausgaben

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call


# ---------------------------------------------------------------------------
# 1. start_simulation → sp500 Universum geladen
# ---------------------------------------------------------------------------


class TestStartSimulation:
    def test_start_simulation_sp500_loads_universe(self):
        """
        Given: universe_type='sp500'
        When:  start_simulation aufgerufen
        Then:  data_provider.get_sp500_symbols wird aufgerufen, status='success'
        """
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)
        runner.simulation_running = False
        runner.simulation = None
        runner._send_update_threadsafe = MagicMock()
        runner.api = MagicMock()

        dp = MagicMock()
        dp.get_sp500_symbols.return_value = ["AAPL", "MSFT", "GOOG"]
        runner.data_provider = dp

        bg = MagicMock()
        with patch("core.engine.simulation_runner.RealisticSimulationClient"):
            result = runner.start_simulation(
                bg, initial_capital=10000.0, universe_type="sp500"
            )

        assert result["status"] == "success"
        dp.get_sp500_symbols.assert_called_once()

    def test_start_simulation_returns_error_if_already_running(self):
        """
        Given: simulation_running=True
        When:  start_simulation erneut aufgerufen
        Then:  status='error'
        """
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)
        runner.simulation_running = True

        bg = MagicMock()
        result = runner.start_simulation(bg)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 2. run_simulation_in_thread → is_simulation=True gesetzt
# ---------------------------------------------------------------------------


class TestRunSimulationSetsFlag:
    def test_simulation_flag_set_while_running(self):
        """
        Given: is_simulation=False
        When:  run_simulation_in_thread aufgerufen
        Then:  is_simulation=True gesetzt, Thread gestartet
        """
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)
        runner.is_simulation = False
        runner._shutdown_event = MagicMock()
        runner._shutdown_event.is_set.return_value = False
        runner._shutdown_event.clear = MagicMock()

        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            runner.run_simulation_in_thread(
                "2025-01-01", "2025-12-31", 100000.0, "sp500"
            )

        assert runner.is_simulation is True
        mock_thread.return_value.start.assert_called_once()


# ---------------------------------------------------------------------------
# 3. _save_benchmark_equity → Redis-Key gesetzt
# ---------------------------------------------------------------------------


class TestSaveBenchmarkEquityToRedis:
    def test_benchmark_equity_written_to_redis(self):
        """
        Given: Simulations-Results mit daily_equity-Punkten
        When:  _save_benchmark_equity aufgerufen
        Then:  Redis-Key 'benchmark_equity_data' enthält korrekte JSON-Daten
        """
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)

        results = {
            "initial_cash": 100000.0,
            "final_equity": 115000.0,
            "daily_equity": [
                {"date": "2025-01-02", "equity": 100100.0},
                {"date": "2025-01-03", "equity": 100800.0},
            ],
        }

        redis_mock = MagicMock()
        stored = {}

        def mock_set(key, value):
            stored[key] = value

        redis_mock.set = mock_set

        with patch("core.engine.simulation_runner.RedisClient") as mock_redis_cls:
            mock_redis_cls.get_sync_redis.return_value = redis_mock
            runner._save_benchmark_equity(results, "2025-01-01", "2025-12-31")

        assert "benchmark_equity_data" in stored
        data = json.loads(stored["benchmark_equity_data"])
        assert data["final_equity"] == 115000.0
        assert len(data["points"]) == 2


# ---------------------------------------------------------------------------
# 4. _compute_spy_equity_curve → korrekte Buy-and-Hold-Berechnung
# ---------------------------------------------------------------------------


class TestComputeSpyEquityCurve:
    def test_spy_equity_curve_matches_buy_and_hold(self):
        """
        Given: SPY Schlusskurse: Tag1=$100, Tag2=$110
        When:  _compute_spy_equity_curve mit initial_capital=10000
        Then:  equity Tag2 = 10000 * (110/100) = 11000.0
        """
        import pandas as pd
        from datetime import date
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)

        d1 = date(2025, 1, 2)
        d2 = date(2025, 1, 3)

        spy_df = pd.DataFrame(
            {"close": [100.0, 110.0]},
            index=[d1, d2],
        )

        sim_client = MagicMock()
        sim_client.simulation_data = {"SPY": spy_df}
        sim_client.date_range = [d1, d2]

        points, first_close = runner._compute_spy_equity_curve(sim_client, 10000.0)

        assert first_close == 100.0
        assert len(points) == 2
        assert points[-1]["equity"] == pytest.approx(11000.0, rel=1e-3)


# ---------------------------------------------------------------------------
# 5. _save_benchmark_comparison_csv → CSV korrekt geschrieben
# ---------------------------------------------------------------------------


class TestBenchmarkComparisonCsv:
    def test_csv_contains_correct_columns(self, tmp_path):
        """
        Given: portfolio_points und spy_points vorhanden
        When:  _save_benchmark_comparison_csv aufgerufen
        Then:  CSV enthält Spalten Date, AI_Bot_Equity, SPY_Equity
        """
        import csv as csv_module
        from core.engine.simulation_runner import SimulationRunnerMixin

        runner = SimulationRunnerMixin.__new__(SimulationRunnerMixin)

        csv_path = str(tmp_path / "bench.csv")

        portfolio = [
            {"date": "2025-01-02", "equity": 100100.0},
            {"date": "2025-01-03", "equity": 100800.0},
        ]
        spy = [
            {"date": "2025-01-02", "equity": 100050.0},
            {"date": "2025-01-03", "equity": 100750.0},
        ]

        with patch("core.engine.simulation_runner.config") as mock_cfg:
            mock_cfg.BENCHMARK_COMPARISON_CSV = csv_path
            runner._save_benchmark_comparison_csv(portfolio, spy, 100000.0)

        with open(csv_path, newline="") as f:
            rows = list(csv_module.reader(f))

        assert rows[0] == ["Date", "AI_Bot_Equity", "SPY_Equity"]
        assert len(rows) == 3  # header + 2 data rows
