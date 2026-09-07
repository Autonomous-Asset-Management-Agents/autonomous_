import allure
import pandas as pd
import pytest

from core.ab_testing import ABTestExperiment


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
def test_ab_test_experiment_compare():
    """
    Test the ABTestExperiment class ensuring it correctly evaluates two
    simulation result dictionaries and computes identical timeframes,
    drawdowns, and correctly identifies the winner.
    """
    # Mock Simulation Results A (LSTM Model - Conservative)
    sim_a_results = {
        "model_name": "LSTM_v1",
        "initial_cash": 100000.0,
        "final_equity": 105000.0,
        "total_return": 5.0,
        "daily_equity": [
            {"date": "2023-01-01", "equity": 100000.0},
            {"date": "2023-01-02", "equity": 103000.0},  # Max
            {
                "date": "2023-01-03",
                "equity": 101000.0,
            },  # Drawdown: (101-103)/103 = -1.94%
            {"date": "2023-01-04", "equity": 105000.0},
        ],
        "trades": [{"side": "buy"}, {"side": "sell"}],
    }

    # Mock Simulation Results B (RL Model - Aggressive)
    sim_b_results = {
        "model_name": "PPO_RL_v2",
        "initial_cash": 100000.0,
        "final_equity": 110000.0,
        "total_return": 10.0,
        "daily_equity": [
            {"date": "2023-01-01", "equity": 100000.0},
            {"date": "2023-01-02", "equity": 108000.0},  # Max
            {
                "date": "2023-01-03",
                "equity": 102000.0,
            },  # Drawdown: (102-108)/108 = -5.55%
            {"date": "2023-01-04", "equity": 110000.0},
        ],
        "trades": [
            {"side": "buy"},
            {"side": "sell"},
            {"side": "buy"},
            {"side": "sell"},
        ],
    }

    # Act
    experiment = ABTestExperiment(
        model_a_results=sim_a_results, model_b_results=sim_b_results
    )
    report = experiment.generate_report()

    # Assert basic stats
    assert report["model_a"]["name"] == "LSTM_v1"
    assert report["model_b"]["name"] == "PPO_RL_v2"

    # Assert winner logic
    assert report["winner"] == "PPO_RL_v2"
    assert report["metrics_diff"]["total_return_diff"] == pytest.approx(
        5.0
    )  # B is 5% better

    # Assert Drawdowns are accurately calculated internally
    assert report["model_a"]["max_drawdown_pct"] == pytest.approx(-1.94, abs=0.1)
    assert report["model_b"]["max_drawdown_pct"] == pytest.approx(-5.55, abs=0.1)

    # Assert printing capabilities don't throw errors
    experiment.print_summary()
