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
from core.smart_exit import resolve_hold_hours, should_sell_smart
from core.strategies.base import BaseStrategy
from core.telemetry import get_tracer

tracer = get_tracer(__name__)


# #1878: fallback ONLY. The effective serve window is self.sequence_length, read
# from the model's own metadata in _load_torch_model_assets() (v1 ships with 20,
# v2 with 60). This module default applies when the metadata carries no valid value.
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
        # #1878: overridden from model metadata in _load_torch_model_assets();
        # initialised here so the missing-assets early-return leaves a sane default.
        self.sequence_length = SEQUENCE_LENGTH
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
            resolve_sequence_length,
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
            # #1878: serve window MUST match the model's validated training window.
            self.sequence_length = resolve_sequence_length(
                self.torch_metadata, SEQUENCE_LENGTH, "LSTMDynamic"
            )
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

    def _z_to_return(self, z: float) -> float:
        """#1878 Fix 2 — map a StandardScaler z-score to a real 5-day return via
        ``return = z * scaler_y.scale_ + scaler_y.mean_``. Strictly monotone
        (scale_ > 0), so converting BOTH the prediction and every comparison
        threshold keeps the buy/sell DECISION byte-identical (behavior-preserving)
        while the reported value + thresholds become real returns.

        BORA: pure numpy affine — identical on Desktop and Enterprise. Degrades to
        identity + WARNING (never crashes) if scaler_y is missing (older bundle)."""
        from models.torch_model import z_to_return

        return z_to_return(self.scaler_y, z, context="LSTMDynamic")

    async def _get_torch_prediction(
        self, symbol: str, current_date: datetime, market_data: Dict[str, Any]
    ) -> Tuple[Optional[float], Optional["pd.DataFrame"]]:
        """LSTM inference. Returns (prediction, features_tail).

        #1878: a return of (None, None) is an ABSTENTION — the symbol must be
        dropped from the ranking, never treated as a prediction. ALL data-error
        paths abstain (short history, failed/short features, unexpected serve
        errors): a numeric pseudo-prediction like 0.0 would rank a held position
        to the tail of the LSTM ranking and force a rank-based SELL on what is
        merely a data outage (#1878 review Finding 2).
        """
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
        from models.torch_model import (  # lazy — avoids module-level torch chain
            FEATURE_WARMUP_ROWS,
            create_live_features,
        )

        if not self.torch_model or not self.scaler_x:
            return 0.0, None
        # #1878: the serve window MUST match the model's validated training window
        # (metadata), not the module default. Bare test doubles built via __new__
        # carry no instance attribute — the module fallback keeps them patchable.
        seq_len = getattr(self, "sequence_length", None) or SEQUENCE_LENGTH
        # #1878 review (Finding 1): the model window alone is NOT enough history —
        # create_live_features needs FEATURE_WARMUP_ROWS rows before its slowest
        # indicators (sma_50, MACD 26+9) carry real signal. With v1's seq=20,
        # gating on seq_len alone would run inference on placeholder features.
        min_rows = max(seq_len, FEATURE_WARMUP_ROWS)
        try:
            days = seq_len + 200
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
            if hist is None or len(hist) < min_rows:
                # #1878 review (Finding 2): data outage → ABSTENTION, never 0.0.
                logging.warning(
                    "LSTMDynamic [%s]: history missing or too short (%d < %d rows "
                    "incl. feature warm-up) — abstaining.",
                    symbol,
                    0 if hist is None else len(hist),
                    min_rows,
                )
                return None, None
            # #1878 Fix 4: preserve the data provider's real vix / sentiment time-series
            # if present; only broadcast the current scalar as a FALLBACK when the column
            # is absent. A constant across the whole window breaks a retrained 34-feature
            # model that expects a real series (harmless for the 23-feature v1 model).
            if "vix" not in hist.columns:
                hist["vix"] = market_data.get("vix", 20.0)
            hist["vix"] = (
                hist["vix"].bfill().ffill().fillna(market_data.get("vix", 20.0))
            )
            if "market_news_sentiment" not in hist.columns:
                hist["market_news_sentiment"] = market_data.get(
                    "latest_news_sentiment", 0.0
                )
            hist["market_news_sentiment"] = (
                hist["market_news_sentiment"]
                .ffill()
                .fillna(market_data.get("latest_news_sentiment", 0.0))
            )
            try:
                features_df = create_live_features(hist)
            except Exception as feat_err:
                # ADR-SEC-03 (revised by #1878): FeatureGenerationError → abstention.
                # Abstention is None — NEVER a numeric pseudo-prediction: 0.0 would
                # silently inject a SELL bias into the Round Table, and the old 0.5
                # is a STRONG-BUY in prediction space (buy threshold 0.2) that could
                # rank a failed symbol into the Top-N and buy it. The Round-Table
                # agent maps a missing signal to vote-abstention (0.5, weight 0) in
                # VOTE space; update_lstm_rankings drops None predictions entirely.
                from models.torch_model import FeatureGenerationError

                if isinstance(feat_err, FeatureGenerationError):
                    logging.warning(
                        "LSTMDynamic [%s] Feature generation failed — abstaining: %s",
                        symbol,
                        feat_err,
                    )
                    return None, None  # Abstention: no prediction, no features
                raise  # Re-raise unexpected errors
            if features_df is None or len(features_df) < min_rows:
                # #1878 review (Finding 2): degraded feature frame → ABSTENTION.
                logging.warning(
                    "LSTMDynamic [%s]: feature frame missing or too short "
                    "(%d < %d rows) — abstaining.",
                    symbol,
                    0 if features_df is None else len(features_df),
                    min_rows,
                )
                return None, None
            X_live = features_df[self.features_list].tail(seq_len).values.copy()
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
            # #1878 Fix 2: apply scaler_y inverse — the returned value is a real
            # 5-day return, not the raw z-score the model emits.
            raw_z = float(pred.cpu().numpy()[0][0])
            return self._z_to_return(raw_z), features_df.tail(1)
        except Exception as e:
            # #1878 review (Finding 2): unexpected serve errors are ABSTENTIONS too —
            # (0.0, None) would rank a held position to the tail → forced rank-SELL.
            logging.warning(
                "LSTMDynamic [%s] LSTM pred failed — abstaining: %s",
                symbol,
                e,
                exc_info=True,
            )
            return None, None

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
            if pred is None:
                # #1878: feature failure → abstention. The symbol must NEVER enter
                # the ranking (the old 0.5-as-prediction ranked it into the Top-N
                # buy list). Rank stays None → smart exit's rank-based SELL does
                # not fire either (core/smart_exit.py:111).
                logging.warning(
                    "LSTMDynamic [%s]: prediction abstained — symbol excluded "
                    "from ranking this cycle.",
                    symbol,
                )
                continue
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

    def _get_pred_for_symbol(self, symbol: str) -> float:
        """#1969: raw continuous LSTM prediction for `symbol` from the rank cache.

        The evaluate-phase SignalEvent must carry the RAW `pred` in
        `decision_context.lstm_prediction` (matching rl_execution's
        `_log_decision_trace` and the field's name/semantics), NOT the ordinal
        rank. The Round-Table LSTMSignalAgent (#1969) and RLConfidenceAgent build
        their continuous vote from this value; feeding them the rank instead
        discretised / saturated the vote and destroyed the +0.067-IC signal.
        """
        for s, pred in self._lstm_rank_cache:
            if s == symbol:
                return float(pred)
        return 0.0

    async def evaluate_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ) -> Optional[SignalEvent]:
        """Evaluate-only: LSTM prediction → Signal. No order execution.

        Art. 14 EU AI Act / #1876: Vote-phase separated from execution.
        """
        if not self.torch_model:
            return None  # Abstention — model not loaded

        lstm_rank, in_top_n = self._get_rank_and_in_top_n(symbol)
        if lstm_rank is None:
            return None  # Abstention — symbol not in rank cache

        curr = ohlc_data.get("close", 0.0)

        # Position check (read-only broker query)
        in_pos = False
        avg = 0.0
        try:
            pos = self.client.get_open_position(symbol)
            if pos is not None:
                avg = float(pos.avg_entry_price)
                in_pos = float(pos.qty) > 0
        except Exception as e:
            logging.warning(
                "LSTM evaluate: error checking position for %s: %s", symbol, e
            )

        action = "HOLD"
        if in_top_n and not in_pos:
            action = "BUY"
        elif in_pos:
            # Smart exit check — module function from core.smart_exit
            # #1952 fail-open: unknown entry (in-memory _entry_time is empty after a
            # restart/reconcile) -> resolve_hold_hours treats it as past the min-hold
            # window so rule #1 rebalance-sell isn't silently frozen. Risk rules unaffected.
            hours_held = resolve_hold_hours(self._entry_time.get(symbol), current_time)
            atr_pct = None
            if symbol in getattr(self, "_last_features", {}):
                feat = self._last_features.get(symbol)
                if (
                    isinstance(feat, dict)
                    and feat.get("atr_14d") is not None
                    and curr > 0
                ):
                    atr_pct = float(feat["atr_14d"]) / curr

            hwm = self.high_water_marks.get(symbol, avg)
            decision = should_sell_smart(
                symbol=symbol,
                entry_price=avg,
                current_price=curr,
                high_water_mark=hwm,
                hours_held=hours_held,
                in_top_n=in_top_n,
                lstm_rank=lstm_rank,
                top_n_size=LSTM_DYNAMIC_TOP_N,
                atr_pct=atr_pct,
                smart_take_profit=True,
            )
            if decision.action == "SELL":
                action = "SELL"

        ctx = DecisionContext(
            symbol=symbol,
            action=action,
            # #1969: raw continuous pred (not the ordinal rank) so the Round-Table
            # agents can build a monotone continuous vote from the real signal.
            lstm_prediction=self._get_pred_for_symbol(symbol),
            current_price=curr,
            reasoning_summary=f"LSTM evaluate-only: rank={lstm_rank}, top_n={in_top_n}",
        )
        return SignalEvent(symbol=symbol, action=action, decision_context=ctx)

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
            # #1952 fail-open: unknown entry (in-memory _entry_time is empty after a
            # restart/reconcile) -> resolve_hold_hours treats it as past the min-hold
            # window so rule #1 rebalance-sell isn't silently frozen. Risk rules unaffected.
            hours_held = resolve_hold_hours(self._entry_time.get(symbol), current_time)
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
                    context = DecisionContext(
                        symbol=symbol,
                        action="HOLD",
                        lstm_prediction=0.0,
                        risk_approved=False,
                        risk_reason=reason,
                        rl_raw_action=2,
                        rl_stabilized_action=0,
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
                        action="HOLD",
                        decision_context=context,
                        is_simulation=isinstance(self.client, SimulationAdapter),
                    )

                # Allowed path:
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
            # #1878 Fix 2: pred is now a real return (inverse-transformed); rescale
            # the z-space threshold with the same affine so the decision is identical.
            buy_floor = self._z_to_return(LSTM_DYNAMIC_MIN_PRED_BUY)
            if pred < buy_floor:
                self.log_thought(
                    f"[{symbol}] LSTMDynamic: Skip buy – LSTM pred {pred:.4f} < {buy_floor:.4f}"
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
