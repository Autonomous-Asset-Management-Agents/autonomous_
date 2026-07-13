# core/engine/order_executor.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: Multi-Tenant Order-Execution, Compliance, PubSub-Events

import asyncio
import json
import logging
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from core.kill_switch import kill_switch
from core.protocols import BrokerClientProtocol
from core.telemetry import get_tracer

tracer = get_tracer(__name__)


# --- ADR-OBS-01 / PR A.2: execution instrumentation (PURE OBSERVATION) --------
# Fail-safe module-level counters bumped at the Alpaca submit success / exception /
# retry points. ``_bump_exec`` is wrapped so a counter failure can NEVER raise into
# the trading path — the submit/retry logic below stays byte-identical. Read-only
# snapshot via ``get_exec_counters`` for /engine-diagnostics.
_EXEC_COUNTERS: Dict[str, Any] = {
    "submit_ok": 0,
    "submit_fail": 0,
    "retry_count": 0,
    "last_fill_ts": None,
}


def _bump_exec(field: str, *, inc: int = 1, set_ts: bool = False) -> None:
    """Fail-safe counter mutation — swallows EVERY error (observation must never
    alter execution control flow)."""
    try:
        if set_ts:
            _EXEC_COUNTERS[field] = _time.time()
        else:
            _EXEC_COUNTERS[field] = _EXEC_COUNTERS.get(field, 0) + inc
    except Exception:  # noqa: BLE001 — a broken counter must never block a trade
        pass


def _safe_bump_exec(field: str, **kw) -> None:
    """Call-site guard: DOUBLE fail-safe so even a wholly-replaced ``_bump_exec``
    (e.g. adversarial test / monkeypatch) can NEVER raise into the trading path."""
    try:
        _bump_exec(field, **kw)
    except Exception:  # noqa: BLE001 — observation must never alter execution flow
        pass


def _rec_outcome(symbol: str, code: str, reason: str = "") -> None:
    """RQ-1 (#1516): record the FINAL execution outcome per symbol for the decision
    badge — Iron-Dome / risk / kill-switch result (display-only; see
    core/round_table/execution_outcomes.py). PURE OBSERVATION: double-guarded so it
    can NEVER raise into the trading path; nothing here changes an order decision."""
    try:
        from core.round_table.execution_outcomes import record_execution_outcome

        record_execution_outcome(symbol, code, reason)
    except Exception:  # noqa: BLE001 — observation must never alter execution flow
        pass


async def _safe_publish(redis_conn, channel: str, message: str) -> None:
    """#1230 (BUG-AI-001): the single guarded publish route for the explainability
    PubSub sink. ``RedisClient.get_redis()`` may legitimately return ``None``
    (Enterprise-degraded / no running loop); never call ``.publish()`` on ``None``.
    Pure observation — a missing sink must never raise into the trading path."""
    if redis_conn is not None:
        await redis_conn.publish(channel, message)


def get_exec_counters() -> Dict[str, Any]:
    """Read-only snapshot of the execution counters + the live shadow_mode flag."""
    snap = dict(_EXEC_COUNTERS)
    try:
        snap["shadow_mode"] = bool(getattr(config, "SHADOW_MODE", False))
    except Exception:  # noqa: BLE001
        snap["shadow_mode"] = None
    return snap


def reset_exec_counters() -> None:
    """Test/daily-reset helper — zeroes the execution counters."""
    _EXEC_COUNTERS.update(
        {"submit_ok": 0, "submit_fail": 0, "retry_count": 0, "last_fill_ts": None}
    )


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
from core.events import SignalEvent
from core.redis_client import RedisClient

# Epic 3.4-pre: user-specific credential resolution (Issue #413)
try:
    from core.user_secrets import (
        UserAlpacaCredentialsNotFoundError,
        user_alpaca_secrets,
    )

    USER_SECRETS_AVAILABLE = True
except ImportError:
    USER_SECRETS_AVAILABLE = False
    user_alpaca_secrets = None  # type: ignore[assignment]
    UserAlpacaCredentialsNotFoundError = Exception  # type: ignore[assignment,misc]


# ── Anti-Churn Redis Persistence (Rev 4 — BORA approved 2026-06-13) ────────
# Module-level functions for direct testability (no OrderExecutor fixture needed).
# Persist/restore _trade_history and _consecutive_sell_signals across process restarts.


async def restore_pm_state_from_redis(
    pm: Any,
    r: Any,
    pm_restored: set,
) -> None:
    """Restore anti-churn state from Redis after process restart.

    Only runs once per PM lifecycle (pm_restored tracks user_ids). Uses
    pm.client.get_all_positions() as source of truth for which symbols to restore
    (NF-1: no pm:tracked_symbols index → no Read-Modify-Write race condition).

    Args:
        pm:          PortfolioManager instance (has .user_id, .client, ._trade_history).
        r:           Redis/LocalStateClient instance (already fetched — no second get_redis()).
        pm_restored: Set[str] held by executor — user_ids already restored this session.
    """
    user_id = pm.user_id
    if user_id in pm_restored:
        return  # Already restored this session — no-op

    # NB-2: Validate r BEFORE setting flag — allows retry if Redis temporarily unavailable
    if r is None or not hasattr(r, "get"):
        return

    try:
        # NB-3: asyncio.to_thread() — consistent with reconciliation.py:122, no event-loop block
        open_positions = await asyncio.to_thread(pm.client.get_all_positions)
    except Exception as e:
        logging.warning("[PM-Restore] get_all_positions failed for %s — %s", user_id, e)
        return

    # NC-1: Filter None symbols (defensive coding for malformed broker responses)
    symbols = [
        sym
        for p in open_positions
        if p is not None
        for sym in [p.symbol if hasattr(p, "symbol") else p.get("symbol")]
        if sym is not None
    ]

    # Flag set here — after r validated, whether or not positions exist
    pm_restored.add(user_id)

    if not symbols:
        return

    restored_count = 0
    for sym in symbols:
        try:
            raw_history = await r.get(f"pm:trade_history:{user_id}:{sym}")
            if raw_history:
                times = [datetime.fromisoformat(t) for t in json.loads(raw_history)]
                pm._trade_history[sym] = times
                restored_count += 1

            raw_sells = await r.get(f"pm:sell_signals:{user_id}:{sym}")
            if raw_sells:
                pm._consecutive_sell_signals[sym] = int(raw_sells)
        except (
            Exception
        ) as e:  # DC-1: broad catch — includes ConnectionError, TimeoutError
            logging.warning("[PM-Restore] Error restoring %s:%s — %s", user_id, sym, e)

    if restored_count > 0:
        logging.info(
            "[PM-Restore] Anti-churn state restored for %d/%d symbols (user=%s)",
            restored_count,
            len(symbols),
            user_id,
        )


async def persist_pm_state_to_redis(
    pm: Any,
    symbol: str,
    r: Any,
) -> None:
    """Persist trade_history + sell_signals for ONE symbol to Redis/LocalStateClient.

    Two atomic set() calls — no Read-Modify-Write, no race condition (NF-1 fix).
    r is passed in from the caller — no redundant get_redis() call (F-4 fix).

    Key schema (TTL 26h — survives overnight restart, daily flush prevents stale data):
      pm:trade_history:{user_id}:{symbol}  → JSON list of ISO timestamps
      pm:sell_signals:{user_id}:{symbol}   → str(int) consecutive sell count
    """
    if r is None or not hasattr(r, "set"):
        return

    user_id = pm.user_id
    TTL_MS = 26 * 60 * 60 * 1000  # ADR: 26h — slightly over one trading day

    try:
        history = pm._trade_history.get(symbol, [])
        await r.set(
            f"pm:trade_history:{user_id}:{symbol}",
            json.dumps([t.isoformat() for t in history]),
            px=TTL_MS,
        )
        sells = pm._consecutive_sell_signals.get(symbol, 0)
        await r.set(
            f"pm:sell_signals:{user_id}:{symbol}",
            str(sells),
            px=TTL_MS,
        )
    except Exception as e:
        logging.warning("[PM-Persist] Write failed for %s:%s — %s", user_id, symbol, e)


class OrderExecutorMixin:
    """
    Mixin für BotEngine: Multi-Tenant Fan-out, Compliance, Signal-Verarbeitung.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    async def get_active_tenant_clients(self) -> List[Dict[str, Any]]:
        """Fetches active wallets and creates TradingClients via OAuth."""
        from alpaca.trading.client import TradingClient

        from core.secret_manager_utils import oauth_secrets
        from core.user_wallet_store import wallet_store

        active_wallets = await wallet_store.get_active_wallets()
        tenant_clients = []
        is_paper = getattr(config, "PAPER_TRADING", True)

        for wallet in active_wallets:
            user_id = wallet["user_id"]

            # Check for local AAAgents keys first
            risk_limits = wallet.get("risk_limits", {})
            if "alpaca_keys" in risk_limits:
                keys = risk_limits["alpaca_keys"]
                try:
                    user_client = TradingClient(
                        api_key=keys.get("api_key"),
                        secret_key=keys.get("secret_key"),
                        paper=is_paper,
                    )
                    acc = await asyncio.to_thread(user_client.get_account)
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
                acc = await asyncio.to_thread(user_client.get_account)
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

    def _get_tenant_portfolio_manager(
        self, user_id: str, client: BrokerClientProtocol, equity: float
    ):
        from core.portfolio_manager import PortfolioManager

        if not hasattr(self, "_pm_restored"):
            self._pm_restored: set = (
                set()
            )  # user_ids restored this session (NB-4: lives in executor, not PM)
        if not hasattr(self, "tenant_portfolio_managers"):
            self.tenant_portfolio_managers = {}
        if user_id not in self.tenant_portfolio_managers:
            max_positions = getattr(config, "MAX_POSITIONS", 10)
            pm = PortfolioManager(
                client,
                total_capital=equity,
                max_positions=max_positions,
                user_id=user_id,
            )
            self.tenant_portfolio_managers[user_id] = pm
        else:
            self.tenant_portfolio_managers[user_id].client = client
            self.tenant_portfolio_managers[user_id].update_total_capital(equity)
        return self.tenant_portfolio_managers[user_id]

    async def _execute_tenant_order(
        self, tenant: Dict[str, Any], event: SignalEvent, source: str = "ai"
    ):
        """Execute an order for a single tenant with full risk/compliance/portfolio checks."""
        user_id = tenant["user_id"]
        client = tenant["client"]
        equity = tenant["equity"]
        action = event.action
        symbol = event.symbol
        context = event.decision_context

        redis_client = await RedisClient.get_redis()
        lock_key = f"order_lock:{user_id}:{symbol}"
        acquired = False

        # Epic 4: Redis Redlock TTL of 12000ms (12 seconds)
        if redis_client:
            lock = redis_client.lock(lock_key, timeout=12.0)
            acquired = await lock.acquire(blocking=False)
            if not acquired:
                logging.warning(
                    f"Concurrent execution blocked for {user_id}:{symbol} by Redlock."
                )
                return
        else:
            lock = None

        try:
            order_submitted = False
            # PR A.2: local marker so the outer except only counts an actual live
            # broker-submit failure as submit_fail (not a pre-submit guard return).
            _submit_attempted = False
            rm = self._get_tenant_risk_manager(user_id, client, equity)
            curr = context.current_price if context else 0.0
            if curr <= 0:
                logging.warning(
                    "⚠️ OrderExecutor: Skipping %s %s - Missing current_price in DecisionContext",
                    action,
                    symbol,
                )
                return

            # Hoist PM init + restore ABOVE action branch:
            # SELL can_sell_position() AND BUY debate_position_swap() both
            # read _trade_history — both paths must see the restored state.
            pm = self._get_tenant_portfolio_manager(user_id, client, equity)
            if not hasattr(self, "_pm_restored"):
                self._pm_restored: set = set()
            # Restore cross-restart anti-churn state (redis_client fetched above)
            await restore_pm_state_from_redis(pm, redis_client, self._pm_restored)

            # #1994: durable entry-time — for positions Redis did NOT restore (e.g.
            # desktop, no Redis) reconcile the entry-time from the Alpaca fill history
            # (broker = source of truth). Once per session; fail-safe (no-op on error).
            if not hasattr(self, "_entry_time_reconciled"):
                self._entry_time_reconciled: set = set()
            if user_id not in self._entry_time_reconciled:
                self._entry_time_reconciled.add(user_id)
                from core.engine.entry_time_reconcile import (
                    reconcile_entry_time_from_alpaca,
                )

                await reconcile_entry_time_from_alpaca(pm)

            if action == "SELL":
                can_sell = True
                reason = ""
                if hasattr(pm, "can_sell_position"):
                    res = pm.can_sell_position(symbol)
                    if isinstance(res, tuple) and len(res) == 2:
                        can_sell, reason = res
                if not can_sell:
                    logging.warning(
                        "[User %s] %s 📊 SELL blocked by anti-churn: %s",
                        user_id,
                        symbol,
                        reason,
                    )
                    try:
                        redis = await RedisClient.get_redis()
                        if redis:
                            await _safe_publish(
                                redis,
                                f"explainability:{user_id}",
                                json.dumps(
                                    {
                                        "type": "trade_rejected",
                                        "title": f"Anti-Churn Blocked: {symbol}",
                                        "message": reason,
                                        "timestamp": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                    }
                                ),
                            )
                    except Exception as redis_e:
                        logging.warning("PubSub error: %s", redis_e)
                    pm.record_sell_signal(symbol)
                    await persist_pm_state_to_redis(pm, symbol, redis_client)
                    return

                try:
                    pos = await asyncio.to_thread(client.get_open_position, symbol)
                    size = (
                        float(pos.qty)
                        if hasattr(pos, "qty")
                        else float(pos.get("qty", 0.0))
                    )
                except Exception as e:
                    logging.warning(
                        "[User %s] Could not fetch open position for %s: %s — aborting SELL.",
                        user_id,
                        symbol,
                        e,
                    )
                    return  # Abort immediately — do not submit MarketOrderRequest(qty=0)
            else:
                account = await asyncio.to_thread(client.get_account)
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
                        await _safe_publish(
                            redis,
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
                        old_pos = await asyncio.to_thread(
                            client.get_open_position, symbol_to_close
                        )
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
                                await asyncio.to_thread(client.submit_order, req_close)
                                pm.record_trade(symbol_to_close, "sell")
                                await persist_pm_state_to_redis(
                                    pm, symbol_to_close, redis_client
                                )
                                await asyncio.sleep(0.5)
                    except Exception as swap_e:
                        logging.warning(
                            f"[User {user_id}] Failed to swap out {symbol_to_close}: {swap_e}"
                        )

            # ADR-016 (EU AI Act Art. 14 + MiFID II RTS 6): a human-approved order executes AT
            # MOST the quantity the human oversaw. The engine never autonomously sizes ABOVE the
            # approved amount — risk/cash may only REDUCE it; the approved qty is a CEILING, never
            # an amplifier. (Variant B — supersedes Rev-11 Decision-1's unconditional re-size, so
            # the immutable audit value matches what the human authorised = MiFID record accuracy.)
            if source == "human_approved":
                _approved_qty = abs(
                    float(getattr(event, "suggested_quantity", 0.0) or 0.0)
                )
                if _approved_qty > 0:
                    size = min(size, _approved_qty)

            if size <= 0:
                reason_msg = "Calculated size is 0 (check risk limits, position sizing, or cash)."
                logging.debug(f"[User {user_id}] {symbol} - Size 0")
                try:
                    redis = await RedisClient.get_redis()
                    await _safe_publish(
                        redis,
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
                    try:
                        redis = await RedisClient.get_redis()
                        await _safe_publish(
                            redis,
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
                    except Exception as e:
                        logging.warning("PubSub error: %s", e)
                    _rec_outcome(
                        symbol, "blocked:order_value", "Order value limit exceeded."
                    )
                    return
                if not self.compliance_guardian.check_trade(
                    compliance_order, source=source
                ):
                    logging.warning(
                        f"[User {user_id}] {symbol} 🛡️ BLOCKED by ComplianceGuardian (daily)"
                    )
                    if not getattr(
                        self.compliance_guardian, "_daily_limit_alert_sent", False
                    ):
                        try:
                            from core.notifier import send_slack_alert

                            msg = f"Compliance: Max daily trades ({self.compliance_guardian.max_daily_trades}) reached. Trading halted."
                            asyncio.create_task(
                                asyncio.to_thread(
                                    send_slack_alert,
                                    f"🛑 *Iron Dome Block*: {msg}",
                                    level="warning",
                                )
                            )
                            self.compliance_guardian._daily_limit_alert_sent = True
                        except Exception as alert_err:
                            logging.error(
                                f"Failed to send Iron Dome Slack alert: {alert_err}"
                            )
                    try:
                        redis = await RedisClient.get_redis()
                        await _safe_publish(
                            redis,
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
                    except Exception as e:
                        logging.warning("PubSub error: %s", e)
                    _rec_outcome(
                        symbol, "blocked:daily_limit", "Daily trade limit reached."
                    )
                    return
                # PR-0a-ii-5a: a human-approved order must NOT consume the autonomous
                # daily-trade budget (its cap was already source-skipped in check_trade).
                if source != "human_approved":
                    # #1849 follow-up: atomic, lock-guarded increment (the bare
                    # ``+= 1`` RMW lost increments under concurrency → the daily cap
                    # could be silently exceeded). Behaviour identical: +1 per trade.
                    self.compliance_guardian.record_trade()

            side_enum = OrderSide.BUY if action == "BUY" else OrderSide.SELL
            client_order_id = (
                getattr(context, "client_order_id", None) if context else None
            )
            client_order_id = (
                client_order_id
                if isinstance(client_order_id, str)
                else str(uuid.uuid4())
            )
            # EXC-1: Limit Order Heuristic (Option B) Factory
            try:
                import config as _cfg

                use_limit = getattr(_cfg, "USE_LIMIT_ORDERS", False)
                spread_buffer = getattr(_cfg, "LIMIT_ORDER_SPREAD_BUFFER_PCT", 0.001)
            except ImportError:
                use_limit = False
                spread_buffer = 0.001

            if use_limit and context and getattr(context, "current_price", None):
                limit_price = context.current_price
                if side_enum == OrderSide.BUY:
                    limit_price *= 1.0 + spread_buffer
                else:
                    limit_price *= 1.0 - spread_buffer

                limit_price = round(limit_price, 2)

                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=size,
                    side=side_enum,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=size,
                    side=side_enum,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )

            # --- FIX: Kill Switch Gate ---
            kill_switch.check_halt(user_id)

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
                    _submit_attempted = True
                    order = await asyncio.to_thread(client.submit_order, req)
                    # PR A.2: fail-safe — the order reached the broker.
                    _safe_bump_exec("submit_ok")
                    _rec_outcome(symbol, "executed")

                    # EXC-1: Task 3 & 4 - Order Lifecycle Polling & Synchronisation
                    from alpaca.trading.enums import OrderStatus

                    max_wait_seconds = 120
                    poll_interval = 2
                    waited = 0
                    is_filled = False

                    while waited < max_wait_seconds:
                        try:
                            # Fetch current state from Alpaca (wrap sync network call to prevent event loop blocking)
                            live_order = await asyncio.to_thread(
                                client.get_order_by_id, order.id
                            )
                            if live_order.status == OrderStatus.FILLED:
                                is_filled = True
                                # PR A.2: fail-safe — stamp the last observed fill.
                                _safe_bump_exec("last_fill_ts", set_ts=True)
                                break
                            elif live_order.status in (
                                OrderStatus.CANCELED,
                                OrderStatus.EXPIRED,
                                OrderStatus.REJECTED,
                            ):
                                logging.warning(
                                    f"[User {user_id}] Order {order.id} for {symbol} ended in status {live_order.status}"
                                )
                                break
                        except Exception as poll_e:
                            logging.warning(
                                f"Error polling order status for {order.id}: {poll_e}"
                            )

                        await asyncio.sleep(poll_interval)
                        waited += poll_interval
                        # PR A.2: fail-safe — count each not-yet-filled poll retry.
                        _safe_bump_exec("retry_count")

                    if not is_filled:
                        logging.warning(
                            f"[User {user_id}] Order {order.id} for {symbol} not filled within {max_wait_seconds}s. Attempting to cancel."
                        )
                        try:
                            await asyncio.to_thread(client.cancel_order_by_id, order.id)
                            logging.info(
                                f"Successfully cancelled hanging order {order.id}"
                            )
                        except Exception as cancel_e:
                            logging.error(
                                f"Failed to cancel hanging order {order.id}: {cancel_e}"
                            )

                        # Early exit: prevent pm.record_trade from running for an unfilled order.
                        # The order DID reach the broker (submitted, then cancelled for non-fill),
                        # so report it submitted — the human-approval drain audits this "approved"
                        # (it reached the market), never "iron_dome_rejected" (PR-0a-ii-5a).
                        return True

            order_submitted = True

            if action == "SELL":
                try:
                    pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                    pm.record_trade(symbol, "sell")
                    await persist_pm_state_to_redis(pm, symbol, redis_client)
                    pm.clear_sell_signals_after_sale(symbol)
                except Exception as e:
                    # ADR-ENG-07: Post-SELL portfolio state failure is ERROR-level.
                    # The broker order was submitted but internal state may be inconsistent.
                    # Risk: Ghost Position — duplicate SELL on next cycle.
                    # Remediation: Hard-sync against Alpaca to determine ground truth.
                    logging.error(
                        "[User %s] CRITICAL: Post-SELL portfolio state update failed for %s: %s"
                        " — initiating hard-sync against broker to prevent ghost position.",
                        user_id,
                        symbol,
                        e,
                    )
                    try:
                        # Broker is source of truth: if position no longer exists,
                        # force-clear local state to prevent duplicate SELL next cycle.
                        # NB: the return value is intentionally discarded — the SIGNAL is
                        # the exception path below (APIError 404 = position gone = SELL
                        # landed). A normal return means the position is still open, which
                        # the lines beneath this call treat as a genuine inconsistency.
                        await asyncio.to_thread(client.get_open_position, symbol)
                        # Position still open at broker → state is genuinely inconsistent.
                        logging.error(
                            "[User %s] Hard-sync: %s position STILL OPEN at broker after SELL."
                            " Manual intervention required.",
                            user_id,
                            symbol,
                        )
                    except APIError as api_err:
                        # ADR-ENG-07 / POLICY-01: ONLY a 404 (position-not-found)
                        # or Alpaca error code 40410000 confirms the SELL landed.
                        # A 429 Rate-Limit or 504 Gateway Timeout MUST NOT clear state —
                        # that would create a Ghost Position on the next cycle.
                        #
                        # APIError.status_code → http_error.response.status_code (read-only)
                        # APIError.code        → json.loads(self._error)["code"] (read-only)
                        status_code = api_err.status_code  # None if no http_error
                        try:
                            error_code = (
                                api_err.code
                            )  # raises if _error is not valid JSON
                        except Exception:
                            error_code = None
                        if status_code == 404 or error_code == 40410000:
                            logging.warning(
                                "[User %s] Hard-sync: %s confirmed SOLD at broker "
                                "(APIError 404/40410000). Force-clearing local portfolio state.",
                                user_id,
                                symbol,
                            )
                            try:
                                pm = self._get_tenant_portfolio_manager(
                                    user_id, client, equity
                                )
                                pm.record_trade(symbol, "sell")
                                await persist_pm_state_to_redis(
                                    pm, symbol, redis_client
                                )
                                pm.clear_sell_signals_after_sale(symbol)
                            except Exception as force_e:
                                logging.error(
                                    "[User %s] Force-clear also failed for %s: %s"
                                    " — state remains inconsistent, alerting via PubSub.",
                                    user_id,
                                    symbol,
                                    force_e,
                                )
                        else:
                            # Other API errors (429 Rate-Limit, 5xx server error) MUST NOT
                            # clear local state — position may still be open at broker.
                            logging.error(
                                "[User %s] Hard-sync check for %s returned unexpected APIError "
                                "(status=%s code=%s) — NOT treating as confirmed SELL.",
                                user_id,
                                symbol,
                                status_code,
                                error_code,
                            )
                            raise api_err

                    try:
                        _redis = await RedisClient.get_redis()
                        await _safe_publish(
                            _redis,
                            f"explainability:{user_id}",
                            json.dumps(
                                {
                                    "type": "state_inconsistency",
                                    "title": f"Portfolio State Error: {symbol}",
                                    "message": f"SELL executed but post-trade bookkeeping failed: {e}",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                    except Exception as redis_err:
                        logging.warning(
                            "[User %s] Failed to publish state_inconsistency alert "
                            "to Redis for %s: %s",
                            user_id,
                            symbol,
                            redis_err,
                        )

            elif action == "BUY":
                try:
                    pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                    pm.record_trade(symbol, "buy")
                    await persist_pm_state_to_redis(pm, symbol, redis_client)
                    conv = getattr(context, "conviction_score", 0.5) if context else 0.5
                    pm.update_position_conviction(symbol, conv)
                except Exception as e:
                    logging.warning(
                        "[User %s] Post-trade conviction update failed for %s: %s",
                        user_id,
                        symbol,
                        e,
                    )

            logging.info(
                f"[User {user_id}] ✅ Executed {action} {size} shares for {symbol}. OrderID: {order.id}"
            )
            try:
                redis = await RedisClient.get_redis()
                await _safe_publish(
                    redis,
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
            except Exception as redis_err:
                logging.warning(
                    "[User %s] Failed to publish trade_executed event to Redis: %s",
                    user_id,
                    redis_err,
                )

        except Exception as e:
            # PR A.2: fail-safe — count as a submit failure ONLY when the live
            # broker submit itself was reached (pre-submit guards return earlier and
            # never set the flag). Bump is first + wrapped so it cannot mask the log.
            if _submit_attempted:
                _safe_bump_exec("submit_fail")
            logging.error(
                f"[User {user_id}] ❌ Order Execution Failed for {symbol}: {e}"
            )
            try:
                redis = await RedisClient.get_redis()
                if order_submitted:
                    await _safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "state_inconsistency",
                                "severity": "CRITICAL",
                                "title": f"API Error: {symbol}",
                                "message": f"Broker API rejected order: {e}",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                else:
                    await _safe_publish(
                        redis,
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
            except Exception as redis_err:
                logging.warning(
                    "[User %s] Failed to publish trade_rejected event to Redis: %s",
                    user_id,
                    redis_err,
                )
            # ADR-ENG-07: Single-symbol failure MUST NOT crash the trading loop.
            # Exception is fully logged above + published to Redis PubSub explainability channel.
            # asyncio.gather(return_exceptions=True) in _process_signal_event catches this.

        finally:
            if lock and acquired:
                try:
                    await lock.release()
                except Exception:
                    logging.exception("Lock release failed")

        # PR-0a-ii-5a: report whether the order reached the broker, so the human-approval
        # drain (execute_approved_order) audits "approved" (submitted) vs "iron_dome_rejected"
        # (a guard blocked it, P3). Pre-submit guard returns above yield None (not submitted);
        # the post-submit unfilled-cancel path returns True (it reached the market).
        return order_submitted

    async def execute_approved_order(
        self, payload: Dict[str, Any], source: str = "human_approved"
    ) -> bool:
        """Execute a human-approved order drained from the HITL queue (PR-0a-ii-5a, Art. 14).

        Bypasses the HITL gate — routing an approved order back through _process_signal_event
        would re-evaluate the threshold and re-queue it (N1). Resolves the tenant by
        payload["user_id"], builds a synthetic SignalEvent (N8), and delegates to
        _execute_tenant_order(source="human_approved") — which skips ONLY the daily-cap gate
        + the autonomous-budget increment; RiskManager / PortfolioManager / check_order /
        kill-switch still apply. The outcome is audited on the Art-14 hash chain.

        Decision-2 Option B (N11): on an OSS engine with no matching OAuth tenant, HOLD + warn
        + audit "rejected" — never reuse the inline global path. Returns True iff submitted.
        """
        from core import hitl_gate
        from core.cloud_logger import DecisionContext
        from core.round_table.senate_log import HITLExecutionEvent

        user_id = payload.get("user_id", "")
        symbol = payload.get("symbol", "")
        action = payload.get("action", "")
        qty = float(payload.get("qty", 0.0) or 0.0)
        price = float(payload.get("price", 0.0) or 0.0)
        order_value = abs(qty) * price
        pol_hash = hitl_gate.policy_hash(hitl_gate.policy_snapshot())

        async def _audit(branch: str, reason: Optional[str] = None) -> None:
            await hitl_gate.log_execution_event(
                HITLExecutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    symbol=symbol,
                    action=action,
                    branch=branch,
                    policy_hash=pol_hash,
                    order_value=order_value,
                    approval_id=payload.get("approval_id"),
                    reason=reason,
                )
            )

        # SHOULD-FIX (review): a malformed payload must never reach the broker path that would
        # silently re-derive a size. An unvaluable order — no price, or a BUY with no quantity —
        # is rejected + audited, not executed on a guess.
        if price <= 0 or (action == "BUY" and qty <= 0):
            logging.warning(
                "[HITL] approved order %s %s: malformed payload (price=%.4f qty=%.4f) — refusing.",
                action,
                symbol,
                price,
                qty,
            )
            await _audit("rejected", reason="malformed_payload")
            return False

        active_tenants = await self.get_active_tenant_clients()
        tenant = next((t for t in active_tenants if t.get("user_id") == user_id), None)
        if tenant is None:
            logging.warning(
                "[HITL] approved order %s %s: no OAuth tenant for user_id=%s — refusing to "
                "execute (HITL approval requires OAuth-tenant setup).",
                action,
                symbol,
                user_id,
            )
            await _audit("rejected", reason="no_oauth_tenant")
            return False

        context = DecisionContext(
            symbol=symbol,
            action=action,
            current_price=price,
            conviction_score=float(payload.get("conviction", 0.0) or 0.0),
            risk_approved=True,
            portfolio_approved=True,
            intelligence_approved=True,
        )
        # Broker-side idempotency (DD F3): stamp the approval_id as the deterministic
        # client_order_id, so a re-submission of the SAME approved order — an accidental
        # double-drain, or a future auto-recovery of an orphaned in-flight approval — is
        # rejected by the broker as a duplicate client_order_id. A single human approval can
        # therefore never execute twice, even if the same payload reaches the broker path more
        # than once. (Default factory would otherwise mint a fresh uuid4 per call.)
        _approval_cid = str(payload.get("approval_id") or "").strip()
        if _approval_cid:
            # Cap at Alpaca's client_order_id limit (128). The real source is always a uuid4
            # (~41 chars incl. the prefix) so this never truncates in practice; it is a
            # defensive floor so a malformed/overlong approval_id can never produce a
            # broker-rejected order id that would block a legitimate human-approved order.
            context.client_order_id = f"hitl-{_approval_cid}"[:128]
        event = SignalEvent(
            symbol=symbol,
            action=action,
            suggested_quantity=qty,
            decision_context=context,
            is_simulation=False,
        )
        # BLOCKER (review): record the human-approval decision on the immutable chain BEFORE the
        # order can reach the broker — capital must never move without a prior Art-14 record (the
        # audit logger can suspend on disk-full; pop_approved already deleted the queue key). The
        # pinned ceiling (ADR-016) makes the audited order_value == what the human authorised.
        await _audit("approved")
        submitted = await self._execute_tenant_order(tenant, event, source=source)
        if not submitted:
            # The approved order was blocked at execution (Iron Dome / risk / position guard —
            # the specific guard is logged by _execute_tenant_order). Never silently dropped (P3).
            await _audit("iron_dome_rejected", reason="execution_blocked")
        return bool(submitted)

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
            # HITL autonomy-policy gate (PR-0a-ii-4b, EU AI Act Art. 14). Default-DORMANT:
            # HITL_ENABLED defaults False ⇒ the gate is never reached and the execution path
            # below is byte-identical. When enabled, an order over the configured per-trade /
            # per-day limits is queued for human approval instead of executed; a HOLD short-
            # circuits the entire execution path for this signal (fail-closed on any error).
            if config.get_config().HITL_ENABLED:
                from core import hitl_gate

                if await hitl_gate.should_hold(
                    event, getattr(self, "active_uid", None) or "global"
                ):
                    return

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
                                pos = await asyncio.to_thread(
                                    trade_client.get_open_position, symbol
                                )
                                qty = (
                                    float(pos.qty)
                                    if hasattr(pos, "qty")
                                    else float(pos.get("qty", 0.0))
                                )
                            except Exception as e:
                                logging.warning(
                                    "[%s] Could not fetch open position qty: %s — defaulting to 0",
                                    symbol,
                                    e,
                                )
                                qty = 0.0
                        elif action == "BUY" and getattr(
                            self, "live_risk_manager", None
                        ):
                            try:
                                curr = context.current_price if context else 0.0
                                acc = await asyncio.to_thread(trade_client.get_account)
                                cash = float(getattr(acc, "cash", 0) or 0)
                                atr = (
                                    getattr(context, "atr_14d", 0.0) if context else 0.0
                                )
                                if atr <= 0:
                                    atr = curr * 0.05
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
                                _rec_outcome(
                                    symbol,
                                    "blocked:order_value",
                                    "Order value limit exceeded.",
                                )
                                approved = False
                            # PR-0a-ii-5a (HITL): this global path is AUTONOMOUS-ONLY.
                            # execute_approved_order (HITL drain) never reaches here —
                            # Decision-2 Option B routes it exclusively through
                            # _execute_tenant_order (source-guarded). No `source`
                            # parameter needed here; adding one would be misleading.
                            elif not self.compliance_guardian.check_trade(
                                compliance_order
                            ):
                                logging.warning(
                                    f"[Global] {symbol} 🛡️ BLOCKED ComplianceGuardian (daily)"
                                )
                                if not getattr(
                                    self.compliance_guardian,
                                    "_daily_limit_alert_sent",
                                    False,
                                ):
                                    try:
                                        from core.notifier import send_slack_alert

                                        msg = f"Compliance: Max daily trades ({self.compliance_guardian.max_daily_trades}) reached. Trading halted."
                                        asyncio.create_task(
                                            asyncio.to_thread(
                                                send_slack_alert,
                                                f"🛑 *Iron Dome Block*: {msg}",
                                                level="warning",
                                            )
                                        )
                                        self.compliance_guardian._daily_limit_alert_sent = (
                                            True
                                        )
                                    except Exception as alert_err:
                                        logging.error(
                                            f"Failed to send Iron Dome Slack alert: {alert_err}"
                                        )
                                _rec_outcome(
                                    symbol,
                                    "blocked:daily_limit",
                                    "Daily trade limit reached.",
                                )
                                approved = False
                            else:
                                # #1849 follow-up: atomic, lock-guarded increment
                                # (see record_trade — the bare ``+= 1`` RMW lost
                                # increments under concurrency). +1 per trade, as before.
                                self.compliance_guardian.record_trade()

                        if approved:
                            side_enum = (
                                OrderSide.BUY if action == "BUY" else OrderSide.SELL
                            )
                            # Defend against MagicMock from tests breaking Pydantic validation
                            client_order_id = (
                                getattr(context, "client_order_id", None)
                                if context
                                else None
                            )
                            client_order_id = (
                                client_order_id
                                if isinstance(client_order_id, str)
                                else str(uuid.uuid4())
                            )
                            req = MarketOrderRequest(
                                symbol=symbol,
                                qty=qty,
                                side=side_enum,
                                time_in_force=TimeInForce.DAY,
                                client_order_id=client_order_id,
                            )

                            # --- FIX: Kill Switch Gate ---
                            kill_switch.check_halt(uid or "global")

                            # Shadow Mode Interception for global/fallback
                            if getattr(config, "SHADOW_MODE", False):
                                logging.info(
                                    f"[Global] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
                                )
                                order = DryRunOrderProxy("global").submit_order(req)
                                context.alpaca_order_id = str(order.id)
                            else:
                                order = await asyncio.to_thread(
                                    trade_client.submit_order, req
                                )
                                context.alpaca_order_id = str(order.id)
                            _rec_outcome(symbol, "executed")

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
                        results = await asyncio.gather(
                            *exec_tasks, return_exceptions=True
                        )
                        failed = [
                            (active_tenants[i]["user_id"], r)
                            for i, r in enumerate(results)
                            if isinstance(r, Exception)
                        ]
                        if failed:
                            for uid_f, exc_f in failed:
                                logging.error(
                                    "[%s] Tenant %s execution raised: %s",
                                    symbol,
                                    uid_f,
                                    exc_f,
                                )
                        logging.info(
                            f"[{symbol}] Async fan-out: {len(exec_tasks)} tenants, "
                            f"{len(failed)} failed."
                        )
                        context.alpaca_order_id = "multi-tenant-batch"
            except Exception as e:
                logging.error("Multi-tenant execution failed for %s: %s", symbol, e)
                context.alpaca_order_id = f"failed: {str(e)[:50]}"

        if should_log and not event.is_simulation:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.cloud_logger.log_decision, context)
