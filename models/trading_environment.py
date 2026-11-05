# trading_environment.py
# --- UPDATED for RL Agent v3/v4 (12-dimensional observation space) ---
# Supports v2 (11 features) and v3/v4 (12 features) based on model file detection

import json
import logging
import os
import pickle

import gymnasium as gym
import joblib
import numpy as np
import pandas as pd
import torch
from gymnasium import spaces

import config
from core.ml.asset_integrity import safe_joblib_load, safe_torch_load

# Import our PyTorch model definition
from models.torch_model import MODEL_FILE_NAME, LSTMModel, get_lstm_paths


def _data_dir():
    return getattr(
        config, "DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def detect_model_version():
    """Detect which RL model version to use based on available files."""
    d = _data_dir()
    # Check for newest model first (v5 > v4 > v3 > v2)
    if os.path.exists(os.path.join(d, "rl_agent_v5.zip")):
        return "rl_agent_v5"
    if os.path.exists(os.path.join(d, "rl_agent_v4.zip")):
        return "rl_agent_v4"
    elif os.path.exists(os.path.join(d, "rl_agent_v3_dsr.zip")):
        return "rl_agent_v3_dsr"
    elif os.path.exists(os.path.join(d, "rl_agent_v2.zip")):
        return "rl_agent_v2"
    else:
        return "rl_agent_v2"  # Default


class StockTradingEnv(gym.Env):
    """
    Stock trading environment for RL Agent v2/v3.

    Observation Space:
    - v2 (11 dimensions): returns, rsi_14, macd, bb_pct, volume_ratio,
                          volatility_20d, momentum_10d, adx_14,
                          position, time_in_position, unrealized_pnl
    - v3 (12 dimensions): Same as v2 + volatility_regime
    """

    def __init__(
        self,
        data_file="rl_training_data.csv",
        model_file=MODEL_FILE_NAME,
        model_version=None,
        force_observation_dim=None,
        is_inference=False,
    ):
        super(StockTradingEnv, self).__init__()

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.is_inference = is_inference

        # --- Feature columns for v2 model (8 market features + 3 position features = 11 total) ---
        self.feature_columns = [
            "returns",
            "rsi_14",
            "macd",
            "bb_pct",
            "volume_ratio",
            "volatility_20d",
            "momentum_10d",
            "adx_14",
        ]

        # === Detect model version for observation space size ===
        if model_version is not None:
            detected_version = model_version
        else:
            detected_version = detect_model_version()

        # v3, v4, v5 use the same 12-feature architecture (unless force_observation_dim overrides)
        self.is_v3_or_v4 = any(
            x in detected_version.lower() for x in ("v3", "v4", "v5")
        )
        # v2: 8 market + 3 position = 11
        # v3/v4: 8 market + 3 position + 1 volatility_regime = 12
        if force_observation_dim == 11:
            self.n_position_features = 3
            self.is_v3_or_v4 = False
        elif force_observation_dim == 12:
            self.n_position_features = 4
            self.is_v3_or_v4 = True
        else:
            self.n_position_features = 4 if self.is_v3_or_v4 else 3

        # Volatility regime thresholds (same as DSR training)
        self.vol_thresholds = {"low": 0.015, "normal": 0.025, "elevated": 0.04}

        # --- 1. Load Data ---
        if not self.is_inference:
            logging.info("Loading RL environment data...")

        # Try clean data first (for v2), fall back to old format
        clean_data_path = os.path.join("clean_training_data", "all_symbols_clean.pkl")
        if os.path.exists(clean_data_path):
            if not self.is_inference:
                logging.info("Loading from clean_training_data...")
            with open(clean_data_path, "rb") as f:
                self.all_data = pickle.load(f)  # nosec B301
            self.symbols = list(self.all_data.keys())
            self.use_clean_data = True
            self.df = None  # Not used for clean data format
            if not self.is_inference:
                logging.info(f"Loaded {len(self.symbols)} symbols from clean data")
        elif os.path.exists(data_file):
            if not self.is_inference:
                logging.info(f"Loading from {data_file}...")
            self.df = pd.read_csv(data_file, index_col="Date", parse_dates=True)
            self.symbols = self.df["symbol"].unique().tolist()
            self.use_clean_data = False
            self.all_data = None
            if not self.is_inference:
                logging.info(f"Loaded {len(self.symbols)} symbols from CSV")
        else:
            if not self.is_inference:
                logging.error(
                    f"No data found. Run prepare_clean_data.py or prepare_rl_data.py first."
                )
            else:
                logging.info(
                    "Live inference mode: Using minimal structure template (no historical data loaded)."
                )
            # Create minimal dummy data for initialization
            self.symbols = ["AAPL"]
            self.use_clean_data = True
            self.all_data = {
                "AAPL": pd.DataFrame(
                    {
                        "Close": [100.0] * 100,
                        "returns": [0.0] * 100,
                        "rsi_14": [50.0] * 100,
                        "macd": [0.0] * 100,
                        "bb_pct": [0.5] * 100,
                        "volume_ratio": [1.0] * 100,
                        "volatility_20d": [0.02] * 100,
                        "momentum_10d": [0.0] * 100,
                        "adx_14": [25.0] * 100,
                    }
                )
            }
            self.df = None

        # --- 2. Load the PyTorch "Quant" Model (optional; uses LSTM v1 or v2 from config) ---
        self.torch_model = None
        self.scaler_x = None
        self.scaler_y = None
        self.sequence_length = 60
        model_path, scaler_x_path, scaler_y_path, metadata_path = get_lstm_paths()

        try:
            logging.info("Loading PyTorch model for RL state...")
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
            self.features_list = metadata["features_list"]
            self.sequence_length = metadata["sequence_length"]
            model_params = metadata["model_params"]

            # Load state dict BEFORE instantiating the model so we can infer the
            # true architecture from actual weight tensor shapes.
            # This guards against stale metadata where hidden_dim was recorded
            # incorrectly — the checkpoint weights are always the ground truth.
            # SEC: weights_only=True prevents Pickle-RCE from a compromised .pth file.
            # Fallback to weights_only=False for legacy ensemble checkpoints that
            # contain non-tensor objects (e.g. cloudpickle'd optimizer state).
            try:
                state_dict = safe_torch_load(
                    model_path, map_location=self.device, weights_only=True
                )
            except Exception:
                logging.warning(
                    "safe_torch_load weights_only=True failed for %s — "
                    "retrying without (legacy checkpoint format)",
                    model_path,
                )
                state_dict = safe_torch_load(
                    model_path, map_location=self.device, weights_only=False
                )

            # Unwrap EnsembleLSTMModel format (models.0.*, models.1.*, …)
            if any(k.startswith("models.") for k in state_dict.keys()):
                logging.info(
                    "Detected ensemble model format. Converting to single model format..."
                )
                new_state_dict = {}
                for key, value in state_dict.items():
                    if key.startswith("models.0."):
                        new_key = key.replace("models.0.", "")
                        new_state_dict[new_key] = value
                if new_state_dict:
                    state_dict = new_state_dict
                    logging.info("Successfully extracted first model from ensemble.")

            # --- Infer true architecture from checkpoint (ADR: metadata-drift guard) ---
            # Bidirectional LSTM produces two key families per layer:
            #   lstm.weight_ih_l{i}         (forward)  shape: [hidden_dim*4, input_dim]
            #   lstm.weight_ih_l{i}_reverse (backward) shape: [hidden_dim*4, hidden_dim]
            # We count only forward keys (exclude "_reverse") for num_layers inference.
            ih_l0 = state_dict.get("lstm.weight_ih_l0")
            if ih_l0 is not None:
                inferred_hidden = ih_l0.shape[0] // 4
                inferred_input = ih_l0.shape[1]
                inferred_layers = sum(
                    1
                    for k in state_dict
                    if k.startswith("lstm.weight_ih_l") and "reverse" not in k
                )
                if (
                    inferred_hidden != model_params["hidden_dim"]
                    or inferred_input != model_params["input_dim"]
                    or inferred_layers != model_params["num_layers"]
                ):
                    logging.warning(
                        "Metadata mismatch — overriding with checkpoint params: "
                        "hidden=%d (was %d), input=%d (was %d), layers=%d (was %d)",
                        inferred_hidden,
                        model_params["hidden_dim"],
                        inferred_input,
                        model_params["input_dim"],
                        inferred_layers,
                        model_params["num_layers"],
                    )
                    model_params = {
                        "input_dim": inferred_input,
                        "hidden_dim": inferred_hidden,
                        "num_layers": inferred_layers,
                        "output_dim": model_params["output_dim"],
                    }

            self.torch_model = LSTMModel(
                input_dim=model_params["input_dim"],
                hidden_dim=model_params["hidden_dim"],
                num_layers=model_params["num_layers"],
                output_dim=model_params["output_dim"],
            ).to(self.device)

            self.torch_model.load_state_dict(state_dict)
            self.torch_model.eval()

            self.scaler_x = safe_joblib_load(scaler_x_path)
            self.scaler_y = safe_joblib_load(scaler_y_path)
            logging.info(
                "PyTorch model loaded successfully (hidden=%d, input=%d, layers=%d).",
                model_params["hidden_dim"],
                model_params["input_dim"],
                model_params["num_layers"],
            )

        except Exception as e:
            logging.warning(
                f"Could not load PyTorch model: {e}. Using market features only."
            )

        # --- 3. Define Action Space ---
        # 0 = Hold, 1 = Buy, 2 = Sell
        self.action_space = spaces.Discrete(3)

        # --- 4. Define Observation Space (11 for v2, 12 for v3) ---
        # v2: 8 market features + position + time_in_position + unrealized_pnl = 11
        # v3: Same + volatility_regime = 12
        n_features = len(self.feature_columns) + self.n_position_features
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(n_features,), dtype=np.float32
        )

        logging.info(
            f"Observation space: {n_features} features ({'v3/v4' if self.is_v3_or_v4 else 'v2'})"
        )

        # --- 5. Environment State Variables ---
        self.current_step = 0
        self.current_symbol_index = 0
        self.current_symbol_data = None
        self.current_symbol = None
        self.position = 0  # 0 = cash, 1 = holding
        self.entry_price = 0
        self.entry_step = 0
        self.balance = 100000
        self.start_balance = 100000
        self.trade_count = 0

        logging.info("StockTradingEnv initialized.")

    def _get_features(self):
        """
        Extract feature vector for RL model.
        - v2: 11 dimensions (8 market + 3 position)
        - v3: 12 dimensions (8 market + 3 position + 1 volatility_regime)
        """
        try:
            # Get current row based on data format
            if self.use_clean_data and self.current_symbol_data is not None:
                row = self.current_symbol_data.iloc[self.current_step]
            elif self.df is not None and self.current_symbol_data is not None:
                row = self.current_symbol_data.iloc[self.current_step]
            else:
                return np.zeros(
                    len(self.feature_columns) + self.n_position_features,
                    dtype=np.float32,
                )

            features = []

            # Extract 8 market features
            for col in self.feature_columns:
                # Handle different column name formats
                val = 0.0
                if col in row.index:
                    val = row[col]
                elif col == "returns" and "Close" in row.index:
                    # Calculate returns from close price if needed
                    if self.current_step > 0:
                        prev_close = self.current_symbol_data.iloc[
                            self.current_step - 1
                        ]["Close"]
                        val = (
                            (row["Close"] - prev_close) / prev_close
                            if prev_close > 0
                            else 0.0
                        )
                elif col == "rsi_14" and "rsi_14d" in row.index:
                    val = row["rsi_14d"]
                elif col == "adx_14" and "adx_14d" in row.index:
                    val = row["adx_14d"]
                elif col == "bb_pct" and "bb_percent" in row.index:
                    val = row["bb_percent"]

                val = 0.0 if pd.isna(val) else float(val)
                val = np.clip(val, -10, 10)  # Clip extreme values
                features.append(val)

            # Add position state (0 or 1)
            features.append(float(self.position))

            # Time in position (normalized to 0-1)
            if self.position == 1:
                days_held = self.current_step - self.entry_step
                features.append(min(days_held / 20.0, 1.0))
            else:
                features.append(0.0)

            # Unrealized PnL
            if self.position == 1 and self.entry_price > 0:
                # Get current price
                if "Close" in row.index:
                    current_price = row["Close"]
                elif "close" in row.index:
                    current_price = row["close"]
                else:
                    current_price = self.entry_price
                unrealized_pnl = (current_price - self.entry_price) / self.entry_price
                features.append(np.clip(unrealized_pnl, -0.5, 0.5))
            else:
                features.append(0.0)

            # === V3/V4 ONLY: Volatility Regime Feature ===
            if self.is_v3_or_v4:
                # Use volatility_20d as proxy for market regime
                vol = row.get("volatility_20d", 0.02)
                if pd.isna(vol):
                    vol = 0.02

                # Convert to regime score: 0=low, 0.33=normal, 0.67=elevated, 1.0=crisis
                if vol < self.vol_thresholds["low"]:
                    regime_score = 0.0
                elif vol < self.vol_thresholds["normal"]:
                    regime_score = 0.33
                elif vol < self.vol_thresholds["elevated"]:
                    regime_score = 0.67
                else:
                    regime_score = 1.0
                features.append(regime_score)

            return np.array(features, dtype=np.float32)

        except Exception as e:
            logging.debug(f"Feature extraction error: {e}")
            return np.zeros(
                len(self.feature_columns) + self.n_position_features, dtype=np.float32
            )

    def _get_state(self):
        """Alias for _get_features for backward compatibility."""
        return self._get_features()

    def reset(self, seed=None, options=None):
        """
        Reset environment for new episode.
        """
        super().reset(seed=seed)

        # Reset account
        self.balance = self.start_balance
        self.position = 0
        self.entry_price = 0
        self.entry_step = 0
        self.trade_count = 0

        # Pick next symbol
        self.current_symbol_index = (self.current_symbol_index + 1) % len(self.symbols)
        self.current_symbol = self.symbols[self.current_symbol_index]

        # Get data for symbol
        if self.use_clean_data:
            self.current_symbol_data = self.all_data.get(self.current_symbol)
            if self.current_symbol_data is None:
                # Pick another symbol
                self.current_symbol = self.symbols[0]
                self.current_symbol_data = self.all_data[self.current_symbol]
        else:
            self.current_symbol_data = self.df[
                self.df["symbol"] == self.current_symbol
            ].copy()

        # Start at random point (after warmup period)
        min_start = 60
        max_start = len(self.current_symbol_data) - 100
        if max_start <= min_start:
            max_start = len(self.current_symbol_data) - 10

        self.current_step = np.random.randint(min_start, max(max_start, min_start + 1))

        return self._get_features(), {}

    def step(self, action):
        """
        Execute action and return next state.
        """
        action = int(action)
        current_data = self.current_symbol_data.iloc[self.current_step]

        # Get current price (handle different column names)
        if "Close" in current_data.index:
            current_price = current_data["Close"]
        elif "close" in current_data.index:
            current_price = current_data["close"]
        else:
            current_price = 100.0  # Fallback

        # Calculate daily return
        if self.current_step > 0:
            prev_data = self.current_symbol_data.iloc[self.current_step - 1]
            if "Close" in prev_data.index:
                prev_price = prev_data["Close"]
            elif "close" in prev_data.index:
                prev_price = prev_data["close"]
            else:
                prev_price = current_price
            daily_return = (
                (current_price - prev_price) / prev_price if prev_price > 0 else 0.0
            )
        else:
            daily_return = 0.0

        reward = 0.0
        terminated = False
        truncated = False

        # === ACTION HANDLING ===

        # Action 1: BUY
        if action == 1:
            if self.position == 0:
                # Enter position
                self.position = 1
                self.entry_price = current_price
                self.entry_step = self.current_step
                self.trade_count += 1
                reward = -0.0001  # Small fee for trading
            else:
                # Already in position - get daily return
                reward = daily_return

        # Action 2: SELL
        elif action == 2:
            if self.position == 1:
                # Exit position
                self.position = 0

                # Calculate trade return
                trade_return = (current_price - self.entry_price) / self.entry_price
                days_held = self.current_step - self.entry_step

                # Base reward is trade return
                reward = trade_return

                # === CONTEXT-AWARE REWARD SHAPING ===
                if days_held < 3:
                    # Quick exit - evaluate if it was smart
                    next_day_return = daily_return  # Proxy for "what would happen"

                    if trade_return < -0.02 and next_day_return < 0:
                        # Stop-loss that saved money
                        reward += 0.01
                    elif trade_return > 0.03:
                        # Good scalp
                        reward += 0.005
                    elif -0.01 < trade_return < 0.01:
                        # Noise trading
                        reward -= 0.005

                # Patience bonus for winners
                if trade_return > 0.02 and days_held >= 5:
                    reward += 0.02

                self.entry_price = 0
                self.entry_step = 0
                self.trade_count += 1
            else:
                # Selling while in cash
                reward = -0.0001

        # Action 0: HOLD
        elif action == 0:
            if self.position == 1:
                # Holding position - get daily return
                reward = daily_return
            else:
                # Holding cash - tiny penalty
                reward = -0.00001

        # Move to next day
        self.current_step += 1

        # Check for end of episode
        if self.current_step >= len(self.current_symbol_data) - 1:
            terminated = True

        # Update balance
        if self.position == 1:
            unrealized_pnl = (current_price - self.entry_price) / self.entry_price
            self.balance = self.start_balance * (1 + unrealized_pnl)

        info = {"balance": self.balance, "trade_count": self.trade_count}
        next_state = self._get_features()

        return next_state, reward, terminated, truncated, info
