import hashlib
import hmac
import json
import os
import time
from unittest.mock import MagicMock, patch

import joblib
import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient
from sb3_contrib import RecurrentPPO
from stable_baselines3.common.vec_env import DummyVecEnv

# --- Setup environment for import-time config ---
os.environ["ENGINE_API_KEY"] = "testkey"
os.environ["LOG_FORMAT"] = "text"

import config

# Import app only — engine is None until lifespan runs.
# Access engine via the module reference so we see the live instance after lifespan.
import core.engine.api_routes as _engine_module
from core.engine import app
from core.strategies.rl_strategy import RL_MODEL_VERSION, _rl_stats_file
from models.torch_model import LSTMModel, get_lstm_paths
from models.trading_environment import StockTradingEnv


def create_dummy_models():
    """Create minimal dummy model files for testing the loading mechanism."""
    data_dir = getattr(config, "DATA_DIR", "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    # 1. LSTM Model
    model_path, scaler_x_path, scaler_y_path, metadata_path = get_lstm_paths()

    # #1875: only create MISSING files (per file) — never overwrite provisioned
    # bundle assets or the committed fixture metadata. Since PR #1702
    # safe_joblib_load fail-closes on a SHA mismatch vs models_manifest.json,
    # dummy overwrites would brick a provisioned tree (and destroy real files).
    if os.path.exists(metadata_path):
        # Keep dummy artefacts dimension-consistent with the existing metadata.
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        mp = metadata["model_params"]
        input_dim = mp["input_dim"]
        hidden_dim = mp["hidden_dim"]
        num_layers = mp["num_layers"]
        output_dim = mp["output_dim"]
    else:
        input_dim = 34
        hidden_dim = 64
        num_layers = 2
        output_dim = 1
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

    if not os.path.exists(model_path):
        model = LSTMModel(input_dim, hidden_dim, num_layers, output_dim)
        torch.save(model.state_dict(), model_path)

    from sklearn.preprocessing import StandardScaler

    if not os.path.exists(scaler_x_path):
        scaler_x = StandardScaler()
        scaler_x.mean_ = np.zeros(input_dim)
        scaler_x.scale_ = np.ones(input_dim)
        scaler_x.n_features_in_ = input_dim
        joblib.dump(scaler_x, scaler_x_path)

    if not os.path.exists(scaler_y_path):
        scaler_y = StandardScaler()
        scaler_y.mean_ = np.zeros(1)
        scaler_y.scale_ = np.ones(1)
        scaler_y.n_features_in_ = 1
        joblib.dump(scaler_y, scaler_y_path)
    print(f"Ensured dummy LSTM files in {data_dir}")

    # 2. RL Model
    rl_version = getattr(config, "RL_MODEL_VERSION", "rl_agent_v3_dsr")
    rl_agent_file = os.path.join(data_dir, f"{rl_version}.zip")
    # #1875: use the SAME resolution as the engine loader (manifest → bundle →
    # legacy) so the dummy stats land where RLStrategy actually looks.
    rl_stats_file = _rl_stats_file(rl_version)

    def make_env():
        return StockTradingEnv(model_version=rl_version)

    if not os.path.exists(rl_agent_file):
        # Create a dummy RecurrentPPO model
        env = DummyVecEnv([make_env])
        model = RecurrentPPO("MlpLstmPolicy", env, verbose=0)
        model.save(rl_agent_file)
        print(f"Created dummy RL agent in {data_dir}")

    if not os.path.exists(rl_stats_file):
        # Stats file (VecNormalize) — never overwrite provisioned bundle stats
        from stable_baselines3.common.vec_env import VecNormalize

        vn = VecNormalize(DummyVecEnv([make_env]))
        joblib.dump(vn, rl_stats_file)
        print(f"Created dummy RL stats in {data_dir}")


@pytest.fixture
def client():
    # Use context manager so that FastAPI lifespan is triggered, which
    # initialises the BotEngine and makes _engine_module.engine non-None.
    with TestClient(app) as c:
        yield c


# #1875: guard on the file the engine loader ACTUALLY resolves (manifest →
# bundle → legacy) — the old guard hardcoded rl_stats_dsr.pkl, a filename the
# models-v1.0 bundle never ships, so this test silently skipped everywhere.
@pytest.mark.skipif(
    not os.path.exists(_rl_stats_file(RL_MODEL_VERSION)),
    reason=(
        "Skipped in CI: RL model assets not provisioned "
        "(models-v1.0 bundle, e.g. rl_agent_v3_dsr_stats.pkl)"
    ),
)
def test_engine_boot_and_models(client):
    """Verify engine diagnostics show correct init state when strategy is started."""
    from core.kill_switch import kill_switch

    kill_switch._local_halted = False
    kill_switch._user_halted.clear()

    create_dummy_models()

    # After lifespan, _engine_module.engine is the live BotEngine instance.
    # #1875: engine init is a fire-and-forget async task (_init_engine_async)
    # since the Cloud-Run startup refactor — wait for readiness, don't race it.
    # (Unnoticed until now because this test always skipped, see issue #1875.)
    start_wait = time.time()
    while _engine_module.engine is None and time.time() - start_wait < 60:
        time.sleep(0.5)
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

    # Headers for auth. #1875: /diagnostics additionally enforces the signed
    # X-User-Id proxy headers when REQUIRE_SIG=true (set by tests/conftest.py)
    # — unnoticed here because this test always skipped. The signature TTL is
    # 60s (core/auth.py), so mint fresh headers per request via _signed_headers.
    shared_secret = os.environ.setdefault(
        "PROXY_ENGINE_SHARED_SECRET", "test-shared-secret-32-chars-long!!!"
    )

    def _signed_headers():
        user_id = "engine-boot-test"
        ts = str(int(time.time()))
        sig = hmac.new(
            shared_secret.encode("utf-8"),
            f"{user_id}:{ts}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Bot-Api-Key": "testkey",
            "X-User-Id": user_id,
            "X-User-Id-Sig": sig,
            "X-User-Id-Ts": ts,
        }

    headers = _signed_headers()

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
            # #1875: /diagnostics requires auth since the X-Engine-Key gate —
            # unnoticed here because this test always skipped.
            resp = client.get("/diagnostics", headers=_signed_headers())
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
    # #1875 Gherkin: When RLStrategy boots Then the VecNormalize stats are loaded
    ml_health = client.get("/engine-diagnostics", headers=_signed_headers()).json()[
        "models"
    ]
    assert (
        ml_health["vec_normalize_loaded"] is True
    ), "VecNormalize stats should be loaded (#1875)"
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
    client.post("/stop", headers=_signed_headers())
