# core/engine/order_executor.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: Multi-Tenant Order-Execution, Compliance, PubSub-Events

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError
from core.telemetry import get_tracer

tracer = get_tracer(__name__)


class DryRunOrder:
    """Mock Order object returned in Shadow Mode."""

    def __init__(self, id, symbol, qty, side, status="accepted"):
        self.id = id
        self.symbol = symbol
        self.qty = qty
        self.side = side
        self.status = status


class DryRunOrderProxy:
    """Intercepts Alpaca API calls when SHADOW_MODE is active."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def submit_order(self, req: MarketOrderRequest):
        order_id = f"shadow_{int(_time.time())}_{req.symbol}"
        logging.info(
            f"[SHADOW MODE] {self.tenant_id} Dry-Run Executed: {req.side} {req.qty} shares of {req.symbol}"
        )
        return DryRunOrder(id=order_id, symbol=req.symbol, qty=req.qty, side=req.side)


import config
from core.redis_client import RedisClient
from core.events import SignalEvent

# Epic 3.4-pre: user-specific credential resolution (Issue #413)
try:
    from core.user_secrets import (
        user_alpaca_secrets,
        UserAlpacaCredentialsNotFoundError,
    )

    USER_SECRETS_AVAILABLE = True
except ImportError:
    USER_SECRETS_AVAILABLE = False
    user_alpaca_secrets = None  # type: ignore[assignment]
    UserAlpacaCredentialsNotFoundError = Exception  # type: ignore[assignment,misc]


class OrderExecutorMixin:
    """
    Mixin für BotEngine: Multi-Tenant Fan-out, Compliance, Signal-Verarbeitung.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    async def get_active_tenant_clients(self) -> List[Dict[str, Any]]:
        """Fetches active wallets and creates TradingClients via OAuth."""
        from core.user_wallet_store import wallet_store
        from core.secret_manager_utils import oauth_secrets
        from alpaca.trading.client import TradingClient

        active_wallets = await wallet_store.get_active_wallets()
        tenant_clients = []
        is_paper = getattr(config, "PAPER_TRADING", True)

        for wallet in active_wallets:
            user_id = wallet["user_id"]

            # Check for local BORA keys first
            risk_limits = wallet.get("risk_limits", {})
            if "alpaca_keys" in risk_limits:
                keys = risk_limits["alpaca_keys"]
                try:
                    user_client = TradingClient(
                        api_key=keys.get("api_key"),
                        secret_key=keys.get("secret_key"),
                        paper=is_paper,
                    )
                    acc = user_client.get_account()
                    equity = float(acc.equity) if acc.equity else 0.0
                    tenant_clients.append(
                        {
                            "user_id": user_id,
                            "client": user_client,
                            "risk_limits": risk_limits,
                            "equity": equity,
                        }
                    )
                    continue
                except APIError as e:
                    logging.error(
                        f"Alpaca API error for user {user_id} using local keys: {e}"
                    )
                    continue
                except Exception as e:
                    logging.error(
                        "Failed to init TradingClient for user %s using local keys: %s",
                        user_id,
                        e,
                    )
                    continue

            # OAuth token logic
            secret_id = wallet.get("secret_manager_id")
            if not secret_id:
                continue

            tokens = oauth_secrets.get_tokens(secret_id)

            if not tokens or "access_token" not in tokens:
                logging.warning("No valid tokens found for active user %s", user_id)
                continue

            try:
                user_client = TradingClient(
                    oauth_token=tokens["access_token"], paper=is_paper
                )
                acc = user_client.get_account()
                equity = float(acc.equity) if acc.equity else 0.0
                tenant_clients.append(
                    {
                        "user_id": user_id,
                        "client": user_client,
                        "risk_limits": risk_limits,
                        "equity": equity,
                    }
                )
            except APIError as e:
                logging.error(f"Alpaca API error for user {user_id}: {e}")
            except Exception as e:
                logging.error(
                    "Failed to init TradingClient for user %s: %s", user_id, e
                )

        return tenant_clients

    def _get_tenant_risk_manager(self, user_id: str, client: Any, equity: float):
        from core.risk_manager import RiskManager

        if not hasattr(self, "tenant_risk_managers"):
            self.tenant_risk_managers = {}
        if user_id not in self.tenant_risk_managers:
            rm = RiskManager(client, equity)
            rm.reset_daily_limit(equity)
            self.tenant_risk_managers[user_id] = rm
        else:
            self.tenant_risk_managers[user_id].client = client
        return self.tenant_risk_managers[user_id]

    def _get_tenant_portfolio_manager(self, user_id: str, client: Any, equity: float):
        from core.portfolio_manager import PortfolioManager

        if not hasattr(self, "tenant_portfolio_managers"):
            self.tenant_portfolio_managers = {}
        if user_id not in self.tenant_portfolio_managers:
            max_positions = getattr(config, "MAX_POSITIONS", 10)
            pm = PortfolioManager(
                client, total_capital=equity, max_positions=max_positions
            )
            self.tenant_portfolio_managers[user_id] = pm
        else:
            self.tenant_portfolio_managers[user_id].client = client
            self.tenant_portfolio_managers[user_id].update_total_capital(equity)
        return self.tenant_portfolio_managers[user_id]

    async def _execute_tenant_order(self, tenant: Dict[str, Any], event: SignalEvent):
        """Execute an order for a single tenant with full risk/compliance/portfolio checks."""
        user_id = tenant["user_id"]
        client = tenant["client"]
        equity = tenant["equity"]
        action = event.action
        symbol = event.symbol
        context = event.decision_context

        try:
            rm = self._get_tenant_risk_manager(user_id, client, equity)
            curr = context.current_price if context else 0.0
            if curr <= 0:
                logging.warning(
                    "⚠️ OrderExecutor: Skipping %s %s - Missing current_price in DecisionContext",
                    action,
                    symbol,
                )
                return

            if action == "SELL":
                try:
                    pos = client.get_open_position(symbol)
                    size = (
                        float(pos.qty)
                        if hasattr(pos, "qty")
                        else float(pos.get("qty", 0.0))
                    )
                except Exception:
                    size = 0.0
            else:
                account = client.get_account()
                cash = float(getattr(account, "cash", 0) or 0)
                atr = (
                    getattr(context, "atr_14d", curr * 0.05) if context else curr * 0.05
                )
                vix = getattr(context, "vix_level", 20.0) if context else 20.0
                conviction = (
                    getattr(context, "conviction_score", 0.5) if context else 0.5
                )

                size = rm.calculate_position_size(
                    stop_loss_atr_multiplier=3.0,
                    atr=atr,
                    confidence="high",
                    size_scaler=1.0,
                    market_data={"vix": vix},
                    num_stocks_in_strategy=(
                        len(self.live_universe) if self.live_universe else 500
                    ),
                    current_price=curr,
                    account_cash=cash,
                    allow_fractional=True,
                    conviction_score=conviction,
                )

                pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                features_dict = {}
                if context and hasattr(context, "__dict__"):
                    for k in ["rsi_14", "macd", "adx_14", "volatility_20d"]:
                        if hasattr(context, k):
                            features_dict[k] = getattr(context, k)

                opp = pm.score_opportunity(
                    symbol=symbol,
                    current_price=curr,
                    rl_action=1,
                    model_confidence=context.lstm_prediction if context else 0.5,
                    features=features_dict,
                )
                should_open, reasoning, symbol_to_close = pm.should_open_new_position(
                    opp
                )
                if not should_open:
                    logging.debug(
                        f"[User {user_id}] {symbol} 📊 Portfolio Blocked: {reasoning}"
                    )
                    try:
                        redis = await RedisClient.get_redis()
                        await redis.publish(
                            f"explainability:{user_id}",
                            json.dumps(
                                {
                                    "type": "trade_rejected",
                                    "title": f"Portfolio Blocked: {symbol}",
                                    "message": reasoning,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                    except Exception as e:
                        logging.warning("PubSub error: %s", e)
                    return

                if symbol_to_close:
                    try:
                        old_pos = client.get_open_position(symbol_to_close)
                        if old_pos:
                            old_qty = (
                                float(old_pos.qty)
                                if hasattr(old_pos, "qty")
                                else float(old_pos.get("qty", 0.0))
                            )
                            if old_qty > 0:
                                req_close = MarketOrderRequest(
                                    symbol=symbol_to_close,
                                    qty=old_qty,
                                    side=OrderSide.SELL,
                                    time_in_force=TimeInForce.DAY,
                                )
                                client.submit_order(req_close)
                                pm.record_trade(symbol_to_close, "sell")
                                await asyncio.sleep(0.5)
                    except Exception as swap_e:
                        logging.warning(
                            f"[User {user_id}] Failed to swap out {symbol_to_close}: {swap_e}"
                        )

            if size <= 0:
                reason_msg = "Calculated size is 0 (check risk limits, position sizing, or cash)."
                logging.debug(f"[User {user_id}] {symbol} - Size 0")
                try:
                    redis = await RedisClient.get_redis()
                    await redis.publish(
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Risk Blocked: {symbol}",
                                "message": reason_msg,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                except Exception as e:
                    logging.warning("PubSub error: %s", e)
                return

            # Compliance check
            if self.compliance_guardian:
                compliance_order = {
                    "symbol": symbol,
                    "side": action.lower(),
                    "quantity": size,
                    "price": curr,
                    "strategy_id": "RLStrategy",
                    "timestamp": _time.time(),
                    "user_id": user_id,
                }
                if not self.compliance_guardian.check_order(compliance_order):
                    logging.warning(
                        f"[User {user_id}] {symbol} 🛡️ BLOCKED by ComplianceGuardian"
                    )
                    await RedisClient.get_redis().publish(
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Compliance Blocked: {symbol}",
                                "message": "Order Value Limit Exceeded.",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                    return
                if not self.compliance_guardian.check_trade(compliance_order):
                    logging.warning(
                        f"[User {user_id}] {symbol} 🛡️ BLOCKED by ComplianceGuardian (daily)"
                    )
                    await RedisClient.get_redis().publish(
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Compliance Blocked: {symbol}",
                                "message": "Daily Trade Limit Reached.",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                    return
                self.compliance_guardian.daily_trades += 1

            side_enum = OrderSide.BUY if action == "BUY" else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=symbol, qty=size, side=side_enum, time_in_force=TimeInForce.DAY
            )

            # Shadow Mode Interception
            if getattr(config, "SHADOW_MODE", False):
                logging.info(
                    f"[User {user_id}] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
                )
                with tracer.start_as_current_span("broker.submit_order.live") as span:
                    span.set_attribute("trade.action", action)
                    order = DryRunOrderProxy(user_id).submit_order(req)
                if context:
                    context.alpaca_order_id = str(order.id)
            else:
                with tracer.start_as_current_span("broker.submit_order.live") as span:
                    span.set_attribute("trade.action", action)
                    order = client.submit_order(req)

            if action == "SELL":
                try:
                    pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                    pm.record_trade(symbol, "sell")
                    pm.clear_sell_signals_after_sale(symbol)
                except Exception:
                    pass
            elif action == "BUY":
                try:
                    pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                    pm.record_trade(symbol, "buy")
                    conv = getattr(context, "conviction_score", 0.5) if context else 0.5
                    pm.update_position_conviction(symbol, conv)
                except Exception:
                    pass

            logging.info(
                f"[User {user_id}] ✅ Executed {action} {size} shares for {symbol}. OrderID: {order.id}"
            )
            try:
                redis = await RedisClient.get_redis()
                await redis.publish(
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "trade_executed",
                            "title": f"{action} Order Executed: {symbol}",
                            "message": f"Successfully routed {size} shares to your broker.",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass

        except Exception as e:
            logging.error(
                f"[User {user_id}] ❌ Order Execution Failed for {symbol}: {e}"
            )
            try:
                redis = await RedisClient.get_redis()
                await redis.publish(
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "trade_rejected",
                            "title": f"API Error: {symbol}",
                            "message": f"Broker API rejected order: {e}",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass

    async def _process_signal_event(self, event: SignalEvent):
        """Processes a SignalEvent. Handles multi-tenant fan-out and logging."""
        symbol = event.symbol
        action = event.action
        qty = event.suggested_quantity
        context = event.decision_context

        should_log = True
        if action == "HOLD":
            if context.lstm_prediction > 0.6 and context.rl_stabilized_action == 0:
                should_log = True
            elif context.lstm_prediction < -0.6 and context.rl_stabilized_action == 0:
                should_log = True
            elif (
                not context.risk_approved
                or not getattr(context, "portfolio_approved", True)
                or not getattr(context, "intelligence_approved", True)
            ):
                should_log = True
            else:
                should_log = False

        if action in ["BUY", "SELL"] and not event.is_simulation:
            try:
                active_tenants = await self.get_active_tenant_clients()
                if not active_tenants:
                    logging.info(
                        f"[{symbol}] No active OAuth tenants. Resolving via user_alpaca_accounts mapping."
                    )
                    # --- Epic 3.4-pre: resolve user-specific Alpaca credentials ---
                    uid = getattr(self, "active_uid", None)
                    resolved_client = None

                    if USER_SECRETS_AVAILABLE and uid:
                        try:
                            creds = user_alpaca_secrets.get_user_alpaca_credentials(uid)
                            is_paper = getattr(config, "PAPER_TRADING", True)
                            resolved_client = TradingClient(
                                api_key=creds.api_key,
                                secret_key=creds.secret_key,
                                paper=is_paper,
                            )
                            logging.info(
                                "[%s] Using user-mapped Alpaca credentials for uid=%s",
                                symbol,
                                uid,
                            )
                        except UserAlpacaCredentialsNotFoundError:
                            logging.warning(
                                "[%s] No user Alpaca mapping found for uid=%s — falling back.",
                                symbol,
                                uid,
                            )
                    elif not USER_SECRETS_AVAILABLE:
                        logging.warning(
                            "[%s] user_secrets module unavailable — falling back.",
                            symbol,
                        )

                    # Use resolved user client or fall back to global self.api
                    trade_client = resolved_client if resolved_client else self.api

                    if qty <= 0:
                        if action == "SELL":
                            try:
                                pos = trade_client.get_open_position(symbol)
                                qty = (
                                    float(pos.qty)
                                    if hasattr(pos, "qty")
                                    else float(pos.get("qty", 0.0))
                                )
                            except Exception:
                                qty = 0.0
                        elif action == "BUY" and getattr(
                            self, "live_risk_manager", None
                        ):
                            try:
                                curr = context.current_price if context else 0.0
                                acc = trade_client.get_account()
                                cash = float(getattr(acc, "cash", 0) or 0)
                                atr = (
                                    getattr(context, "atr_14d", curr * 0.05)
                                    if context
                                    else curr * 0.05
                                )
                                vix = (
                                    getattr(context, "vix_level", 20.0)
                                    if context
                                    else 20.0
                                )
                                conviction = (
                                    getattr(context, "conviction_score", 0.5)
                                    if context
                                    else 0.5
                                )

                                qty = self.live_risk_manager.calculate_position_size(
                                    stop_loss_atr_multiplier=3.0,
                                    atr=atr,
                                    confidence="high",
                                    size_scaler=1.0,
                                    market_data={"vix": vix},
                                    num_stocks_in_strategy=len(
                                        getattr(self, "live_universe", [])
                                    )
                                    or 500,
                                    current_price=curr,
                                    account_cash=cash,
                                    allow_fractional=True,
                                    conviction_score=conviction,
                                )
                                logging.info(
                                    "[%s] Dynamically calculated global qty: %f",
                                    symbol,
                                    qty,
                                )
                            except Exception as e:
                                logging.warning(
                                    "[%s] Global RiskManager sizing failed: %s",
                                    symbol,
                                    e,
                                )
                                qty = 0.0

                    if qty > 0:

                        approved = True
                        if self.compliance_guardian:
                            curr = context.current_price if context else 0.0
                            compliance_order = {
                                "symbol": symbol,
                                "side": action.lower(),
                                "quantity": qty,
                                "price": curr,
                                "strategy_id": "RLStrategy",
                                "timestamp": _time.time(),
                                "user_id": uid or "global",
                            }
                            if not self.compliance_guardian.check_order(
                                compliance_order
                            ):
                                logging.warning(
                                    f"[Global] {symbol} 🛡️ BLOCKED by ComplianceGuardian"
                                )
                                approved = False
                            elif not self.compliance_guardian.check_trade(
                                compliance_order
                            ):
                                logging.warning(
                                    f"[Global] {symbol} 🛡️ BLOCKED ComplianceGuardian (daily)"
                                )
                                approved = False
                            else:
                                self.compliance_guardian.daily_trades += 1

                        if approved:
                            side_enum = (
                                OrderSide.BUY if action == "BUY" else OrderSide.SELL
                            )
                            req = MarketOrderRequest(
                                symbol=symbol,
                                qty=qty,
                                side=side_enum,
                                time_in_force=TimeInForce.DAY,
                            )

                            # Shadow Mode Interception for global/fallback
                            if getattr(config, "SHADOW_MODE", False):
                                logging.info(
                                    f"[Global] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
                                )
                                order = DryRunOrderProxy("global").submit_order(req)
                                context.alpaca_order_id = str(order.id)
                            else:
                                order = trade_client.submit_order(req)
                                context.alpaca_order_id = str(order.id)

                            logging.info(
                                f"[{symbol}] ✅ Executed {action} {qty} shares "
                                f"(uid={uid or 'global'}). OrderID: {order.id}"
                            )

                else:
                    exec_tasks = [
                        self._execute_tenant_order(tenant, event)
                        for tenant in active_tenants
                    ]
                    if exec_tasks:
                        await asyncio.gather(*exec_tasks, return_exceptions=True)
                        logging.info(
                            f"[{symbol}] Async fan-out for {len(exec_tasks)} tenants."
                        )
                        context.alpaca_order_id = "multi-tenant-batch"
            except Exception as e:
                logging.error("Multi-tenant execution failed for %s: %s", symbol, e)
                context.alpaca_order_id = f"failed: {str(e)[:50]}"

        if should_log and not event.is_simulation:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.cloud_logger.log_decision, context)
