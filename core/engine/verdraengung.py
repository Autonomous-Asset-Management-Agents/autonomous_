# core/engine/verdraengung.py
# ARC-E6 H-1l (#4241) — Verdrängung, ausgezogen aus core/engine/order_executor.py.
"""Die Verdrängung: Compliance-Sicht auf die Legs, Kaufkraft, aufgeschobener SELL, Rückkauf.

Hierher gezogen sind die drei freien Funktionen ``register_displacement_leg``,
``displacement_available_bp`` und ``submit_deferred_displacement_sell`` und die Methode
``_recover_displacement`` (jetzt ``VerdraengungMixin``). Gerufen werden sie aus
``absendung_vorlauf.py``, ``absendung_abgang.py``, ``signal_desktop_absendung.py`` und
``signal_uebergabe.py``; der Kern ruft keine davon.

Die Rümpfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten Abbildung
wie in G-1a, G-1b und H-1d bis H-1k (Entscheidung #4183 §3, Weg b): Jeder Name, der im Kern
gebunden ist, wird als ``order_executor.<name>`` gelesen — sonst griffe ein Patch am Kern ins
Leere, darunter ``kill_switch``, ``gateway_for``, ``RedisClient``,
``persist_pm_state_to_redis``, ``logging`` und das Tor
``order_executor.OrderExecutorMixin._sende_durchs_tor``. Was der Kern danach nicht mehr
bindet, importiert dieses Modul selbst (Weg a): ``APIError``, ``ersatz_decision_id``,
``OrderIntent``, ``ComplianceDecision`` und ``ReasonCode``. Ein Span steht in keinem Symbol.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4241-h-1l-a-verdrangung-a-verdraengung-py/implementation_plan.md``.
"""

from typing import Any, Optional

from alpaca.common.exceptions import APIError

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode
from core.idempotency import ersatz_decision_id

from . import order_executor


def register_displacement_leg(
    guardian, *, symbol, side, qty, price, user_id, held_qty, kind
) -> bool:
    """#2712 Inc 1: make displacement/recovery legs VISIBLE to the ComplianceGuardian.

    Observe-only by design: the result is returned for logging, but the caller never blocks
    on it — the displacement SELL funds an approved swap and the recovery BUY is a rollback
    that must not strand a position. What this fixes: both legs now land in check_order (the
    60s wash window finally sees the opposite side) AND record_trade (buffer/audit). Fail-open
    on any guardian error; None guardian is a no-op (desktop boot paths).
    """
    if guardian is None:
        return True
    try:
        order = {
            "symbol": symbol,
            "side": str(side).lower(),
            "quantity": float(qty),
            "price": float(price or 0.0),
            "strategy_id": kind,
            "timestamp": order_executor.CompositionRoot.get_instance().clock_port.time(),
            "user_id": user_id or "global",
            "held_qty": float(held_qty or 0.0),
        }
        ok = bool(guardian.check_order(order))
        if not ok:
            order_executor.logging.warning(
                "[displacement-compliance] %s %s %s qty=%s flagged by guardian "
                "(wash/limits) - leg proceeds (rollback integrity), now AUDITED (#2712)",
                kind,
                side,
                symbol,
                qty,
            )
        try:
            guardian.record_trade(order)
        except Exception:
            order_executor.logging.warning(
                "[displacement-compliance] record_trade failed (non-fatal)",
                exc_info=True,
            )
        return ok
    except Exception:
        order_executor.logging.warning(
            "[displacement-compliance] guardian error on %s %s - fail-open",
            kind,
            symbol,
            exc_info=True,
        )
        return True


def displacement_available_bp(
    *, buy_first: bool, buying_power: float, sell_value: float, multiplier: float
) -> float:
    """#2712 Inc 2: the buying power a displacement swap may assume.

    Buy-first (Archon Auflage 3, mandatory cash guard): the old position is NOT sold
    yet, so the estimate is the account's buying power ALONE - never the sale proceeds.
    Insufficient => the caller skips the swap entirely (no liquidation, never a silent
    sell-first fallback). Legacy sell-first keeps the historical post-sell estimate
    (margin accounts add sell_value * multiplier).
    """
    bp = float(buying_power or 0.0)
    if buy_first:
        return bp
    m = float(multiplier or 1.0)
    if m == 1.0:
        return bp
    return bp + float(sell_value or 0.0) * m


def submit_deferred_displacement_sell(
    *,
    client,
    pm,
    guardian,
    user_id: str,
    symbol: str,
    qty: float,
    decision_id: Optional[str] = None,
) -> bool:
    """#2712 Inc 2: the displacement SELL, submitted ONLY after the swap BUY confirmed.

    Failure mode is benign by construction: if this SELL fails, the account holds one
    position more than the book cap for a while (CRITICAL log; the next rebalance/trim
    cycle de-concentrates). That replaces the old failure mode - a completed SELL plus
    a recovery re-BUY burning the spread. Never raises into the order path.
    """
    try:
        req = order_executor.MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_executor.OrderSide.SELL,
            time_in_force=order_executor.TimeInForce.DAY,
        )
        # #3279 (Owner-Entscheid 2026-09-16, Option B): Die Pruefung laeuft VOR dem
        # Absenden. Inhaltlich aendert sich nichts — `register_displacement_leg` ist
        # unveraendert observe-only und fail-open, der Verkauf geht in jedem Fall raus
        # (ein geblockter Verdraengungs-SELL liesse das Konto mit einer Position ueber
        # dem Buch-Deckel und einer bereits ausgefuehrten BUY-Seite zurueck, #2712).
        #
        # Was sich aendert, ist der Zeitpunkt: Das 60-Sekunden-Wash-Fenster sieht die
        # Gegenseite jetzt VOR der Ausfuehrung statt danach, und der Pfad schreibt einen
        # Datensatz, bevor Kapital bewegt wird. Genau das verlangt CH-2 aus #3366 —
        # ohne diese Reihenfolge ist „keine Order ohne geprueffte Entscheidung" fuer den
        # mengenmaessig groessten SELL-Pfad nicht belegbar.
        geprueft = register_displacement_leg(
            guardian,
            symbol=symbol,
            side="sell",
            qty=qty,
            price=0.0,
            user_id=user_id,
            held_qty=qty,
            kind="displacement_sell_deferred",
        )
        # #3429: die echte Entscheidung des Aufrufers — nur aus ihr leitet das Tor einen
        # Idempotenz-Schluessel ab. Die frueher hier gebaute ID
        # ``displacement-<nutzer>-<symbol>`` war fuer zwei Verdraengungen desselben
        # Symbols dieselbe.
        _entscheidung = (
            decision_id
            if isinstance(decision_id, str) and decision_id
            else ersatz_decision_id("displacement", user_id, symbol)
        )

        # #3379: Dieser Pfad geht ab jetzt durch das Tor statt direkt zum Broker. Er ist
        # der erste von 21 — jede weitere Umstellung senkt die Zahl, die der
        # Architektur-Test aus #3377 misst.
        order_executor.gateway_for(client).submit(
            OrderIntent(
                decision_id=_entscheidung,
                symbol=symbol,
                side="sell",
                qty=float(qty),
                intent_kind="displacement",
                halted=order_executor.kill_switch.is_halted(user_id),
            ),
            request=req,
            decision=ComplianceDecision(
                decision_id=_entscheidung,
                approved=bool(geprueft),
                reason_code=(
                    ReasonCode.APPROVED if geprueft else ReasonCode.WASH_TRADE
                ),
                detail=(
                    ""
                    if geprueft
                    else "vom Guardian beanstandet — Leg laeuft weiter (#2712)"
                ),
                halted=order_executor.kill_switch.is_halted(user_id),
            ),
        )
        if pm is not None:
            try:
                pm.record_trade(symbol, "sell")
            except Exception:
                order_executor.logging.warning(
                    "[displacement-buy-first] record_trade failed (non-fatal)",
                    exc_info=True,
                )
        order_executor.logging.info(
            "[User %s] buy-first displacement SELL submitted for %s x %s (#2712)",
            user_id,
            symbol,
            qty,
        )
        return True
    except Exception:
        order_executor.logging.critical(
            "[User %s] buy-first displacement SELL FAILED for %s x %s - position "
            "RETAINED (book temporarily over cap, no loss); next rebalance cycle "
            "de-concentrates (#2712)",
            user_id,
            symbol,
            qty,
            exc_info=True,
        )
        return False


class VerdraengungMixin:
    """Rückkauf nach gescheiterter Verdrängung; über ``AusfuehrungMixin`` zusammengesetzt."""

    async def _recover_displacement(
        self,
        user_id: str,
        client: Any,
        pm: Any,
        redis_client: Any,
        symbol_to_close: str,
        displacement_sell_order_id: Optional[str],
        displacement_sell_qty: float,
        context: Any = None,
    ) -> None:
        """Reactive recovery flow for Issue #2189."""
        if not symbol_to_close:
            return

        order_executor.logging.info(
            f"[User {user_id}] Initiating displacement recovery for {symbol_to_close}."
        )

        filled_qty = displacement_sell_qty
        if displacement_sell_order_id:
            try:
                # 1. Attempt to cancel the SELL order (just in case it is somehow not fully filled yet)
                try:
                    await order_executor.asyncio.to_thread(
                        client.cancel_order_by_id, displacement_sell_order_id
                    )
                    order_executor.logging.info(
                        f"[User {user_id}] Dispatched cancel for displacement SELL order {displacement_sell_order_id}"
                    )
                except APIError as ce:
                    order_executor.logging.debug(
                        f"[User {user_id}] Cancel of displacement SELL rejected (likely already filled): {ce}"
                    )

                # 2. Fetch the actual filled quantity
                live_sell_order = await order_executor.asyncio.to_thread(
                    client.get_order_by_id, displacement_sell_order_id
                )
                if live_sell_order:
                    filled_qty = float(
                        getattr(live_sell_order, "filled_qty", 0.0) or 0.0
                    )
                    order_executor.logging.info(
                        f"[User {user_id}] Displacement SELL order {displacement_sell_order_id} filled qty: {filled_qty}"
                    )
            except Exception as err:
                order_executor.logging.warning(
                    f"[User {user_id}] Failed to cancel/query displacement SELL order {displacement_sell_order_id}: {err}. "
                    f"Falling back to original qty: {filled_qty}"
                )

        if filled_qty <= 0:
            order_executor.logging.info(
                f"[User {user_id}] Displacement recovery skipped: filled quantity is 0."
            )
            return

        # 3. Submit compensating re-BUY order
        try:
            # #3387: trug bisher keinen client_order_id. Diese Order ist die ZWEITE
            # Broker-Handlung am Verdraengungs-Leg derselben Entscheidung — sie macht
            # den Verkauf rueckgaengig. Deshalb Leg `displacement`, Versuch 1: damit ist
            # sie vom Verkauf (Versuch 0) unterscheidbar und bleibt nachrechenbar.
            re_buy_req = order_executor.MarketOrderRequest(
                symbol=symbol_to_close,
                qty=filled_qty,
                side=order_executor.OrderSide.BUY,
                time_in_force=order_executor.TimeInForce.DAY,
                client_order_id=order_executor._derived_coid(
                    context, "displacement", 1
                ),
            )
            # Kill-switch (#2467): the compensating re-BUY is still a broker order, so it must
            # honour a tripped circuit breaker like every other submit (mirrors :807). A genuine
            # safety trip keeps the account flat rather than autonomously re-establishing the
            # position we just sold — "no orders after emergency stop" must be airtight.
            order_executor.kill_switch.check_halt(user_id)
            # #3447: durchs Tor, als ``entry`` — NICHT als ``displacement``. Die
            # Freistellungs-Matrix naehme ``displacement`` vom Halt aus; diese Order
            # respektiert den Halt aber bewusst (#2467, siehe oben).
            await order_executor.OrderExecutorMixin._sende_durchs_tor(
                client=client,
                request=re_buy_req,
                symbol=symbol_to_close,
                side_enum=order_executor.OrderSide.BUY,
                qty=filled_qty,
                user_id=user_id,
                decision_id=getattr(context, "decision_id", "") or "",
            )
            # #2712 Inc1: rollback leg visible to compliance (observe-only, fail-open).
            register_displacement_leg(
                getattr(self, "compliance_guardian", None),
                symbol=symbol_to_close,
                side="buy",
                qty=filled_qty,
                price=0.0,
                user_id=user_id,
                held_qty=0.0,
                kind="displacement_recovery_buy",
            )
            order_executor.logging.info(
                f"[User {user_id}] Displacement recovery re-BUY submitted for {filled_qty} shares of {symbol_to_close}."
            )

            # 4. Port Manager Re-sync
            if pm is not None:
                pm.record_trade(symbol_to_close, "buy")
                await order_executor.persist_pm_state_to_redis(
                    pm, symbol_to_close, redis_client
                )
        except Exception as re_buy_err:
            order_executor.logging.critical(
                f"[User {user_id}] 🚨 CRITICAL: Displacement recovery failed! "
                f"Could not re-buy {filled_qty} shares of {symbol_to_close}: {re_buy_err}"
            )
            try:
                redis = await order_executor.RedisClient.get_redis()
                if redis:
                    await order_executor._safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        order_executor.json.dumps(
                            {
                                "type": "state_inconsistency",
                                "severity": "CRITICAL",
                                "title": f"Recovery Failure: {symbol_to_close}",
                                "message": f"Failed to submit recovery re-BUY for {symbol_to_close} after failed swap: {re_buy_err}",
                                "timestamp": order_executor.CompositionRoot.get_instance()
                                .clock_port.now()
                                .isoformat(),
                            }
                        ),
                    )
            except Exception as redis_e:
                order_executor.logging.warning("PubSub error: %s", redis_e)
