# core/strategies/base.py
# Epic 1.7 / PR-B — BaseStrategy mit _submit_order_safe
# Enthält: BaseStrategy (ABC), _get_trade_context, log_thought, _submit_order_safe
# _submit_order_safe wird aus RLStrategy und LSTMDynamicStrategy DRY konsolidiert.

import asyncio
import inspect
import logging
import math
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import config
from core.ai_rules import AILearnedRules
from core.cloud_logger import DecisionContext
from core.data_provider import HistoricalDataProvider
from core.risk_manager import RiskManager


class BaseStrategy(ABC):
    """Abstrakte Basisklasse für alle Trading-Strategien.

    Enthält gemeinsame Infrastruktur:
    - Initialisierung aller geteilten Abhängigkeiten
    - log_thought: Erklärungstext via Callback oder Logger
    - _get_trade_context: Kontextdikt für Entscheidungs-Trace
    - _submit_order_safe: Auftragseinreichung mit Compliance, PDT, Buying-Power-Check
    """

    def __init__(
        self,
        client: Any,
        symbols: List[str],
        running_event: Optional[Any],
        total_capital: float,
        risk_manager: RiskManager,
        data_provider: HistoricalDataProvider,
        thought_callback: Optional[Callable[[str], None]] = None,
        compliance_guardian: Any = None,
    ):
        self.client = client
        self.symbols = symbols
        self.running_event = running_event
        self.total_capital = total_capital
        self.risk_manager = risk_manager
        self.data_provider = data_provider
        self.ai_rules = AILearnedRules()
        self.strategy_name = self.__class__.__name__
        self.current_recommendation_confidence = "high"
        self.thought_callback = thought_callback
        self.compliance_guardian = compliance_guardian
        self.last_thought_time: Dict[str, datetime] = {}
        # Order tracking (used by _submit_order_safe)
        self._pending_orders: Dict[str, str] = {}
        self._last_order_time: Dict[str, float] = {}
        self._last_gtc_buy_submit_time: float = 0.0

    def log_thought(self, message: str) -> None:
        """Sendet einen Erklärungstext an den konfigurierten Callback oder Logger."""
        if self.thought_callback:
            self.thought_callback(message)
        else:
            logging.info("[THOUGHT] %s", message)

    @abstractmethod
    async def run_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ):
        pass

    def _get_trade_context(
        self, symbol: str, indicators: Dict, market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Erstellt einen Kontext-Dict für MiFID-Reasoning Trace."""
        return {
            "strategy": self.strategy_name,
            "regime": market_data.get("regime", "Unknown"),
            "vix": market_data.get("vix"),
            "news_sentiment": market_data.get("latest_news_sentiment"),
            "indicators": indicators,
            "recommendation_confidence": self.current_recommendation_confidence,
        }

    async def _submit_order_safe(
        self,
        symbol: str,
        qty: float,
        side: str,
        expected_cost: float = 0.0,
        current_price: Optional[float] = None,
    ) -> bool:
        """Sichere Order-Einreichung mit vollständiger Vorprüfung.

        Guards (nur im Live-Mode, nicht in Simulation):
        1. ComplianceGuardian check_order + check_trade
        2. Market-Closed-Check via get_clock
        3. Order-Deduplication (keine doppelten Pending-Orders)
        4. Buying-Power-Check (Cash-Only-Mode respektiert)
        5. PDT-Handling (GTC für erschöpfte Day-Trading-BP, 90s-Cooldown)

        Args:
            symbol:        Ticker-Symbol
            qty:           Order-Anzahl (kann fraktional sein)
            side:          "buy" oder "sell"
            expected_cost: Erwartete Kosten in USD (für Buying-Power-Check)

        Returns:
            True bei Erfolg, False bei blockiertem/fehlerhaftem Order.
        """
        try:
            if not hasattr(self.client, "submit_order"):
                self.log_thought(f"[{symbol}] ❌ No submit_order method available!")
                return False

            is_simulation = (
                hasattr(self.client, "simulation_data")
                or "Simulation" in type(self.client).__name__
            )

            # ── 1. ComplianceGuardian ─────────────────────────────────────────
            if not is_simulation and self.compliance_guardian:
                compliance_order = {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "price": expected_cost / qty if qty > 0 else 0,
                    "strategy_id": self.strategy_name,
                    "timestamp": time.time(),
                }
                if not self.compliance_guardian.check_order(compliance_order):
                    self.log_thought(
                        f"[{symbol}] 🛡️ BLOCKED by ComplianceGuardian (order check)"
                    )
                    return False
                if not self.compliance_guardian.check_trade(compliance_order):
                    self.log_thought(
                        f"[{symbol}] 🛡️ BLOCKED by ComplianceGuardian (daily trade limit)"
                    )
                    return False

            # ── 2. Market-Closed ──────────────────────────────────────────────
            # TODO(MOD-1): migrate config import to RuntimeConfigState
            from config import BYPASS_MARKET_HOURS

            if (
                not is_simulation
                and not BYPASS_MARKET_HOURS
                and hasattr(self.client, "get_clock")
            ):
                try:
                    clock = self.client.get_clock()
                    if not getattr(clock, "is_open", True):
                        self.log_thought(
                            f"[{symbol}] ⏸️ Market closed – skipping {side.upper()}"
                        )
                        return False
                except Exception as e:
                    logging.warning("[%s] Could not check market clock: %s", symbol, e)

            # ── 3. Order-Deduplication ────────────────────────────────────────
            if not is_simulation:
                try:
                    if hasattr(self.client, "get_orders"):
                        from alpaca.trading.enums import QueryOrderStatus
                        from alpaca.trading.requests import GetOrdersRequest

                        req = GetOrdersRequest(
                            status=QueryOrderStatus.OPEN, symbols=[symbol]
                        )
                        open_orders = self.client.get_orders(req)
                    else:
                        open_orders = self.client.list_orders(status="open")

                    for order in open_orders:
                        # order.side is an Enum in alpaca-py -> str(order.side).lower()
                        if order.symbol == symbol and side in str(order.side).lower():
                            self.log_thought(
                                f"[{symbol}] ⏳ Pending {side.upper()} order already exists"
                            )
                            return False
                except Exception as e:
                    logging.warning(
                        "[%s] Could not check pending orders: %s", symbol, e
                    )

            # ── 4. Buying Power (nur BUY, Live) ───────────────────────────────
            time_in_force = "day"
            use_fractional = True

            if side == "buy" and expected_cost > 0 and not is_simulation:
                try:
                    account = self.client.get_account()
                    dt_bp = float(
                        getattr(account, "daytrading_buying_power", None) or 0
                    )
                    reg_bp = float(getattr(account, "buying_power", None) or 0)
                    reg_cash = float(getattr(account, "cash", 0) or 0)
                    is_pdt = getattr(account, "pattern_day_trader", False)

                    if reg_cash <= 0 and (reg_bp or 0) <= 0:
                        self.log_thought(
                            f"[{symbol}] ⚠️ BLOCKED - Invalid account state (cash=${reg_cash:.2f})."
                        )
                        return False

                    # ── 5. PDT Handling ───────────────────────────────────────
                    if is_pdt and dt_bp == 0:
                        time_in_force = "gtc"
                        use_fractional = False
                        now_ts = time.time()
                        if now_ts - self._last_gtc_buy_submit_time < 90:
                            self.log_thought(
                                f"[{symbol}] ⏳ PDT: one GTC buy per 90s. Skipping."
                            )
                            return False
                        # dt_bp==0 → Broker würde ablehnen; Slot reservieren und skip
                        self._last_gtc_buy_submit_time = time.time()
                        self.log_thought(
                            f"[{symbol}] ⏭️ PDT: Day trading BP $0 – skipping GTC submit."
                        )
                        return False

                    use_cash_only = getattr(config, "USE_CASH_ONLY", True)
                    if use_cash_only:
                        cash_available = reg_cash - 500  # $500 Buffer
                        if expected_cost > max(0, cash_available):
                            self.log_thought(
                                f"[{symbol}] ⚠️ SKIPPED - Order ${expected_cost:.2f} exceeds cash (${reg_cash:.2f})."
                            )
                            return False
                        current_bp = reg_cash
                    else:
                        current_bp = reg_bp if dt_bp == 0 else max(dt_bp, reg_bp)
                        if current_bp == 0:
                            current_bp = reg_cash

                    min_required = expected_cost + 500
                    if current_bp < min_required:
                        self.log_thought(
                            f"[{symbol}] ⚠️ SKIPPED - Insufficient buying power: ${current_bp:.2f} < ${min_required:.2f}"
                        )
                        return False

                except Exception as e:
                    self.log_thought(
                        f"[{symbol}] ⚠️ Could not verify buying power, skipping: {e}"
                    )
                    return False

            # ── Quantity rounding (GTC benötigt ganze Anteile) ────────────────
            order_qty = qty
            if not use_fractional:
                order_qty = math.floor(qty)
                if order_qty < 1:
                    self.log_thought(
                        f"[{symbol}] ⚠️ SKIPPED - Less than 1 whole share ({qty:.2f})"
                    )
                    return False

            # ── PDT GTC Slot reservieren ──────────────────────────────────────
            if not is_simulation and side == "buy" and time_in_force == "gtc":
                self._last_gtc_buy_submit_time = time.time()

            # ── Order abschicken ──────────────────────────────────────────────
            submit_method = self.client.submit_order

            if inspect.iscoroutinefunction(submit_method):
                await submit_method(symbol, order_qty, side)
                self.log_thought(f"[{symbol}] ✅ Order submitted (async)")
            else:
                if is_simulation:
                    self.client.submit_order(symbol=symbol, qty=order_qty, side=side)
                    self.log_thought(f"[{symbol}] ✅ Order submitted (simulation)")
                else:
                    try:
                        from core.kill_switch import kill_switch

                        kill_switch.check_halt()
                    except ImportError:
                        pass

                    start_meas = time.perf_counter()
                    loop = asyncio.get_event_loop()

                    def _do_submit():
                        import inspect

                        sig = inspect.signature(self.client.submit_order)
                        if "order_data" in sig.parameters:
                            from alpaca.trading.enums import OrderSide
                            from alpaca.trading.enums import TimeInForce as AlpacaTIF
                            from alpaca.trading.requests import (
                                LimitOrderRequest,
                                MarketOrderRequest,
                            )

                            alpaca_side = (
                                OrderSide.BUY if side == "buy" else OrderSide.SELL
                            )
                            alpaca_tif = (
                                AlpacaTIF.GTC
                                if time_in_force == "gtc"
                                else AlpacaTIF.DAY
                            )

                            use_limit = getattr(config, "USE_LIMIT_ORDERS", False)
                            spread_buffer = getattr(
                                config, "LIMIT_ORDER_SPREAD_BUFFER_PCT", 0.001
                            )

                            if use_limit and current_price is not None:
                                limit_price = current_price
                                if side == "buy":
                                    limit_price *= 1.0 + spread_buffer
                                else:
                                    limit_price *= 1.0 - spread_buffer
                                limit_price = round(limit_price, 2)

                                req = LimitOrderRequest(
                                    symbol=symbol,
                                    qty=order_qty,
                                    side=alpaca_side,
                                    limit_price=limit_price,
                                    time_in_force=alpaca_tif,
                                )
                            else:
                                req = MarketOrderRequest(
                                    symbol=symbol,
                                    qty=order_qty,
                                    side=alpaca_side,
                                    type="market",
                                    time_in_force=alpaca_tif,
                                )
                            return self.client.submit_order(req)
                        else:
                            use_limit = getattr(config, "USE_LIMIT_ORDERS", False)
                            spread_buffer = getattr(
                                config, "LIMIT_ORDER_SPREAD_BUFFER_PCT", 0.001
                            )

                            if use_limit and current_price is not None:
                                limit_price = current_price
                                if side == "buy":
                                    limit_price *= 1.0 + spread_buffer
                                else:
                                    limit_price *= 1.0 - spread_buffer
                                limit_price = round(limit_price, 2)

                                return self.client.submit_order(
                                    symbol=symbol,
                                    qty=order_qty,
                                    side=side,
                                    type="limit",
                                    limit_price=limit_price,
                                    time_in_force=time_in_force,
                                )
                            else:
                                return self.client.submit_order(
                                    symbol=symbol,
                                    qty=order_qty,
                                    side=side,
                                    type="market",
                                    time_in_force=time_in_force,
                                )

                    await loop.run_in_executor(
                        None,
                        _do_submit,
                    )
                    latency_ms = (time.perf_counter() - start_meas) * 1000.0
                    try:
                        from core.latency_watchdog import latency_watchdog

                        latency_watchdog.record_passive_latency(
                            latency_ms, "submit_order"
                        )
                    except ImportError:
                        pass
                    self.log_thought(
                        f"[{symbol}] ✅ Order submitted to Alpaca ({time_in_force.upper()}) [Lat: {latency_ms:.1f}ms]"
                    )

            if not is_simulation:
                self._last_order_time[symbol] = time.time()
                self._pending_orders[symbol] = side
                if self.compliance_guardian:
                    self.compliance_guardian.daily_trades += 1

            return True

        except Exception as e:
            err_str = str(e).lower()
            if "day trading buying power" in err_str or "insufficient" in err_str:
                self.log_thought(
                    f"[{symbol}] ❌ Order failed (PDT): {e}. Using GTC next cycle."
                )
            else:
                self.log_thought(f"[{symbol}] ❌ Order failed: {e}")
            logging.error("Order submission failed for %s: %s", symbol, e)
            return False
