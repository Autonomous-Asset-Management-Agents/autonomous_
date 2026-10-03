# core/engine/signal_uebergabe.py
# ARC-E6 G-1a (#3819) — Schritte der Signal-Uebergabe, ausgezogen aus
# OrderExecutorMixin._process_signal_event (core/engine/order_executor.py).
"""Die benannten Schritte der Signal-Uebergabe auf dem Desktop-/[Global]-Pfad.

Der Dirigent ``OrderExecutorMixin._process_signal_event`` bleibt in
``order_executor.py`` und ruft diese Schritte der Reihe nach auf. Hierher gezogen sind
nur Bloecke **ohne** nacktes ``return``: Ein ``return`` im Schritt verliesse den
Schritt, nicht die Uebergabe. Die Bloecke mit ``return`` (Markt zu, Verdraengungs-
Entscheid, HITL, Verkaufstor, Kaufkraft) bleiben deshalb beim Dirigenten.

Die Rümpfe sind **wortgleich** mit dem Stand vor dem Umbau (AST-Vergleich im PR), mit
genau einer angemeldeten Abbildung: Namen, die Tests am Modul ``order_executor`` neu
binden, lesen die Rümpfe als ``order_executor.<name>`` — sonst griffe der Patch ins
Leere. Das sind ``USER_SECRETS_AVAILABLE``, ``_rec_outcome``,
``_regime_throttled_size``, ``config``, ``kill_switch``, ``logging``,
``persist_pm_state_to_redis``, ``tracer`` und ``create_trading_client``. ``tracer`` ist
damit derselbe Tracer wie im Executor; die Spans der Absendung bleiben unveraendert.

Die Importrichtung ist erzwungen: Die Schritte rufen
``OrderExecutorMixin._sende_durchs_tor`` namentlich, also importiert dieses Modul den
Executor — nie umgekehrt. Zusammengesetzt wird in ``BotEngine`` (``base.py``).

Plan: ``docs/3819-signal-uebergabe-process-signal-event-in-benannt/implementation_plan.md``.
"""

import asyncio

from alpaca.common.exceptions import APIError

from core.composition.root import CompositionRoot
from core.risk_manager import (
    apply_sizing_mode_audit,
    apply_vol_targeting_audit,
    effective_free_slots,
    is_immaterial_entry,
)
from core.telemetry_attrs import order_span_attributes

from . import order_executor
from .order_executor import (
    DryRunOrderProxy,
    OrderExecutorMixin,
    UserAlpacaCredentialsNotFoundError,
    _audit_skipped_signal,
    _capture_enabled,
    _dust_floor_skips_exit,
    _safe_bump_exec,
    _topup_gap_capped_qty,
    classify_exit_kind,
    halt_exempt_protective_exit,
    submit_deferred_displacement_sell,
    user_alpaca_secrets,
)


class SignalUebergabeMixin:
    """Schritte von ``_process_signal_event``; in ``BotEngine`` zusammengesetzt."""

    def _schritt_secrets(
        self,
        *,
        symbol,
        uid,
        resolved_client,
    ):
        """Epic 3.4-pre: den Broker-Zugang des zugeordneten Nutzers aufloesen.

        Liefert den Client des Nutzers oder unveraendert ``resolved_client``."""
        if order_executor.USER_SECRETS_AVAILABLE and uid:
            try:
                creds = user_alpaca_secrets.get_user_alpaca_credentials(uid)
                is_paper = getattr(order_executor.config, "PAPER_TRADING", True)
                resolved_client = order_executor.create_trading_client(
                    api_key=creds.api_key,
                    secret_key=creds.secret_key,
                    paper=is_paper,
                )
                order_executor.logging.info(
                    "[%s] Using user-mapped Alpaca credentials for uid=%s",
                    symbol,
                    uid,
                )
            except UserAlpacaCredentialsNotFoundError:
                order_executor.logging.warning(
                    "[%s] No user Alpaca mapping found for uid=%s — falling back.",
                    symbol,
                    uid,
                )
        elif not order_executor.USER_SECRETS_AVAILABLE:
            order_executor.logging.warning(
                "[%s] user_secrets module unavailable — falling back.",
                symbol,
            )
        return resolved_client

    async def _schritt_verkaufsmenge(
        self,
        *,
        symbol,
        held_broker_qty,
        trade_client,
    ):
        """SELL ohne Menge: die verkaufbare Menge aus der Broker-Position holen.

        Liefert ``(qty, held_broker_qty)``; ein Fehlschlag ergibt ``qty = 0.0``."""
        try:
            pos = await asyncio.to_thread(trade_client.get_open_position, symbol)
            qty = float(pos.qty) if hasattr(pos, "qty") else float(pos.get("qty", 0.0))
            held_broker_qty = abs(qty)
        except Exception as e:
            # #2118: a 404 / Alpaca code 40410000 means the position is already
            # flat (nothing to sell) — a clean "closed", NOT an error. Only genuine
            # (transient/other) errors stay "error". #2065: the drop stays auditable
            # + fail-safe (qty=0 → no submit).
            _status = getattr(e, "status_code", None)
            try:
                _code = e.code  # APIError.code may raise on non-JSON body
            except Exception:
                _code = None
            if _status == 404 or _code == 40410000:
                order_executor.logging.info(
                    "[%s] SELL skipped — no open position (already flat/closed).",
                    symbol,
                )
                order_executor._rec_outcome(
                    symbol,
                    "closed",
                    "no open position — already flat",
                )
            else:
                order_executor.logging.warning(
                    "[%s] Could not fetch open position qty: %s — SELL dropped (fail-safe, qty=0)",
                    symbol,
                    e,
                    exc_info=True,
                )
                order_executor._rec_outcome(
                    symbol, "error", f"position fetch failed: {e}"
                )
            qty = 0.0
        return qty, held_broker_qty

    async def _schritt_bemessung(
        self,
        *,
        symbol,
        action,
        context,
        curr,
        _original_qty,
        _sizing_trace,
        trade_client,
        _fb_pm,
    ):
        """BUY: die Positionsgroesse ueber den globalen RiskManager bemessen.

        Liefert ``(qty, _fb_pm, curr)``; ein Fehlschlag ergibt ``qty = 0.0``."""
        try:
            curr = context.current_price if context else 0.0
            acc = await asyncio.to_thread(trade_client.get_account)
            cash = float(getattr(acc, "cash", 0) or 0)
            # #2981: equity aus dem bereits geladenen acc
            # (kein zusaetzlicher Broker-Call) fuer das
            # Materiality-Gate; fail-safe auf 0.0.
            equity = float(getattr(acc, "equity", 0) or 0)
            atr = getattr(context, "atr_14d", 0.0) if context else 0.0
            if atr <= 0:
                atr = curr * 0.05
            vix = getattr(context, "vix_level", 20.0) if context else 20.0
            conviction = getattr(context, "conviction_score", 0.5) if context else 0.5
            # #1953: forward-vol from the DecisionContext;
            # None => fail-safe no-op in the sizer.
            forecast_vol = getattr(context, "forecast_vol", None) if context else None
            # #3199: RR skew percentile; None => fail-open
            # no-op in the sizer AND the audit mirror.
            rr_percentile = (
                getattr(context, "skew_percentile", None) if context else None
            )
            # #3210: directional-vote coverage; None => fail-open
            # no-op in the sizer AND the audit mirror.
            coverage = getattr(context, "vote_coverage", None) if context else None

            # O3: resolve the desktop fallback PM once (getattr-safe) for both the
            # held count and the refresh-freshness flag.
            _fb_pm = getattr(
                getattr(self, "active_strategy", None),
                "portfolio_manager",
                None,
            )
            # #2811: `_sizing_trace` is the per-call sink initialised
            # above — this desktop/[Global] path is where 9,146 chain
            # entries carried the three-item list.
            max_allowed_qty = self.live_risk_manager.calculate_position_size(
                stop_loss_atr_multiplier=3.0,
                atr=atr,
                confidence="high",
                size_scaler=1.0,
                market_data={"vix": vix},
                sizing_trace=_sizing_trace,
                # #2153: book-cap slots, not the ~500-name scan universe
                # (see _execute_tenant_order) — fixes the ~50x under-size.
                # O3 (CASH_AWARE_SLOTS_ENABLED): split by the REMAINING free
                # slots (cap − held) so a mostly-invested book funds proper-sized
                # positions. Held count = the desktop fallback PM's last-refreshed
                # positions (no new broker call); no PM → 0 → cap (byte-identical),
                # and an unconfirmed/stale refresh → cap (positions_confirmed).
                num_stocks_in_strategy=effective_free_slots(
                    len(getattr(_fb_pm, "_position_scores", {}) or {}),
                    positions_confirmed=getattr(_fb_pm, "_last_refresh_ok", True),
                ),
                current_price=curr,
                account_cash=cash,
                allow_fractional=True,
                conviction_score=conviction,
                forecast_vol=forecast_vol,
                rr_percentile=rr_percentile,
                coverage=coverage,
            )
            # #1953/#3199/#3210 audit mirror (MiFID II Art. 17):
            # APPLIED combined vol*skew*coverage scaler ->
            # context.risk_size_scaler; no-op when flags off.
            apply_vol_targeting_audit(context, forecast_vol, rr_percentile, coverage)
            # #3284 Punkt 3: record the sizing PROCEDURE + base weight.
            apply_sizing_mode_audit(context, _sizing_trace)
            # #3361: regime-beyond-VIX throttle (desktop/[Global]
            # path — the one the desktop actually trades through).
            max_allowed_qty = order_executor._regime_throttled_size(
                symbol, action, max_allowed_qty, context
            )
            # #3963: a top-up buys at most the gap to its target.
            max_allowed_qty = _topup_gap_capped_qty(
                _fb_pm,
                symbol,
                max_allowed_qty,
                curr,
                context,
                _sizing_trace,
            )
            if _original_qty > 0:
                qty = min(_original_qty, max_allowed_qty)
            else:
                qty = max_allowed_qty
            # Issue 2935: Materialitaets-Riegel (Option A) - Desktop-Pfad
            import config as _cfg

            _cfg_obj = _cfg.get_config()
            _mat_pct = float(
                getattr(
                    _cfg_obj,
                    "MATERIAL_ENTRY_MIN_PCT_OF_TARGET",
                    0.0,
                )
            )
            if _mat_pct > 0 and qty > 0 and action == "BUY":
                # #2981: shared Materiality-Helper, Divisor
                # equity statt cash -> schliesst die ~100x-Luecke
                # (cash << equity bei investiertem Buch).
                _order_val = qty * curr
                if is_immaterial_entry(_order_val, equity, _mat_pct):
                    await _audit_skipped_signal(
                        symbol,
                        action,
                        "skipped:immaterial_entry",
                        f"Order size  is < {_mat_pct*100:.1f}% of target allocation ",
                        order_value=_order_val,
                    )
                    qty = 0.0
            order_executor.logging.info(
                "[%s] Dynamically calculated global qty: %f",
                symbol,
                qty,
            )
        except Exception as e:
            order_executor.logging.warning(
                "[%s] Global RiskManager sizing failed: %s",
                symbol,
                e,
            )
            qty = 0.0
        return qty, _fb_pm, curr

    async def _schritt_compliance(
        self,
        *,
        symbol,
        action,
        context,
        event,
        qty,
        curr,
        held_broker_qty,
        trade_client,
        uid,
        approved,
    ):
        """Iron Dome: Orderwert, Staub-Schwelle und Tagesbudget pruefen und verbuchen.

        Liefert ``(approved, curr)``."""
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
                "timestamp": CompositionRoot.get_instance().clock_port.time(),
                "user_id": uid or "global",
                # #2065/#2553: the REAL broker-held qty for a SELL (independent
                # witness, NOT an echo of the order qty) — bounds the exit-exemption
                # to a genuinely risk-reducing exit (quantity <= held_qty).
                "held_qty": (held_broker_qty if action == "SELL" else 0.0),
                # #2713: labeled churn loses the exemption (risk exempt).
                "exit_kind": classify_exit_kind(event),
            }
            if not self.compliance_guardian.check_order(compliance_order):
                order_executor.logging.warning(
                    f"[Global] {symbol} 🛡️ BLOCKED by ComplianceGuardian"
                )
                order_executor._rec_outcome(
                    symbol,
                    "blocked:order_value",
                    "Order value limit exceeded.",
                )
                approved = False
            # #2789: relative dust floor for EXITS. Placed INSIDE this chain
            # deliberately: it must sit AFTER the Iron Dome (compliance
            # outranks a product guard) but BEFORE record_trade below, or the
            # churn counter would count an order that is never submitted.
            # `_rec_outcome` keeps a suppressed exit VISIBLE — a silently
            # dropped de-concentration wish is worse than the dust.
            # Exemptions (stop/None/full close) live in core/dust_floor.py.
            elif action == "SELL" and _dust_floor_skips_exit(
                event=event,
                context=context,
                qty=qty,
                held_qty=held_broker_qty,
                strategy=getattr(self, "active_strategy", None),
                symbol=symbol,
                rec_outcome=order_executor._rec_outcome,
            ):
                approved = False
            # PR-0a-ii-5a (HITL): this global path is AUTONOMOUS-ONLY.
            # execute_approved_order (HITL drain) never reaches here —
            # Decision-2 Option B routes it exclusively through
            # _execute_tenant_order (source-guarded). No `source`
            # parameter needed here; adding one would be misleading.
            elif not self.compliance_guardian.check_trade(compliance_order):
                order_executor.logging.warning(
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
                        self.compliance_guardian._daily_limit_alert_sent = True
                    except Exception as alert_err:
                        order_executor.logging.error(
                            f"Failed to send Iron Dome Slack alert: {alert_err}"
                        )
                order_executor._rec_outcome(
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
        return approved, curr

    async def _schritt_absenden(
        self,
        *,
        symbol,
        action,
        context,
        qty,
        req,
        side_enum,
        trade_client,
        uid,
        _schutz_exit,
        _fb_pm,
        _fb_redis,
        _pc_pm,
        _pc_symbol_to_close,
        _fallback_displacement_sell_order_id,
        _fallback_displacement_sell_qty,
        _fb_deferred_close_symbol,
        _fb_deferred_close_qty,
    ):
        """Die Order durchs Tor senden (Schatten oder live) und liefern."""
        if getattr(order_executor.config, "SHADOW_MODE", False):
            order_executor.logging.info(
                f"[Global] {symbol} 🛡️ SHADOW MODE ACTIVE: Bypassing Alpaca for Paper Trade."
            )
            order = await OrderExecutorMixin._sende_durchs_tor(
                client=DryRunOrderProxy("global"),
                request=req,
                symbol=symbol,
                side_enum=side_enum,
                qty=qty,
                user_id=uid or "global",
                decision_id=getattr(context, "decision_id", "") or "",
                is_protective_exit=halt_exempt_protective_exit(context, action),
            )
            context.alpaca_order_id = str(order.id)
            # Part B (exec-visibility): a dry-run submit still IS a
            # decision the operator must see — mark it executed, flagged
            # as a simulation so the surface can distinguish it.
            context.action_executed = True
            context.is_simulation = True
        else:
            with order_executor.tracer.start_as_current_span(
                "broker.submit_order.live"
            ) as span:
                span.set_attribute("trade.action", action)
                for _k, _v in order_span_attributes(symbol, action, req).items():
                    span.set_attribute(_k, _v)
                # LSR R1 (#2252): re-check the halt IMMEDIATELY before the
                # live BUY. The :1841 gate is separated from this submit by the
                # displacement awaits + asyncio.sleep(0.5); a trip in that
                # window (operator /api/live/disable) must abort before a real
                # order leaves. Mirrors the tenant gate (:947).
                try:
                    # #3380: fuer den Schutz-Exit gilt auch diese
                    # Wiederholung nicht — sonst oeffnete das erste
                    # Tor, und das zweite schloesse wieder.
                    if not _schutz_exit:
                        order_executor.kill_switch.check_halt(uid or "global")
                except Exception:
                    span.set_attribute("halt_blocked", True)
                    # #2113 (flag-gated, PURE OBSERVATION): a trip
                    # in the displacement window lands here — the
                    # :1841 gate never saw it. Re-raise unchanged.
                    if _capture_enabled():
                        order_executor._rec_outcome(
                            symbol,
                            "blocked:kill_switch",
                            "kill-switch halt — order blocked "
                            "(submit boundary, #2252)",
                            decision_id=getattr(context, "decision_id", None),
                        )
                    raise
                try:
                    # #3447: durchs Tor. Das ``check_halt`` direkt
                    # darueber bleibt unveraendert stehen.
                    order = await OrderExecutorMixin._sende_durchs_tor(
                        client=trade_client,
                        request=req,
                        symbol=symbol,
                        side_enum=side_enum,
                        qty=qty,
                        user_id=uid or "global",
                        decision_id=getattr(context, "decision_id", "") or "",
                        is_protective_exit=halt_exempt_protective_exit(context, action),
                    )
                    if _fb_deferred_close_symbol:
                        # #2712 Inc 2: BUY at the broker - NOW sell
                        # the displaced position (halt honored;
                        # failure retains the position, no loss).
                        try:
                            order_executor.kill_switch.check_halt(uid or "global")
                            submit_deferred_displacement_sell(
                                client=trade_client,
                                pm=_fb_pm,
                                guardian=self.compliance_guardian,
                                user_id=uid or "global",
                                symbol=_fb_deferred_close_symbol,
                                qty=_fb_deferred_close_qty,
                                decision_id=getattr(context, "decision_id", None),
                            )
                        except Exception:
                            order_executor.logging.critical(
                                "[Global] halt before deferred "
                                "displacement SELL of %s - position "
                                "retained (#2712)",
                                _fb_deferred_close_symbol,
                                exc_info=True,
                            )
                        _fb_deferred_close_symbol = None
                except APIError as submit_err:
                    if _pc_symbol_to_close and _fallback_displacement_sell_order_id:
                        await self._recover_displacement(
                            user_id=uid or "global",
                            client=trade_client,
                            pm=_pc_pm,
                            redis_client=_fb_redis,
                            symbol_to_close=_pc_symbol_to_close,
                            displacement_sell_order_id=_fallback_displacement_sell_order_id,
                            displacement_sell_qty=_fallback_displacement_sell_qty,
                            context=context,
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
        return order

    async def _schritt_nachbuchen(
        self,
        *,
        symbol,
        action,
        context,
        pm,
        _fb_redis,
    ):
        """Den Trade beim Desktop-PortfolioManager nachtragen (Anti-Churn, Min-Hold)."""
        if pm is not None:
            try:
                pm.record_trade(symbol, action.lower())
                if action == "SELL":
                    pm.clear_sell_signals_after_sale(symbol)
                elif action == "BUY":
                    conv = getattr(context, "conviction_score", 0.5) if context else 0.5
                    pm.update_position_conviction(symbol, conv)
                # Part C — persist the updated anti-churn state so the
                # cooldown/trade-history survives a restart (parity with
                # the tenant path). No-op if Redis is unavailable.
                await order_executor.persist_pm_state_to_redis(pm, symbol, _fb_redis)
            except Exception as pm_err:
                order_executor.logging.warning(
                    "[%s] Fallback post-trade PortfolioManager update failed: %s",
                    symbol,
                    pm_err,
                )
