import os
import time
import json
import torch
import joblib
import pytest
import numpy as np
from sb3_contrib import RecurrentPPO
from stable_baselines3.common.vec_env import DummyVecEnv
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

# --- Setup environment for import-time config ---
os.environ["ENGINE_API_KEY"] = "testkey"
os.environ["LOG_FORMAT"] = "text"

# Import app only — engine is None until lifespan runs.
# Access engine via the module reference so we see the live instance after lifespan.
import core.engine.api_routes as _engine_module
from core.engine import app
import config
from models.torch_model import get_lstm_paths, LSTMModel
from models.trading_environment import StockTradingEnv


def create_dummy_models():
    """Create minimal dummy model files for testing the loading mechanism."""
    data_dir = getattr(config, "DATA_DIR", "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    # 1. LSTM Model
    model_path, scaler_x_path, scaler_y_path, metadata_path = get_lstm_paths()

    # We always recreate for testing to ensure current architecture matches
    input_dim = 34
    hidden_dim = 64
    num_layers = 2
    output_dim = 1

    model = LSTMModel(input_dim, hidden_dim, num_layers, output_dim)
    torch.save(model.state_dict(), model_path)

    # Metadata
    metadata = {
        "features_list": [f"feat_{i}" for i in range(input_dim)],
        "sequence_length": 60,
        "model_params": {
            "input_dim": input_dim,
            "hidden_dim": hidden_dim,
            "num_layers": num_layers,
            "output_dim": output_dim,
        },
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    # Scalers
    from sklearn.preprocessing import StandardScaler

    scaler_x = StandardScaler()
    scaler_x.mean_ = np.zeros(input_dim)
    scaler_x.scale_ = np.ones(input_dim)
    scaler_x.n_features_in_ = input_dim
    joblib.dump(scaler_x, scaler_x_path)

    scaler_y = StandardScaler()
    scaler_y.mean_ = np.zeros(1)
    scaler_y.scale_ = np.ones(1)
    scaler_y.n_features_in_ = 1
    joblib.dump(scaler_y, scaler_y_path)
    print(f"Created dummy LSTM files in {data_dir}")

    # 2. RL Model
    rl_version = getattr(config, "RL_MODEL_VERSION", "rl_agent_v3_dsr")
    rl_agent_file = os.path.join(data_dir, f"{rl_version}.zip")
    rl_stats_file = os.path.join(data_dir, f"rl_stats_{rl_version.split('_')[-1]}.pkl")

    if not os.path.exists(rl_agent_file):
        # Create a dummy RecurrentPPO model
        def make_env():
            return StockTradingEnv(model_version=rl_version)

        env = DummyVecEnv([make_env])
        model = RecurrentPPO("MlpLstmPolicy", env, verbose=0)
        model.save(rl_agent_file)

        # Stats file (VecNormalize)
        from stable_baselines3.common.vec_env import VecNormalize

        vn = VecNormalize(env)
        joblib.dump(vn, rl_stats_file)
        print(f"Created dummy RL files in {data_dir}")


@pytest.fixture
def client():
    # Use context manager so that FastAPI lifespan is triggered, which
    # initialises the BotEngine and makes _engine_module.engine non-None.
    with TestClient(app) as c:
        yield c


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.getenv("DATA_DIR", "data"), "rl_stats_dsr.pkl")),
    reason="Skipped in CI: RL training data not available (run prepare_rl_data.py first)",
)
def test_engine_boot_and_models(client):
    """Verify engine diagnostics show correct init state when strategy is started."""
    from core.kill_switch import kill_switch

    kill_switch._local_halted = False
    kill_switch._user_halted.clear()

    create_dummy_models()

    # After lifespan, _engine_module.engine is the live BotEngine instance.
    engine = _engine_module.engine
    assert engine is not None, "BotEngine should be initialized by lifespan"

    # Mock classes instead of MagicMock to allow joblib/pickle serialization
    class MockClock:
        is_open = True

    class MockAccount:
        equity = 100000.0

    class MockAPI:
        def get_account(self):
            return MockAccount()

        def get_clock(self):
            return MockClock()

    class MockDataAPI:
        pass

    mock_api = MockAPI()
    mock_data_api = MockDataAPI()

    # Replace global engine components
    engine.api = mock_api
    engine.data_api = mock_data_api
    engine.data_provider.api = mock_data_api
    engine.data_provider.trading_api = mock_api

    # Headers for auth
    headers = {"X-Bot-Api-Key": "testkey"}

    # Ensure strategy is RL (matching our dummy RL model)
    client.post("/set-strategy", json={"strategy": "RLAgent"}, headers=headers)

    # Patch scan_market to avoid crashes and return fixed stocks
    async def dummy_scan_market(*args, **kwargs):
        return {
            "top_stocks": [
                {"symbol": "AAPL", "score": 0.9},
                {"symbol": "MSFT", "score": 0.8},
            ],
            "recommendation_confidence": "high",
        }

    with patch("core.engine.base.AIMarketScanner.scan_market", new=dummy_scan_market):

        # Start live strategy
        response = client.post("/start-live", headers=headers)
        assert response.status_code == 200, f"Start failed: {response.text}"

        # Wait for monitor loop to initialize strategy (runs in separate thread)
        start_wait = time.time()
        data = {}
        while time.time() - start_wait < 30:
            resp = client.get("/diagnostics")
            data = resp.json()
            if data.get("active_strategy") == "RLStrategy" and data.get(
                "rl_model_loaded"
            ):
                break
            time.sleep(1)

    print(f"Diagnostics after wait: {data}")

    # Assertions based on Acceptance Criteria
    assert data["rl_model_loaded"] is True, "RL model should be loaded"
    assert data["lstm_model_loaded"] is True, "LSTM model should be loaded"
    assert (
        data["has_portfolio_manager"] is True
    ), "Portfolio Manager should be initialized"
    assert (
        data["has_trade_intelligence"] is True
    ), "Trade Intelligence should be initialized"

    # Verify /health as well
    health_url = "/health"
    health_resp = client.get(health_url)
    assert health_resp.status_code == 200
    data = health_resp.json()
    assert data["status"] in ("healthy", "starting")
    assert data["version"] == "2.5.0"
    assert data["strategy_running"] is True

    # Stop for cleanup
    client.post("/stop", headers=headers)
