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

# core/strategies/lstm_strategy.py
# Epic 1.7 / PR-B — LSTMDynamicStrategy vollständig (aus strategies.py Z.1803-2413)
# Nutzt BaseStrategy._submit_order_safe (DRY – kein doppelter submit-Code mehr)

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401 — type-checking only; lazy-loaded in __init__

# NOTE: torch, joblib, numpy, pandas are intentionally NOT imported at module level.
# Lazy imports in __init__() prevent coverage.py from triggering the heavy ML
# import chain when this module is scanned. (Epic 2.3 / I-1)

import config
from core.cloud_logger import DecisionContext
from core.events import SignalEvent
from core.ml.asset_integrity import safe_joblib_load
from core.simulation_adapter import SimulationAdapter
from core.smart_exit import should_sell_smart
from core.strategies.base import BaseStrategy
from core.telemetry import get_tracer

tracer = get_tracer(__name__)


SEQUENCE_LENGTH = 60
LSTM_DYNAMIC_TOP_N = 10
LSTM_DYNAMIC_MIN_PRED_BUY = 0.2
LSTM_DYNAMIC_MIN_POSITION_VALUE = 500.0


class LSTMDynamicStrategy(BaseStrategy):
    """LSTM-Only Strategy: Ranking → Positionsgröße via Conviction Weight → Smart Exit.

    Kein RL-Agent. LSTM treibt Entry + Ranking, smart_exit treibt Exit.
    Nutzt BaseStrategy._submit_order_safe (DRY-konsolidiert mit RLStrategy).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # --- Lazy ML imports (Epic 2.3 / I-1) ---
        import joblib as _joblib
        import numpy as _np
        import pandas as _pd
        import torch as _torch

        self.torch = _torch
        self.np = _np
        self.pd = _pd
        self.joblib = _joblib
        # -----------------------------------------
        self.strategy_name = "LSTMDynamic"
        self.device = self.torch.device(
            "cuda" if self.torch.cuda.is_available() else "cpu"
        )
        self.torch_model = None
        self.scaler_x = None
        self.scaler_y = None
        self.features_list = []
        self._model_lock = __import__("threading").Lock()  # Epic 2.3-Pre / PR-C
        self._load_torch_model_assets()
        self._lstm_rank_cache: List[Tuple[str, float]] = []
        self._allocation_weights: Dict[str, float] = {}
        self._last_symbols_set: Optional[set] = None
        self._bought_this_window: set = set()
        self.high_water_marks: Dict[str, float] = {}
        self._entry_time: Dict[str, datetime] = {}
        try:
            from config import MAX_POSITIONS

            self._max_positions = MAX_POSITIONS
        except ImportError:
            self._max_positions = 10

    def reload_weights(self, model_path: str) -> bool:
        """Lädt neue LSTM-Gewichte atomar und thread-safe (Epic 2.3-Pre / Issue E)."""
        if not os.path.exists(model_path):
            logging.error(
                "LSTMDynamic.reload_weights: Datei nicht gefunden: %s", model_path
            )
            return False
        try:
            state_dict = self.torch.load(  # nosec
                model_path, map_location=self.device, weights_only=True
            )
            with self._model_lock:
                self.torch_model.load_state_dict(state_dict)
                self.torch_model.eval()
            logging.info(
                "LSTMDynamic.reload_weights: Gewichte erfolgreich geladen aus %s",
                model_path,
            )
            return True
        except Exception as e:
            logging.error(
                "LSTMDynamic.reload_weights: Fehler beim Laden von %s: %s",
                model_path,
                e,
            )
            return False

    def _load_torch_model_assets(self) -> None:
        """Lädt LSTM-Modell, Scaler und Metadaten."""
        torch = self.torch
        joblib = self.joblib
        from models.torch_model import (  # lazy — avoids module-level torch
            LSTMModel,
            get_lstm_paths,
        )

        model_path, scaler_x_path, scaler_y_path, metadata_path = get_lstm_paths()
        required_files = [model_path, scaler_x_path, scaler_y_path, metadata_path]
        missing = [f for f in required_files if not os.path.exists(f)]
        if missing:
            logging.error("LSTMDynamic: Cannot load LSTM – missing %s", missing)
            return
        try:
            with open(metadata_path, "r") as f:
                self.torch_metadata = json.load(f)
            all_features = self.torch_metadata["features_list"]
            mp = self.torch_metadata["model_params"]
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
                input_dim = state_dict[w_key].shape[1]
                if input_dim != mp["input_dim"]:
                    logging.warning(
                        "LSTMDynamic: Checkpoint input_dim=%d vs metadata %d; using checkpoint.",
                        input_dim,
                        mp["input_dim"],
                    )
                self.features_list = all_features[:input_dim]
            else:
                input_dim = mp["input_dim"]
                self.features_list = all_features
            self.torch_model = LSTMModel(
                input_dim, mp["hidden_dim"], mp["num_layers"], mp["output_dim"]
            ).to(self.device)
            if first_key.startswith("models."):
                single_model_state = {
                    k.replace("models.0.", ""): v
                    for k, v in state_dict.items()
                    if k.startswith("models.0.")
                }
                self.torch_model.load_state_dict(single_model_state)
            else:
                self.torch_model.load_state_dict(state_dict)
            self.torch_model.eval()
            self.scaler_x = safe_joblib_load(scaler_x_path)
            self.scaler_y = safe_joblib_load(scaler_y_path)
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
            logging.info("LSTMDynamic: LSTM model loaded.")
        except Exception as e:
            logging.error("LSTMDynamic: Failed to load LSTM: %s", e)

    async def _get_torch_prediction(
        self, symbol: str, current_date: datetime, market_data: Dict[str, Any]
    ) -> Tuple[float, Optional["pd.DataFrame"]]:
        if current_date is None:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            current_date = _dt.now(_tz.utc)
            logging.warning(
                "LSTMDynamic [%s] ⚠️ current_date unexpectedly None! Using current UTC time.",
                symbol,
            )

        torch = self.torch

        np = self.np
        pd = self.pd
        from models.torch_model import create_live_features  # lazy

        if not self.torch_model or not self.scaler_x:
            return 0.0, None
        try:
            days = SEQUENCE_LENGTH + 200
            if isinstance(self.client, SimulationAdapter):
                hist = self.client.get_bars(symbol, "1d", limit=days * 2)
                if hist is not None:
                    hist = hist[
                        hist.index <= pd.Timestamp(current_date).tz_localize(None)
                    ]
            else:
                loop = asyncio.get_running_loop()
                hist = await loop.run_in_executor(
                    None, self.data_provider.get_data, symbol, current_date, days
                )
            if hist is None or len(hist) < SEQUENCE_LENGTH:
                return 0.0, None
            hist["vix"] = market_data.get("vix", 20.0)
            hist["vix"] = hist["vix"].bfill().ffill()
            hist["market_news_sentiment"] = market_data.get(
                "latest_news_sentiment", 0.0
            )
            hist["market_news_sentiment"] = hist["market_news_sentiment"].ffill()
            try:
                features_df = create_live_features(hist)
            except Exception as feat_err:
                # ADR-SEC-03: FeatureGenerationError → abstention (0.5 = neutral),
                # NOT 0.0 which would silently inject a SELL bias into the Round Table.
                from models.torch_model import FeatureGenerationError

                if isinstance(feat_err, FeatureGenerationError):
                    logging.warning(
                        "LSTMDynamic [%s] Feature generation failed — abstaining: %s",
                        symbol,
                        feat_err,
                    )
                    return 0.5, None  # Abstention: neutral score, no features
                raise  # Re-raise unexpected errors
            if features_df is None or len(features_df) < SEQUENCE_LENGTH:
                return 0.0, None
            X_live = features_df[self.features_list].tail(SEQUENCE_LENGTH).values.copy()
            X_live[~np.isfinite(X_live)] = 0.0
            X_scaled = self.scaler_x.transform(X_live)
            X_tensor = torch.tensor(np.array([X_scaled]), dtype=torch.float32).to(
                self.device
            )
            with tracer.start_as_current_span("model.inference") as span:
                span.set_attribute("symbol", symbol)
                span.set_attribute("market.vix", float(market_data.get("vix", 20.0)))
                with torch.no_grad():
                    pred = self.torch_model(X_tensor)
            return pred.cpu().numpy()[0][0], features_df.tail(1)
        except Exception as e:
            import traceback

            tb_str = traceback.format_exc()
            logging.warning(
                "LSTMDynamic [%s] LSTM pred failed: %s\nTraceback:\n%s",
                symbol,
                e,
                tb_str,
            )
            return 0.0, None

    async def update_lstm_rankings(
        self,
        symbols: List[str],
        snapshots: Dict,
        market_data: Dict[str, Any],
        current_time: datetime,
    ) -> None:
        """Baut LSTM-Ranking und Allokations-Gewichte aus aktuellen Symbolen auf."""
        np = self.np
        if not self.torch_model:
            return
        cache = []
        for symbol in symbols:
            if symbol not in snapshots:
                continue
            snap = (
                getattr(snapshots[symbol], "latest_trade", None)
                if hasattr(snapshots[symbol], "latest_trade")
                else None
            )
            if snap is None or not getattr(snap, "p", None):
                continue
            pred, _ = await self._get_torch_prediction(
                symbol, current_time, market_data
            )
            cache.append((symbol, float(pred)))
        self._lstm_rank_cache = sorted(cache, key=lambda x: x[1], reverse=True)
        if not self._lstm_rank_cache:
            self._allocation_weights = {}
            return
        symbols_in_order = [s for s, _ in self._lstm_rank_cache[:LSTM_DYNAMIC_TOP_N]]
        preds = np.array(
            [p for _, p in self._lstm_rank_cache[:LSTM_DYNAMIC_TOP_N]], dtype=np.float64
        )
        preds_shifted = np.clip(preds - preds.min() + 0.1, 0.1, None)
        weights = preds_shifted / preds_shifted.sum()
        self._allocation_weights = {
            s: float(w) for s, w in zip(symbols_in_order, weights)
        }
        logging.info(
            "LSTMDynamic: Ranked top %d: %s...",
            len(self._allocation_weights),
            list(self._allocation_weights.keys())[:5],
        )

    def _ensure_bought_window_reset(self) -> None:
        current_set = set(self.symbols)
        if self._last_symbols_set is not None and current_set != self._last_symbols_set:
            self._bought_this_window.clear()
        self._last_symbols_set = current_set

    def _get_rank_and_in_top_n(self, symbol: str) -> Tuple[Optional[int], bool]:
        for i, (s, _) in enumerate(self._lstm_rank_cache):
            if s == symbol:
                return i + 1, i < LSTM_DYNAMIC_TOP_N
        return None, False

    async def run_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ):
        self._ensure_bought_window_reset()
        if not self.torch_model:
            self.log_thought(f"[{symbol}] LSTMDynamic: LSTM not loaded.")
            return
        lstm_rank, in_top_n = self._get_rank_and_in_top_n(symbol)
        curr = ohlc_data["close"]
        in_pos, qty, avg = False, 0.0, 0.0
        try:
            pos = self.client.get_open_position(symbol)
            if pos is not None:
                qty = float(pos.qty)
                avg = float(pos.avg_entry_price)
                in_pos = qty > 0
        except Exception:
            pass

        if in_pos:
            self.high_water_marks[symbol] = max(
                self.high_water_marks.get(symbol, avg), curr
            )
            entry_time = self._entry_time.get(symbol, current_time)
            hours_held = (current_time - entry_time).total_seconds() / 3600
            atr_pct = None
            if symbol in getattr(self, "_last_features", {}):
                feat = self._last_features.get(symbol)
                if (
                    isinstance(feat, dict)
                    and feat.get("atr_14d") is not None
                    and curr > 0
                ):
                    atr_pct = float(feat["atr_14d"]) / curr
            decision = should_sell_smart(
                symbol=symbol,
                entry_price=avg,
                current_price=curr,
                high_water_mark=self.high_water_marks[symbol],
                hours_held=hours_held,
                in_top_n=in_top_n,
                lstm_rank=lstm_rank,
                top_n_size=LSTM_DYNAMIC_TOP_N,
                atr_pct=atr_pct,
                smart_take_profit=True,
            )
            if decision.action == "SELL":
                self.log_thought(f"[{symbol}] LSTMDynamic SELL: {decision.reason}")
                allowed, reason, _ = self.risk_manager.evaluate_new_trade(
                    symbol, "SELL", market_data, 3.0
                )
                if not allowed:
                    self.log_thought(
                        f"[{symbol}] SELL blocked by Risk Manager: {reason}"
                    )
                    self.high_water_marks.pop(symbol, None)
                    self._entry_time.pop(symbol, None)
                context = DecisionContext(
                    symbol=symbol,
                    action="SELL",
                    lstm_prediction=0.0,
                    rl_raw_action=2,
                    rl_stabilized_action=2,
                    conviction_score=0.0,
                    current_price=curr,
                    vix_level=float(market_data.get("vix", 20.0)),
                    market_regime=str(market_data.get("regime", "normal")),
                    in_position=in_pos,
                    position_qty=qty,
                    position_avg_price=avg,
                    unrealized_pnl=float(((curr - avg) * qty) if in_pos else 0.0),
                    unrealized_pnl_pct=float(
                        ((curr - avg) / avg) if in_pos and avg > 0 else 0.0
                    ),
                    triggered_by_stop=True,
                    stop_type=decision.reason,
                    model_version_id="lstm_dynamic_v1",
                )
                return SignalEvent(
                    symbol=symbol,
                    action="SELL",
                    decision_context=context,
                    suggested_quantity=qty,
                    is_simulation=isinstance(self.client, SimulationAdapter),
                )
        else:
            self.high_water_marks.pop(symbol, None)
            self._entry_time.pop(symbol, None)

        if not in_pos and in_top_n and symbol in self._allocation_weights:
            pred = next((p for s, p in self._lstm_rank_cache if s == symbol), 0.0)
            if pred < LSTM_DYNAMIC_MIN_PRED_BUY:
                self.log_thought(
                    f"[{symbol}] LSTMDynamic: Skip buy – LSTM pred {pred:.2f} < {LSTM_DYNAMIC_MIN_PRED_BUY}"
                )
                context = DecisionContext(
                    symbol=symbol,
                    action="HOLD",
                    lstm_prediction=pred,
                    rl_raw_action=0,
                    rl_stabilized_action=0,
                    current_price=curr,
                    vix_level=float(market_data.get("vix", 20.0)),
                    in_position=in_pos,
                    model_version_id="lstm_dynamic_v1",
                )
                return SignalEvent(
                    symbol=symbol,
                    action="HOLD",
                    decision_context=context,
                    is_simulation=isinstance(self.client, SimulationAdapter),
                )

            if symbol in self._bought_this_window:
                return

            try:
                account = self.client.get_account()
                total_capital = float(account.equity or account.cash or 0)
                reg_cash = float(account.cash or 0)
                reg_bp = float(account.buying_power or 0)
                cash = (
                    reg_cash
                    if getattr(config, "USE_CASH_ONLY", True)
                    else (reg_bp or reg_cash)
                )
            except Exception:
                total_capital = self.total_capital
                cash = total_capital * 0.95

            weight = self._allocation_weights.get(symbol, 0.0)
            position_value = total_capital * weight * 0.95
            position_value = min(position_value, cash * 0.95)
            position_value = max(LSTM_DYNAMIC_MIN_POSITION_VALUE, position_value)
            size = position_value / curr if curr > 0 else 0

            if size <= 0:
                self.log_thought(f"[{symbol}] LSTMDynamic: Skip buy – size 0")
                context = DecisionContext(
                    symbol=symbol,
                    action="HOLD",
                    lstm_prediction=pred,
                    rl_raw_action=1,
                    rl_stabilized_action=0,
                    current_price=curr,
                    vix_level=float(market_data.get("vix", 20.0)),
                    in_position=in_pos,
                    model_version_id="lstm_dynamic_v1",
                )
                return SignalEvent(
                    symbol=symbol,
                    action="HOLD",
                    decision_context=context,
                    is_simulation=isinstance(self.client, SimulationAdapter),
                )

            allowed, reason, _ = self.risk_manager.evaluate_new_trade(
                symbol, "BUY", market_data, 3.0
            )
            if not allowed:
                self.log_thought(f"[{symbol}] LSTMDynamic BUY blocked: {reason}")
                context = DecisionContext(
                    symbol=symbol,
                    action="HOLD",
                    lstm_prediction=pred,
                    risk_approved=False,
                    risk_reason=reason,
                    rl_raw_action=1,
                    rl_stabilized_action=0,
                    current_price=curr,
                    vix_level=float(market_data.get("vix", 20.0)),
                    in_position=in_pos,
                    model_version_id="lstm_dynamic_v1",
                )
                return SignalEvent(
                    symbol=symbol,
                    action="HOLD",
                    decision_context=context,
                    is_simulation=isinstance(self.client, SimulationAdapter),
                )

            self.log_thought(
                f"[{symbol}] LSTMDynamic BUY: {size:.4f} shares (LSTM rank {lstm_rank}, weight {weight:.2%})"
            )
            self._bought_this_window.add(symbol)
            self._entry_time[symbol] = current_time
            self.high_water_marks[symbol] = curr

            # Nutzt BaseStrategy._submit_order_safe (DRY)
            await self._submit_order_safe(
                symbol, size, "buy", expected_cost=size * curr, current_price=curr
            )

            context = DecisionContext(
                symbol=symbol,
                action="BUY",
                lstm_prediction=pred,
                rl_raw_action=1,
                rl_stabilized_action=1,
                conviction_score=weight,
                current_price=curr,
                vix_level=float(market_data.get("vix", 20.0)),
                market_regime=str(market_data.get("regime", "normal")),
                in_position=in_pos,
                position_qty=qty,
                position_avg_price=avg,
                model_version_id="lstm_dynamic_v1",
            )
            return SignalEvent(
                symbol=symbol,
                action="BUY",
                decision_context=context,
                suggested_quantity=size,
                is_simulation=isinstance(self.client, SimulationAdapter),
            )

        elif in_pos:
            self.log_thought(
                f"[{symbol}] LSTMDynamic HOLD (in top {LSTM_DYNAMIC_TOP_N}, smart exit not triggered)"
            )
            pred = next((p for s, p in self._lstm_rank_cache if s == symbol), 0.0)
            context = DecisionContext(
                symbol=symbol,
                action="HOLD",
                lstm_prediction=pred,
                rl_raw_action=0,
                rl_stabilized_action=0,
                current_price=curr,
                vix_level=float(market_data.get("vix", 20.0)),
                market_regime=str(market_data.get("regime", "normal")),
                in_position=in_pos,
                position_qty=qty,
                position_avg_price=avg,
                model_version_id="lstm_dynamic_v1",
            )
            return SignalEvent(
                symbol=symbol,
                action="HOLD",
                decision_context=context,
                is_simulation=isinstance(self.client, SimulationAdapter),
            )

        # Fallback
        pred = (
            next((p for s, p in self._lstm_rank_cache if s == symbol), 0.0)
            if hasattr(self, "_lstm_rank_cache")
            else 0.0
        )
        context = DecisionContext(
            symbol=symbol,
            action="HOLD",
            lstm_prediction=pred,
            rl_raw_action=0,
            rl_stabilized_action=0,
            current_price=curr,
            in_position=in_pos,
            model_version_id="lstm_dynamic_v1",
        )
        return SignalEvent(
            symbol=symbol,
            action="HOLD",
            decision_context=context,
            is_simulation=isinstance(self.client, SimulationAdapter),
        )
