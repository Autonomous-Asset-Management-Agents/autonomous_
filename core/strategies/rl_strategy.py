# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/strategies/rl_strategy.py
# Epic 1.7 / PR-B — RLStrategy-Klasse
# Enthält: RLStrategy.__init__, _load_torch_model_assets, run_for_symbol (mit Lock)
# Koordiniert RLSignalMixin + RLExecutionMixin + BaseStrategy

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import torch
from sb3_contrib import RecurrentPPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

import config
from core.data_provider import HistoricalDataProvider
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from core.simulation_adapter import SimulationAdapter
from core.strategies.base import BaseStrategy
from core.strategies.rl_execution import RLExecutionMixin
from core.strategies.rl_signal import RLSignalMixin
from core.trade_intelligence import get_trade_intelligence
from models.torch_model import LSTMModel, get_lstm_paths
from models.trading_environment import StockTradingEnv

try:
    from google.cloud import aiplatform
except ImportError:
    aiplatform = None

RL_MODEL_VERSION = os.getenv("RL_MODEL_VERSION", "rl_agent_v3_dsr")
_data_dir = getattr(
    config, "DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SEQUENCE_LENGTH = 60


def _rl_agent_file(version: str) -> str:
    return os.path.join(_data_dir, f"{version}.zip")


def _rl_stats_file(version: str) -> str:
    suffix = version.split("_")[-1]
    return os.path.join(_data_dir, f"rl_stats_{suffix}.pkl")


class RLStrategy(RLSignalMixin, RLExecutionMixin, BaseStrategy):
    """RL+LSTM Trading-Strategie (RecurrentPPO + LSTMModel).

    Koordiniert:
    - RLSignalMixin: _get_torch_prediction, _stabilize_signal, _calculate_conviction_score etc.
    - RLExecutionMixin: _run_for_symbol_impl aufgebrochen in 8 Methoden
    - BaseStrategy: _submit_order_safe, log_thought
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.strategy_name = "RLAgent"
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.rl_model: Optional[RecurrentPPO] = None
        self.vec_normalize: Optional[VecNormalize] = None

        is_simulation = isinstance(self.client, SimulationAdapter)
        rl_version = (
            getattr(config, "SIMULATION_RL_VERSION", "rl_agent_v3_dsr")
            if is_simulation
            else RL_MODEL_VERSION
        )
        self._rl_model_version = rl_version
        rl_agent_file = _rl_agent_file(rl_version)
        rl_stats_file = _rl_stats_file(rl_version)

        if os.path.exists(rl_agent_file):
            try:
                logging.info(
                    "🔄 Loading RL agent from %s ('%s')...",
                    rl_agent_file,
                    "sim v3" if is_simulation else "live",
                )

                def make_dummy_env(force_dim=None):
                    return StockTradingEnv(
                        model_version=rl_version,
                        force_observation_dim=force_dim,
                        is_inference=True,
                    )

                try:
                    self.rl_model = RecurrentPPO.load(
                        rl_agent_file,
                        env=DummyVecEnv([lambda: make_dummy_env(None)]),
                        device=self.device,
                    )
                except Exception as load_err:
                    err_msg = str(load_err).lower()
                    if "observation" in err_msg and (
                        "match" in err_msg or "12" in err_msg or "11" in err_msg
                    ):
                        logging.warning(
                            "RL observation space mismatch – retrying with 11-dim env..."
                        )
                        self.rl_model = RecurrentPPO.load(
                            rl_agent_file,
                            env=DummyVecEnv([lambda: make_dummy_env(11)]),
                            device=self.device,
                        )
                    else:
                        raise
                logging.info("✅ RL agent loaded successfully!")
            except Exception as e:
                logging.error("❌ FAILED to load RL agent: %s: %s", type(e).__name__, e)
        else:
            logging.warning(
                "⚠️ RL agent file not found at %s – using LSTM-only mode.",
                rl_agent_file,
            )

        if os.path.exists(rl_stats_file):
            try:
                logging.info("🔄 Loading RL stats from %s...", rl_stats_file)
                self.vec_normalize = joblib.load(rl_stats_file)
                logging.info("✅ RL stats loaded successfully!")
            except Exception as e:
                logging.error("❌ FAILED to load RL stats: %s: %s", type(e).__name__, e)

        self.torch_model = None
        self.scaler_x = None
        self.scaler_y = None
        self.features_list = []
        self._load_torch_model_assets()
        if self.torch_model is None:
            logging.error(
                "❌ CRITICAL: torch_model=None nach _load_torch_model_assets — "
                "LSTMSignalAgent (w:0.40) im Round Table deaktiviert"
            )
        else:
            logging.info(
                "✅ torch_model startup OK: %d Features, device=%s",
                len(self.features_list),
                self.device,
            )

        self._lstm_states: Dict[str, Any] = {}
        self.high_water_marks: Dict[str, float] = {}
        self._entry_time: Dict[str, Any] = {}
        self._signal_history: Dict[str, list] = {}
        self._last_action: Dict[str, int] = {}
        self._action_hold_cycles: Dict[str, int] = {}
        self._current_vix: float = 20.0
        self._vix_regime: str = "normal"
        self._pending_orders: Dict[str, str] = {}
        self._last_order_time: Dict[str, float] = {}
        self._per_loop_symbol_locks: Dict[int, Dict[str, asyncio.Lock]] = {}
        self._last_gtc_buy_submit_time: float = 0.0

        # Portfolio Intelligence
        try:
            max_positions = 10
            try:
                from config import MAX_POSITIONS

                max_positions = MAX_POSITIONS
            except ImportError:
                pass
            self.portfolio_manager = PortfolioManager(
                client=self.client,
                total_capital=self.total_capital,
                max_positions=max_positions,
            )
            logging.info(
                "📊 Portfolio Manager integrated with RLStrategy (max %d positions)",
                max_positions,
            )
        except Exception as e:
            logging.warning("Portfolio Manager initialization failed: %s", e)
            self.portfolio_manager = None

        # Trade Intelligence
        try:
            self.trade_intelligence = get_trade_intelligence()
            logging.info("🧠 Trade Intelligence System integrated")
        except Exception as e:
            logging.warning("Trade Intelligence initialization failed: %s", e)
            self.trade_intelligence = None

    def _load_torch_model_assets(self) -> None:
        """Lädt LSTM-Modell, Scaler und Metadaten von Disk."""
        model_path, scaler_x_path, scaler_y_path, metadata_path = get_lstm_paths()
        required_files = [model_path, scaler_x_path, scaler_y_path, metadata_path]
        missing_files = [f for f in required_files if not os.path.exists(f)]
        if missing_files:
            logging.error(
                "❌ Cannot load PyTorch model – missing files: %s", missing_files
            )
            return
        try:
            logging.info("🔄 Loading PyTorch model from %s...", model_path)
            with open(metadata_path, "r") as f:
                self.torch_metadata = json.load(f)
            logging.info("📋 Metadata keys: %s", list(self.torch_metadata.keys()))
            all_features = self.torch_metadata.get("features_list")
            mp = self.torch_metadata.get("model_params")
            if not all_features or not mp:
                logging.error(
                    "❌ model_metadata fehlt 'features_list' oder 'model_params'. "
                    "Vorhandene Keys: %s",
                    list(self.torch_metadata.keys()),
                )
                return

            state_dict = torch.load(  # nosec
                model_path, map_location=self.device, weights_only=True
            )
            first_key = next(iter(state_dict.keys()), "")
            w_key = (
                "models.0.lstm.weight_ih_l0"
                if first_key.startswith("models.")
                else "lstm.weight_ih_l0"
            )

            if w_key in state_dict:
                checkpoint_input_dim = state_dict[w_key].shape[1]
                if checkpoint_input_dim != mp["input_dim"]:
                    logging.warning(
                        "🧠 Checkpoint input_dim=%d differs from metadata %d; using checkpoint.",
                        checkpoint_input_dim,
                        mp["input_dim"],
                    )
                input_dim = checkpoint_input_dim
                self.features_list = all_features[:input_dim]
            else:
                input_dim = mp["input_dim"]
                self.features_list = all_features

            logging.info(
                "🧠 Model params: input_dim=%d, hidden_dim=%d, num_layers=%d, output_dim=%d",
                input_dim,
                mp["hidden_dim"],
                mp["num_layers"],
                mp["output_dim"],
            )
            self.torch_model = LSTMModel(
                input_dim, mp["hidden_dim"], mp["num_layers"], mp["output_dim"]
            ).to(self.device)

            if first_key.startswith("models."):
                logging.info(
                    "🔄 Detected ensemble format. Converting to single model..."
                )
                single_model_state = {
                    k.replace("models.0.", ""): v
                    for k, v in state_dict.items()
                    if k.startswith("models.0.")
                }
                if not single_model_state:
                    raise ValueError(
                        "Could not extract single model from ensemble format"
                    )
                logging.info(
                    "✅ Extracted first model from ensemble (%d params)",
                    len(single_model_state),
                )
                self.torch_model.load_state_dict(single_model_state)
            else:
                self.torch_model.load_state_dict(state_dict)

            self.torch_model.eval()
            self.scaler_x = joblib.load(scaler_x_path)
            self.scaler_y = joblib.load(scaler_y_path)

            n_scaler = getattr(
                self.scaler_x,
                "n_features_in_",
                len(getattr(self.scaler_x, "mean_", [])),
            )
            if n_scaler > input_dim:
                from sklearn.preprocessing import StandardScaler

                sub = StandardScaler()
                sub.mean_ = self.scaler_x.mean_[:input_dim].copy()
                sub.scale_ = self.scaler_x.scale_[:input_dim].copy()
                sub.n_features_in_ = input_dim
                self.scaler_x = sub
                logging.info(
                    "✅ Scaler subset to %d features (saved had %d).",
                    input_dim,
                    n_scaler,
                )

            logging.info(
                "✅ PyTorch model loaded! Features: %d, device=%s",
                len(self.features_list),
                self.device,
            )
        except Exception as e:
            logging.error(
                "❌ FAILED to load PyTorch model: %s: %s", type(e).__name__, e
            )
            import traceback

            logging.error(traceback.format_exc())

    async def run_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: Any,
    ):
        """Entry-Point mit per-Symbol-Lock (verhindert parallele Evaluierungen desselben Symbols)."""
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        if loop_id not in self._per_loop_symbol_locks:
            self._per_loop_symbol_locks[loop_id] = {}
        if symbol not in self._per_loop_symbol_locks[loop_id]:
            self._per_loop_symbol_locks[loop_id][symbol] = asyncio.Lock()
        async with self._per_loop_symbol_locks[loop_id][symbol]:
            return await self._run_for_symbol_impl(
                symbol, ohlc_data, market_data, current_time
            )
