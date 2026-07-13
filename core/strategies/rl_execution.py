# core/strategies/rl_execution.py
# Epic 1.7 / PR-B — _run_for_symbol_impl aufgebrochen in ≥8 testbare Methoden
# Extrahiert aus core/strategies.py Z.958-1553
# Kein Verhalten geändert — nur strukturell in fokussierte Methoden zerlegt.

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from alpaca.common.exceptions import APIError

from core.cloud_logger import DecisionContext
from core.events import SignalEvent
from core.smart_exit import should_sell_smart

try:
    from core.intelligent_exit import PositionContext as IntelligentPositionContext
    from core.intelligent_exit import analyze_exit

    _INTELLIGENT_EXIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INTELLIGENT_EXIT_AVAILABLE = False


class RLExecutionMixin:
    """Mixin für die strukturierte Ausführungslogik des RLAgent.

    _run_for_symbol_impl (599 LOC, CC≈130) wurde in 8 fokussierte
    Methoden mit je CC < 15 zerlegt:

    _run_for_symbol_impl
      ├── _gather_market_inputs()    → state, features, pred
      ├── _check_position_state()   → in_position, qty, avg
      ├── _evaluate_signal()        → signal, raw/rl_action
      ├── _check_smart_exit()       → triggered, updated signal
      ├── _check_trade_intelligence → should_trade decision (BUY only)
      ├── _check_portfolio_manager  → should_open decision (BUY only)
      ├── _apply_risk_filters()     → allowed, mods
      └── _log_decision_trace()     → SignalEvent
    """

    # ── 1. _gather_market_inputs ─────────────────────────────────────────────

    async def _gather_market_inputs(
        self,
        symbol: str,
        current_time: datetime,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Aktualisiert VIX und baut State + Features via LSTM-Inferenz auf.

        Returns:
            dict mit keys: state, features, pred
        """
        self._update_vix_from_market_data(market_data)
        state, features, pred = await self._get_current_state(
            symbol, current_time, market_data
        )
        return {"state": state, "features": features, "pred": pred}

    # ── 2. _check_position_state ─────────────────────────────────────────────

    def _check_position_state(self, symbol: str) -> Dict[str, Any]:
        """Liest aktuellen Positionsstatus vom Broker/Adapter.

        Returns:
            dict mit keys: in_position (bool), qty (float), avg (float)
        """
        in_pos = False
        qty = 0.0
        avg = 0.0
        try:
            pos = self.client.get_open_position(symbol)
            if pos is not None:
                qty = float(pos.qty)
                avg = float(pos.avg_entry_price)
                in_pos = qty > 0
                if in_pos:
                    logging.debug(
                        "[%s] Position detected: %.4f shares @ $%.2f", symbol, qty, avg
                    )
        except Exception as e:
            is_404 = False
            if isinstance(e, APIError) and (
                getattr(e, "status_code", None) == 404
                or getattr(e, "code", None) == 40410000
            ):
                is_404 = True
            if not is_404:
                logging.warning("[%s] Error checking position: %s", symbol, e)
        return {"in_position": in_pos, "qty": qty, "avg": avg}

    # ── 3. _evaluate_signal ──────────────────────────────────────────────────

    async def _evaluate_signal(
        self,
        symbol: str,
        state: Optional[np.ndarray],
        features: Optional[pd.DataFrame],
        pred: float,
        in_position: bool,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Leitet das finale Signal aus RL + LSTM ab.

        Returns:
            dict mit keys: signal (str), raw_rl_action (int), rl_action (int)
        """
        if self.rl_model is not None:
            if state is None:
                self._generate_thought(symbol, 0, None, 0.0, market_data)
                return {"signal": "HOLD", "raw_rl_action": 0, "rl_action": 0}
            dones = np.array([False])
            last_state = self._lstm_states.get(symbol)
            # #1847: run the CPU-bound policy inference OFF the event loop so N symbols don't
            # serialize on a blocking predict. Thread-safe: read-only deterministic forward, the
            # recurrent state is passed in/out per symbol (see
            # docs/plans/loop_perf_rl_predict_offload_implementation_plan.md). Behaviour is
            # byte-identical — proven by test_predict_serial_equals_concurrent.
            action, self._lstm_states[symbol] = await asyncio.to_thread(
                self.rl_model.predict,
                state.reshape(1, -1),
                state=last_state,
                episode_start=dones,
                deterministic=True,
            )
            raw_rl_action = int(action[0])
        else:
            raw_rl_action = 1 if pred > 0.35 else (2 if pred < -0.35 else 0)
            if state is not None and symbol in self._lstm_states:
                self._lstm_states[symbol] = state
            logging.debug(
                "[%s] LSTM-only: pred=%.2f → %s",
                symbol,
                pred,
                ["HOLD", "BUY", "SELL"][raw_rl_action],
            )

        # Position-Enforcements (kein BUY wenn in Pos, kein SELL wenn keine Pos)
        rl_action = raw_rl_action
        if raw_rl_action == 1 and in_position:
            rl_action = 0
        if raw_rl_action == 2 and not in_position:
            rl_action = 0

        # Thought generieren (nicht in Simulation)
        from core.simulation_adapter import SimulationAdapter

        if not isinstance(self.client, SimulationAdapter):
            self._generate_thought(
                symbol,
                rl_action,
                (
                    features.iloc[0]
                    if features is not None and not features.empty
                    else None
                ),
                pred,
                market_data,
                raw_action=raw_rl_action,
            )

        signal = {0: "HOLD", 1: "BUY", 2: "SELL"}.get(rl_action, "HOLD")
        return {
            "signal": signal,
            "raw_rl_action": raw_rl_action,
            "rl_action": rl_action,
        }

    # ── 4. _check_smart_exit ─────────────────────────────────────────────────

    def _check_smart_exit(
        self,
        symbol: str,
        in_position: bool,
        qty: float,
        avg: float,
        curr: float,
        current_time: datetime,
        features: Optional[pd.DataFrame],
        pred: float = 0.0,
    ) -> Dict[str, Any]:
        """Prüft Exit-Bedingungen (Epic 2.4 Intelligent Exit als Primär-Pfad).

        Primär:  analyze_exit() aus core.intelligent_exit (5-Tier adaptive logic)
        Fallback: should_sell_smart() aus core.smart_exit (rule-based)

        Returns:
            dict mit keys: triggered (bool), signal (str)
        """
        if not in_position:
            return {"triggered": False, "signal": "HOLD"}

        self.high_water_marks[symbol] = max(
            self.high_water_marks.get(symbol, avg), curr
        )
        entry_time = self._entry_time.get(symbol, current_time)
        hours_held = (current_time - entry_time).total_seconds() / 3600

        # ── Primär: Intelligent Exit (Epic 2.4) ──────────────────────
        if _INTELLIGENT_EXIT_AVAILABLE:
            try:
                momentum_history = list(self._signal_history.get(symbol, []))[-5:]
                ctx = IntelligentPositionContext(
                    symbol=symbol,
                    entry_price=avg,
                    current_price=curr,
                    high_water_mark=self.high_water_marks[symbol],
                    hours_held=hours_held,
                    entry_time=entry_time,
                    lstm_prediction=float(pred),
                    momentum_history=momentum_history,
                )
                if features is not None and not features.empty:
                    feat = features.iloc[0]
                    ctx.rsi = float(feat.get("rsi_14", 50.0))
                    ctx.macd = float(feat.get("macd", 0.0))
                analysis = analyze_exit(ctx)
                if analysis.should_sell:
                    self.log_thought(
                        f"[{symbol}] INTELLIGENT EXIT: {analysis.reason} "
                        f"(score={analysis.total_score:.0f}/100)"
                    )
                    return {"triggered": True, "signal": "SELL"}
                return {"triggered": False, "signal": "HOLD"}
            except Exception as exc:
                logging.warning(
                    "[%s] Intelligent exit error, falling back to smart_exit: %s",
                    symbol,
                    exc,
                )

        # ── Fallback: Smart Exit (legacy) ─────────────────────────────
        atr_pct = None
        if features is not None and not features.empty:
            feat = features.iloc[0]
            atr_val = feat.get("atr_14d", feat.get("atr_14", None))
            if atr_val is not None and curr and float(atr_val) > 0:
                atr_pct = float(atr_val) / curr

        decision = should_sell_smart(
            symbol=symbol,
            entry_price=avg,
            current_price=curr,
            high_water_mark=self.high_water_marks[symbol],
            hours_held=hours_held,
            in_top_n=True,
            lstm_rank=1,
            top_n_size=10,
            atr_pct=atr_pct,
            smart_take_profit=True,
        )

        if decision.action == "SELL":
            self.log_thought(f"[{symbol}] SMART EXIT (fallback): {decision.reason}")
            return {"triggered": True, "signal": "SELL"}
        return {"triggered": False, "signal": "HOLD"}

    # ── 5. _check_trade_intelligence ─────────────────────────────────────────

    def _check_trade_intelligence(
        self,
        symbol: str,
        pred: float,
        features: Optional[pd.DataFrame],
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fragt Trade Intelligence ob der Trade erlaubt ist (nur für BUY, kein Sim).

        Returns:
            dict mit keys: allowed (bool), reason (str), insight (str)
        """
        from core.simulation_adapter import SimulationAdapter

        if not self.trade_intelligence or isinstance(self.client, SimulationAdapter):
            return {"allowed": True, "reason": "TI not active", "insight": ""}

        features_dict = features.iloc[0].to_dict() if features is not None else {}
        should_trade, intel_reason = self.trade_intelligence.should_trade(
            symbol=symbol, confidence=pred, signal="BUY", features=features_dict
        )

        override_threshold = getattr(
            __import__("config"), "INTEL_OVERRIDE_CONFIDENCE", 0.85
        )
        if not should_trade:
            if pred >= override_threshold:
                self.log_thought(
                    f"[{symbol}] 🧠 OVERRIDE: High confidence ({pred:.2f}) "
                    f"– taking trade despite Intelligence ({intel_reason})"
                )
                return {"allowed": True, "reason": "override", "insight": intel_reason}
            else:
                self.log_thought(f"[{symbol}] 🧠 INTELLIGENCE BLOCKED: {intel_reason}")
                return {"allowed": False, "reason": intel_reason, "insight": ""}

        insight = self.trade_intelligence.get_entry_insight(symbol, pred, features_dict)
        self.log_thought(f"[{symbol}] 🧠 Trade Intelligence: {insight}")
        return {"allowed": True, "reason": "approved", "insight": insight}

    # ── 6. _check_portfolio_manager ──────────────────────────────────────────

    def _check_portfolio_manager(
        self,
        symbol: str,
        curr: float,
        rl_action: int,
        pred: float,
        features: Optional[pd.DataFrame],
    ) -> Dict[str, Any]:
        """Fragt Portfolio Manager ob neue Position geöffnet werden soll.

        Returns:
            dict mit keys: allowed (bool), reason (str), symbol_to_close (str | None)
        """
        from core.simulation_adapter import SimulationAdapter

        if not self.portfolio_manager or isinstance(self.client, SimulationAdapter):
            return {"allowed": True, "reason": "PM not active", "symbol_to_close": None}

        features_dict = features.iloc[0].to_dict() if features is not None else {}
        opportunity = self.portfolio_manager.score_opportunity(
            symbol=symbol,
            current_price=curr,
            rl_action=rl_action,
            model_confidence=pred,
            features=features_dict,
        )
        should_open, reasoning, symbol_to_close = (
            self.portfolio_manager.should_open_new_position(opportunity)
        )
        if not should_open:
            self.log_thought(f"[{symbol}] 📊 PORTFOLIO DEBATE: {reasoning}")
        elif symbol_to_close:
            self.log_thought(
                f"[{symbol}] 📊 PORTFOLIO SWAP: Closing {symbol_to_close} to open {symbol}"
            )
            self.log_thought(f"[{symbol}] 📊 Reasoning: {reasoning}")
        else:
            summary = self.portfolio_manager.get_portfolio_summary()
            self.log_thought(
                f"[{symbol}] 📊 Opening position ({summary['num_positions']}/{summary['max_positions']} slots)"
            )
        return {
            "allowed": should_open,
            "reason": reasoning,
            "symbol_to_close": symbol_to_close,
        }

    # ── 7. _apply_risk_filters ────────────────────────────────────────────────

    def _apply_risk_filters(
        self,
        symbol: str,
        signal: str,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fragt Risk Manager ob der Trade erlaubt ist.

        Returns:
            dict mit keys: allowed (bool), reason (str), mods (dict)
        """
        allowed, reason, mods = self.risk_manager.evaluate_new_trade(
            symbol, signal, market_data, 3.0
        )
        if not allowed:
            self.log_thought(
                f"[{symbol}] ❌ {signal} BLOCKED by Risk Manager: {reason}"
            )
        else:
            self.log_thought(
                f"[{symbol}] ✅ {signal} APPROVED by Risk Manager (modifiers: {mods})"
            )
        return {"allowed": allowed, "reason": reason, "mods": mods}

    # ── 8. _log_decision_trace ────────────────────────────────────────────────

    def _log_decision_trace(
        self,
        symbol: str,
        signal: str,
        pred: float,
        raw_rl_action: int,
        rl_action: int,
        conviction: float,
        curr: float,
        in_position: bool,
        qty: float,
        avg: float,
        triggered_exit: bool,
        features: Optional[pd.DataFrame],
        market_data: Dict[str, Any],
        suggested_qty: float,
    ) -> SignalEvent:
        """Erstellt DecisionContext + SignalEvent für Compliance/Cloud-Logging."""
        feat_row = (
            features.iloc[0] if features is not None and not features.empty else None
        )

        def _f(key, default):
            return (
                float(feat_row.get(key, default)) if feat_row is not None else default
            )

        context = DecisionContext(
            symbol=symbol,
            action=signal,
            lstm_prediction=float(pred),
            rl_raw_action=raw_rl_action,
            rl_stabilized_action=int(rl_action),
            conviction_score=float(conviction) if signal == "BUY" else 0.0,
            current_price=curr,
            vix_level=float(market_data.get("vix", 20.0)),
            market_regime=str(market_data.get("regime", "normal")),
            rsi_14=_f("rsi_14", 50.0),
            macd=_f("macd", 0.0),
            macd_signal=(
                float(feat_row.get("macd_signal", 0.0))
                if feat_row is not None and "macd_signal" in features.columns
                else 0.0
            ),
            adx_14=_f("adx_14", 25.0),
            bb_pct=_f("bb_pct", 0.5),
            volume_ratio=_f("volume_ratio", 1.0),
            volatility_20d=_f("volatility_20d", 0.02),
            atr_14d=_f("atr_14d", 0.0),
            in_position=in_position,
            position_qty=qty,
            position_avg_price=avg,
            unrealized_pnl=float(((curr - avg) * qty) if in_position else 0.0),
            unrealized_pnl_pct=float(
                ((curr - avg) / avg) if in_position and avg > 0 else 0.0
            ),
            triggered_by_stop=triggered_exit,
            model_version_id=str(self._rl_model_version),
        )

        from core.simulation_adapter import SimulationAdapter

        return SignalEvent(
            symbol=symbol,
            action=signal,
            decision_context=context,
            suggested_quantity=suggested_qty,
            is_simulation=isinstance(self.client, SimulationAdapter),
        )

    # ── _evaluate_for_symbol_impl (evaluate-only, NO execution) ──────────────

    async def _evaluate_for_symbol_impl(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ) -> Optional["SignalEvent"]:
        """Evaluate-only: Steps 1-3 → SignalEvent. No order execution.

        Art. 14 EU AI Act: Vote-phase must not trigger broker orders.
        Fix for #1876: Vote-Side-Effect.

        Returns:
            SignalEvent with action + decision_context, or None (abstention).
        """
        # ── 1. State aufbauen (existing Step 1) ──
        inputs = await self._gather_market_inputs(symbol, current_time, market_data)
        state, features, pred = inputs["state"], inputs["features"], inputs["pred"]

        if features is None or features.empty:
            return None  # Abstention — no features available

        # ── 2. Positionsstatus (existing Step 2, read-only) ──
        pos_info = self._check_position_state(symbol)
        in_pos = pos_info["in_position"]
        qty = pos_info["qty"]
        avg = pos_info["avg"]
        curr = ohlc_data.get("close", 0.0)

        # ── 3. Signal ableiten (existing Step 3) ──
        sig_info = await self._evaluate_signal(
            symbol, state, features, pred, in_pos, market_data
        )
        signal = sig_info["signal"]
        raw_rl_action = sig_info["raw_rl_action"]
        rl_action = sig_info["rl_action"]

        # ── Return SignalEvent (NO execution, NO state mutation) ──
        return self._log_decision_trace(
            symbol,
            signal,
            pred,
            raw_rl_action,
            rl_action,
            conviction=0.0,
            curr=curr,
            in_position=in_pos,
            qty=qty,
            avg=avg,
            triggered_exit=False,
            features=features,
            market_data=market_data,
            suggested_qty=0.0,
        )

    # ── _run_for_symbol_impl (orchestriert alle 8 Methoden) ──────────────────

    async def _run_for_symbol_impl(  # noqa: C901
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ):
        """Trading-Entscheidungslogik für ein Symbol (orchestriert alle 8 Teilschritte).

        Cyclomatic Complexity dieses Wrappers: ~8 (war vorher ~130).
        """
        from core.simulation_adapter import SimulationAdapter

        # ── 1. State aufbauen ────────────────────────────────────────
        inputs = await self._gather_market_inputs(symbol, current_time, market_data)
        state, features, pred = inputs["state"], inputs["features"], inputs["pred"]

        if features is None or features.empty:
            self._generate_thought(symbol, 0, None, 0.0, market_data)
            return

        # ── 2. Positionsstatus ────────────────────────────────────────
        pos_info = self._check_position_state(symbol)
        in_pos, qty, avg = pos_info["in_position"], pos_info["qty"], pos_info["avg"]
        curr = ohlc_data["close"]

        # ── 3. Signal ableiten ────────────────────────────────────────
        sig_info = await self._evaluate_signal(
            symbol, state, features, pred, in_pos, market_data
        )
        signal, raw_rl_action, rl_action = (
            sig_info["signal"],
            sig_info["raw_rl_action"],
            sig_info["rl_action"],
        )

        # ── 4. Smart Exit prüfen ──────────────────────────────────────
        exit_info = self._check_smart_exit(
            symbol, in_pos, qty, avg, curr, current_time, features, pred
        )
        triggered_exit = exit_info["triggered"]
        if triggered_exit:
            signal = "SELL"
        if signal == "SELL":
            self.high_water_marks.pop(symbol, None)
            self._entry_time.pop(symbol, None)
        elif not in_pos:
            self.high_water_marks.pop(symbol, None)
            self._entry_time.pop(symbol, None)

        # ── HOLD: Portfolio-Manager reset + return ────────────────────
        if signal == "HOLD" and not triggered_exit:
            if self.portfolio_manager and not isinstance(
                self.client, SimulationAdapter
            ):
                self.portfolio_manager.reset_sell_signals(symbol)
            conviction = 0.0
            return self._log_decision_trace(
                symbol,
                "HOLD",
                pred,
                raw_rl_action,
                rl_action,
                conviction,
                curr,
                in_pos,
                qty,
                avg,
                False,
                features,
                market_data,
                0.0,
            )

        if signal == "BUY" and in_pos:
            self.log_thought(f"[{symbol}] Already in position. Skipping BUY signal.")
            return

        # ── 5. Trade Intelligence (BUY) ───────────────────────────────
        if signal == "BUY" and not in_pos:
            ti_info = self._check_trade_intelligence(
                symbol, pred, features, market_data
            )
            if not ti_info["allowed"]:
                return self._log_decision_trace(
                    symbol,
                    "HOLD",
                    pred,
                    raw_rl_action,
                    rl_action,
                    0.0,
                    curr,
                    in_pos,
                    qty,
                    avg,
                    False,
                    features,
                    market_data,
                    0.0,
                )

        # ── 6. Portfolio Manager (BUY) ────────────────────────────────
        symbol_to_close = None
        if signal == "BUY" and not in_pos:
            pm_info = self._check_portfolio_manager(
                symbol, curr, rl_action, pred, features
            )
            if not pm_info["allowed"]:
                return self._log_decision_trace(
                    symbol,
                    "HOLD",
                    pred,
                    raw_rl_action,
                    rl_action,
                    0.0,
                    curr,
                    in_pos,
                    qty,
                    avg,
                    False,
                    features,
                    market_data,
                    0.0,
                )
            symbol_to_close = pm_info["symbol_to_close"]

        # ── SELL Consecutive-Signal-Count ─────────────────────────────
        if (
            signal == "SELL"
            and in_pos
            and self.portfolio_manager
            and not isinstance(self.client, SimulationAdapter)
        ):
            sell_count = self.portfolio_manager.record_sell_signal(symbol)
            logging.debug("[%s] Consecutive SELL signals: %s/5", symbol, sell_count)
        if (
            signal == "BUY"
            and self.portfolio_manager
            and not isinstance(self.client, SimulationAdapter)
        ):
            self.portfolio_manager.reset_sell_signals(symbol)

        # ── 7. Risk Filter ────────────────────────────────────────────
        risk_info = self._apply_risk_filters(symbol, signal, market_data)
        if not risk_info["allowed"]:
            return self._log_decision_trace(
                symbol,
                "HOLD",
                pred,
                raw_rl_action,
                rl_action,
                0.0,
                curr,
                in_pos,
                qty,
                avg,
                False,
                features,
                market_data,
                0.0,
            )
        mods = risk_info["mods"]

        # ── 8a. Swap Execution (Pre-Fetch) ────────────────────────────
        if (
            signal == "BUY"
            and not in_pos
            and symbol_to_close
            and self.portfolio_manager
        ):
            try:
                old_pos = self.client.get_open_position(symbol_to_close)
                if old_pos:
                    old_qty = (
                        float(old_pos.qty)
                        if hasattr(old_pos, "qty")
                        else float(old_pos.get("qty", 0))
                    )
                    old_price = (
                        float(old_pos.current_price)
                        if hasattr(old_pos, "current_price")
                        else float(old_pos.get("current_price", 0))
                    )
                    if old_qty > 0:
                        self.log_thought(
                            f"[{symbol_to_close}] 🔄 CLOSING for portfolio swap"
                        )
                        await self._submit_order_safe(
                            symbol_to_close, old_qty, "sell", current_price=old_price
                        )
                        self.portfolio_manager.record_trade(symbol_to_close, "sell")
                        if self.trade_intelligence:
                            self.trade_intelligence.record_exit(
                                symbol_to_close, old_price, exit_reason="swap"
                            )
                        await asyncio.sleep(1.0)
            except Exception as e:
                is_404 = False
                if isinstance(e, APIError) and (
                    e.status_code == 404 or getattr(e, "code", None) == 40410000
                ):
                    is_404 = True

                if is_404:
                    self.log_thought(
                        f"[{symbol_to_close}] ℹ️ No position open at broker to close for swap."
                    )
                else:
                    self.log_thought(
                        f"[{symbol_to_close}] ⚠️ Critical swap API failure: {e}. Gracefully skipping swap."
                    )
                    # Instead of raising e, we gracefully abort the current strategy run for this symbol.
                    return

        # ── Buying Power + Position Size ──────────────────────────────
        size = 0.0
        conviction = 0.0
        if signal == "BUY" and not in_pos:
            try:
                account = self.client.get_account()
                dt_bp = float(getattr(account, "daytrading_buying_power", None) or 0)
                reg_bp = float(getattr(account, "buying_power", None) or 0)
                reg_cash = float(getattr(account, "cash", 0) or 0)
                if self.portfolio_manager and not isinstance(
                    self.client, SimulationAdapter
                ):
                    live_equity = float(account.equity or 0)
                    if live_equity > 0:
                        self.portfolio_manager.update_total_capital(live_equity)
                if reg_cash <= 0 and (reg_bp or 0) <= 0:
                    self.log_thought(f"[{symbol}] ⚠️ BLOCKED - Invalid account state.")
                    return
                import config as _cfg

                use_cash_only = getattr(_cfg, "USE_CASH_ONLY", True)
                cash = (
                    reg_cash
                    if use_cash_only
                    else (reg_bp if dt_bp == 0 else max(dt_bp, reg_bp))
                )
                if cash == 0:
                    cash = reg_cash
                if cash <= 0:
                    self.log_thought(
                        f"[{symbol}] ⚠️ BLOCKED - No buying power available."
                    )
                    return
            except Exception as e:
                logging.warning("[%s] Could not get account: %s", symbol, e)
                cash = 0.0

            conviction = self._calculate_conviction_score(
                features.iloc[0] if features is not None else None, pred, market_data
            )
            size = self.risk_manager.calculate_position_size(
                mods.get("sl_multiplier", 3.0),
                (
                    features.iloc[0].get("atr_14d", curr * 0.05)
                    if features is not None
                    else curr * 0.05
                ),
                "high",
                mods.get("size_scaler", 1.0),
                market_data,
                len(self.symbols),
                curr,
                cash,
                allow_fractional=True,
                conviction_score=conviction,
            )

            if size == 0:
                self.log_thought(
                    f"[{symbol}] ⚠️ Position size is 0 – insufficient cash!"
                )
                return

        # ── 8c. Anti-churn check ──────────────────────────────────────
        if signal == "SELL" and in_pos and self.portfolio_manager:
            can_sell, reason = self.portfolio_manager.can_sell_position(symbol)
            if not can_sell:
                self.log_thought(f"[{symbol}] 📊 SELL blocked by anti-churn: {reason}")
                signal = "HOLD"

        # ── 8b. Order ausführen (BUY) ─────────────────────────────────
        if signal == "BUY" and not in_pos and size > 0:
            order_cost = size * curr
            self.log_thought(
                f"[{symbol}] 💰 EXECUTING BUY ORDER: {size:.6f} shares @ ${curr:.2f}"
            )
            self.high_water_marks[symbol] = curr
            order_success = await self._submit_order_safe(
                symbol, size, "buy", expected_cost=order_cost, current_price=curr
            )

            if order_success:
                self._entry_time[symbol] = current_time
                if self.portfolio_manager:
                    self.portfolio_manager.record_trade(symbol, "buy")
                    self.portfolio_manager.update_position_conviction(
                        symbol, conviction
                    )
                from core.simulation_adapter import SimulationAdapter

                if self.trade_intelligence and not isinstance(
                    self.client, SimulationAdapter
                ):
                    features_dict = (
                        features.iloc[0].to_dict() if features is not None else {}
                    )
                    self.trade_intelligence.record_entry(
                        symbol=symbol,
                        entry_price=curr,
                        qty=size,
                        confidence=pred,
                        features=features_dict,
                        market_data=market_data,
                    )

        elif signal == "SELL" and in_pos:
            self.log_thought(
                f"[{symbol}] 💸 EXECUTING SELL ORDER: {qty} shares @ ${curr:.2f}"
            )
            self.high_water_marks.pop(symbol, None)
            self._entry_time.pop(symbol, None)
            order_success = await self._submit_order_safe(
                symbol, qty, "sell", current_price=curr
            )
            if order_success:
                if self.portfolio_manager:
                    self.portfolio_manager.record_trade(symbol, "sell")
                    self.portfolio_manager.clear_sell_signals_after_sale(symbol)
                from core.simulation_adapter import SimulationAdapter

                if self.trade_intelligence and not isinstance(
                    self.client, SimulationAdapter
                ):
                    exit_reason = (
                        "trailing_stop"
                        if triggered_exit and "TRAILING" in str(triggered_exit)
                        else "stop_loss" if triggered_exit else "signal"
                    )
                    self.trade_intelligence.record_exit(
                        symbol=symbol, exit_price=curr, exit_reason=exit_reason
                    )

        # ── 8. Decision Trace ─────────────────────────────────────────
        suggested_qty = (
            float(size)
            if signal == "BUY" and not in_pos
            else float(qty) if signal == "SELL" and in_pos else 0.0
        )
        return self._log_decision_trace(
            symbol,
            signal,
            pred,
            raw_rl_action,
            rl_action,
            conviction,
            curr,
            in_pos,
            qty,
            avg,
            triggered_exit,
            features,
            market_data,
            suggested_qty,
        )
