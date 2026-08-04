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
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

try:
    from core.client_factory import create_trading_client
except ImportError:
    from ai_trading_bot.core.client_factory import create_trading_client

from core.kill_switch import kill_switch
from core.protocols import BrokerClientProtocol
from core.risk_manager import (
    apply_vol_targeting_audit,
    effective_free_slots,
    effective_max_positions,
)
from core.telemetry import get_tracer
from core.telemetry_attrs import order_span_attributes

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


def _rec_outcome(
    symbol: str, code: str, reason: str = "", decision_id: Optional[str] = None
) -> None:
    """RQ-1 (#1516): record the FINAL execution outcome per symbol for the decision
    badge — Iron-Dome / risk / kill-switch result (display-only; see
    core/round_table/execution_outcomes.py). PURE OBSERVATION: double-guarded so it
    can NEVER raise into the trading path; nothing here changes an order decision.
    ``decision_id`` (#2113, optional): stamps the record so the durable
    decision_outcomes capture joins per decision_id (legacy call sites unchanged)."""
    try:
        from core.round_table.execution_outcomes import record_execution_outcome

        record_execution_outcome(symbol, code, reason, decision_id=decision_id)
    except Exception:  # noqa: BLE001 — observation must never alter execution flow
        pass
    try:  # #2548 sim: capture the outcome for the runner (blocked + reasons + fills). No-op off-sim.
        from core.sim.recorder import get_recorder

        get_recorder().record(symbol, code, reason)
    except Exception:  # noqa: BLE001 — observation must never alter execution flow
        pass


def _capture_enabled() -> bool:
    """#2113: is the decision-outcome capture flag ON? Fail-safe: any config
    error reads as OFF — the new outcome codes then stay unemitted, exactly the
    flag-off (byte-identical) posture."""
    try:
        import config as _cfg

        return bool(getattr(_cfg.get_config(), "DECISION_CAPTURE_ENABLED", False))
    except Exception:  # noqa: BLE001 — an unreadable flag must behave as OFF
        return False


def _capture_outcome(context) -> None:
    """#2113: enqueue the durable decision_outcomes capture row for this decision
    (flag-gated inside the capture module, default OFF). PURE OBSERVATION:
    double-guarded lazy import so it can NEVER raise into the trading path."""
    try:
        from core.decision_capture.capture import capture_decision_outcome

        capture_decision_outcome(context)
    except Exception:  # noqa: BLE001 — observation must never alter execution flow
        pass


# --- #2585 (P0): SKIPPED-signal audit trail (PURE OBSERVATION) ----------------
# ADR-OBS-02: an APPROVED Round-Table signal that reaches the execution layer and
# then does NOT execute must never vanish silently again. Incident 2026-07-30:
# an approved INCY BUY ("BUY - 91% consensus, Approved" on the audit chain,
# 16:29) died at the tenant `size <= 0` guard below — a DEBUG-only line — with
# the account at cash -39k$ / 133% invested. No WARNING, no audit entry, no
# ComplianceGuardian call, no order. The chain's last word was "Approved".
#
# Closed, deliberately SMALL reason taxonomy — consumers may rely on exactly
# this set (do not grow it casually; map new causes to `other` + detail):
SKIP_REASON_INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
# `below_order_floor` is RESERVED: the sizer's $1 fractional floor
# (risk_manager.py `min_fractional_shares`) returns 0 shares, indistinguishable
# from any other zero at this seam — it therefore surfaces as `sizing_zero`
# today. The code stays in the taxonomy so chain consumers have a stable set
# when the sizer one day distinguishes the floor (#2585 is observability-only:
# the sizer itself must not change here).
SKIP_REASON_BELOW_ORDER_FLOOR = "below_order_floor"
SKIP_REASON_SIZING_ZERO = "sizing_zero"
SKIP_REASON_EXECUTION_ERROR = "execution_error"
SKIP_REASON_OTHER = "other"


async def _audit_skipped_signal(
    symbol: str,
    action: str,
    reason_code: str,
    detail: str = "",
    order_value: float = 0.0,
) -> None:
    """#2585: make the NON-execution of an approved signal visible.

    Emits (a) a WARNING log (never DEBUG — CLAUDE.md 5.6) naming symbol, side and
    reason, and (b) a ``SKIPPED: <reason>`` ``HITLExecutionEvent`` (branch
    ``"skipped"``) on the SAME tamper-evident Art-14 hash chain that carries the
    Round-Table approval — reusing the EXISTING chain writer
    ``hitl_gate.log_execution_event`` (no second writer, the chain stays
    single-writer). The chain then shows approval and non-execution side by side.

    PURE OBSERVATION: best-effort, double-guarded, never raises into the order
    path; a failed chain write degrades to the WARNING alone. Lazy imports keep
    a broken audit module from ever taking the executor down (mirrors
    ``_rec_outcome``).
    """
    detail = str(detail or "")[:200]
    logging.warning(
        "[%s] SKIPPED approved %s signal: %s%s -- no order submitted (#2585).",
        symbol,
        action,
        reason_code,
        f" ({detail})" if detail else "",
    )
    try:
        from core import hitl_gate
        from core.round_table.senate_log import HITLExecutionEvent

        await hitl_gate.log_execution_event(
            HITLExecutionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=symbol,
                action=action,
                branch="skipped",
                policy_hash=hitl_gate.policy_hash(hitl_gate.policy_snapshot()),
                order_value=float(order_value or 0.0),
                reason=(
                    f"SKIPPED: {reason_code} -- {detail}"
                    if detail
                    else f"SKIPPED: {reason_code}"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must never break the path
        logging.warning(
            "[%s] #2585 skip-audit chain write failed (%s): %s",
            symbol,
            reason_code,
            exc,
        )


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
        try:
            from core.client_factory import create_trading_client
        except ImportError:
            from ai_trading_bot.core.client_factory import create_trading_client
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
                    user_client = create_trading_client(
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
                user_client = create_trading_client(
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
            # Phase B (ADR-FU02): with the full universe the gremium judges ~500
            # names, so far more clear the bar than there is capital for. The slot
            # count IS the portfolio's scarcity — a better chance must DISPLACE the
            # weakest holding (debate_position_swap already arbitrates that) rather
            # than the book growing unbounded. Only the READ is overridden here;
            # config.MAX_POSITIONS itself stays 10, so the default path is unchanged.
            if config.get_config().FULL_UNIVERSE_TRADING_ENABLED:
                max_positions = int(
                    getattr(config.get_config(), "FULL_UNIVERSE_MAX_POSITIONS", 10)
                )
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

    async def _recover_displacement(
        self,
        user_id: str,
        client: Any,
        pm: Any,
        redis_client: Any,
        symbol_to_close: str,
        displacement_sell_order_id: Optional[str],
        displacement_sell_qty: float,
    ) -> None:
        """Reactive recovery flow for Issue #2189."""
        if not symbol_to_close:
            return

        logging.info(
            f"[User {user_id}] Initiating displacement recovery for {symbol_to_close}."
        )

        filled_qty = displacement_sell_qty
        if displacement_sell_order_id:
            try:
                # 1. Attempt to cancel the SELL order (just in case it is somehow not fully filled yet)
                try:
                    await asyncio.to_thread(
                        client.cancel_order_by_id, displacement_sell_order_id
                    )
                    logging.info(
                        f"[User {user_id}] Dispatched cancel for displacement SELL order {displacement_sell_order_id}"
                    )
                except APIError as ce:
                    logging.debug(
                        f"[User {user_id}] Cancel of displacement SELL rejected (likely already filled): {ce}"
                    )

                # 2. Fetch the actual filled quantity
                live_sell_order = await asyncio.to_thread(
                    client.get_order_by_id, displacement_sell_order_id
                )
                if live_sell_order:
                    filled_qty = float(
                        getattr(live_sell_order, "filled_qty", 0.0) or 0.0
                    )
                    logging.info(
                        f"[User {user_id}] Displacement SELL order {displacement_sell_order_id} filled qty: {filled_qty}"
                    )
            except Exception as err:
                logging.warning(
                    f"[User {user_id}] Failed to cancel/query displacement SELL order {displacement_sell_order_id}: {err}. "
                    f"Falling back to original qty: {filled_qty}"
                )

        if filled_qty <= 0:
            logging.info(
                f"[User {user_id}] Displacement recovery skipped: filled quantity is 0."
            )
            return

        # 3. Submit compensating re-BUY order
        try:
            re_buy_req = MarketOrderRequest(
                symbol=symbol_to_close,
                qty=filled_qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            # Kill-switch (#2467): the compensating re-BUY is still a broker order, so it must
            # honour a tripped circuit breaker like every other submit (mirrors :807). A genuine
            # safety trip keeps the account flat rather than autonomously re-establishing the
            # position we just sold — "no orders after emergency stop" must be airtight.
            kill_switch.check_halt(user_id)
            await asyncio.to_thread(client.submit_order, re_buy_req)
            logging.info(
                f"[User {user_id}] Displacement recovery re-BUY submitted for {filled_qty} shares of {symbol_to_close}."
            )

            # 4. Port Manager Re-sync
            if pm is not None:
                pm.record_trade(symbol_to_close, "buy")
                await persist_pm_state_to_redis(pm, symbol_to_close, redis_client)
        except Exception as re_buy_err:
            logging.critical(
                f"[User {user_id}] 🚨 CRITICAL: Displacement recovery failed! "
                f"Could not re-buy {filled_qty} shares of {symbol_to_close}: {re_buy_err}"
            )
            try:
                redis = await RedisClient.get_redis()
                if redis:
                    await _safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "state_inconsistency",
                                "severity": "CRITICAL",
                                "title": f"Recovery Failure: {symbol_to_close}",
                                "message": f"Failed to submit recovery re-BUY for {symbol_to_close} after failed swap: {re_buy_err}",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
            except Exception as redis_e:
                logging.warning("PubSub error: %s", redis_e)

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
            displacement_sell_order_id = None
            displacement_sell_qty = 0.0
            rm = self._get_tenant_risk_manager(user_id, client, equity)
            curr = context.current_price if context else 0.0
            if curr <= 0:
                logging.warning(
                    "⚠️ OrderExecutor: Skipping %s %s - Missing current_price in DecisionContext",
                    action,
                    symbol,
                )
                # #2585: approved signal will not execute — audit the skip.
                await _audit_skipped_signal(
                    symbol,
                    action,
                    SKIP_REASON_OTHER,
                    "missing current_price in DecisionContext",
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
                    # #2585: approved SELL held back by anti-churn — audit the skip.
                    await _audit_skipped_signal(
                        symbol, action, SKIP_REASON_OTHER, f"anti-churn: {reason}"
                    )
                    return

                # #2553: capture the REAL broker holding as an INDEPENDENT held-qty witness for the
                # compliance exit-exemption, BEFORE any human-approved down-sizing below. Echoing the
                # (reducible) order `size` made quantity<=held_qty a tautology — a SELL exceeding the
                # position would self-exempt. A fetch failure leaves it 0.0 → the exemption fails closed.
                held_broker_qty = 0.0
                try:
                    pos = await asyncio.to_thread(client.get_open_position, symbol)
                    size = (
                        float(pos.qty)
                        if hasattr(pos, "qty")
                        else float(pos.get("qty", 0.0))
                    )
                    held_broker_qty = abs(size)
                except Exception as e:
                    # #2118: 404 / Alpaca code 40410000 = position already flat → clean "closed"
                    # (was a silent "pending" — Archon Warning #2); genuine errors → "error".
                    # Still aborts (fail-safe, no submit).
                    _status = getattr(e, "status_code", None)
                    try:
                        _code = e.code  # APIError.code may raise on non-JSON body
                    except Exception:
                        _code = None
                    if _status == 404 or _code == 40410000:
                        logging.info(
                            "[User %s] %s SELL skipped — no open position (already flat/closed).",
                            user_id,
                            symbol,
                        )
                        _rec_outcome(
                            symbol, "closed", "no open position — already flat"
                        )
                        # #2585: approved SELL cannot execute (already flat) — audit.
                        await _audit_skipped_signal(
                            symbol,
                            action,
                            SKIP_REASON_OTHER,
                            "no open position (already flat/closed)",
                        )
                    else:
                        logging.warning(
                            "[User %s] Could not fetch open position for %s: %s — aborting SELL.",
                            user_id,
                            symbol,
                            e,
                        )
                        _rec_outcome(symbol, "error", f"position fetch failed: {e}")
                        # #2585: approved SELL aborted on a broker error — audit.
                        await _audit_skipped_signal(
                            symbol,
                            action,
                            SKIP_REASON_EXECUTION_ERROR,
                            f"position fetch failed: {e}",
                        )
                    return  # Abort immediately — do not submit MarketOrderRequest(qty=0)
            else:
                account = await asyncio.to_thread(client.get_account)
                cash = float(getattr(account, "cash", 0) or 0)
                # #2389: parity with the global fallback call-site below — context.atr_14d
                # EXISTS as 0.0 on the neutral default snapshot, so the getattr default
                # never fired and calculate_position_size returned 0 shares (silent
                # no-trade at the `size <= 0` return). Treat atr<=0 (or a non-numeric
                # value) like a missing ATR and fall back to the conservative
                # 5%-of-price proxy.
                atr = getattr(context, "atr_14d", 0.0) if context else 0.0
                if not isinstance(atr, (int, float)) or atr <= 0:
                    atr = curr * 0.05
                vix = getattr(context, "vix_level", 20.0) if context else 20.0
                conviction = (
                    getattr(context, "conviction_score", 0.5) if context else 0.5
                )
                # #1953: HAR-RV forward-vol plumbed via the DecisionContext
                # (runner._score_to_signal); None => fail-safe no-op in the sizer.
                forecast_vol = (
                    getattr(context, "forecast_vol", None) if context else None
                )

                size = rm.calculate_position_size(
                    stop_loss_atr_multiplier=3.0,
                    atr=atr,
                    confidence="high",
                    size_scaler=1.0,
                    market_data={"vix": vix},
                    # #2153: split cash by the BOOK CAP (max_positions), not the ~500-name
                    # scan universe — the book holds at most max_positions names, so the old
                    # len(live_universe) divisor under-sized every order ~50x → fractional churn.
                    # O3 (CASH_AWARE_SLOTS_ENABLED): split by the REMAINING free slots (cap − held),
                    # not the fixed cap, so a mostly-invested book funds proper-sized positions. Held
                    # count = the tenant PM's last-refreshed positions (no new broker call); getattr-
                    # safe (missing/None → 0 → effective_free_slots returns the cap = byte-identical).
                    num_stocks_in_strategy=effective_free_slots(
                        len(getattr(pm, "_position_scores", {}) or {}),
                        positions_confirmed=getattr(pm, "_last_refresh_ok", True),
                    ),
                    current_price=curr,
                    account_cash=cash,
                    allow_fractional=True,
                    conviction_score=conviction,
                    forecast_vol=forecast_vol,
                )
                # #1953 audit mirror (MiFID II Art. 17): record the APPLIED vol
                # scaler in context.risk_size_scaler — no-op when flag off/1.0.
                apply_vol_targeting_audit(context, forecast_vol)

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
                    # #2585: approved BUY declined by the PortfolioManager — before
                    # this, the drop was DEBUG-only + Redis-only (a true silent skip).
                    await _audit_skipped_signal(
                        symbol,
                        action,
                        SKIP_REASON_OTHER,
                        f"portfolio declined: {reasoning}",
                        order_value=abs(size) * curr,
                    )
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
                            old_price = (
                                float(old_pos.current_price)
                                if hasattr(old_pos, "current_price")
                                else float(old_pos.get("current_price", 0.0))
                            )
                            if old_qty > 0:
                                # Preventive Precheck:
                                multiplier_str = getattr(account, "multiplier", "1")
                                multiplier_val = (
                                    float(multiplier_str) if multiplier_str else 1.0
                                )
                                buying_power_val = float(
                                    getattr(account, "buying_power", 0.0) or 0.0
                                )

                                sell_value = old_qty * old_price
                                if multiplier_val == 1.0:
                                    buying_power_available = buying_power_val
                                else:
                                    buying_power_available = buying_power_val + (
                                        sell_value * multiplier_val
                                    )

                                required_buying_power = size * curr
                                if buying_power_available < required_buying_power:
                                    logging.warning(
                                        f"[User {user_id}] ⚠️ Displacement pre-check failed. "
                                        f"Required BP: {required_buying_power}, Available BP (post-sell estimate): {buying_power_available}. "
                                        f"Aborting swap for {symbol_to_close} -> {symbol}."
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
                                                        "title": f"Precheck Blocked: {symbol}",
                                                        "message": f"Insufficient settled buying power (multiplier={multiplier_str}). Available: {buying_power_available}, Required: {required_buying_power}",
                                                        "timestamp": datetime.now(
                                                            timezone.utc
                                                        ).isoformat(),
                                                    }
                                                ),
                                            )
                                    except Exception as redis_e:
                                        logging.warning("PubSub error: %s", redis_e)
                                    # #2585: approved BUY dropped at the buying-power
                                    # precheck — audit the skip.
                                    await _audit_skipped_signal(
                                        symbol,
                                        action,
                                        SKIP_REASON_INSUFFICIENT_BUYING_POWER,
                                        f"required={required_buying_power:.2f} "
                                        f"available={buying_power_available:.2f}",
                                        order_value=required_buying_power,
                                    )
                                    return

                                req_close = MarketOrderRequest(
                                    symbol=symbol_to_close,
                                    qty=old_qty,
                                    side=OrderSide.SELL,
                                    time_in_force=TimeInForce.DAY,
                                )
                                # #2467 FIX 5: re-check the kill switch IMMEDIATELY before the
                                # displacement-SELL. It is submitted BEFORE the main gate (~:996), so a
                                # trip landing during the many awaits above would otherwise leak one
                                # real SELL through. Mirrors the fallback-path pre-SELL gate (#2252).
                                kill_switch.check_halt(user_id)
                                sell_order = await asyncio.to_thread(
                                    client.submit_order, req_close
                                )
                                displacement_sell_order_id = sell_order.id
                                displacement_sell_qty = old_qty

                                pm.record_trade(symbol_to_close, "sell")
                                await persist_pm_state_to_redis(
                                    pm, symbol_to_close, redis_client
                                )
                                await asyncio.sleep(0.5)
                    except Exception as swap_e:
                        logging.warning(
                            f"[User {user_id}] Failed to swap out {symbol_to_close}: {swap_e}"
                        )
                        # #2585: approved BUY dies with the failed displacement — audit.
                        await _audit_skipped_signal(
                            symbol,
                            action,
                            SKIP_REASON_EXECUTION_ERROR,
                            f"displacement swap of {symbol_to_close} failed: {swap_e}",
                        )
                        return

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
                # #2069 gap-fix: RECORD the primary risk block (size<=0) — this path
                # published to Redis only, so BLOCKED_RISK never reached the outcome
                # store (and thus never the telemetry choke-point). Double-guarded,
                # display/observability-only — behaviour (early return) unchanged.
                _rec_outcome(symbol, "blocked:risk", reason_msg)
                # #2585: THE INCY drop point (2026-07-30). The DEBUG line above is
                # invisible at the default INFO level, and nothing here reached the
                # audit chain — the approved BUY simply ceased to exist. WARNING +
                # SKIPPED entry; the early return itself is unchanged (fail-safe).
                await _audit_skipped_signal(
                    symbol, action, SKIP_REASON_SIZING_ZERO, reason_msg
                )
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
                    # #2065/#2553: the REAL broker-held qty for a SELL (independent witness, NOT an
                    # echo of the possibly-reduced order size) — bounds the ComplianceGuardian
                    # exit-exemption to a genuinely risk-reducing exit (quantity <= held_qty).
                    "held_qty": held_broker_qty if action == "SELL" else 0.0,
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
                    # ADR-C04-EXIT: pass the order so a risk-reducing exit does not
                    # consume a buy slot (symmetric with the check_trade block-exemption).
                    self.compliance_guardian.record_trade(compliance_order)

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
            try:
                kill_switch.check_halt(user_id)
            except Exception:
                # #2113 (flag-gated, PURE OBSERVATION): persist WHY the order died —
                # blocked:kill_switch was defined but never emitted. The re-raise
                # keeps the gate's control flow byte-identical to main.
                if _capture_enabled():
                    _rec_outcome(
                        symbol,
                        "blocked:kill_switch",
                        "kill-switch halt — order blocked",
                        decision_id=getattr(context, "decision_id", None),
                    )
                raise

            # Shadow Mode Interception
            if getattr(config, "SHADOW_MODE", False):
                logging.info(
                    f"[User {user_id}] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
                )
                with tracer.start_as_current_span("broker.submit_order.live") as span:
                    span.set_attribute("trade.action", action)
                    for _k, _v in order_span_attributes(symbol, action, req).items():
                        span.set_attribute(_k, _v)
                    order = DryRunOrderProxy(user_id).submit_order(req)
                if context:
                    context.alpaca_order_id = str(order.id)
                    # Part B (exec-visibility): dry-run submit is still an executed decision.
                    context.action_executed = True
                    context.is_simulation = True
            else:
                with tracer.start_as_current_span("broker.submit_order.live") as span:
                    span.set_attribute("trade.action", action)
                    for _k, _v in order_span_attributes(symbol, action, req).items():
                        span.set_attribute(_k, _v)
                    _submit_attempted = True
                    try:
                        order = await asyncio.to_thread(client.submit_order, req)
                    except APIError as submit_err:
                        if symbol_to_close and displacement_sell_order_id:
                            await self._recover_displacement(
                                user_id=user_id,
                                client=client,
                                pm=pm,
                                redis_client=redis_client,
                                symbol_to_close=symbol_to_close,
                                displacement_sell_order_id=displacement_sell_order_id,
                                displacement_sell_qty=displacement_sell_qty,
                            )
                        # #2118 Reserve-and-Reclaim: deterministic 4xx broker rejection → give back
                        # the daily slot reserved for this autonomous order. Ambiguous 5xx/timeout
                        # keeps the reservation (fill may have landed → fail-closed).
                        _sc = getattr(submit_err, "status_code", None)
                        if (
                            self.compliance_guardian
                            and source != "human_approved"
                            and _sc is not None
                            and 400 <= _sc < 500
                        ):
                            self.compliance_guardian.refund_trade()
                        raise
                    # PR A.2: fail-safe — the order reached the broker.
                    _safe_bump_exec("submit_ok")
                    _rec_outcome(symbol, "executed")

                    if context:
                        context.alpaca_order_id = str(order.id)
                        context.action_executed = True
                        context.is_simulation = False

                    # EXC-1: Task 3 & 4 - Order Lifecycle Polling & Synchronisation
                    from alpaca.trading.enums import OrderStatus

                    max_wait_seconds = 120
                    poll_interval = 2
                    waited = 0
                    is_filled = False
                    live_order = None  # #2118: last observed order state (for cancel-0-fills refund)

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

                        if symbol_to_close and displacement_sell_order_id:
                            await self._recover_displacement(
                                user_id=user_id,
                                client=client,
                                pm=pm,
                                redis_client=redis_client,
                                symbol_to_close=symbol_to_close,
                                displacement_sell_order_id=displacement_sell_order_id,
                                displacement_sell_qty=displacement_sell_qty,
                            )

                        # #2118 Reserve-and-Reclaim: the order reached the broker but was cancelled
                        # UNFILLED → give back the reserved daily slot. Guard strictly: refund ONLY on
                        # CONFIRMED 0 fills — a partial fill DID trade (keep the reservation), and an
                        # unknown fill state (never polled) also keeps it (fail-closed).
                        _filled_qty = None
                        try:
                            if live_order is not None:
                                _filled_qty = float(
                                    getattr(live_order, "filled_qty", 0) or 0
                                )
                        except Exception:
                            _filled_qty = None
                        if (
                            self.compliance_guardian
                            and source != "human_approved"
                            and _filled_qty == 0.0
                        ):
                            self.compliance_guardian.refund_trade()

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
            # #2585: an approved signal that never reached the broker died in this
            # swallower with an ERROR log only — no audit-chain record. Audit it.
            # order_submitted=True means the order DID reach the market (post-trade
            # bookkeeping failed) — that is not a skip, so no entry then.
            if not order_submitted:
                await _audit_skipped_signal(
                    symbol, action, SKIP_REASON_EXECUTION_ERROR, str(e)
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

    async def _market_closed_blocks_order(self, symbol: str, action: str) -> bool:
        """INC-6 (continuous operation): the explicit market-open gate at the ORDER step.

        Research/analysis/report-evaluation runs CONTINUOUSLY even when the market
        is closed (the trading loop no longer sleep-skips a closed cycle), but order
        PLACEMENT stays market-gated: we must NOT submit to the broker while the
        market is closed unless ``BYPASS_MARKET_HOURS`` is set (test override).

        Returns ``True`` — and records the ``blocked:market_closed`` execution
        outcome plus a telemetry log — when the order MUST be blocked. Fail-OPEN on
        any clock error: mirrors the trading loop, which proceeds to trade when the
        clock call fails, so a transient clock outage can never freeze an open book.
        """
        try:
            if config.get_config().BYPASS_MARKET_HOURS:
                return False
        except Exception:  # noqa: BLE001 — an unreadable bypass flag must not gate
            pass
        try:
            clock = await asyncio.to_thread(self.api.get_clock)
            is_open = bool(clock.is_open)
        except Exception as e:  # noqa: BLE001 — fail-open (mirror the trading loop)
            logging.warning(
                "[%s] INC-6 order market-gate: clock check failed (%s) — allowing "
                "order (fail-open, mirrors trading loop).",
                symbol,
                e,
            )
            return False
        # Pure observation: keep /engine-diagnostics' cached flag fresh.
        try:
            self._last_market_open = is_open
        except Exception:  # noqa: BLE001
            pass
        if is_open:
            return False
        logging.warning(
            "[%s] 🌙 INC-6 order market-gate: %s BLOCKED — market CLOSED "
            "(BYPASS_MARKET_HOURS off). No broker submission.",
            symbol,
            action,
        )
        _rec_outcome(
            symbol,
            "blocked:market_closed",
            "Market closed — order not submitted (INC-6 order gate).",
        )
        return True

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
            curr = context.current_price if context else 0.0
            # INC-6 (continuous operation): the explicit market-open gate at the ORDER
            # step. Research/analysis runs continuously even with the market closed,
            # but order PLACEMENT stays market-gated — we must NOT submit to the broker
            # while closed unless BYPASS_MARKET_HOURS. This single gate covers BOTH the
            # multi-tenant fan-out and the single-tenant desktop fallback below (and the
            # #2066 position-stop SELLs routed here). Records `blocked:market_closed`.
            if await self._market_closed_blocks_order(symbol, action):
                # #2481: Log the decision before the early return so off-hours/research
                # decisions (BUY/SELL when market is closed) are persisted to the decisions table.
                if should_log and not event.is_simulation:
                    _mc_loop = asyncio.get_running_loop()
                    await _mc_loop.run_in_executor(
                        None,
                        self.cloud_logger.log_decision,
                        context,
                    )
                    _capture_outcome(context)
                return
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
                    # #2113 (flag-gated, PURE OBSERVATION): hitl_held was defined
                    # but never emitted — record + durably capture the held
                    # decision (it never reaches the log_decision tail below).
                    # The `return` is unchanged: the hold behaves byte-identically.
                    if _capture_enabled():
                        _rec_outcome(
                            symbol,
                            "hitl_held",
                            "queued for human approval (HITL gate)",
                            decision_id=getattr(context, "decision_id", None),
                        )
                        _capture_outcome(context)
                    return

            try:
                _fallback_displacement_sell_order_id = None
                _fallback_displacement_sell_qty = 0.0
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
                            resolved_client = create_trading_client(
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

                    # Part C — restore the desktop PortfolioManager's anti-churn state
                    # from Redis once per process, so the per-symbol cooldown (ADR-R12) and
                    # trade history survive a restart (parity with the tenant path). Null-safe;
                    # any legacy naive timestamp restored here is tolerated by the PM's
                    # _ensure_aware_utc normalisation. _fb_redis is reused for the persists below.
                    _fb_pm = getattr(
                        getattr(self, "active_strategy", None),
                        "portfolio_manager",
                        None,
                    )
                    _fb_redis = None
                    if _fb_pm is not None:
                        try:
                            _fb_redis = await RedisClient.get_redis()
                            if not hasattr(self, "_pm_restored"):
                                self._pm_restored: set = set()
                            await restore_pm_state_from_redis(
                                _fb_pm, _fb_redis, self._pm_restored
                            )
                        except Exception:
                            logging.warning(
                                "[%s] fallback PM state restore failed",
                                symbol,
                                exc_info=True,
                            )

                    # #2553: independent held-qty witness (real broker holding) for the exit-exemption;
                    # resolved below for every SELL so held_qty is never an echo of the order qty.
                    held_broker_qty = 0.0
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
                                held_broker_qty = abs(qty)
                            except Exception as e:
                                # #2118: a 404 / Alpaca code 40410000 means the position is already
                                # flat (nothing to sell) — a clean "closed", NOT an error. Only genuine
                                # (transient/other) errors stay "error". #2065: the drop stays auditable
                                # + fail-safe (qty=0 → no submit).
                                _status = getattr(e, "status_code", None)
                                try:
                                    _code = (
                                        e.code
                                    )  # APIError.code may raise on non-JSON body
                                except Exception:
                                    _code = None
                                if _status == 404 or _code == 40410000:
                                    logging.info(
                                        "[%s] SELL skipped — no open position (already flat/closed).",
                                        symbol,
                                    )
                                    _rec_outcome(
                                        symbol,
                                        "closed",
                                        "no open position — already flat",
                                    )
                                else:
                                    logging.warning(
                                        "[%s] Could not fetch open position qty: %s — SELL dropped (fail-safe, qty=0)",
                                        symbol,
                                        e,
                                        exc_info=True,
                                    )
                                    _rec_outcome(
                                        symbol, "error", f"position fetch failed: {e}"
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
                                # #1953: forward-vol from the DecisionContext;
                                # None => fail-safe no-op in the sizer.
                                forecast_vol = (
                                    getattr(context, "forecast_vol", None)
                                    if context
                                    else None
                                )

                                # O3: resolve the desktop fallback PM once (getattr-safe) for both the
                                # held count and the refresh-freshness flag.
                                _fb_pm = getattr(
                                    getattr(self, "active_strategy", None),
                                    "portfolio_manager",
                                    None,
                                )
                                qty = self.live_risk_manager.calculate_position_size(
                                    stop_loss_atr_multiplier=3.0,
                                    atr=atr,
                                    confidence="high",
                                    size_scaler=1.0,
                                    market_data={"vix": vix},
                                    # #2153: book-cap slots, not the ~500-name scan universe
                                    # (see _execute_tenant_order) — fixes the ~50x under-size.
                                    # O3 (CASH_AWARE_SLOTS_ENABLED): split by the REMAINING free
                                    # slots (cap − held) so a mostly-invested book funds proper-sized
                                    # positions. Held count = the desktop fallback PM's last-refreshed
                                    # positions (no new broker call); no PM → 0 → cap (byte-identical),
                                    # and an unconfirmed/stale refresh → cap (positions_confirmed).
                                    num_stocks_in_strategy=effective_free_slots(
                                        len(
                                            getattr(_fb_pm, "_position_scores", {})
                                            or {}
                                        ),
                                        positions_confirmed=getattr(
                                            _fb_pm, "_last_refresh_ok", True
                                        ),
                                    ),
                                    current_price=curr,
                                    account_cash=cash,
                                    allow_fractional=True,
                                    conviction_score=conviction,
                                    forecast_vol=forecast_vol,
                                )
                                # #1953 audit mirror (MiFID II Art. 17): APPLIED
                                # vol scaler -> context.risk_size_scaler; no-op
                                # when flag off / scaler 1.0.
                                apply_vol_targeting_audit(context, forecast_vol)
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

                        # Part C (#2176, Piece 2): arm the PortfolioManager gate on the
                        # single-tenant fallback path — mirror _execute_tenant_order so the
                        # desktop book's cooldown (ADR-R12) and full-book displacement fire
                        # here too. This path never called should_open_new_position before,
                        # leaving Increment 2's per-symbol cooldown dormant on desktop.
                        # Null-safe: no PortfolioManager ⇒ behaviour is identical to before.
                        _pc_symbol_to_close = None
                        _pc_strat = getattr(self, "active_strategy", None)
                        _pc_pm = (
                            getattr(_pc_strat, "portfolio_manager", None)
                            if _pc_strat
                            else None
                        )
                        if action == "BUY" and _pc_pm is not None:
                            try:
                                _pc_curr = context.current_price if context else 0.0
                                _pc_features = {}
                                if context and hasattr(context, "__dict__"):
                                    for _pc_k in [
                                        "rsi_14",
                                        "macd",
                                        "adx_14",
                                        "volatility_20d",
                                    ]:
                                        if hasattr(context, _pc_k):
                                            _pc_features[_pc_k] = getattr(
                                                context, _pc_k
                                            )
                                _pc_opp = _pc_pm.score_opportunity(
                                    symbol=symbol,
                                    current_price=_pc_curr,
                                    rl_action=1,
                                    model_confidence=(
                                        context.lstm_prediction if context else 0.5
                                    ),
                                    features=_pc_features,
                                )
                                (
                                    _pc_open,
                                    _pc_reason,
                                    _pc_symbol_to_close,
                                ) = _pc_pm.should_open_new_position(_pc_opp)
                                if not _pc_open:
                                    _pc_code = (
                                        "blocked:churn"
                                        if "cooldown" in (_pc_reason or "").lower()
                                        else "blocked:portfolio"
                                    )
                                    logging.info(
                                        "[Global] %s 📊 Portfolio declined open: %s",
                                        symbol,
                                        _pc_reason,
                                    )
                                    _rec_outcome(symbol, _pc_code, _pc_reason)
                                    # ADR-OBS-01: log the declined decision before the
                                    # early return so a portfolio/cooldown-blocked BUY is
                                    # not silently absent from the decisions table (every
                                    # other block path reaches log_decision at the tail).
                                    if should_log and not event.is_simulation:
                                        _pc_loop = asyncio.get_running_loop()
                                        await _pc_loop.run_in_executor(
                                            None,
                                            self.cloud_logger.log_decision,
                                            context,
                                        )
                                        # #2113: durable capture beside log_decision
                                        # (flag-gated, fail-safe, observation-only).
                                        _capture_outcome(context)
                                    # #2585: approved BUY declined by the desktop
                                    # PortfolioManager — audit the skip.
                                    await _audit_skipped_signal(
                                        symbol,
                                        action,
                                        SKIP_REASON_OTHER,
                                        f"portfolio declined: {_pc_reason}",
                                    )
                                    return
                            except Exception:
                                # Fail-OPEN: a scoring error must not newly block a BUY the
                                # legacy path would have executed. Proceed without
                                # displacement (identical to pre-Part-C behaviour).
                                logging.warning(
                                    "[Global] %s displacement gate errored (proceeding "
                                    "without it)",
                                    symbol,
                                    exc_info=True,
                                )
                                _pc_symbol_to_close = None

                        approved = True
                        if self.compliance_guardian:
                            curr = context.current_price if context else 0.0
                            # #2553: a SELL with a signal-supplied qty>0 skipped the position fetch
                            # above, so resolve the REAL broker holding here (once) — held_qty must be
                            # an INDEPENDENT witness, not an echo of the order qty. A fetch failure
                            # leaves it 0.0 → the exemption fails closed (SELL subject to the normal
                            # order-value cap, never silently self-exempted).
                            if action == "SELL" and held_broker_qty <= 0.0:
                                try:
                                    _hp = await asyncio.to_thread(
                                        trade_client.get_open_position, symbol
                                    )
                                    held_broker_qty = abs(
                                        float(_hp.qty)
                                        if hasattr(_hp, "qty")
                                        else float(_hp.get("qty", 0.0))
                                    )
                                except Exception:
                                    held_broker_qty = 0.0  # fail-closed exemption
                            compliance_order = {
                                "symbol": symbol,
                                "side": action.lower(),
                                "quantity": qty,
                                "price": curr,
                                "strategy_id": "RLStrategy",
                                "timestamp": _time.time(),
                                "user_id": uid or "global",
                                # #2065/#2553: the REAL broker-held qty for a SELL (independent
                                # witness, NOT an echo of the order qty) — bounds the exit-exemption
                                # to a genuinely risk-reducing exit (quantity <= held_qty).
                                "held_qty": (
                                    held_broker_qty if action == "SELL" else 0.0
                                ),
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
                                # ADR-C04-EXIT: pass the order so a risk-reducing exit does
                                # not consume a buy slot (the desktop/[Global] path — this
                                # is the one the 2026-07-28 rotation flush burned through).
                                self.compliance_guardian.record_trade(compliance_order)

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
                            try:
                                kill_switch.check_halt(uid or "global")
                            except Exception:
                                # #2113 (flag-gated, PURE OBSERVATION): durable
                                # blocked:kill_switch — re-raise keeps the gate
                                # byte-identical (caught by the outer handler,
                                # then the log_decision tail captures the row).
                                if _capture_enabled():
                                    _rec_outcome(
                                        symbol,
                                        "blocked:kill_switch",
                                        "kill-switch halt — order blocked",
                                        decision_id=getattr(
                                            context, "decision_id", None
                                        ),
                                    )
                                raise

                            # Part C (#2176): displacement SELL of the weakest holding —
                            # placed AFTER the halt gate (a halt aborts before any SELL) and
                            # after compliance-approval (a blocked BUY never leaves a naked
                            # SELL), and routed through the SAME shadow/live path as the BUY
                            # so a paper run never fires a real exit. Mirrors the tenant path;
                            # the exit is risk-reducing (R1 exemption).
                            if _pc_symbol_to_close:
                                try:
                                    # Pre-check for Global/Fallback Account
                                    _pc_account = await asyncio.to_thread(
                                        trade_client.get_account
                                    )
                                    _pc_multiplier = getattr(
                                        _pc_account, "multiplier", "1"
                                    )
                                    _pc_bp = float(
                                        getattr(_pc_account, "buying_power", 0.0) or 0.0
                                    )

                                    _pc_old = await asyncio.to_thread(
                                        trade_client.get_open_position,
                                        _pc_symbol_to_close,
                                    )
                                    _pc_oq = 0.0
                                    _pc_price = 0.0
                                    if _pc_old:
                                        _pc_oq = (
                                            float(_pc_old.qty)
                                            if hasattr(_pc_old, "qty")
                                            else float(_pc_old.get("qty", 0.0))
                                        )
                                        _pc_price = (
                                            float(_pc_old.current_price)
                                            if hasattr(_pc_old, "current_price")
                                            else float(
                                                _pc_old.get("current_price", 0.0)
                                            )
                                        )
                                    if _pc_oq > 0:
                                        sell_value = _pc_oq * _pc_price
                                        multiplier_val = (
                                            float(_pc_multiplier)
                                            if _pc_multiplier
                                            else 1.0
                                        )
                                        if multiplier_val == 1.0:
                                            buying_power_available = _pc_bp
                                        else:
                                            buying_power_available = _pc_bp + (
                                                sell_value * multiplier_val
                                            )

                                        required_buying_power = qty * curr
                                        if (
                                            buying_power_available
                                            < required_buying_power
                                        ):
                                            logging.warning(
                                                f"[Global] ⚠️ Displacement pre-check failed. "
                                                f"Required BP: {required_buying_power}, Available BP (post-sell estimate): {buying_power_available}. "
                                                f"Aborting swap for {_pc_symbol_to_close} -> {symbol}."
                                            )
                                            try:
                                                if _fb_redis:
                                                    await _safe_publish(
                                                        _fb_redis,
                                                        f"explainability:{uid or 'global'}",
                                                        json.dumps(
                                                            {
                                                                "type": "trade_rejected",
                                                                "title": f"Precheck Blocked: {symbol}",
                                                                "message": f"Insufficient settled buying power (multiplier={_pc_multiplier}). Available: {buying_power_available}, Required: {required_buying_power}",
                                                                "timestamp": datetime.now(
                                                                    timezone.utc
                                                                ).isoformat(),
                                                            }
                                                        ),
                                                    )
                                            except Exception as redis_e:
                                                logging.warning(
                                                    "PubSub error: %s", redis_e
                                                )
                                            _rec_outcome(
                                                symbol,
                                                "blocked:precheck",
                                                "Insufficient settled buying power.",
                                            )
                                            # #2585: approved BUY dropped at the
                                            # buying-power precheck — audit the skip.
                                            await _audit_skipped_signal(
                                                symbol,
                                                action,
                                                SKIP_REASON_INSUFFICIENT_BUYING_POWER,
                                                f"required={required_buying_power:.2f}"
                                                f" available="
                                                f"{buying_power_available:.2f}",
                                                order_value=required_buying_power,
                                            )
                                            return

                                        _pc_sell_req = MarketOrderRequest(
                                            symbol=_pc_symbol_to_close,
                                            qty=_pc_oq,
                                            side=OrderSide.SELL,
                                            time_in_force=TimeInForce.DAY,
                                        )
                                        if getattr(config, "SHADOW_MODE", False):
                                            DryRunOrderProxy("global").submit_order(
                                                _pc_sell_req
                                            )
                                        else:
                                            # LSR R1 (#2252): submit-boundary halt gate. The
                                            # :1841 check_halt is separated from THIS live SELL by
                                            # the get_account/get_open_position awaits above — a
                                            # trip in that window (operator /api/live/disable) must
                                            # abort BEFORE a real displacement SELL leaves. Raising
                                            # here is caught by the surrounding displacement
                                            # try/except so the SELL never submits; the propagating
                                            # abort follows at the BUY gate below.
                                            kill_switch.check_halt(uid or "global")
                                            _fb_sell_order = await asyncio.to_thread(
                                                trade_client.submit_order, _pc_sell_req
                                            )
                                            _fallback_displacement_sell_order_id = (
                                                _fb_sell_order.id
                                            )
                                            _fallback_displacement_sell_qty = _pc_oq
                                        if _pc_pm is not None:
                                            _pc_pm.record_trade(
                                                _pc_symbol_to_close, "sell"
                                            )
                                            await persist_pm_state_to_redis(
                                                _pc_pm, _pc_symbol_to_close, _fb_redis
                                            )
                                        await asyncio.sleep(0.5)
                                except Exception as swap_e:
                                    logging.warning(
                                        "[Global] Failed to displace %s: %s",
                                        _pc_symbol_to_close,
                                        swap_e,
                                        exc_info=True,
                                    )
                            if getattr(config, "SHADOW_MODE", False):
                                logging.info(
                                    f"[Global] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
                                )
                                order = DryRunOrderProxy("global").submit_order(req)
                                context.alpaca_order_id = str(order.id)
                                # Part B (exec-visibility): a dry-run submit still IS a
                                # decision the operator must see — mark it executed, flagged
                                # as a simulation so the surface can distinguish it.
                                context.action_executed = True
                                context.is_simulation = True
                            else:
                                with tracer.start_as_current_span(
                                    "broker.submit_order.live"
                                ) as span:
                                    span.set_attribute("trade.action", action)
                                    for _k, _v in order_span_attributes(
                                        symbol, action, req
                                    ).items():
                                        span.set_attribute(_k, _v)
                                    # LSR R1 (#2252): re-check the halt IMMEDIATELY before the
                                    # live BUY. The :1841 gate is separated from this submit by the
                                    # displacement awaits + asyncio.sleep(0.5); a trip in that
                                    # window (operator /api/live/disable) must abort before a real
                                    # order leaves. Mirrors the tenant gate (:947).
                                    try:
                                        kill_switch.check_halt(uid or "global")
                                    except Exception:
                                        span.set_attribute("halt_blocked", True)
                                        # #2113 (flag-gated, PURE OBSERVATION): a trip
                                        # in the displacement window lands here — the
                                        # :1841 gate never saw it. Re-raise unchanged.
                                        if _capture_enabled():
                                            _rec_outcome(
                                                symbol,
                                                "blocked:kill_switch",
                                                "kill-switch halt — order blocked "
                                                "(submit boundary, #2252)",
                                                decision_id=getattr(
                                                    context, "decision_id", None
                                                ),
                                            )
                                        raise
                                    try:
                                        order = await asyncio.to_thread(
                                            trade_client.submit_order, req
                                        )
                                    except APIError as submit_err:
                                        if (
                                            _pc_symbol_to_close
                                            and _fallback_displacement_sell_order_id
                                        ):
                                            await self._recover_displacement(
                                                user_id=uid or "global",
                                                client=trade_client,
                                                pm=_pc_pm,
                                                redis_client=_fb_redis,
                                                symbol_to_close=_pc_symbol_to_close,
                                                displacement_sell_order_id=_fallback_displacement_sell_order_id,
                                                displacement_sell_qty=_fallback_displacement_sell_qty,
                                            )
                                        # #2118 Reserve-and-Reclaim: a DETERMINISTIC 4xx broker
                                        # rejection (insufficient qty, wash-trade, invalid) means the
                                        # slot reserved at record_trade() was never used — give it back.
                                        # Ambiguous failures (timeout/5xx) are NOT refunded: the order
                                        # may have landed → keep the reservation (fail-closed).
                                        _sc = getattr(submit_err, "status_code", None)
                                        if (
                                            self.compliance_guardian
                                            and _sc is not None
                                            and 400 <= _sc < 500
                                        ):
                                            self.compliance_guardian.refund_trade()
                                        raise
                                    # Part B (exec-visibility): the order reached the broker on
                                    # the single-tenant fallback path — surface it in the exec
                                    # counters AND on the decision, mirroring the tenant path.
                                    _safe_bump_exec("submit_ok")
                                    context.alpaca_order_id = str(order.id)
                                    context.action_executed = True
                                    context.is_simulation = False
                            _rec_outcome(symbol, "executed")

                            # Record the trade on the desktop PortfolioManager for churn prevention / min-hold tracking
                            strat = getattr(self, "active_strategy", None)
                            pm = (
                                getattr(strat, "portfolio_manager", None)
                                if strat
                                else None
                            )
                            if pm is not None:
                                try:
                                    pm.record_trade(symbol, action.lower())
                                    if action == "SELL":
                                        pm.clear_sell_signals_after_sale(symbol)
                                    elif action == "BUY":
                                        conv = (
                                            getattr(context, "conviction_score", 0.5)
                                            if context
                                            else 0.5
                                        )
                                        pm.update_position_conviction(symbol, conv)
                                    # Part C — persist the updated anti-churn state so the
                                    # cooldown/trade-history survives a restart (parity with
                                    # the tenant path). No-op if Redis is unavailable.
                                    await persist_pm_state_to_redis(
                                        pm, symbol, _fb_redis
                                    )
                                except Exception as pm_err:
                                    logging.warning(
                                        "[%s] Fallback post-trade PortfolioManager update failed: %s",
                                        symbol,
                                        pm_err,
                                    )

                            logging.info(
                                f"[{symbol}] ✅ Executed {action} {qty} shares "
                                f"(uid={uid or 'global'}). OrderID: {order.id}"
                            )
                    else:
                        # ADR-OBS-01: `if qty > 0:` had no else. An order that sized to zero
                        # fell through in total silence — no log, no audit entry, no outcome.
                        # It did not fail; it ceased to exist. On 2026-07-16 the `decisions`
                        # table held 1013 gatekeeper-approved BUYs, every one with
                        # execution_qty=0.0, while compliance_audit.log held 9 lines (all
                        # SELLs from the day before): 1013 approvals, zero compliance checks,
                        # because the orders died one step earlier without a word.
                        #
                        # This ANNOUNCES; it does not decide. qty <= 0 still submits nothing —
                        # the fail-safe is unchanged and deliberate.
                        #
                        # Nothing new is invented: BLOCKED_RISK ("blocked:risk",
                        # execution_outcomes.py:30) is documented verbatim as "position size
                        # resolved to 0 (risk/cash)", and BOTH frontends already render it as
                        # "Blocked · risk sizing" (console Decisions.tsx:45, LiveDemo.tsx:51).
                        # The badge was built, wired and unreachable — nothing emitted the
                        # code. Zero producers, two consumers. This connects them.
                        #
                        # WARNING, not DEBUG (CLAUDE.md 5.6). The sibling site at :631 handles
                        # the same condition at DEBUG, which is precisely why 1013 vanishing
                        # orders looked like nothing at all.
                        #
                        # The code is spelled as a LITERAL, matching the three existing
                        # _rec_outcome calls in this file, NOT imported from
                        # execution_outcomes. That is deliberate: _rec_outcome imports the
                        # recorder lazily *inside* its try/except so a broken observability
                        # module can never raise into the trading path (:64-69). A
                        # module-level `from ... import BLOCKED_RISK` would hand that
                        # guarantee back — an import error would take the executor down.
                        # test_sizing_drop_is_visible.py pins literal == BLOCKED_RISK.
                        logging.warning(
                            "[%s] %s dropped: position size resolved to 0 "
                            "(risk limits / exposure cap / available cash) — no order "
                            "submitted.",
                            symbol,
                            action,
                        )
                        _rec_outcome(
                            symbol,
                            "blocked:risk",
                            "position size resolved to 0 (risk/cash)",
                        )
                        # #2585: the ADR-OBS-01 WARNING above made this drop visible
                        # in the LOGS — but the audit chain still ended at "Approved".
                        # Seal the skip onto the chain too. A SELL lands here when no
                        # sellable quantity resolved (flat / fetch failed), a BUY when
                        # risk/cash sizing returned 0 — name the honest cause.
                        await _audit_skipped_signal(
                            symbol,
                            action,
                            SKIP_REASON_SIZING_ZERO,
                            (
                                "position size resolved to 0 "
                                "(risk limits / exposure cap / available cash)"
                                if action == "BUY"
                                else "no sellable quantity (flat or fetch failed)"
                            ),
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
                                # #2585: a tenant-level infrastructure failure
                                # (raised before _execute_tenant_order's own
                                # swallower) also means the approved signal did
                                # not execute for this tenant — audit it.
                                await _audit_skipped_signal(
                                    symbol,
                                    action,
                                    SKIP_REASON_EXECUTION_ERROR,
                                    f"tenant {uid_f}: {exc_f}",
                                )
                        logging.info(
                            f"[{symbol}] Async fan-out: {len(exec_tasks)} tenants, "
                            f"{len(failed)} failed."
                        )
                        context.alpaca_order_id = "multi-tenant-batch"
            except Exception as e:
                logging.error("Multi-tenant execution failed for %s: %s", symbol, e)
                context.alpaca_order_id = f"failed: {str(e)[:50]}"
                # #2585: the outer swallower — an approved signal that died here
                # (fallback submit exception, kill-switch trip, infra error) left
                # only an ERROR log and no audit-chain record. Audit the skip.
                await _audit_skipped_signal(
                    symbol, action, SKIP_REASON_EXECUTION_ERROR, str(e)
                )

        if should_log and not event.is_simulation:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.cloud_logger.log_decision, context)
            # #2113: durable decision_outcomes capture beside log_decision — same
            # decision_id, carries the final execution outcome of THIS decision
            # (flag-gated + fail-safe inside; pure observation).
            _capture_outcome(context)
