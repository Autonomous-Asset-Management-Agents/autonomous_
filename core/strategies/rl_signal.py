# core/strategies/rl_signal.py
# Epic 1.7 / PR-B — Signal-Funktionen (reine Berechnungen ohne Seiteneffekte)
# Enthält: RLSignalMixin mit _calculate_conviction_score, _get_vix_adaptive_thresholds,
#          _update_vix_from_market_data, _stabilize_signal, _normalize_state,
#          _generate_thought, _get_torch_prediction, _get_current_state,
#          _get_vertex_prediction
# Extrahiert aus core/strategies.py Z.355-856

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from alpaca.common.exceptions import APIError

try:
    from core.telemetry import get_meter
except ImportError:  # pragma: no cover
    get_meter = None  # type: ignore[assignment]

SEQUENCE_LENGTH = 60


# Module-level counter — cheap singleton, no-op if OTel not available
def get_fallback_counter():
    """Return OTel counter for agent fallback events."""
    if get_meter is None:

        class _Noop:
            def add(self, *a, **kw):
                pass

        return _Noop()
    return get_meter("aaa-engine").create_counter(
        "agent.fallback",
        description="Incremented when an agent returns fallback value (model not loaded)",
    )


_FALLBACK_COUNTER = get_fallback_counter()


# --- ADR-OBS-01 / PR D: ml-fallback instrumentation (PURE OBSERVATION, VC-1) ---
# Fail-safe module-level counter bumped at the inference model-not-loaded fallback
# point (below). PURE OBSERVATION — a counter failure can never alter the signal
# path (the neutral-fallback return is byte-identical). Read-only via
# ``get_ml_fallback_count`` for the /engine-diagnostics ``models`` subsystem.
_ML_FALLBACK: Dict[str, int] = {"count": 0}


def _bump_ml_fallback() -> None:
    """Fail-safe ml-fallback counter bump — swallows EVERY error (observation must
    never break inference)."""
    try:
        _ML_FALLBACK["count"] = int(_ML_FALLBACK.get("count", 0) or 0) + 1
    except Exception:  # noqa: BLE001 — a broken counter must never break inference
        pass


def get_ml_fallback_count() -> int:
    """Read-only snapshot of the ml-fallback (model-not-loaded) counter."""
    try:
        return int(_ML_FALLBACK.get("count", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def reset_ml_fallback_count() -> None:
    """Test/daily-reset helper — zeroes the ml-fallback counter."""
    try:
        _ML_FALLBACK["count"] = 0
    except Exception:  # noqa: BLE001
        pass


class RLSignalMixin:
    """Mixin für alle Signal-Berechnungen des RLAgent.

    Diese Methoden sind pure Berechnungen ohne Trading-Seiteneffekte:
    kein Order-Submit, kein Portfolio-Update, kein Cloud-Logging.
    """

    # Subklassen müssen diese Attribute setzen (via RLStrategy.__init__):
    # self.torch_model, self.scaler_x, self.scaler_y, self.features_list
    # self.vec_normalize, self._current_vix, self._vix_regime
    # self.client, self.symbols, self.data_provider
    # self._rl_model_version, self.last_thought_time

    # ── Conviction Score ──────────────────────────────────────────────────────

    def _calculate_conviction_score(
        self,
        features: Optional[pd.Series],
        model_pred: float,
        market_data: Dict[str, Any],
    ) -> float:
        """Conviction Score (0.0–1.0) für dynamisches Position-Sizing.

        Faktoren: LSTM-Prediction-Stärke, RSI-Extreme, ADX-Trendstärke,
        MACD-Ausrichtung, VIX-Level.
        """
        if features is None:
            return 0.2

        score = 0.0

        # 1. Modell-Konfidenz (0–0.40)
        model_strength = min(abs(model_pred) / 1.2, 1.0)
        score += model_strength * 0.40

        # 2. RSI-Signalqualität (0–0.25)
        rsi = features.get("rsi_14", 50.0)
        if rsi < 30:
            score += 0.25
        elif rsi < 40:
            score += 0.15
        elif 40 <= rsi <= 60:
            score += 0.10
        elif rsi > 70:
            score += 0.05

        # 3. Trendstärke via ADX (0–0.20)
        adx = features.get("adx_14", 20.0)
        if adx > 40:
            score += 0.20
        elif adx > 30:
            score += 0.15
        elif adx > 25:
            score += 0.10
        elif adx > 20:
            score += 0.05

        # 4. MACD-Ausrichtung (0–0.15)
        macd = features.get("macd", 0.0)
        macd_signal = (
            features.get("macd_signal", 0.0) if "macd_signal" in features.index else 0.0
        )
        if model_pred > 0 and macd > macd_signal:
            score += 0.15
        elif model_pred < 0 and macd < macd_signal:
            score += 0.15
        elif macd > 0 and model_pred > 0:
            score += 0.08

        # 5. VIX-Anpassung (0–0.10)
        vix = market_data.get("vix", 20.0) if market_data else 20.0
        if vix < 15:
            score += 0.10
        elif vix < 20:
            score += 0.07
        elif vix < 25:
            score += 0.03

        return max(0.0, min(1.0, score))

    # ── VIX Adaptive Thresholds ───────────────────────────────────────────────

    def _get_vix_adaptive_thresholds(self) -> Dict[str, Any]:
        """VIX-Regime-abhängige Handelsschwellenwerte.

        Regimes: low (<15), normal (15–25), elevated (25–35), crisis (≥35).
        """
        vix = self._current_vix

        if vix < 15:
            self._vix_regime = "low"
            return {
                "buy_votes_required": 2,
                "sell_votes_required": 2,
                "strong_signal_threshold": 1.3,
                "reversal_cooldown": 3,
                "lstm_buy_threshold": 0.4,
                "lstm_sell_threshold": -0.4,
            }
        elif vix < 25:
            self._vix_regime = "normal"
            return {
                "buy_votes_required": 2,
                "sell_votes_required": 3,
                "strong_signal_threshold": 1.5,
                "reversal_cooldown": 5,
                "lstm_buy_threshold": 0.5,
                "lstm_sell_threshold": -0.5,
            }
        elif vix < 35:
            self._vix_regime = "elevated"
            return {
                "buy_votes_required": 3,
                "sell_votes_required": 2,
                "strong_signal_threshold": 1.8,
                "reversal_cooldown": 7,
                "lstm_buy_threshold": 0.7,
                "lstm_sell_threshold": -0.3,
            }
        else:
            self._vix_regime = "crisis"
            return {
                "buy_votes_required": 4,
                "sell_votes_required": 2,
                "strong_signal_threshold": 2.5,
                "reversal_cooldown": 10,
                "lstm_buy_threshold": 1.0,
                "lstm_sell_threshold": -0.2,
            }

    def _update_vix_from_market_data(self, market_data: Dict[str, Any]) -> None:
        """Aktualisiert VIX-Level aus market_data für adaptive Thresholds."""
        vix = market_data.get("vix")
        if vix is not None and isinstance(vix, (int, float)) and vix > 0:
            self._current_vix = float(vix)
        regime_info = market_data.get("regime_info", {})
        if isinstance(regime_info, dict) and "value" in regime_info:
            self._current_vix = float(regime_info["value"])

    # ── Signal Stabilisierung ─────────────────────────────────────────────────

    def _stabilize_signal(
        self, symbol: str, raw_action: int, pred: float, in_position: bool
    ) -> int:
        """Kombiniert RL + LSTM zu einem stabilen finalen Signal.

        Logik:
        - BUY wenn bereits in Position → HOLD
        - SELL wenn nicht in Position → HOLD
        - Starkes LSTM kann RL-HOLD überschreiben (BUY wenn pred > 0.6, SELL wenn pred < -0.6)
        - RL kann LSTM-Neutral überschreiben wenn LSTM nicht gegenläufig ist
        """
        if raw_action == 1 and in_position:
            return 0
        if raw_action == 2 and not in_position:
            return 0

        LSTM_BUY = 0.25
        LSTM_SELL = -0.25
        LSTM_STRONG = 0.6

        lstm_buy = pred > LSTM_BUY
        lstm_sell = pred < LSTM_SELL
        lstm_strong_buy = pred > LSTM_STRONG
        lstm_strong_sell = pred < -LSTM_STRONG
        rl_buy = raw_action == 1
        rl_sell = raw_action == 2

        if not in_position:
            if lstm_buy and (rl_buy or lstm_strong_buy):
                if not rl_buy and lstm_strong_buy:
                    logging.info(
                        "[%s] BUY: strong LSTM (%.2f) – RL HOLD overridden",
                        symbol,
                        pred,
                    )
                return 1
            if rl_buy and not lstm_sell:
                logging.info(
                    "[%s] BUY: RL overrides LSTM (LSTM=%.2f neutral)", symbol, pred
                )
                return 1

        if in_position:
            if lstm_sell and (rl_sell or lstm_strong_sell):
                if not rl_sell and lstm_strong_sell:
                    logging.info(
                        "[%s] SELL: strong LSTM (%.2f) – RL HOLD overridden",
                        symbol,
                        pred,
                    )
                return 2
            if rl_sell and not lstm_buy:
                logging.info(
                    "[%s] SELL: RL overrides LSTM (LSTM=%.2f neutral)", symbol, pred
                )
                return 2

        return 0

    # ── State Normalisierung ──────────────────────────────────────────────────

    def _normalize_state(self, raw_state: np.ndarray) -> np.ndarray:
        """Normalisiert den RL-Beobachtungsvektor mit VecNormalize (wenn verfügbar)."""
        if self.vec_normalize is None:
            return raw_state
        try:
            return np.clip(
                (raw_state - self.vec_normalize.obs_rms.mean)
                / np.sqrt(self.vec_normalize.obs_rms.var + 1e-8),
                -10.0,
                10.0,
            )
        except Exception:
            return raw_state

    # ── Thought Generation ────────────────────────────────────────────────────

    def _generate_thought(
        self,
        symbol: str,
        action: int,
        features: Optional[pd.Series],
        torch_pred: float,
        market_data: Dict,
        raw_action: int = None,
    ) -> None:
        """Generiert einen Erklärungstext für BUY/SELL/HOLD-Entscheidungen."""
        now = datetime.now()
        last_time = self.last_thought_time.get(symbol)
        symbol_rank = self.symbols.index(symbol) if symbol in self.symbols else 999
        is_priority = symbol_rank < 10
        limit_sec = getattr(__import__("config"), "THOUGHT_RATE_LIMIT_SECONDS", 120)
        signal_was_stabilized = raw_action is not None and raw_action != action

        should_speak = (
            (last_time is None)
            or (action != 0)
            or is_priority
            or signal_was_stabilized
            or ((now - last_time).total_seconds() > limit_sec)
        )
        if not should_speak:
            return

        self.last_thought_time[symbol] = now
        msg = f"[{symbol}] "

        if features is None:
            msg += "⚠️ Insufficient historical data. Waiting for more data..."
            self.log_thought(msg)
            return

        rsi = features.get("rsi_14", 50.0)
        macd = features.get("macd", 0.0)
        adx = features.get("adx_14", 0.0)
        regime = market_data.get("regime", "Unknown")

        if action == 1:
            msg += f"🟢 LSTM+RL AGREE: BUY! (LSTM: {torch_pred:.2f}, RSI: {rsi:.1f}, MACD: {macd:.3f}, ADX: {adx:.1f})"
        elif action == 2:
            msg += f"🔴 LSTM+RL AGREE: SELL! (LSTM: {torch_pred:.2f}, RSI: {rsi:.1f})"
        else:
            reasons = []
            if signal_was_stabilized and raw_action is not None:
                if raw_action == 1:
                    reasons.append("Already in position")
                elif raw_action == 2:
                    reasons.append("No position to sell")
            if torch_pred > 0.5 and raw_action != 1:
                reasons.append(f"LSTM bullish ({torch_pred:.2f}) but RL disagrees")
            elif torch_pred < -0.5 and raw_action != 2:
                reasons.append(f"LSTM bearish ({torch_pred:.2f}) but RL disagrees")
            elif abs(torch_pred) < 0.5:
                reasons.append(f"LSTM neutral ({torch_pred:.2f})")
            if rsi > 70:
                reasons.append(f"RSI overbought ({rsi:.1f})")
            if rsi < 30:
                reasons.append(f"RSI oversold ({rsi:.1f})")
            if adx < 20:
                reasons.append(f"Trend weak (ADX {adx:.1f})")
            if not reasons:
                reasons.append("Awaiting signal alignment")
            msg += f"⚪ HOLD - {', '.join(reasons)}. Market: {regime}"

        self.log_thought(msg)

    # ── LSTM / Vertex Prediction ──────────────────────────────────────────────

    async def _get_vertex_prediction(self, X_scaled: np.ndarray) -> float:
        """Sendet Features an Vertex AI Endpoint für Inferenz."""
        try:
            from google.cloud import aiplatform
        except ImportError:
            aiplatform = None

        if not aiplatform or not getattr(
            __import__("config"), "VERTEX_ENDPOINT_ID", None
        ):
            raise ValueError("Vertex AI not configured")

        loop = asyncio.get_running_loop()

        def _predict():
            endpoint = aiplatform.Endpoint(
                endpoint_name=__import__("config").VERTEX_ENDPOINT_ID,
                project=__import__("config").GCP_PROJECT_ID,
                location=__import__("config").GCP_REGION,
            )
            return endpoint.predict(instances=[X_scaled.tolist()])

        response = await loop.run_in_executor(None, _predict)
        preds = response.predictions
        if preds and len(preds) > 0:
            val = preds[0]
            if isinstance(val, list):
                return float(val[0])
            return float(val)
        return 0.0

    async def _get_torch_prediction(
        self, symbol: str, current_date: datetime, market_data: Dict[str, Any]
    ) -> Tuple[float, Optional[pd.DataFrame]]:
        """LSTM-Inferenz + optionale Vertex-AI-Delegation."""
        if current_date is None:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            current_date = _dt.now(_tz.utc)
            self.log_thought(
                f"[{symbol}] ⚠️ current_date unexpectedly None! Using current UTC time."
            )

        try:
            from google.cloud import aiplatform as _aip
        except ImportError:
            _aip = None

        config = __import__("config")
        use_vertex = bool(getattr(config, "VERTEX_ENDPOINT_ID", None) and _aip)

        if not self.torch_model and not use_vertex:
            self.log_thought(
                f"[{symbol}] ⚠️ PyTorch model not loaded and Vertex not configured!"
            )
            _FALLBACK_COUNTER.add(1, {"agent": "rl", "reason": "model_not_loaded"})
            _bump_ml_fallback()  # PR D: fail-safe machine counter for diagnostics
            return 0.0, None
        if not self.scaler_x:
            self.log_thought(f"[{symbol}] ⚠️ Scaler not loaded!")
            return 0.0, None

        from core.simulation_adapter import SimulationAdapter
        from models.torch_model import create_live_features

        try:
            days = SEQUENCE_LENGTH + 200
            min_rows = SEQUENCE_LENGTH

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

            if hist is None:
                self.log_thought(f"[{symbol}] ⚠️ Data fetch returned None")
                return 0.0, None
            if len(hist) < min_rows:
                self.log_thought(
                    f"[{symbol}] ⚠️ Not enough history: {len(hist)} rows, need {min_rows}"
                )
                return 0.0, None

            hist["vix"] = market_data.get("vix", 20.0)
            hist["vix"] = hist["vix"].bfill().ffill()
            hist["market_news_sentiment"] = market_data.get(
                "latest_news_sentiment", 0.0
            )
            hist["market_news_sentiment"] = hist["market_news_sentiment"].ffill()
            features_df = create_live_features(hist)

            if features_df is None:
                return 0.0, None
            if len(features_df) < SEQUENCE_LENGTH:
                return 0.0, None

            X_live = features_df[self.features_list].tail(SEQUENCE_LENGTH).values.copy()
            X_live[~np.isfinite(X_live)] = 0.0
            X_scaled = self.scaler_x.transform(X_live)

            if use_vertex:
                try:
                    pred_val = await self._get_vertex_prediction(X_scaled)
                    return pred_val, features_df.tail(1)
                except Exception as ve:
                    logging.warning(
                        "[%s] Vertex AI failed (%s). Falling back to local model.",
                        symbol,
                        ve,
                    )

            if not self.torch_model:
                return 0.0, features_df.tail(1)

            X_tensor = torch.tensor(np.array([X_scaled]), dtype=torch.float32).to(
                self.device
            )
            with torch.no_grad():
                pred = self.torch_model(X_tensor)
            return pred.cpu().numpy()[0][0], features_df.tail(1)

        except Exception as e:
            import traceback

            tb_str = traceback.format_exc()
            self.log_thought(
                f"[{symbol}] ⚠️ Exception in _get_torch_prediction: {type(e).__name__}: {e}\nTraceback:\\n{tb_str}"
            )
            return 0.0, None

    async def _get_current_state(
        self, symbol: str, current_date: datetime, market_data: Dict[str, Any]
    ) -> Tuple[Optional[np.ndarray], Optional[pd.DataFrame], float]:
        """Baut den RL-Beobachtungsvektor auf (11 oder 12 Dimensionen je nach Modell-Version)."""
        pred, features = await self._get_torch_prediction(
            symbol, current_date, market_data
        )
        if features is None or features.empty:
            return None, None, pred

        feat = features.iloc[0]

        try:
            pos = self.client.get_open_position(symbol)
            if pos is not None:
                if hasattr(pos, "qty"):
                    pos_qty = float(pos.qty)
                    avg_price = (
                        float(pos.avg_entry_price)
                        if hasattr(pos, "avg_entry_price")
                        else 0.0
                    )
                elif isinstance(pos, dict):
                    pos_qty = float(pos.get("qty", 0))
                    avg_price = float(pos.get("avg_entry_price", 0))
                else:
                    pos_qty = 0
                    avg_price = 0.0
                pos_state = 1 if pos_qty > 0 else 0
            else:
                pos_state = 0
                pos_qty = 0
                avg_price = 0.0
        except Exception as e:
            is_404 = False
            if isinstance(e, APIError) and (
                e.status_code == 404 or getattr(e, "code", None) == 40410000
            ):
                is_404 = True

            if is_404:
                # 404 position not found is normal for Alpaca when we have no position open
                pos_state = 0
                pos_qty = 0
                avg_price = 0.0
            else:
                logging.warning(
                    "[%s] Indeterminate position state due to API failure: %s. Gracefully aborting state generation.",
                    symbol,
                    e,
                )
                return None, None, pred

        current_price = feat.get("close", feat.get("Close", 0.0))
        returns = feat.get("returns", feat.get("price_change_1d", 0.0))
        rsi_14 = feat.get("rsi_14", feat.get("rsi_14d", 50.0))
        macd = feat.get("macd", 0.0)
        bb_pct = feat.get("bb_pct", feat.get("bb_percent", 0.5))
        volume = feat.get("volume", 0)
        volume_sma = feat.get("volume_sma_20d", volume if volume > 0 else 1)
        volume_ratio = volume / volume_sma if volume_sma > 0 else 1.0
        volatility_20d = feat.get("volatility_20d", 0.02)
        momentum_10d = feat.get("momentum_10d", feat.get("price_change_5d", 0.0))
        adx_14 = feat.get("adx_14", feat.get("adx_14d", 25.0))
        time_in_position = 0.0
        unrealized_pnl = (
            np.clip((current_price - avg_price) / avg_price, -0.5, 0.5)
            if pos_state == 1 and avg_price > 0 and current_price > 0
            else 0.0
        )

        # Clippen
        returns = np.clip(returns if not pd.isna(returns) else 0.0, -10, 10)
        rsi_14 = np.clip(rsi_14 if not pd.isna(rsi_14) else 50.0, 0, 100) / 100.0
        macd = np.clip(macd if not pd.isna(macd) else 0.0, -10, 10)
        bb_pct = np.clip(bb_pct if not pd.isna(bb_pct) else 0.5, -1, 2)
        volume_ratio = np.clip(
            volume_ratio if not pd.isna(volume_ratio) else 1.0, 0, 10
        )
        volatility_20d = np.clip(
            volatility_20d if not pd.isna(volatility_20d) else 0.02, 0, 1
        )
        momentum_10d = np.clip(
            momentum_10d if not pd.isna(momentum_10d) else 0.0, -10, 10
        )
        adx_14 = np.clip(adx_14 if not pd.isna(adx_14) else 25.0, 0, 100) / 100.0

        is_v3 = any(
            x in getattr(self, "_rl_model_version", "").lower()
            for x in ("v3", "v4", "v5")
        )

        if is_v3:
            vol = volatility_20d
            if vol < 0.015:
                regime_score = 0.0
            elif vol < 0.025:
                regime_score = 0.33
            elif vol < 0.04:
                regime_score = 0.67
            else:
                regime_score = 1.0
            raw = np.array(
                [
                    returns,
                    rsi_14,
                    macd,
                    bb_pct,
                    volume_ratio,
                    volatility_20d,
                    momentum_10d,
                    adx_14,
                    float(pos_state),
                    time_in_position,
                    unrealized_pnl,
                    regime_score,
                ],
                dtype=np.float32,
            )
        else:
            raw = np.array(
                [
                    returns,
                    rsi_14,
                    macd,
                    bb_pct,
                    volume_ratio,
                    volatility_20d,
                    momentum_10d,
                    adx_14,
                    float(pos_state),
                    time_in_position,
                    unrealized_pnl,
                ],
                dtype=np.float32,
            )

        return self._normalize_state(np.nan_to_num(raw)), features, pred
