# core/engine/absendung_vorlauf.py
# ARC-E6 H-1f (#4235) — Vorlauf der Absendung je Mandant, ausgezogen aus
# OrderExecutorMixin (core/engine/order_executor.py).
"""Die Schritte der Absendung je Mandant, die VOR dem Abgang zum Broker laufen.

Der Dirigent ``OrderExecutorMixin._execute_tenant_order`` bleibt in
``order_executor.py``. Hierher gezogen sind die sieben Schritte des Vorlaufs: Mandant
vorbereiten, Bestand (SELL), Bemessung (BUY), Portfolio-Tor samt Art-14-Tor, Verdraengung,
sofortiger Verdraengungs-Verkauf und die Menge. Der Verdraengungs-Verkauf geht wie zuvor
durchs Tor (``OrderExecutorMixin._sende_durchs_tor``); keiner der Schritte ruft eine
mutierende Broker-Methode oder liest die Wanduhr.

Die Ruempfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten
Abbildung wie in G-1a, G-1b, H-1d und H-1e (Entscheidung #4183 §3, Weg b): Jeder Name,
der im Kern gebunden ist, wird als ``order_executor.<name>`` gelesen — sonst griffe ein
Patch am Kern ins Leere. Das sind ``RedisClient``, ``config``, ``kill_switch``,
``logging``, ``restore_pm_state_from_redis``, ``persist_pm_state_to_redis``,
``_rec_outcome``, ``_regime_throttled_size``, ``_earnings_guard_veto``,
``_audit_skipped_signal``, ``OrderExecutorMixin``, die vier ``SKIP_REASON_*``,
``_safe_publish``, ``_derived_coid``, ``_gebunden`` und ``_topup_gap_capped_qty``.
``displacement_available_bp`` und ``register_displacement_leg`` liest es seit H-1l (#4241)
als ``verdraengung.<name>``. Die funktionslokalen Importe bleiben funktionslokal.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4235-*/implementation_plan.md``.
"""

import asyncio
import json

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from core.composition.root import CompositionRoot
from core.risk_manager import (
    apply_sizing_mode_audit,
    apply_vol_targeting_audit,
    effective_free_slots,
    is_immaterial_entry,
)

from . import order_executor, verdraengung
from .order_executor import _Absendung


class AbsendungVorlaufMixin:
    """Vorlauf-Schritte von ``_execute_tenant_order``; ueber ``AusfuehrungMixin`` zusammengesetzt."""

    async def _schritt_mandant_vorbereiten(self, st: _Absendung) -> bool:
        """Risiko- und PortfolioManager des Mandanten, Preis und wiederhergestellter Zustand."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        context = st.context
        st.rm = self._get_tenant_risk_manager(user_id, st.client, st.equity)
        st.curr = context.current_price if context else 0.0
        if st.curr <= 0:
            order_executor.logging.warning(
                "⚠️ OrderExecutor: Skipping %s %s - Missing current_price in DecisionContext",
                action,
                symbol,
            )
            # #2585: approved signal will not execute — audit the skip.
            await order_executor._audit_skipped_signal(
                symbol,
                action,
                order_executor.SKIP_REASON_OTHER,
                "missing current_price in DecisionContext",
            )
            return False

        # Hoist PM init + restore ABOVE action branch:
        # SELL can_sell_position() AND BUY debate_position_swap() both
        # read _trade_history — both paths must see the restored state.
        st.pm = pm = self._get_tenant_portfolio_manager(user_id, st.client, st.equity)
        if not hasattr(self, "_pm_restored"):
            self._pm_restored: set = set()
        # Restore cross-restart anti-churn state (redis_client fetched above)
        await order_executor.restore_pm_state_from_redis(
            pm, st.redis_client, self._pm_restored
        )

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
        return True

    async def _schritt_mandant_bestand(self, st: _Absendung) -> bool:
        """SELL: Anti-Churn-Sperre, dann der echte Broker-Bestand als Menge und Zeuge."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        pm, client = st.pm, st.client
        can_sell = True
        reason = ""
        if hasattr(pm, "can_sell_position"):
            res = pm.can_sell_position(symbol)
            if isinstance(res, tuple) and len(res) == 2:
                can_sell, reason = res
        if not can_sell:
            order_executor.logging.warning(
                "[User %s] %s 📊 SELL blocked by anti-churn: %s",
                user_id,
                symbol,
                reason,
            )
            try:
                redis = await order_executor.RedisClient.get_redis()
                if redis:
                    await order_executor._safe_publish(
                        redis,
                        f"explainability:{user_id}",
                        json.dumps(
                            {
                                "type": "trade_rejected",
                                "title": f"Anti-Churn Blocked: {symbol}",
                                "message": reason,
                                "timestamp": CompositionRoot.get_instance()
                                .clock_port.now()
                                .isoformat(),
                            }
                        ),
                    )
            except Exception as redis_e:
                order_executor.logging.warning("PubSub error: %s", redis_e)
            pm.record_sell_signal(symbol)
            await order_executor.persist_pm_state_to_redis(pm, symbol, st.redis_client)
            # #2585: approved SELL held back by anti-churn — audit the skip.
            await order_executor._audit_skipped_signal(
                symbol,
                action,
                order_executor.SKIP_REASON_OTHER,
                f"anti-churn: {reason}",
            )
            return False

        # #2553: capture the REAL broker holding as an INDEPENDENT held-qty witness for the
        # compliance exit-exemption, BEFORE any human-approved down-sizing below. Echoing the
        # (reducible) order `size` made quantity<=held_qty a tautology — a SELL exceeding the
        # position would self-exempt. A fetch failure leaves it 0.0 → the exemption fails closed.
        held_broker_qty = 0.0
        try:
            pos = await asyncio.to_thread(client.get_open_position, symbol)
            size = float(pos.qty) if hasattr(pos, "qty") else float(pos.get("qty", 0.0))
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
                order_executor.logging.info(
                    "[User %s] %s SELL skipped — no open position (already flat/closed).",
                    user_id,
                    symbol,
                )
                order_executor._rec_outcome(
                    symbol, "closed", "no open position — already flat"
                )
                # #2585: approved SELL cannot execute (already flat) — audit.
                await order_executor._audit_skipped_signal(
                    symbol,
                    action,
                    order_executor.SKIP_REASON_OTHER,
                    "no open position (already flat/closed)",
                )
            else:
                order_executor.logging.warning(
                    "[User %s] Could not fetch open position for %s: %s — aborting SELL.",
                    user_id,
                    symbol,
                    e,
                )
                order_executor._rec_outcome(
                    symbol, "error", f"position fetch failed: {e}"
                )
                # #2585: approved SELL aborted on a broker error — audit.
                await order_executor._audit_skipped_signal(
                    symbol,
                    action,
                    order_executor.SKIP_REASON_EXECUTION_ERROR,
                    f"position fetch failed: {e}",
                )
            return False  # Abort immediately — do not submit MarketOrderRequest(qty=0)
        st.size, st.held_broker_qty = size, held_broker_qty
        return True

    async def _schritt_mandant_bemessung(self, st: _Absendung) -> bool:
        """BUY: Menge aus Konto und Kontext, Drossel, Nachkauf-Luecke, Einstiegs-Riegel."""
        action, symbol, context = st.action, st.symbol, st.context
        curr, pm = st.curr, st.pm
        st.account = account = await asyncio.to_thread(st.client.get_account)
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
        conviction = getattr(context, "conviction_score", 0.5) if context else 0.5
        # #1953: HAR-RV forward-vol plumbed via the DecisionContext
        # (runner._score_to_signal); None => fail-safe no-op in the sizer.
        st.forecast_vol = forecast_vol = (
            getattr(context, "forecast_vol", None) if context else None
        )
        # #3199: 25Δ RR skew percentile from the DecisionContext; None =>
        # fail-open no-op in the sizer AND the audit mirror.
        st.rr_percentile = rr_percentile = (
            getattr(context, "skew_percentile", None) if context else None
        )
        # #3210: directional-vote coverage from the DecisionContext; None =>
        # fail-open no-op in the sizer AND the audit mirror.
        st.coverage = coverage = (
            getattr(context, "vote_coverage", None) if context else None
        )

        # #2811: per-call sink — records WHICH limit zeroed the size, so the
        # audit entry below can name the cause instead of a three-item list.
        st.sizing_trace = sizing_trace = {}
        size = st.rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=atr,
            confidence="high",
            size_scaler=1.0,
            market_data={"vix": vix},
            sizing_trace=sizing_trace,
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
            rr_percentile=rr_percentile,
            coverage=coverage,
        )
        # #1953/#3199/#3210 audit mirror (MiFID II Art. 17): record the APPLIED
        # combined vol*skew*coverage scaler in context.risk_size_scaler — no-op
        # when all flags off / scaler 1.0.
        apply_vol_targeting_audit(context, forecast_vol, rr_percentile, coverage)
        # #3284 Punkt 3: record the sizing PROCEDURE + base weight.
        apply_sizing_mode_audit(context, sizing_trace)
        # #3361: regime-beyond-VIX throttle — shrinks a NEW BUY in a credit-led
        # risk-off reading. Dark default ⇒ untouched. Before the materiality gate,
        # so a throttled dust order is skipped there instead of being sent.
        size = order_executor._regime_throttled_size(symbol, action, size, context)
        # #3963: a top-up buys at most the gap to its target (after the throttle).
        if action == "BUY":
            size = order_executor._topup_gap_capped_qty(
                pm, symbol, size, curr, context, sizing_trace
            )
        st.size = size
        # Issue 2935: Materialitaets-Riegel (Option A) - Tenant-Pfad
        import config as _cfg

        _cfg_obj = _cfg.get_config()
        _mat_pct = float(getattr(_cfg_obj, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", 0.0))
        if _mat_pct > 0 and size > 0 and action == "BUY":
            # #2981: shared Materiality-Helper, Divisor equity (unveraendert)
            _order_val = size * curr
            if is_immaterial_entry(_order_val, st.equity, _mat_pct):
                await order_executor._audit_skipped_signal(
                    symbol,
                    action,
                    "skipped:immaterial_entry",
                    f"Order size  is < {_mat_pct*100:.1f}% of target allocation ",
                    order_value=_order_val,
                )
                return False

        # #3349 Earnings-Proximity-Guard (Epic #2963): BUY-only entry gate.
        # Dark default (EARNINGS_GUARD_ENABLED=False) ⇒ the helper returns None
        # without touching the cache ⇒ byte-identical. Shared with the
        # desktop/[Global] path so both money paths apply the same rule.
        _eg = order_executor._earnings_guard_veto(symbol, action)
        if _eg is not None:
            _eg_tag, _eg_reason = _eg
            order_executor._rec_outcome(
                symbol,
                _eg_tag,
                _eg_reason,
                decision_id=getattr(context, "decision_id", None),
            )
            await order_executor._audit_skipped_signal(
                symbol, action, _eg_tag, _eg_reason
            )
            return False
        return True

    async def _schritt_mandant_portfolio(self, st: _Absendung) -> bool:
        """BUY: Entscheidung des PortfolioManagers, dann das Art-14-Tor (HITL)."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        context, curr, pm, size = st.context, st.curr, st.pm, st.size
        features_dict = {}
        if context and hasattr(context, "__dict__"):
            for k in ["rsi_14", "macd", "adx_14", "volatility_20d"]:
                if hasattr(context, k):
                    # #3251: skip unset (None) fields — adx_14/volatility_20d now
                    # default to None (no fabricated 25.0/0.02 in the audit record).
                    # Omitting them lets score_opportunity apply its OWN neutral
                    # default (.get(..., 20.0)) instead of receiving a None that
                    # would break the numeric comparisons.
                    _v = getattr(context, k)
                    if _v is not None:
                        features_dict[k] = _v

        opp = pm.score_opportunity(
            symbol=symbol,
            current_price=curr,
            rl_action=1,
            model_confidence=context.lstm_prediction if context else 0.5,
            features=features_dict,
            # #3619: the dead-band derives the sizer's clean-weight target.
            forecast_vol=st.forecast_vol,
            skew_percentile=st.rr_percentile,
            vote_coverage=st.coverage,
        )
        should_open, reasoning, symbol_to_close = pm.should_open_new_position(opp)
        st.symbol_to_close = symbol_to_close
        # #3008 step 1 (observation-only): record which incumbent this BUY
        # displaces on the decision row. `symbol_to_close`/`reasoning` are already
        # computed and drive the swap below; this only attaches them to the logged
        # context (columns + dataclass field already exist). Nothing branches on
        # the field, so no order can change (verified: sole consumer is
        # cloud_logger.build_reasoning_summary).
        if context is not None:
            context.symbol_to_close = symbol_to_close or ""
            context.portfolio_reason = reasoning or ""
        if not should_open:
            order_executor.logging.debug(
                f"[User {user_id}] {symbol} 📊 Portfolio Blocked: {reasoning}"
            )
            try:
                redis = await order_executor.RedisClient.get_redis()
                await order_executor._safe_publish(
                    redis,
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "trade_rejected",
                            "title": f"Portfolio Blocked: {symbol}",
                            "message": reasoning,
                            "timestamp": CompositionRoot.get_instance()
                            .clock_port.now()
                            .isoformat(),
                        }
                    ),
                )
            except Exception as e:
                order_executor.logging.warning("PubSub error: %s", e)
            # #2585: approved BUY declined by the PortfolioManager — before
            # this, the drop was DEBUG-only + Redis-only (a true silent skip).
            await order_executor._audit_skipped_signal(
                symbol,
                action,
                order_executor.SKIP_REASON_OTHER,
                f"portfolio declined: {reasoning}",
                order_value=abs(size) * curr,
            )
            return False

        # #2704 — Art-14 gate, tenant path. Position: after the portfolio decision,
        # BEFORE the displacement step (`_schritt_mandant_verdraengung`). Two reasons it
        # cannot go later:
        #   1. the displacement SELL reaches the broker inside that step (before the
        #      compliance counter), so a gate after it would have sold a position to make
        #      room for an order that then waits for approval — the book one position
        #      shorter, for nothing;
        #   2. `if symbol_to_close:` is a branch only SOME orders take. Inside it, every
        #      order with room in the book — the common case — would execute UNGATED.
        # Guarded on `size > 0`: this path's own `if size <= 0: return` comes AFTER the
        # displacement sell, so a 0-size order must not be queued here.
        if size > 0 and await self._hitl_holds_order(
            st.event, context, symbol, user_id, size
        ):
            return False
        return True

    async def _schritt_mandant_verdraengung(self, st: _Absendung) -> bool:
        """BUY: den verdraengten Titel pruefen; Verkauf nach dem Kauf oder sofort."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        client, account, size, curr = st.client, st.account, st.size, st.curr
        symbol_to_close = st.symbol_to_close
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
                        # #2712 Inc 2: buy-first assumes STRICT BP (no proceeds).
                        # #4235: einzeilig, denn die Ratsche (regeln.RATSCHEN_MUSTER) zaehlt
                        # je Zeile; umbrochen fiele diese Fundstelle aus der Zaehlung.
                        # fmt: off
                        _buy_first = bool(
                            getattr(order_executor.config, "DISPLACEMENT_BUY_FIRST", True)
                        )
                        # fmt: on
                        buying_power_available = verdraengung.displacement_available_bp(
                            buy_first=_buy_first,
                            buying_power=buying_power_val,
                            sell_value=sell_value,
                            multiplier=multiplier_val,
                        )

                        required_buying_power = size * curr
                        if buying_power_available < required_buying_power:
                            order_executor.logging.warning(
                                f"[User {user_id}] ⚠️ Displacement pre-check failed. "
                                f"Required BP: {required_buying_power}, Available BP (post-sell estimate): {buying_power_available}. "
                                f"Aborting swap for {symbol_to_close} -> {symbol}."
                            )
                            try:
                                redis = await order_executor.RedisClient.get_redis()
                                if redis:
                                    await order_executor._safe_publish(
                                        redis,
                                        f"explainability:{user_id}",
                                        json.dumps(
                                            {
                                                "type": "trade_rejected",
                                                "title": f"Precheck Blocked: {symbol}",
                                                "message": f"Insufficient settled buying power (multiplier={multiplier_str}). Available: {buying_power_available}, Required: {required_buying_power}",
                                                "timestamp": CompositionRoot.get_instance()
                                                .clock_port.now()
                                                .isoformat(),
                                            }
                                        ),
                                    )
                            except Exception as redis_e:
                                order_executor.logging.warning(
                                    "PubSub error: %s", redis_e
                                )
                            # #2585: approved BUY dropped at the buying-power
                            # precheck — audit the skip.
                            await order_executor._audit_skipped_signal(
                                symbol,
                                action,
                                order_executor.SKIP_REASON_INSUFFICIENT_BUYING_POWER,
                                f"required={required_buying_power:.2f} "
                                f"available={buying_power_available:.2f}",
                                order_value=required_buying_power,
                            )
                            return False

                        if _buy_first:
                            # #2712 Inc 2: DEFER the SELL until the BUY is
                            # confirmed - a failed BUY then changes nothing
                            # (no recovery roundtrip, no spread loss).
                            st.deferred_close_symbol = symbol_to_close
                            st.deferred_close_qty = old_qty
                        else:
                            # Verkauf VOR dem Kauf: `_schritt_mandant_sofort_verkaufen`.
                            st.sofort_verkaufen_qty = old_qty
            except Exception as swap_e:
                order_executor.logging.warning(
                    f"[User {user_id}] Failed to swap out {symbol_to_close}: {swap_e}"
                )
                # #2585: approved BUY dies with the failed displacement — audit.
                await order_executor._audit_skipped_signal(
                    symbol,
                    action,
                    order_executor.SKIP_REASON_EXECUTION_ERROR,
                    f"displacement swap of {symbol_to_close} failed: {swap_e}",
                )
                return False
        return True

    async def _schritt_mandant_sofort_verkaufen(self, st: _Absendung) -> bool:
        """BUY, Verdraengung ohne Kauf-zuerst: den verdraengten Titel jetzt verkaufen."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        client, context, pm = st.client, st.context, st.pm
        symbol_to_close, old_qty = st.symbol_to_close, st.sofort_verkaufen_qty
        if not old_qty:
            return True
        try:
            # #3387: trug bisher UEBERHAUPT keinen
            # client_order_id — auch keinen zufaelligen. Leg
            # `displacement` haelt ihn vom Einstieg derselben
            # Entscheidung (Leg `entry`) getrennt.
            req_close = MarketOrderRequest(
                symbol=symbol_to_close,
                qty=old_qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=order_executor._derived_coid(context, "displacement"),
            )
            # #2467 FIX 5: re-check the kill switch IMMEDIATELY before the
            # displacement-SELL. It is submitted BEFORE the main gate (~:996), so a
            # trip landing during the many awaits above would otherwise leak one
            # real SELL through. Mirrors the fallback-path pre-SELL gate (#2252).
            order_executor.kill_switch.check_halt(user_id)
            # #3447: durchs Tor. Das ``check_halt`` direkt
            # darueber BLEIBT: Hier kommt der SELL vor dem BUY,
            # ein Halt laesst das Konto unveraendert (#2467). Die
            # Matrix des Tors stellt ``displacement`` vom Halt
            # frei — ohne dieses ``check_halt`` rutschte nach
            # einem Not-Stopp ein SELL durch.
            sell_order = await order_executor.OrderExecutorMixin._sende_durchs_tor(
                client=client,
                request=req_close,
                symbol=symbol_to_close,
                side_enum=OrderSide.SELL,
                qty=old_qty,
                user_id=user_id,
                decision_id=getattr(context, "decision_id", "") or "",
                intent_kind="displacement",
            )
            st.displacement_sell_order_id = sell_order.id
            # #2712 Inc1: leg visible to compliance (observe-only).
            verdraengung.register_displacement_leg(
                self.compliance_guardian,
                symbol=symbol_to_close,
                side="sell",
                qty=old_qty,
                price=0.0,
                user_id=user_id,
                held_qty=old_qty,
                kind="displacement_sell",
            )
            st.displacement_sell_qty = old_qty

            pm.record_trade(symbol_to_close, "sell")
            await order_executor.persist_pm_state_to_redis(
                pm, symbol_to_close, st.redis_client
            )
            await asyncio.sleep(0.5)
        except Exception as swap_e:
            # Dieselbe Behandlung wie in `_schritt_mandant_verdraengung` — bis #3821
            # war es derselbe try-Block.
            order_executor.logging.warning(
                f"[User {user_id}] Failed to swap out {symbol_to_close}: {swap_e}"
            )
            # #2585: approved BUY dies with the failed displacement — audit.
            await order_executor._audit_skipped_signal(
                symbol,
                action,
                order_executor.SKIP_REASON_EXECUTION_ERROR,
                f"displacement swap of {symbol_to_close} failed: {swap_e}",
            )
            return False
        return True

    async def _schritt_mandant_menge(self, st: _Absendung) -> bool:
        """Freigegebene Menge als Obergrenze; Menge null beendet die Absendung."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        # ADR-016 (EU AI Act Art. 14 + MiFID II RTS 6): a human-approved order executes AT
        # MOST the quantity the human oversaw. The engine never autonomously sizes ABOVE the
        # approved amount — risk/cash may only REDUCE it; the approved qty is a CEILING, never
        # an amplifier. (Variant B — supersedes Rev-11 Decision-1's unconditional re-size, so
        # the immutable audit value matches what the human authorised = MiFID record accuracy.)
        if st.source == "human_approved":
            _approved_qty = abs(
                float(getattr(st.event, "suggested_quantity", 0.0) or 0.0)
            )
            if _approved_qty > 0:
                st.size = min(st.size, _approved_qty)

        if st.size <= 0:
            # #2811: name the traced cause first; the generic hint only when the
            # sizer recorded nothing (an unknown path must not invent a cause).
            # #3821: auf dem SELL-Pfad ist `sizing_trace` nie gebunden (siehe _gebunden).
            _zr = order_executor._gebunden(st, "sizing_trace").get("zero_reason")
            reason_msg = (
                f"size 0 — {_zr}"
                if _zr
                else "Calculated size is 0 (check risk limits, position sizing, or cash)."
            )
            order_executor.logging.debug(f"[User {user_id}] {symbol} - Size 0")
            try:
                redis = await order_executor.RedisClient.get_redis()
                await order_executor._safe_publish(
                    redis,
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "trade_rejected",
                            "title": f"Risk Blocked: {symbol}",
                            "message": reason_msg,
                            "timestamp": CompositionRoot.get_instance()
                            .clock_port.now()
                            .isoformat(),
                        }
                    ),
                )
            except Exception as e:
                order_executor.logging.warning("PubSub error: %s", e)
            # #2069 gap-fix: RECORD the primary risk block (size<=0) — this path
            # published to Redis only, so BLOCKED_RISK never reached the outcome
            # store (and thus never the telemetry choke-point). Double-guarded,
            # display/observability-only — behaviour (early return) unchanged.
            order_executor._rec_outcome(symbol, "blocked:risk", reason_msg)
            # #2585: THE INCY drop point (2026-07-30). The DEBUG line above is
            # invisible at the default INFO level, and nothing here reached the
            # audit chain — the approved BUY simply ceased to exist. WARNING +
            # SKIPPED entry; the early return itself is unchanged (fail-safe).
            await order_executor._audit_skipped_signal(
                symbol, action, order_executor.SKIP_REASON_SIZING_ZERO, reason_msg
            )
            return False
        return True
