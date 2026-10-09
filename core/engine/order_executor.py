# core/engine/order_executor.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: Multi-Tenant Order-Execution, Compliance, PubSub-Events

import asyncio
import json  # noqa: F401 — gelesen als order_executor.json (#4239, #4241)
import logging
import time as _time
import uuid
from datetime import timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

from alpaca.trading.enums import OrderSide, TimeInForce  # noqa: F401 (#4239, #4241)
from alpaca.trading.requests import MarketOrderRequest

from core.composition.root import CompositionRoot

try:
    from core.client_factory import create_trading_client  # noqa: F401 (#4234)
except ImportError:
    from ai_trading_bot.core.client_factory import create_trading_client  # noqa: F401

import core.gateway.fabrik as _fabrik
from core.engine.absendung_tor import (  # noqa: F401 — Re-Export (#4237, H-1h)
    TorMixin,
    _outbox_schritt,
    _outbox_sitzung,
    ist_duplikat,
)
from core.engine.bemessung_helfer import (  # noqa: F401 — Re-Export (#4232, H-1c)
    _earnings_guard_veto,
    _regime_throttled_size,
    _topup_gap_capped_qty,
)
from core.engine.order_aufbau import (  # noqa: F401 — Re-Export (#4231, H-1b)
    _dust_floor_skips_exit,
    _zahl,
    baue_trade_record,
    build_broker_order_request,
    classify_exit_kind,
    erfasse_order_endzustand,
    exit_failsafe_remaining_qty,
    halt_exempt_protective_exit,
)
from core.engine.pm_zustand import (  # noqa: F401 — Re-Export (#4232, H-1c, #4238)
    persist_pm_state_to_redis,
    restore_pm_state_from_redis,
)
from core.gateway import OrderGateway
from core.idempotency import (  # #3387: Schluessel ableiten statt wuerfeln
    derive_client_order_id,
    next_attempt,
)
from core.kill_switch import kill_switch
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


def _derived_coid(context, leg: str = "entry", attempt: int = 0) -> str:
    """client_order_id aus der Entscheidung ableiten (#3387).

    Gewuerfelt wird nur noch, wenn es gar keine Entscheidung gibt, aus der sich etwas
    ableiten liesse — und dann sichtbar, nicht still (CODING_POLICY §5.6).
    """
    decision_id = getattr(context, "decision_id", None) if context is not None else None
    if isinstance(decision_id, str) and decision_id:
        try:
            return derive_client_order_id(decision_id, leg, attempt)
        except ValueError:
            logging.warning(
                "client_order_id: Ableitung aus decision_id=%r leg=%r fehlgeschlagen "
                "— zufaelliger Schluessel, Wiederholung nicht erkennbar",
                decision_id,
                leg,
            )
    else:
        logging.warning(
            "client_order_id: kein Entscheidungskontext (context=%r) — zufaelliger "
            "Schluessel, eine Wiederholung ist damit nicht erkennbar (#3387)",
            type(context).__name__ if context is not None else None,
        )
    return str(uuid.uuid4())


def _bump_exec(field: str, *, inc: int = 1, set_ts: bool = False) -> None:
    """Fail-safe counter mutation — swallows EVERY error (observation must never
    alter execution control flow)."""
    try:
        if set_ts:
            _EXEC_COUNTERS[field] = CompositionRoot.get_instance().clock_port.time()
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
                timestamp=CompositionRoot.get_instance().clock_port.now().isoformat(),
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
        order_id = f"shadow_{int(CompositionRoot.get_instance().clock_port.time())}_{req.symbol}"
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


# #3447 Schritt 3: Senke und Fabrik liegen jetzt beim Tor (``core/gateway/fabrik.py``),
# damit der Strategiepfad sie ohne ``core/engine/`` erreicht. Der Name bleibt hier
# erhalten — Tests ersetzen die Senke an diesem Modul, und ``gateway_for`` schlaegt sie
# zur Aufrufzeit hier nach.
_record_gateway_decision = _fabrik.record_gateway_decision


def gateway_for(client) -> OrderGateway:
    """Baut das Tor fuer einen Broker-Zugang — ueber die eine Fabrik beim Tor.

    Halt-Abfrage und Senke kommen aus diesem Modul, damit sie hier ersetzbar bleiben.
    """
    return _fabrik.gateway_for(
        client,
        is_halted=lambda user_id=None: kill_switch.is_halted(user_id),
        record=lambda d: _record_gateway_decision(d),
    )


class _Absendung(SimpleNamespace):
    """Der Zustand einer Absendung je Mandant (#3821, ARC-E6 G-1b).

    Traegt von Schritt zu Schritt, was in ``_execute_tenant_order`` frueher lokale
    Variablen waren. Ein Feld, das ein Zweig nie setzt, fehlt hier, wie dort die Bindung
    fehlte — gelesen wird es dann ueber ``_gebunden``.
    """


_UNGEBUNDEN = object()


def _gebunden(st: _Absendung, name: str) -> Any:
    """Liest ein Feld wie frueher die lokale Variable: ungebunden wirft dasselbe (#3821).

    Auf dem SELL-Pfad werden ``sizing_trace`` und ``symbol_to_close`` nie gebunden (sie
    entstehen im Kauf-Zweig). Wer sie dort las, scheiterte bisher an einem
    ``UnboundLocalError``, den die Fehlerbehandlung der Absendung auffing. Der Umbau ist
    verhaltensneutral und behebt das nicht — er benennt die Stelle.
    """
    wert = getattr(st, name, _UNGEBUNDEN)
    if wert is _UNGEBUNDEN:
        raise UnboundLocalError(
            f"cannot access local variable '{name}' where it is not associated with a value"
        )
    return wert


class _Uebergabe(SimpleNamespace):
    """Der Zustand des Desktop-Zweigs der Signal-Übergabe (#4238, ARC-E6 H-1i).

    Traegt von Schritt zu Schritt, was in ``_process_signal_event`` auf dem Pfad ohne
    Mandanten frueher lokale Variablen waren (Muster ``_Absendung``). Jedes Feld wird vor
    seiner ersten Lesestelle gesetzt; ``_gebunden`` braucht es deshalb bisher nicht.
    """


class OrderExecutorMixin(TorMixin):
    """
    Mixin für BotEngine: Multi-Tenant Fan-out, Compliance, Signal-Verarbeitung.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    async def _execute_tenant_order(
        self, tenant: Dict[str, Any], event: SignalEvent, source: str = "ai"
    ):
        """Execute an order for a single tenant with full risk/compliance/portfolio checks.

        #3821 (G-1b): liest sich als Folge der Absendungs-Schritte ``_schritt_mandant_*``.
        Ein Schritt, der ``False`` liefert, beendet die Absendung vor dem Broker
        (Rueckgabe ``None``). Was frueher lokale Variablen waren, traegt ``_Absendung``.
        """
        st = _Absendung(
            user_id=tenant["user_id"],
            client=tenant["client"],
            equity=tenant["equity"],
            action=event.action,
            symbol=event.symbol,
            context=event.decision_context,
            event=event,
            source=source,
        )
        user_id, symbol = st.user_id, st.symbol

        st.redis_client = await RedisClient.get_redis()
        lock_key = f"order_lock:{user_id}:{symbol}"
        acquired = False

        # Epic 4: Redis Redlock TTL of 12000ms (12 seconds)
        if st.redis_client:
            lock = st.redis_client.lock(lock_key, timeout=12.0)
            acquired = await lock.acquire(blocking=False)
            if not acquired:
                logging.warning(
                    f"Concurrent execution blocked for {user_id}:{symbol} by Redlock."
                )
                return
        else:
            lock = None

        try:
            st.order_submitted = False
            # PR A.2: local marker so the outer except only counts an actual live
            # broker-submit failure as submit_fail (not a pre-submit guard return).
            st.submit_attempted = False
            st.displacement_sell_order_id = None
            st.deferred_close_symbol = None  # #2712 Inc 2 (buy-first)
            st.deferred_close_qty = 0.0
            st.displacement_sell_qty = 0.0
            st.sofort_verkaufen_qty = None
            if not await self._schritt_mandant_vorbereiten(st):
                return
            if st.action == "SELL":
                if not await self._schritt_mandant_bestand(st):
                    return
            else:
                if not await self._schritt_mandant_bemessung(st):
                    return
                if not await self._schritt_mandant_portfolio(st):
                    return
                if not await self._schritt_mandant_verdraengung(st):
                    return
                if not await self._schritt_mandant_sofort_verkaufen(st):
                    return
            if not await self._schritt_mandant_menge(st):
                return
            if not await self._schritt_mandant_compliance(st):
                return
            self._schritt_mandant_auftrag(st)

            # Shadow Mode Interception
            st.live_order = None
            if getattr(config, "SHADOW_MODE", False):
                await self._schritt_mandant_schatten(st)
            else:
                with tracer.start_as_current_span("broker.submit_order.live") as span:
                    span.set_attribute("trade.action", st.action)
                    for _k, _v in order_span_attributes(
                        symbol, st.action, st.req
                    ).items():
                        span.set_attribute(_k, _v)
                    await self._schritt_mandant_senden(st)
                    if not await self._schritt_mandant_fuellung(st):
                        await self._schritt_mandant_storno(st)
                        # Early exit: prevent pm.record_trade from running for an unfilled
                        # order. The order DID reach the broker (submitted, then cancelled
                        # for non-fill), so report it submitted — the human-approval drain
                        # audits this "approved" (it reached the market), never
                        # "iron_dome_rejected" (PR-0a-ii-5a).
                        return True

            st.order_submitted = True
            await self._schritt_mandant_endzustand(st)
            if st.action == "SELL":
                await self._schritt_mandant_verkauf_nachbuchen(st)
            elif st.action == "BUY":
                await self._schritt_mandant_kauf_nachbuchen(st)
            await self._schritt_mandant_melden(st)

        except Exception as e:
            await self._schritt_mandant_fehler(st, e)
            # ADR-ENG-07: Single-symbol failure MUST NOT crash the trading loop.
            # Exception is fully logged + published to Redis PubSub explainability channel.
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
        return st.order_submitted

    async def _process_signal_event(self, event: SignalEvent):
        """Processes a SignalEvent. Handles multi-tenant fan-out and logging."""
        symbol = event.symbol
        action = event.action
        context = event.decision_context

        should_log = self._schritt_protokollpflicht(action, context)

        if action in ["BUY", "SELL"] and not event.is_simulation:
            curr = context.current_price if context else 0.0
            if not await self._schritt_markt_offen(event, should_log):
                return

            try:
                active_tenants = await self.get_active_tenant_clients()
                if not active_tenants:
                    logging.info(
                        f"[{symbol}] No active OAuth tenants. Resolving via user_alpaca_accounts mapping."
                    )
                    # --- Epic 3.4-pre: resolve user-specific Alpaca credentials ---
                    # #4238 (H-1i): der Desktop-Zweig traegt seine Werte im Zustand ``st``.
                    # #4240 (H-1k): auch held_broker_qty und sizing_trace.
                    st = self._uebergabe_anlegen(event, should_log, curr)
                    resolved_client = self._schritt_secrets(
                        symbol=symbol, uid=st.uid, resolved_client=None
                    )

                    # Use resolved user client or fall back to global self.api
                    st.trade_client = resolved_client if resolved_client else self.api

                    await self._schritt_desktop_pm_laden(st)

                    _original_qty = st.qty
                    if _original_qty <= 0 or action == "BUY":
                        if action == "SELL" and _original_qty <= 0:
                            st.qty, st.held_broker_qty = (
                                await self._schritt_verkaufsmenge(
                                    symbol=symbol,
                                    held_broker_qty=st.held_broker_qty,
                                    trade_client=st.trade_client,
                                )
                            )
                        elif action == "BUY" and getattr(
                            self, "live_risk_manager", None
                        ):
                            st.qty, st.fb_pm, st.curr = await self._schritt_bemessung(
                                symbol=symbol,
                                action=action,
                                context=context,
                                curr=st.curr,
                                _original_qty=_original_qty,
                                _sizing_trace=st.sizing_trace,
                                trade_client=st.trade_client,
                                _fb_pm=st.fb_pm,
                            )

                    await self._schritt_desktop_earnings(st)

                    if st.qty > 0:

                        if not await self._schritt_desktop_verdraengung(st):
                            return

                        # #2704 — Art-14 gate, desktop/global path. Position: after sizing
                        # (`qty` above) and the portfolio decision, BEFORE the compliance
                        # counter (`record_trade`) and the displacement SELL further down — so a
                        # held order consumes no daily trade budget and triggers no sell.
                        # Because cash, book cap, cooldown and displacement have all run by now,
                        # only orders that WOULD execute reach the queue: the 13-at-once list of
                        # 2026-08-06 becomes the one or two that actually fit.
                        if st.qty > 0 and await self._hitl_holds_order(
                            event,
                            context,
                            symbol,
                            getattr(self, "active_uid", None) or "global",
                            st.qty,
                        ):
                            return

                        if not await self._schritt_desktop_verkaufstor(st):
                            return

                        approved = True
                        approved, st.curr = await self._schritt_compliance(
                            symbol=symbol,
                            action=action,
                            context=context,
                            event=event,
                            qty=st.qty,
                            curr=st.curr,
                            held_broker_qty=st.held_broker_qty,
                            trade_client=st.trade_client,
                            uid=st.uid,
                            approved=approved,
                        )

                        if approved:
                            await self._schritt_desktop_auftrag(st)

                            # Part C (#2176): displacement SELL of the weakest holding —
                            # placed AFTER the halt gate (a halt aborts before any SELL) and
                            # after compliance-approval (a blocked BUY never leaves a naked
                            # SELL), and routed through the SAME shadow/live path as the BUY
                            # so a paper run never fires a real exit. Mirrors the tenant path;
                            # the exit is risk-reducing (R1 exemption).
                            if not await self._schritt_desktop_kaufkraft(st):
                                return
                            await self._schritt_desktop_verdraengung_sell(st)
                            order = await self._schritt_absenden(
                                **self._absende_argumente(st)
                            )
                            _rec_outcome(symbol, "executed")

                            # Record the trade on the desktop PortfolioManager for churn prevention / min-hold tracking
                            strat = getattr(self, "active_strategy", None)
                            pm = (
                                getattr(strat, "portfolio_manager", None)
                                if strat
                                else None
                            )
                            await self._schritt_nachbuchen(
                                symbol=symbol,
                                action=action,
                                context=context,
                                pm=pm,
                                _fb_redis=st.fb_redis,
                            )

                            logging.info(
                                f"[{symbol}] ✅ Executed {action} {st.qty} shares "
                                f"(uid={st.uid or 'global'}). OrderID: {order.id}"
                            )
                    else:
                        # ADR-OBS-01 (#4239, H-1j): Null-Menge wird gemeldet, nicht abgesendet.
                        await self._schritt_desktop_null_menge(st)

                else:
                    await self._schritt_verteilung(event, active_tenants)
            except Exception as e:
                await self._schritt_uebergabe_fehler(symbol, action, context, e)

        if should_log and not event.is_simulation:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.cloud_logger.log_decision, context)
            # #2113: durable decision_outcomes capture beside log_decision — same
            # decision_id, carries the final execution outcome of THIS decision
            # (flag-gated + fail-safe inside; pure observation).
            _capture_outcome(context)
