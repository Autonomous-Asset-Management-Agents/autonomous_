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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import config
from core.ai_rules import AILearnedRules
from core.cloud_logger import DecisionContext
from core.data_provider import HistoricalDataProvider
from core.exceptions import TradingHaltedError
from core.risk_manager import RiskManager


@dataclass
class OrderEinreichung:
    """Zustand zwischen den Schritten von ``_submit_order_safe`` (#4089, ARC-E6 G-7c).

    Pflicht sind die sieben Parameter. Die uebrigen Felder sind die Werte, die im alten Rumpf
    von Block zu Block flossen; jedes setzt sein Block, bevor ein spaeterer es liest. Die
    Defaults sind nur die Startbelegung.
    """

    symbol: str
    qty: float
    side: str
    expected_cost: float
    current_price: Optional[float]
    held_qty: float
    is_protective_exit: bool
    is_simulation: bool = False
    compliance_order: Optional[Dict[str, Any]] = None
    time_in_force: str = "day"
    use_fractional: bool = True
    order_qty: float = 0.0


#: Ein Schritt ist fertig, der Dirigent ruft den naechsten.
_WEITER = object()


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
        trade_intelligence: Any = None,
        clock: Any = None,
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
        self.trade_intelligence = trade_intelligence
        self.clock = clock
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

    @abstractmethod
    async def evaluate_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict[str, Any],
        current_time: datetime,
    ) -> Optional[Any]:
        """Pure evaluation: Features → Prediction → Signal.

        MUST NOT submit orders, mutate broker state, or call _submit_order_safe().
        Called by Round Table agents during the vote (opinion) phase.

        Art. 14 EU AI Act: Opinion-building separated from execution.
        Fix for #1876: Vote-Side-Effect.
        """
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
        held_qty: float = 0.0,
        is_protective_exit: bool = False,
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
            z = OrderEinreichung(
                symbol,
                qty,
                side,
                expected_cost,
                current_price,
                held_qty,
                is_protective_exit,
            )
            for schritt in (
                self._schritt_client,
                self._schritt_compliance,
                self._schritt_markt_geschlossen,
                self._schritt_dedup,
                self._schritt_kaufkraft_pdt,
                self._schritt_menge,
                self._schritt_absenden,
                self._schritt_nachbuchen,
            ):
                ergebnis = await schritt(z)
                if ergebnis is not _WEITER:
                    return ergebnis

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

    async def _schritt_client(self, z: OrderEinreichung):
        """Ohne ``submit_order`` keine Order; danach steht fest, ob simuliert wird."""
        if not hasattr(self.client, "submit_order"):
            self.log_thought(f"[{z.symbol}] ❌ No submit_order method available!")
            return False

        z.is_simulation = (
            hasattr(self.client, "simulation_data")
            or "Simulation" in type(self.client).__name__
        )
        return _WEITER

    async def _schritt_compliance(self, z: OrderEinreichung):
        """1. ComplianceGuardian: ``check_order`` und ``check_trade`` (nur live)."""
        # ── 1. ComplianceGuardian ─────────────────────────────────────────
        if not z.is_simulation and self.compliance_guardian:
            z.compliance_order = {
                "symbol": z.symbol,
                "side": z.side,
                "quantity": z.qty,
                "price": z.expected_cost / z.qty if z.qty > 0 else 0,
                "strategy_id": self.strategy_name,
                "timestamp": time.time(),
                # #2712 Inc1: explicit user_id — without it the wash-trade window keys
                # this path family under None and never cross-matches the executor
                # paths ("global"), fragmenting the 60s opposite-side detection.
                "user_id": getattr(self, "user_id", None) or "global",
                # ADR-C04-EXIT: how the guardian tells a risk-reducing exit from a
                # position-opening trade. Without it every SELL from THIS path reads
                # as non-exempt (the predicate is fail-closed), so the RLAgent
                # stop-loss — the DEFAULT strategy's stop path — would stay blocked
                # by the daily cap even with the exemption flag on. 0.0 for BUYs.
                "held_qty": z.held_qty,
            }
            if not self.compliance_guardian.check_order(z.compliance_order):
                self.log_thought(
                    f"[{z.symbol}] 🛡️ BLOCKED by ComplianceGuardian (order check)"
                )
                return False
            if not self.compliance_guardian.check_trade(z.compliance_order):
                self.log_thought(
                    f"[{z.symbol}] 🛡️ BLOCKED by ComplianceGuardian (daily trade limit)"
                )
                return False
        return _WEITER

    async def _schritt_markt_geschlossen(self, z: OrderEinreichung):
        """2. Market-Closed-Check ueber ``get_clock`` (nur live)."""
        # ── 2. Market-Closed ──────────────────────────────────────────────
        # TODO(MOD-1): migrate config import to RuntimeConfigState
        from config import BYPASS_MARKET_HOURS

        if (
            not z.is_simulation
            and not BYPASS_MARKET_HOURS
            and hasattr(self.client, "get_clock")
        ):
            try:
                clock = self.client.get_clock()
                if not getattr(clock, "is_open", True):
                    self.log_thought(
                        f"[{z.symbol}] ⏸️ Market closed – skipping {z.side.upper()}"
                    )
                    return False
            except Exception as e:
                logging.warning("[%s] Could not check market clock: %s", z.symbol, e)
        return _WEITER

    async def _schritt_dedup(self, z: OrderEinreichung):
        """3. Order-Deduplication: keine zweite offene Order derselben Seite."""
        # ── 3. Order-Deduplication ────────────────────────────────────────
        if not z.is_simulation:
            try:
                if hasattr(self.client, "get_orders"):
                    from alpaca.trading.enums import QueryOrderStatus
                    from alpaca.trading.requests import GetOrdersRequest

                    req = GetOrdersRequest(
                        status=QueryOrderStatus.OPEN, symbols=[z.symbol]
                    )
                    open_orders = self.client.get_orders(req)
                else:
                    open_orders = self.client.list_orders(status="open")

                for order in open_orders:
                    # order.side is an Enum in alpaca-py -> str(order.side).lower()
                    if order.symbol == z.symbol and z.side in str(order.side).lower():
                        self.log_thought(
                            f"[{z.symbol}] ⏳ Pending {z.side.upper()} order already exists"
                        )
                        return False
            except Exception as e:
                logging.warning("[%s] Could not check pending orders: %s", z.symbol, e)
        return _WEITER

    async def _schritt_kaufkraft_pdt(self, z: OrderEinreichung):
        """4. Buying Power und 5. PDT-Handling (nur BUY, nur live)."""
        # ── 4. Buying Power (nur BUY, Live) ───────────────────────────────
        z.time_in_force = "day"
        z.use_fractional = True

        if z.side == "buy" and z.expected_cost > 0 and not z.is_simulation:
            try:
                account = self.client.get_account()
                dt_bp = float(getattr(account, "daytrading_buying_power", None) or 0)
                reg_bp = float(getattr(account, "buying_power", None) or 0)
                reg_cash = float(getattr(account, "cash", 0) or 0)
                is_pdt = getattr(account, "pattern_day_trader", False)

                if reg_cash <= 0 and (reg_bp or 0) <= 0:
                    self.log_thought(
                        f"[{z.symbol}] ⚠️ BLOCKED - Invalid account state (cash=${reg_cash:.2f})."
                    )
                    return False

                # ── 5. PDT Handling ───────────────────────────────────────
                if is_pdt and dt_bp == 0:
                    z.time_in_force = "gtc"
                    z.use_fractional = False
                    now_ts = time.time()
                    if now_ts - self._last_gtc_buy_submit_time < 90:
                        self.log_thought(
                            f"[{z.symbol}] ⏳ PDT: one GTC buy per 90s. Skipping."
                        )
                        return False
                    # dt_bp==0 → Broker würde ablehnen; Slot reservieren und skip
                    self._last_gtc_buy_submit_time = time.time()
                    self.log_thought(
                        f"[{z.symbol}] ⏭️ PDT: Day trading BP $0 – skipping GTC submit."
                    )
                    return False

                use_cash_only = getattr(config, "USE_CASH_ONLY", True)
                if use_cash_only:
                    cash_available = reg_cash - 500  # $500 Buffer
                    if z.expected_cost > max(0, cash_available):
                        self.log_thought(
                            f"[{z.symbol}] ⚠️ SKIPPED - Order ${z.expected_cost:.2f} exceeds cash (${reg_cash:.2f})."
                        )
                        if self.compliance_guardian:
                            self.compliance_guardian.log_execution_outcome(
                                z.compliance_order,
                                False,
                                f"cash gate: order ${z.expected_cost:.2f} exceeds cash ${reg_cash:.2f}",
                            )
                        return False
                    current_bp = reg_cash
                else:
                    current_bp = reg_bp if dt_bp == 0 else max(dt_bp, reg_bp)
                    if current_bp == 0:
                        current_bp = reg_cash

                min_required = z.expected_cost + 500
                if current_bp < min_required:
                    self.log_thought(
                        f"[{z.symbol}] ⚠️ SKIPPED - Insufficient buying power: ${current_bp:.2f} < ${min_required:.2f}"
                    )
                    if self.compliance_guardian:
                        self.compliance_guardian.log_execution_outcome(
                            z.compliance_order,
                            False,
                            f"buying-power gate: ${current_bp:.2f} < required ${min_required:.2f}",
                        )
                    return False

            except Exception as e:
                self.log_thought(
                    f"[{z.symbol}] ⚠️ Could not verify buying power, skipping: {e}"
                )
                return False
        return _WEITER

    async def _schritt_menge(self, z: OrderEinreichung):
        """Mengenrundung (GTC braucht ganze Anteile) und PDT-GTC-Slot."""
        # ── Quantity rounding (GTC benötigt ganze Anteile) ────────────────
        z.order_qty = z.qty
        if not z.use_fractional:
            z.order_qty = math.floor(z.qty)
            if z.order_qty < 1:
                self.log_thought(
                    f"[{z.symbol}] ⚠️ SKIPPED - Less than 1 whole share ({z.qty:.2f})"
                )
                return False

        # ── PDT GTC Slot reservieren ──────────────────────────────────────
        if not z.is_simulation and z.side == "buy" and z.time_in_force == "gtc":
            self._last_gtc_buy_submit_time = time.time()
        return _WEITER

    async def _schritt_absenden(self, z: OrderEinreichung):
        """Order abschicken: async, Simulation oder live durchs Tor."""
        # ── Order abschicken ──────────────────────────────────────────────
        submit_method = self.client.submit_order

        if inspect.iscoroutinefunction(submit_method):
            await submit_method(z.symbol, z.order_qty, z.side)
            self.log_thought(f"[{z.symbol}] ✅ Order submitted (async)")
        else:
            if z.is_simulation:
                self.client.submit_order(symbol=z.symbol, qty=z.order_qty, side=z.side)
                self.log_thought(f"[{z.symbol}] ✅ Order submitted (simulation)")
            else:
                # #3380/#3466: Ein Halt stoppt neue Einstiege, nicht den Schutz
                # offener Positionen. Hier laeuft der Stop-Loss der Default-Strategie;
                # ohne Freistellung blieb eine Position genau dann ohne Schwelle, wenn
                # die Lage ohnehin schlecht ist. Eng: nur SELL, nur mit Kennzeichen
                # vom Aufrufer (Risiko-Stufe aus #3180, ``ist_schutz_exit``).
                _schutz_exit = bool(z.is_protective_exit) and z.side == "sell"
                try:
                    from core.kill_switch import kill_switch

                    if not _schutz_exit:
                        kill_switch.check_halt()
                    elif kill_switch.is_halted():
                        logging.warning(
                            "[Halt] %s: Schutz-Exit trotz Kill-Switch-Halt "
                            "ausgefuehrt (Strategiepfad) — Freistellung nach "
                            "#3380, nicht blockiert.",
                            z.symbol,
                        )
                except ImportError:
                    pass

                start_meas = time.perf_counter()
                loop = asyncio.get_event_loop()

                _do_submit = self._einreicher(z, _schutz_exit)

                await loop.run_in_executor(
                    None,
                    _do_submit,
                )
                latency_ms = (time.perf_counter() - start_meas) * 1000.0
                try:
                    from core.latency_watchdog import latency_watchdog

                    latency_watchdog.record_passive_latency(latency_ms, "submit_order")
                except ImportError:
                    pass
                self.log_thought(
                    f"[{z.symbol}] ✅ Order submitted to Alpaca ({z.time_in_force.upper()}) [Lat: {latency_ms:.1f}ms]"
                )
        return _WEITER

    def _einreicher(self, z: OrderEinreichung, _schutz_exit: bool):
        """Baut ``_do_submit`` fuer den Executor (#4089, ARC-E6 G-7c).

        Die beiden Closures des Live-Zweigs stehen hier wortgleich; sie lesen dieselben
        freien Namen wie vorher, gebunden in der ersten Zeile. ``_durchs_tor`` ist seit
        #3447 der einzige Brokerweg.
        """
        symbol, order_qty, side, current_price, time_in_force = (
            z.symbol,
            z.order_qty,
            z.side,
            z.current_price,
            z.time_in_force,
        )

        from core.gateway.order_gateway import KwargsAuftrag

        def _durchs_tor(auftrag):
            """#3447 Schritt 3: der Broker-Aufruf geht durchs ``OrderGateway``.

            Die vier Regeln oben sind gefallen; das Tor schreibt den
            Datensatz und ist die einzige Stelle, die den Broker ruft. Eine
            Ablehnung meldet es als dieselbe Art Ausnahme wie
            ``check_halt`` — der ``except`` unten gibt ``False`` zurueck wie
            bisher.
            """
            from core.contracts import OrderIntent
            from core.gateway.fabrik import gateway_for
            from core.idempotency import ersatz_decision_id
            from core.kill_switch import kill_switch as _ks

            art = "stop" if _schutz_exit else ("entry" if side == "buy" else "trim")
            # Der Strategiepfad traegt keine decision_id. Ersatzschluessel
            # statt Validierungsfehler — dieselbe Regel wie im Executor:
            # Buchfuehrung verhindert nie eine Order.
            wer = getattr(self, "user_id", None) or "global"
            intent = OrderIntent(
                decision_id=ersatz_decision_id(art, wer, symbol),
                symbol=symbol,
                side=side,
                qty=float(order_qty),
                intent_kind=art,
                is_protective_exit=_schutz_exit,
                halted=bool(_ks.is_halted()),
            )
            entscheidung, antwort = gateway_for(self.client).submit_with_result(
                intent, request=auftrag
            )
            if not entscheidung.approved:
                raise TradingHaltedError(
                    f"TRADING HALTED: {symbol} nicht abgesetzt — das Tor "
                    f"hat abgelehnt ({entscheidung.reason_code.value}: "
                    f"{entscheidung.detail})"
                )
            return antwort

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

                alpaca_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
                alpaca_tif = AlpacaTIF.GTC if time_in_force == "gtc" else AlpacaTIF.DAY

                use_limit = getattr(config, "USE_LIMIT_ORDERS", False)
                # #4089: mehrzeilig wie vor G-7c — die getattr-Ratsche zaehlt zeilenweise.
                spread_buffer = getattr(
                    config,
                    "LIMIT_ORDER_SPREAD_BUFFER_PCT",
                    0.001,
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
                return _durchs_tor(req)
            else:
                use_limit = getattr(config, "USE_LIMIT_ORDERS", False)
                # #4089: mehrzeilig wie vor G-7c — die getattr-Ratsche zaehlt zeilenweise.
                spread_buffer = getattr(
                    config,
                    "LIMIT_ORDER_SPREAD_BUFFER_PCT",
                    0.001,
                )

                if use_limit and current_price is not None:
                    limit_price = current_price
                    if side == "buy":
                        limit_price *= 1.0 + spread_buffer
                    else:
                        limit_price *= 1.0 - spread_buffer
                    limit_price = round(limit_price, 2)

                    return _durchs_tor(
                        KwargsAuftrag(
                            symbol=symbol,
                            qty=order_qty,
                            side=side,
                            type="limit",
                            limit_price=limit_price,
                            time_in_force=time_in_force,
                        )
                    )
                else:
                    return _durchs_tor(
                        KwargsAuftrag(
                            symbol=symbol,
                            qty=order_qty,
                            side=side,
                            type="market",
                            time_in_force=time_in_force,
                        )
                    )

        return _do_submit

    async def _schritt_nachbuchen(self, z: OrderEinreichung):
        """Nachbuchung (live): Zeitstempel, Pending, Tageszaehler, Audit."""
        if not z.is_simulation:
            self._last_order_time[z.symbol] = time.time()
            self._pending_orders[z.symbol] = z.side
            if self.compliance_guardian:
                # #1849 follow-up: atomic, lock-guarded increment (the bare
                # ``+= 1`` RMW lost increments under concurrency → the daily cap
                # could be silently exceeded). Behaviour identical: +1 per trade.
                self.compliance_guardian.record_trade()
                # Honesty fix: pair the pre-trade "approved" entry with the real
                # execution outcome so the audit trail reconciles (approved == submitted).
                self.compliance_guardian.log_execution_outcome(
                    z.compliance_order, True, "submitted to broker"
                )

        return True
