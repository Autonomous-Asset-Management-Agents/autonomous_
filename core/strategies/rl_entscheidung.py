"""Die Schritte von ``RLExecutionMixin._run_for_symbol_impl`` (#4088, ARC-E6 G-7b).

``_run_for_symbol_impl`` (``rl_execution.py``) war ein Rumpf von 430 Zeilen. Er ist hier
wortgleich in elf Schritte zerlegt, je ein Block des alten Rumpfs (Plan §1). Der Dirigent ruft
sie der Reihe nach und gibt das erste Ergebnis zurueck, das nicht ``_WEITER`` ist — ein
Trace-Dict oder ``None`` (beides echte Ergebnisse des alten Rumpfs).

Werte, die zwischen Bloecken fliessen, liegen in ``SymbolEntscheidung`` (``z``). Gegen den
alten Rumpf unterscheiden sich die Schritte nur in ``z.<feld>``, ``return _WEITER`` am
Blockende und dem lokalen ``SimulationAdapter``-Import, den jeder Schritt wiederholt, der ihn
braucht. Das beweist ``tests/unit/test_rl_entscheidung_wortgleich.py`` per AST gegen die
eingecheckte Kopie ``tests/fixtures/rl_run_for_symbol_vor_g7b.py.txt``.

Die Helfer (``_gather_market_inputs``, ``_check_exit``, ``_log_decision_trace``, …) bleiben in
``rl_execution.py``; die Schritte rufen sie ueber ``self``.

Plan: ``docs/4088-g7b-rl-execution-schritte/implementation_plan.md``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from alpaca.common.exceptions import APIError

from core.risk_manager import effective_free_slots


def ist_schutz_exit(triggered_exit, exit_info) -> bool:
    """#3380/#3466: Ist dieser SELL ein Schutz-Exit, der einen Halt passieren darf?

    Genau dann, wenn der Smart-Exit ausgeloest hat **und** die Stufe ``risk`` traegt —
    harter Stop, Trailing-Stop, Loss-Cut (``core/intelligent_exit.py``). Dieselbe
    Unterscheidung, mit der #3180 Meinungs-Ausstiege vom Konsens-Gate ausnimmt.

    Eng gefasst: Ein Modell-SELL, eine Gewinnmitnahme (``opinion``) und ein Ausstieg ohne
    Stufe sind keine Schutz-Exits. Die Ausfallrichtung ist „blockieren".
    """
    if not triggered_exit or not isinstance(exit_info, dict):
        return False
    return exit_info.get("tier") == "risk"


@dataclass
class SymbolEntscheidung:
    """Zustand zwischen den Schritten — die Werte, die im alten Rumpf von Block zu Block
    flossen (Plan §1). Jedes Feld setzt sein Block, bevor ein spaeterer es liest; die
    Defaults sind nur die Startbelegung."""

    symbol: str
    ohlc_data: Dict[str, float]
    market_data: Dict[str, Any]
    current_time: datetime
    features: Any = None
    pred: Any = None
    raw_rl_action: Any = None
    rl_action: Any = None
    in_pos: bool = False
    qty: float = 0.0
    avg: float = 0.0
    curr: float = 0.0
    exit_info: Optional[Dict[str, Any]] = None
    triggered_exit: Any = False
    signal: str = "HOLD"
    symbol_to_close: Optional[str] = None
    mods: Optional[Dict[str, Any]] = None
    size: float = 0.0
    conviction: float = 0.0


#: Ein Schritt ist fertig, der Dirigent ruft den naechsten.
_WEITER = object()


class RLEntscheidungsSchritte:
    """Mixin mit den elf Schritten; ``RLExecutionMixin`` erbt sie und stellt die Helfer."""

    async def _schritt_lage(self, z: SymbolEntscheidung):
        """Eingaenge, Position, Signal und Exit-Pruefung."""
        # ── 1. State aufbauen ────────────────────────────────────────
        inputs = await self._gather_market_inputs(
            z.symbol, z.current_time, z.market_data
        )
        state, z.features, z.pred = inputs["state"], inputs["features"], inputs["pred"]

        if z.features is None or z.features.empty:
            self._generate_thought(z.symbol, 0, None, 0.0, z.market_data)
            return

        # ── 2. Positionsstatus ────────────────────────────────────────
        pos_info = self._check_position_state(z.symbol)
        z.in_pos, z.qty, z.avg = (
            pos_info["in_position"],
            pos_info["qty"],
            pos_info["avg"],
        )
        z.curr = z.ohlc_data["close"]

        # ── 3. Signal ableiten ────────────────────────────────────────
        sig_info = await self._evaluate_signal(
            z.symbol, state, z.features, z.pred, z.in_pos, z.market_data
        )
        z.signal, z.raw_rl_action, z.rl_action = (
            sig_info["signal"],
            sig_info["raw_rl_action"],
            sig_info["rl_action"],
        )

        # -- 4. Exit-Autoritaet pruefen (#3632: ein Regelwerk) --------------------
        z.exit_info = self._check_exit(
            z.symbol, z.in_pos, z.qty, z.avg, z.curr, z.current_time, z.features, z.pred
        )
        z.triggered_exit = z.exit_info["triggered"]
        if z.triggered_exit:
            z.signal = "SELL"
        return _WEITER

    async def _schritt_ausstieg_halten(self, z: SymbolEntscheidung):
        """#3180 Consensus-Retention, HWM und Einstiegszeit aufraeumen."""
        # ── #3180 Consensus Retention Gate ────────────────────────────
        # An OPINION exit — the model SELL (LSTM pred<-0.35 bypassing the round table)
        # or an intelligent-exit OPINION tier (momentum-fade / weighted-composite) — is
        # SUPPRESSED when the live round-table blend-consensus still rates the held name
        # highly. RISK tiers (hard stop / trailing / loss-cut, and the legacy fallback)
        # are NEVER gated. Default OFF (threshold<=0) ⇒ byte-identical; fail-open.
        if z.signal == "SELL" and z.in_pos:
            from core.consensus_retention import consensus_retention_veto

            _exit_kind = (
                z.exit_info.get("tier", "risk") if z.triggered_exit else "model"
            )
            if consensus_retention_veto(
                z.symbol, _exit_kind, getattr(self, "portfolio_manager", None)
            ):
                self.log_thought(
                    f"[{z.symbol}] 🛑 SELL RETAINED — live round-table consensus still "
                    f"rates the name (#3180 gate, kind={_exit_kind})"
                )
                z.signal = "HOLD"
                z.triggered_exit = False

        if z.signal == "SELL":
            self.high_water_marks.pop(z.symbol, None)
            self._entry_time.pop(z.symbol, None)
        elif not z.in_pos:
            self.high_water_marks.pop(z.symbol, None)
            self._entry_time.pop(z.symbol, None)
        return _WEITER

    async def _schritt_halten_abschliessen(self, z: SymbolEntscheidung):
        """HOLD abschliessen, BUY bei offener Position ueberspringen."""
        from core.simulation_adapter import SimulationAdapter

        # ── HOLD: Portfolio-Manager reset + return ────────────────────
        if z.signal == "HOLD" and not z.triggered_exit:
            if self.portfolio_manager and not isinstance(
                self.client, SimulationAdapter
            ):
                self.portfolio_manager.reset_sell_signals(z.symbol)
            z.conviction = 0.0
            return self._log_decision_trace(
                z.symbol,
                "HOLD",
                z.pred,
                z.raw_rl_action,
                z.rl_action,
                z.conviction,
                z.curr,
                z.in_pos,
                z.qty,
                z.avg,
                False,
                z.features,
                z.market_data,
                0.0,
            )

        if z.signal == "BUY" and z.in_pos:
            self.log_thought(f"[{z.symbol}] Already in position. Skipping BUY signal.")
            return
        return _WEITER

    async def _schritt_kauf_freigabe(self, z: SymbolEntscheidung):
        """5. Trade Intelligence und 6. Portfolio-Manager, nur BUY."""
        # ── 5. Trade Intelligence (BUY) ───────────────────────────────
        if z.signal == "BUY" and not z.in_pos:
            ti_info = self._check_trade_intelligence(
                z.symbol, z.pred, z.features, z.market_data
            )
            if not ti_info["allowed"]:
                return self._log_decision_trace(
                    z.symbol,
                    "HOLD",
                    z.pred,
                    z.raw_rl_action,
                    z.rl_action,
                    0.0,
                    z.curr,
                    z.in_pos,
                    z.qty,
                    z.avg,
                    False,
                    z.features,
                    z.market_data,
                    0.0,
                )

        # ── 6. Portfolio Manager (BUY) ────────────────────────────────
        z.symbol_to_close = None
        if z.signal == "BUY" and not z.in_pos:
            pm_info = self._check_portfolio_manager(
                z.symbol, z.curr, z.rl_action, z.pred, z.features
            )
            if not pm_info["allowed"]:
                return self._log_decision_trace(
                    z.symbol,
                    "HOLD",
                    z.pred,
                    z.raw_rl_action,
                    z.rl_action,
                    0.0,
                    z.curr,
                    z.in_pos,
                    z.qty,
                    z.avg,
                    False,
                    z.features,
                    z.market_data,
                    0.0,
                )
            z.symbol_to_close = pm_info["symbol_to_close"]
        return _WEITER

    async def _schritt_verkaufszaehler(self, z: SymbolEntscheidung):
        """SELL-Zaehler fuehren bzw. zuruecksetzen."""
        from core.simulation_adapter import SimulationAdapter

        # ── SELL Consecutive-Signal-Count ─────────────────────────────
        if (
            z.signal == "SELL"
            and z.in_pos
            and self.portfolio_manager
            and not isinstance(self.client, SimulationAdapter)
        ):
            sell_count = self.portfolio_manager.record_sell_signal(z.symbol)
            logging.debug("[%s] Consecutive SELL signals: %s/5", z.symbol, sell_count)
        if (
            z.signal == "BUY"
            and self.portfolio_manager
            and not isinstance(self.client, SimulationAdapter)
        ):
            self.portfolio_manager.reset_sell_signals(z.symbol)
        return _WEITER

    async def _schritt_risikofilter(self, z: SymbolEntscheidung):
        """7. Risk Filter."""
        # ── 7. Risk Filter ────────────────────────────────────────────
        risk_info = self._apply_risk_filters(z.symbol, z.signal, z.market_data)
        if not risk_info["allowed"]:
            return self._log_decision_trace(
                z.symbol,
                "HOLD",
                z.pred,
                z.raw_rl_action,
                z.rl_action,
                0.0,
                z.curr,
                z.in_pos,
                z.qty,
                z.avg,
                False,
                z.features,
                z.market_data,
                0.0,
            )
        z.mods = risk_info["mods"]
        return _WEITER

    async def _schritt_tausch(self, z: SymbolEntscheidung):
        """8a. Swap-Ausfuehrung, Pre-Fetch."""
        # ── 8a. Swap Execution (Pre-Fetch) ────────────────────────────
        if (
            z.signal == "BUY"
            and not z.in_pos
            and z.symbol_to_close
            and self.portfolio_manager
        ):
            try:
                old_pos = self.client.get_open_position(z.symbol_to_close)
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
                            f"[{z.symbol_to_close}] 🔄 CLOSING for portfolio swap"
                        )
                        await self._submit_order_safe(
                            z.symbol_to_close,
                            old_qty,
                            "sell",
                            current_price=old_price,
                            # ADR-C04-EXIT: old_qty IS the held position (swap-close
                            # path), so this is a risk-reducing exit. The guardian's
                            # predicate is fail-closed — omit this and the daily cap
                            # blocks the close.
                            held_qty=old_qty,
                        )
                        self.portfolio_manager.record_trade(z.symbol_to_close, "sell")
                        if self.trade_intelligence:
                            self.trade_intelligence.record_exit(
                                z.symbol_to_close, old_price, exit_reason="swap"
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
                        f"[{z.symbol_to_close}] ℹ️ No position open at broker to close for swap."
                    )
                else:
                    self.log_thought(
                        f"[{z.symbol_to_close}] ⚠️ Critical swap API failure: {e}. Gracefully skipping swap."
                    )
                    # Instead of raising e, we gracefully abort the current strategy run for this symbol.
                    return
        return _WEITER

    async def _schritt_groesse(self, z: SymbolEntscheidung):
        """Kaufkraft und Positionsgroesse."""
        from core.simulation_adapter import SimulationAdapter

        # ── Buying Power + Position Size ──────────────────────────────
        z.size = 0.0
        z.conviction = 0.0
        if z.signal == "BUY" and not z.in_pos:
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
                    self.log_thought(f"[{z.symbol}] ⚠️ BLOCKED - Invalid account state.")
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
                        f"[{z.symbol}] ⚠️ BLOCKED - No buying power available."
                    )
                    return
            except Exception as e:
                logging.warning("[%s] Could not get account: %s", z.symbol, e)
                cash = 0.0

            z.conviction = self._calculate_conviction_score(
                z.features.iloc[0] if z.features is not None else None,
                z.pred,
                z.market_data,
            )
            z.size = self.risk_manager.calculate_position_size(
                z.mods.get("sl_multiplier", 3.0),
                (
                    z.features.iloc[0].get("atr_14d", z.curr * 0.05)
                    if z.features is not None
                    else z.curr * 0.05
                ),
                "high",
                z.mods.get("size_scaler", 1.0),
                z.market_data,
                # #2153: book-cap slots, not the strategy's scan-universe size
                # (len(self.symbols)) — the old divisor under-sized every order.
                # O3 (CASH_AWARE_SLOTS_ENABLED): divide by the REMAINING free slots
                # (cap − held), not the fixed cap, so a mostly-invested round-table book
                # funds proper-sized buys. RLStrategy is the DEFAULT active strategy
                # (ACTIVE_STRATEGY="RLAgent", round-table host) → this is a LIVE sizing
                # path. Held = the strategy's own PM positions (no new broker call);
                # None/empty PM → 0 → cap (byte-identical); unconfirmed/stale refresh → cap.
                effective_free_slots(
                    len(
                        getattr(
                            getattr(self, "portfolio_manager", None),
                            "_position_scores",
                            {},
                        )
                        or {}
                    ),
                    positions_confirmed=getattr(
                        getattr(self, "portfolio_manager", None),
                        "_last_refresh_ok",
                        True,
                    ),
                ),
                z.curr,
                cash,
                allow_fractional=True,
                conviction_score=z.conviction,
            )

            if z.size == 0:
                self.log_thought(
                    f"[{z.symbol}] ⚠️ Position size is 0 – insufficient cash!"
                )
                return
        return _WEITER

    async def _schritt_anti_churn(self, z: SymbolEntscheidung):
        """8c. Anti-Churn, nur SELL."""
        # ── 8c. Anti-churn check ──────────────────────────────────────
        if z.signal == "SELL" and z.in_pos and self.portfolio_manager:
            can_sell, reason = self.portfolio_manager.can_sell_position(z.symbol)
            if not can_sell:
                self.log_thought(
                    f"[{z.symbol}] 📊 SELL blocked by anti-churn: {reason}"
                )
                z.signal = "HOLD"
        return _WEITER

    async def _schritt_kauf_absenden(self, z: SymbolEntscheidung):
        """8b. Order ausfuehren."""
        # ── 8b. Order ausführen (BUY) ─────────────────────────────────
        if z.signal == "BUY" and not z.in_pos and z.size > 0:
            order_cost = z.size * z.curr
            self.log_thought(
                f"[{z.symbol}] 💰 EXECUTING BUY ORDER: {z.size:.6f} shares @ ${z.curr:.2f}"
            )
            self.high_water_marks[z.symbol] = z.curr
            order_success = await self._submit_order_safe(
                z.symbol, z.size, "buy", expected_cost=order_cost, current_price=z.curr
            )

            if order_success:
                self._entry_time[z.symbol] = z.current_time
                if self.portfolio_manager:
                    self.portfolio_manager.record_trade(z.symbol, "buy")
                    self.portfolio_manager.update_position_conviction(
                        z.symbol, z.conviction
                    )
                from core.simulation_adapter import SimulationAdapter

                if self.trade_intelligence and not isinstance(
                    self.client, SimulationAdapter
                ):
                    features_dict = (
                        z.features.iloc[0].to_dict() if z.features is not None else {}
                    )
                    self.trade_intelligence.record_entry(
                        symbol=z.symbol,
                        entry_price=z.curr,
                        qty=z.size,
                        confidence=z.pred,
                        features=features_dict,
                        market_data=z.market_data,
                    )

        elif z.signal == "SELL" and z.in_pos:
            self.log_thought(
                f"[{z.symbol}] 💸 EXECUTING SELL ORDER: {z.qty} shares @ ${z.curr:.2f}"
            )
            self.high_water_marks.pop(z.symbol, None)
            self._entry_time.pop(z.symbol, None)
            order_success = await self._submit_order_safe(
                z.symbol,
                z.qty,
                "sell",
                current_price=z.curr,
                # ADR-C04-EXIT: reached only under `elif signal == "SELL" and in_pos`,
                # so qty IS the held position. This is the DEFAULT strategy's stop-loss
                # path (ACTIVE_STRATEGY="RLAgent") — without held_qty the exemption never
                # fires here and the stop stays blocked by the daily cap, which is the
                # whole defect this fixes.
                held_qty=z.qty,
                # #3380/#3466: ein Risiko-Ausstieg passiert auch einen Halt.
                is_protective_exit=ist_schutz_exit(z.triggered_exit, z.exit_info),
            )
            if order_success:
                if self.portfolio_manager:
                    self.portfolio_manager.record_trade(z.symbol, "sell")
                    self.portfolio_manager.clear_sell_signals_after_sale(z.symbol)
                from core.simulation_adapter import SimulationAdapter

                if self.trade_intelligence and not isinstance(
                    self.client, SimulationAdapter
                ):
                    exit_reason = (
                        "trailing_stop"
                        if z.triggered_exit and "TRAILING" in str(z.triggered_exit)
                        else "stop_loss" if z.triggered_exit else "signal"
                    )
                    self.trade_intelligence.record_exit(
                        symbol=z.symbol, exit_price=z.curr, exit_reason=exit_reason
                    )
        return _WEITER

    async def _schritt_protokoll(self, z: SymbolEntscheidung):
        """8. Decision Trace."""
        # ── 8. Decision Trace ─────────────────────────────────────────
        suggested_qty = (
            float(z.size)
            if z.signal == "BUY" and not z.in_pos
            else float(z.qty) if z.signal == "SELL" and z.in_pos else 0.0
        )
        return self._log_decision_trace(
            z.symbol,
            z.signal,
            z.pred,
            z.raw_rl_action,
            z.rl_action,
            z.conviction,
            z.curr,
            z.in_pos,
            z.qty,
            z.avg,
            z.triggered_exit,
            z.features,
            z.market_data,
            suggested_qty,
        )
