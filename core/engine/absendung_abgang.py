# core/engine/absendung_abgang.py
# ARC-E6 H-1g (#4236) — Abgang der Absendung je Mandant, ausgezogen aus
# OrderExecutorMixin (core/engine/order_executor.py).
"""Die Schritte der Absendung je Mandant, die den Auftrag zum Broker bringen.

Der Dirigent ``OrderExecutorMixin._execute_tenant_order`` bleibt in
``order_executor.py``. Hierher gezogen sind die sechs Schritte des Abgangs: Iron Dome
samt Tageslimit, die Broker-Anfrage samt Halt-Tor, die Schatten- und die Live-Absendung,
die Fuellungsabfrage und der Storno einer haengenden Order mit Markt-Rueckfall und
Slot-Rueckgabe. Jede Absendung geht wie zuvor durchs Tor
(``OrderExecutorMixin._sende_durchs_tor``, live ueber ``_submit_with_market_failsafe``);
der eingefrorene Storno ``cancel_order_by_id`` zog mit ``_schritt_mandant_storno`` um
(``vertrag.toml``, ``[broker_aufrufer.ausnahmen]``).

Die Ruempfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten
Abbildung wie in G-1a, G-1b und H-1d bis H-1f (Entscheidung #4183 §3, Weg b): Jeder
Name, der im Kern gebunden ist, wird als ``order_executor.<name>`` gelesen — sonst griffe
ein Patch am Kern ins Leere. Das sind ``RedisClient``, ``config``, ``kill_switch``,
``logging``, ``tracer``, ``_rec_outcome``, ``_safe_publish``, ``_safe_bump_exec``,
``_capture_enabled``, ``_derived_coid``, ``_gebunden``, ``DryRunOrderProxy``,
``OrderExecutorMixin`` und die Re-Exporte aus
``order_aufbau.py`` (``classify_exit_kind``, ``build_broker_order_request``,
``halt_exempt_protective_exit``, ``exit_failsafe_remaining_qty``,
``_dust_floor_skips_exit``). Dazu ``asyncio``: Die Tests ersetzen den Schlaf der
Fuellungsabfrage ueber ``schlaf_nur_im_modul("core.engine.order_executor")``, also die
Referenz ``asyncio`` im Kern. Der Span ``broker.submit_order.live`` oeffnet weiter
am Tracer des Kerns (Name ``core.engine.order_executor``). Die funktionslokalen Importe
bleiben funktionslokal. ``submit_deferred_displacement_sell`` liest das Modul seit H-1l
(#4241) als ``verdraengung.submit_deferred_displacement_sell``.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4236-*/implementation_plan.md``.
"""

import json
import uuid

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from core.composition.root import CompositionRoot
from core.idempotency import next_attempt
from core.telemetry_attrs import order_span_attributes

from . import order_executor, verdraengung
from .order_executor import _Absendung


class AbsendungAbgangMixin:
    """Abgang-Schritte von ``_execute_tenant_order``; ueber ``AusfuehrungMixin`` zusammengesetzt."""

    async def _schritt_mandant_compliance(self, st: _Absendung) -> bool:
        """Iron Dome: Orderwert, Staubboden fuer Ausstiege, Tageslimit, Verbuchung."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        event, context, size, source = st.event, st.context, st.size, st.source
        # Compliance check
        if self.compliance_guardian:
            compliance_order = {
                "symbol": symbol,
                "side": action.lower(),
                "quantity": size,
                "price": st.curr,
                "strategy_id": "RLStrategy",
                "timestamp": CompositionRoot.get_instance().clock_port.time(),
                "user_id": user_id,
                # #2065/#2553: the REAL broker-held qty for a SELL (independent witness, NOT an
                # echo of the possibly-reduced order size) — bounds the ComplianceGuardian
                # exit-exemption to a genuinely risk-reducing exit (quantity <= held_qty).
                "held_qty": st.held_broker_qty if action == "SELL" else 0.0,
                # #2713: rotation/trim churn loses the exit exemption; risk stays exempt.
                "exit_kind": order_executor.classify_exit_kind(event),
            }
            if not self.compliance_guardian.check_order(compliance_order):
                order_executor.logging.warning(
                    f"[User {user_id}] {symbol} 🛡️ BLOCKED by ComplianceGuardian"
                )
                try:
                    redis = await order_executor.RedisClient.get_redis()
                    await order_executor._safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Compliance Blocked: {symbol}",
                                "message": "Order Value Limit Exceeded.",
                                "timestamp": CompositionRoot.get_instance()
                                .clock_port.now()
                                .isoformat(),
                            }
                        ),
                    )
                except Exception as e:
                    order_executor.logging.warning("PubSub error: %s", e)
                order_executor._rec_outcome(
                    symbol, "blocked:order_value", "Order value limit exceeded."
                )
                return False
            # #2789: relative dust floor for EXITS. Position in the chain is load-bearing
            # — AFTER the Iron Dome (compliance outranks a product guard) and BEFORE
            # record_trade at :~1399, or the churn budget would be spent on an order that
            # is never submitted. Same adapter as the desktop/[Global] path so the two
            # cannot drift apart; exemptions live in core/dust_floor.py.
            if action == "SELL" and order_executor._dust_floor_skips_exit(
                event=event,
                context=context,
                qty=size,
                held_qty=st.held_broker_qty,
                symbol=symbol,
                equity=st.equity,
                rec_outcome=order_executor._rec_outcome,
            ):
                return False
            if not self.compliance_guardian.check_trade(
                compliance_order, source=source
            ):
                order_executor.logging.warning(
                    f"[User {user_id}] {symbol} 🛡️ BLOCKED by ComplianceGuardian (daily)"
                )
                if not getattr(
                    self.compliance_guardian, "_daily_limit_alert_sent", False
                ):
                    try:
                        from core.notifier import send_slack_alert

                        msg = f"Compliance: Max daily trades ({self.compliance_guardian.max_daily_trades}) reached. Trading halted."
                        order_executor.asyncio.create_task(
                            order_executor.asyncio.to_thread(
                                send_slack_alert,
                                f"🛑 *Iron Dome Block*: {msg}",
                                level="warning",
                            )
                        )
                        self.compliance_guardian._daily_limit_alert_sent = True
                    except Exception as alert_err:
                        order_executor.logging.error(
                            f"Failed to send Iron Dome Slack alert: {alert_err}"
                        )
                try:
                    redis = await order_executor.RedisClient.get_redis()
                    await order_executor._safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Compliance Blocked: {symbol}",
                                "message": "Daily Trade Limit Reached.",
                                "timestamp": CompositionRoot.get_instance()
                                .clock_port.now()
                                .isoformat(),
                            }
                        ),
                    )
                except Exception as e:
                    order_executor.logging.warning("PubSub error: %s", e)
                order_executor._rec_outcome(
                    symbol, "blocked:daily_limit", "Daily trade limit reached."
                )
                return False
            # PR-0a-ii-5a: a human-approved order must NOT consume the autonomous
            # daily-trade budget (its cap was already source-skipped in check_trade).
            if source != "human_approved":
                # #1849 follow-up: atomic, lock-guarded increment (the bare
                # ``+= 1`` RMW lost increments under concurrency → the daily cap
                # could be silently exceeded). Behaviour identical: +1 per trade.
                # ADR-C04-EXIT: pass the order so a risk-reducing exit does not
                # consume a buy slot (symmetric with the check_trade block-exemption).
                self.compliance_guardian.record_trade(compliance_order)
        return True

    def _schritt_mandant_auftrag(self, st: _Absendung) -> None:
        """Die Broker-Anfrage bauen, dann das Halt-Tor (wirft bei Halt)."""
        user_id, action, symbol, context = st.user_id, st.action, st.symbol, st.context
        st.side_enum = side_enum = OrderSide.BUY if action == "BUY" else OrderSide.SELL
        client_order_id = getattr(context, "client_order_id", None) if context else None
        # #3387: `isinstance(..., str)` liess einen LEEREN String durch — der waere
        # als client_order_id beim Broker gelandet. Jetzt: erst der Kontextwert,
        # sonst aus der decision_id abgeleitet, und nur ohne beides gewuerfelt.
        st.client_order_id = client_order_id = (
            client_order_id
            if isinstance(client_order_id, str) and client_order_id
            else order_executor._derived_coid(context)
        )
        # EXC-1 / #2558: marketable-limit factory (exit-selective).
        # USE_LIMIT_ORDERS is the GLOBAL flag (BUYs + exits, pre-existing).
        # USE_LIMIT_EXITS is the exit-selective flag: it routes only SELL/exit
        # orders through the marketable-limit path and leaves the buy side
        # untouched. Both feed the SAME price heuristic (no duplication).
        try:
            import config as _cfg

            use_limit = getattr(_cfg, "USE_LIMIT_ORDERS", False)
            use_limit_exits = getattr(_cfg, "USE_LIMIT_EXITS", False)
            spread_buffer = getattr(_cfg, "LIMIT_ORDER_SPREAD_BUFFER_PCT", 0.001)
        except ImportError:
            use_limit = False
            use_limit_exits = False
            spread_buffer = 0.001

        _current_price = getattr(context, "current_price", None) if context else None
        st.req = req = order_executor.build_broker_order_request(
            symbol=symbol,
            qty=st.size,
            side_enum=side_enum,
            client_order_id=client_order_id,
            current_price=_current_price,
            use_limit_orders=use_limit,
            use_limit_exits=use_limit_exits,
            spread_buffer=spread_buffer,
        )
        # #2558: an EXIT (SELL) routed to a marketable limit must always fill —
        # a broker rejection / non-fill falls back to a plain market order below.
        st.is_limit_exit = isinstance(req, LimitOrderRequest) and (
            side_enum == OrderSide.SELL
        )

        # --- FIX: Kill Switch Gate ---
        # #3380 (ARC-E1.4): Ein Schutz-Exit darf am Halt nicht scheitern.
        # `risk_konto.py::_konto_breaker` setzt `trading_halted` und loest im
        # selben Block den Kill-Switch aus — ohne diese Freistellung waere die
        # Reihenfolge-Korrektur in trading_loop.py wirkungslos: der Stop-SELL
        # erreicht den Broker gar nicht.
        #
        # Die Freistellung ist bewusst ENG: nur ein SELL, den der Stop-Pfad als
        # Schutz-Exit ausgewiesen hat (`triggered_by_stop`, gesetzt in
        # schleifen_stops.py:231). Ein Einstieg bleibt geblockt, eine Rotation oder
        # ein Trim ebenfalls — die tragen das Kennzeichen nicht
        # (ausstieg_hebel.py:312, :519).
        if order_executor.halt_exempt_protective_exit(context, action):
            order_executor.logging.warning(
                "[Halt] %s: Schutz-Exit trotz Kill-Switch-Halt ausgefuehrt "
                "(stop_type=%s) — Freistellung nach #3380, nicht blockiert.",
                symbol,
                getattr(context, "stop_type", "") or "unbekannt",
            )
        else:
            try:
                order_executor.kill_switch.check_halt(user_id)
            except Exception:
                # #2113 (flag-gated, PURE OBSERVATION): persist WHY the order died —
                # blocked:kill_switch was defined but never emitted. The re-raise
                # keeps the gate's control flow byte-identical to main.
                if order_executor._capture_enabled():
                    order_executor._rec_outcome(
                        symbol,
                        "blocked:kill_switch",
                        "kill-switch halt — order blocked",
                        decision_id=getattr(context, "decision_id", None),
                    )
                raise

    async def _schritt_mandant_schatten(self, st: _Absendung) -> None:
        """Schattenmodus: die Order geht durchs Tor an den Dry-Run-Broker."""
        user_id, action, symbol, context = st.user_id, st.action, st.symbol, st.context
        order_executor.logging.info(
            f"[User {user_id}] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
        )
        with order_executor.tracer.start_as_current_span(
            "broker.submit_order.live"
        ) as span:
            span.set_attribute("trade.action", action)
            for _k, _v in order_span_attributes(symbol, action, st.req).items():
                span.set_attribute(_k, _v)
            # #3447: auch der Schatten-Broker haengt hinter dem Tor — eine
            # Schatten-Order ohne ComplianceDecision waere im Audit nicht von
            # einer Umgehung zu unterscheiden.
            st.order = order = (
                await order_executor.OrderExecutorMixin._sende_durchs_tor(
                    client=order_executor.DryRunOrderProxy(user_id),
                    request=st.req,
                    symbol=symbol,
                    side_enum=st.side_enum,
                    qty=st.size,
                    user_id=user_id,
                    decision_id=getattr(context, "decision_id", "") or "",
                    is_protective_exit=order_executor.halt_exempt_protective_exit(
                        context, action
                    ),
                )
            )
        if context:
            context.alpaca_order_id = str(order.id)
            # Part B (exec-visibility): dry-run submit is still an executed decision.
            context.action_executed = True
            context.is_simulation = True
        if st.deferred_close_symbol:
            # #2712 Inc 2: shadow keeps parity - the deferred SELL goes through
            # the dry-run proxy so paper/live sequencing is byte-comparable.
            verdraengung.submit_deferred_displacement_sell(
                client=order_executor.DryRunOrderProxy(user_id),
                pm=st.pm,
                guardian=self.compliance_guardian,
                user_id=user_id,
                symbol=st.deferred_close_symbol,
                qty=st.deferred_close_qty,
                decision_id=getattr(context, "decision_id", None),
            )
            st.deferred_close_symbol = None

    async def _schritt_mandant_senden(self, st: _Absendung) -> None:
        """Live: Absendung mit Markt-Rueckfall; danach der aufgeschobene Verdraengungs-SELL."""
        user_id, action, symbol, context = st.user_id, st.action, st.symbol, st.context
        client = st.client
        st.submit_attempted = True
        try:
            # #2558: a rejected marketable-limit EXIT falls back to a
            # market order here so the position always closes; a BUY (or
            # any non-exit) re-raises unchanged into the handler below.
            st.order = await self._submit_with_market_failsafe(
                client=client,
                primary_req=st.req,
                symbol=symbol,
                qty=st.size,
                side_enum=st.side_enum,
                user_id=user_id,
                is_exit=st.is_limit_exit,
                decision_id=getattr(context, "decision_id", "") or "",
                # Dasselbe Urteil wie das Halt-Tor (#3380, `_schritt_mandant_auftrag`)
                # — hereingereicht, damit das Tor es nicht neu faellt.
                is_protective_exit=order_executor.halt_exempt_protective_exit(
                    context, action
                ),
            )
        except APIError as submit_err:
            # #3821: auf dem SELL-Pfad ist `symbol_to_close` nie gebunden (_gebunden).
            if (
                order_executor._gebunden(st, "symbol_to_close")
                and st.displacement_sell_order_id
            ):
                await self._recover_displacement(
                    user_id=user_id,
                    client=client,
                    pm=st.pm,
                    redis_client=st.redis_client,
                    symbol_to_close=st.symbol_to_close,
                    displacement_sell_order_id=st.displacement_sell_order_id,
                    displacement_sell_qty=st.displacement_sell_qty,
                    context=context,
                )
            # #2118 Reserve-and-Reclaim: deterministic 4xx broker rejection → give back
            # the daily slot reserved for this autonomous order. Ambiguous 5xx/timeout
            # keeps the reservation (fill may have landed → fail-closed).
            _sc = getattr(submit_err, "status_code", None)
            if (
                self.compliance_guardian
                and st.source != "human_approved"
                and _sc is not None
                and 400 <= _sc < 500
            ):
                self.compliance_guardian.refund_trade()
            raise
        # PR A.2: fail-safe — the order reached the broker.
        order_executor._safe_bump_exec("submit_ok")
        order_executor._rec_outcome(symbol, "executed")

        if context:
            context.alpaca_order_id = str(st.order.id)
            context.action_executed = True
            context.is_simulation = False

        # #1016: Die Frist ist ein Feld (ADR-H01 in settings.py), kein
        # Literal. Sie entscheidet, wie lange Kapital in einer nicht
        # gefuellten Order gebunden bleibt.
        st.max_wait_seconds = float(order_executor.config.ORDER_FILL_TIMEOUT_SECONDS)
        st.poll_interval = float(order_executor.config.ORDER_FILL_POLL_SECONDS)
        if st.deferred_close_symbol:
            # #2712 Inc 2: BUY is at the broker - NOW sell the displaced
            # position (kill-switch honored; failure retains the position,
            # never a loss roundtrip).
            try:
                order_executor.kill_switch.check_halt(user_id)
                verdraengung.submit_deferred_displacement_sell(
                    client=client,
                    pm=st.pm,
                    guardian=self.compliance_guardian,
                    user_id=user_id,
                    symbol=st.deferred_close_symbol,
                    qty=st.deferred_close_qty,
                    decision_id=getattr(context, "decision_id", None),
                )
            except Exception:
                order_executor.logging.critical(
                    "[User %s] halt tripped before the deferred "
                    "displacement SELL of %s - position retained (#2712)",
                    user_id,
                    st.deferred_close_symbol,
                    exc_info=True,
                )
            st.deferred_close_symbol = None

    async def _schritt_mandant_fuellung(self, st: _Absendung) -> bool:
        """Live: den Orderstand bis zur Frist abfragen. ``True`` heisst gefuellt."""
        # EXC-1: Task 3 & 4 - Order Lifecycle Polling & Synchronisation
        from alpaca.trading.enums import OrderStatus

        user_id, symbol, order = st.user_id, st.symbol, st.order
        max_wait_seconds, poll_interval = st.max_wait_seconds, st.poll_interval
        waited = 0
        is_filled = False
        st.live_order = (
            None  # #2118: last observed order state (for cancel-0-fills refund)
        )

        while waited < max_wait_seconds:
            try:
                # Fetch current state from Alpaca (wrap sync network call to prevent event loop blocking)
                live_order = await order_executor.asyncio.to_thread(
                    st.client.get_order_by_id, order.id
                )
                st.live_order = live_order
                if live_order.status == OrderStatus.FILLED:
                    is_filled = True
                    # PR A.2: fail-safe — stamp the last observed fill.
                    order_executor._safe_bump_exec("last_fill_ts", set_ts=True)
                    break
                elif live_order.status in (
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                    OrderStatus.REJECTED,
                ):
                    order_executor.logging.warning(
                        f"[User {user_id}] Order {order.id} for {symbol} ended in status {live_order.status}"
                    )
                    break
            except Exception as poll_e:
                order_executor.logging.warning(
                    f"Error polling order status for {order.id}: {poll_e}"
                )

            await order_executor.asyncio.sleep(poll_interval)
            waited += poll_interval
            # PR A.2: fail-safe — count each not-yet-filled poll retry.
            order_executor._safe_bump_exec("retry_count")
        return is_filled

    async def _schritt_mandant_storno(self, st: _Absendung) -> None:
        """Live, nicht gefuellt: stornieren, Ausstieg zum Markt, Verdraengung, Slot."""
        user_id, action, symbol, context = st.user_id, st.action, st.symbol, st.context
        client, order, live_order = st.client, st.order, st.live_order
        order_executor.logging.warning(
            f"[User {user_id}] Order {order.id} for {symbol} not filled within {st.max_wait_seconds}s. Attempting to cancel."
        )
        try:
            await order_executor.asyncio.to_thread(client.cancel_order_by_id, order.id)
            order_executor.logging.info(
                f"Successfully cancelled hanging order {order.id}"
            )
        except Exception as cancel_e:
            order_executor.logging.error(
                f"Failed to cancel hanging order {order.id}: {cancel_e}"
            )

        # #2558 fail-safe — a marketable-limit EXIT that did not fill in
        # time was just cancelled; re-submit the CONFIRMED-unfilled
        # remainder as a MARKET order so the position always closes.
        # exit_failsafe_remaining_qty returns None (skip) unless the
        # remainder is observed, so a partial/unknown fill can never
        # oversell.
        _failsafe_qty = order_executor.exit_failsafe_remaining_qty(
            is_limit_exit=st.is_limit_exit,
            order_qty=st.size,
            live_order=live_order,
        )
        if _failsafe_qty:
            try:
                # #3387: wie oben — Folgeversuch desselben Legs,
                # abgeleitet statt gewuerfelt.
                _mkt_failsafe = MarketOrderRequest(
                    symbol=symbol,
                    qty=_failsafe_qty,
                    side=st.side_enum,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=next_attempt(
                        st.client_order_id or str(uuid.uuid4())
                    ),
                )
                await order_executor.OrderExecutorMixin._sende_durchs_tor(
                    client=client,
                    request=_mkt_failsafe,
                    symbol=symbol,
                    side_enum=st.side_enum,
                    qty=_failsafe_qty,
                    user_id=user_id,
                    decision_id=getattr(context, "decision_id", "") or "",
                    is_protective_exit=order_executor.halt_exempt_protective_exit(
                        context, action
                    ),
                )
                order_executor.logging.warning(
                    "[User %s] %s marketable-limit EXIT non-fill — "
                    "MARKET fallback submitted for %s share(s) so the "
                    "position closes (#2558).",
                    user_id,
                    symbol,
                    _failsafe_qty,
                )
            except Exception as _failsafe_err:
                order_executor.logging.error(
                    "[User %s] %s EXIT market fallback after non-fill "
                    "FAILED: %s — position may remain open (#2558).",
                    user_id,
                    symbol,
                    _failsafe_err,
                )

        # #3821: auf dem SELL-Pfad ist `symbol_to_close` nie gebunden (_gebunden).
        if (
            order_executor._gebunden(st, "symbol_to_close")
            and st.displacement_sell_order_id
        ):
            await self._recover_displacement(
                user_id=user_id,
                client=client,
                pm=st.pm,
                redis_client=st.redis_client,
                symbol_to_close=st.symbol_to_close,
                displacement_sell_order_id=st.displacement_sell_order_id,
                displacement_sell_qty=st.displacement_sell_qty,
                context=context,
            )

        # #2118 Reserve-and-Reclaim: the order reached the broker but was cancelled
        # UNFILLED → give back the reserved daily slot. Guard strictly: refund ONLY on
        # CONFIRMED 0 fills — a partial fill DID trade (keep the reservation), and an
        # unknown fill state (never polled) also keeps it (fail-closed).
        _filled_qty = None
        try:
            if live_order is not None:
                _filled_qty = float(getattr(live_order, "filled_qty", 0) or 0)
        except Exception:
            _filled_qty = None
        if (
            self.compliance_guardian
            and st.source != "human_approved"
            and _filled_qty == 0.0
        ):
            self.compliance_guardian.refund_trade()
