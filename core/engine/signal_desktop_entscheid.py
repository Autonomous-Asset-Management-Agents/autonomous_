# core/engine/signal_desktop_entscheid.py
# ARC-E6 H-1i (#4238) — Entscheid-Schritte des Desktop-Zweigs, ausgezogen aus
# OrderExecutorMixin._process_signal_event (core/engine/order_executor.py).
"""Die Schritte der Signal-Übergabe, die auf dem Desktop-Zweig über den Auftrag entscheiden.

Der Dirigent ``OrderExecutorMixin._process_signal_event`` bleibt in ``order_executor.py``.
Hierher gezogen sind vier Blöcke seines Desktop-Zweigs (keine aktiven Mandanten), die keinen
anderen Schritt rufen: das Laden des PM-Zustands aus Redis, der Earnings-Guard, der
Verdrängungs-Entscheid des PortfolioManagers und das Verkaufstor (Mindesthaltedauer). Die
Werte, die von Schritt zu Schritt wandern, trägt der Zustand ``_Uebergabe`` (Kern). Die beiden
Blöcke mit frühem ``return`` liefern ``bool``; der Dirigent schreibt an ihre Stelle
``if not await self._schritt_desktop_x(st): return`` (Abbruch-Ergebnis, Muster G-1b). Ein
Abbruch verlässt damit weiter die ganze Übergabe und überspringt den Abschluss
``log_decision``/``_capture_outcome``; die Schritte stehen im selben ``try`` wie zuvor.

Die Rümpfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten Abbildung
wie in G-1a, G-1b und H-1d bis H-1h (Entscheidung #4183 §3, Weg b): Jeder Name, der im Kern
gebunden ist, wird als ``order_executor.<name>`` gelesen — sonst griffe ein Patch am Kern ins
Leere. Das sind ``RedisClient``, ``logging``, ``asyncio``, ``_rec_outcome``,
``_capture_outcome``, ``_audit_skipped_signal``, ``SKIP_REASON_OTHER`` und die Re-Exporte
``restore_pm_state_from_redis`` (``pm_zustand.py``), ``_earnings_guard_veto``
(``bemessung_helfer.py``) und ``classify_exit_kind`` (``order_aufbau.py``). Ein Span steht in
keinem der vier Schritte.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4238-*/implementation_plan.md``.
"""

from . import order_executor
from .order_executor import _Uebergabe


class SignalDesktopEntscheidMixin:
    """Entscheid-Schritte von ``_process_signal_event``; über ``AusfuehrungMixin`` zusammengesetzt."""

    async def _schritt_desktop_pm_laden(self, st: _Uebergabe) -> None:
        """Part C: PM-Zustand des Desktops aus Redis; setzt ``st.fb_pm`` und ``st.fb_redis``."""
        symbol = st.symbol
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
                _fb_redis = await order_executor.RedisClient.get_redis()
                if not hasattr(self, "_pm_restored"):
                    self._pm_restored: set = set()
                await order_executor.restore_pm_state_from_redis(
                    _fb_pm, _fb_redis, self._pm_restored
                )
            except Exception:
                order_executor.logging.warning(
                    "[%s] fallback PM state restore failed",
                    symbol,
                    exc_info=True,
                )
        st.fb_pm, st.fb_redis = _fb_pm, _fb_redis

    async def _schritt_desktop_earnings(self, st: _Uebergabe) -> None:
        """#3349: Earnings-Guard; setzt bei Veto ``st.qty`` auf 0."""
        symbol, action, context, qty = st.symbol, st.action, st.context, st.qty
        # #3349: the Earnings-Proximity-Guard on the desktop/[Global] path —
        # the path a desktop install actually trades through. Same shared
        # helper as the tenant path; placed after sizing so it covers every
        # way `qty` came about. Dark default ⇒ no-op.
        if qty > 0:
            _eg = order_executor._earnings_guard_veto(symbol, action)
            if _eg is not None:
                order_executor._rec_outcome(
                    symbol,
                    _eg[0],
                    _eg[1],
                    decision_id=getattr(context, "decision_id", None),
                )
                await order_executor._audit_skipped_signal(
                    symbol, action, _eg[0], _eg[1]
                )
                qty = 0.0
        st.qty = qty

    async def _schritt_desktop_verdraengung(self, st: _Uebergabe) -> bool:
        """#2176 Piece 2: Verdrängungs-Entscheid; ``False`` = Portfolio lehnt ab (Abbruch).

        Setzt ``st.pc_symbol_to_close`` und ``st.pc_pm``. Ein Scoring-Fehler bleibt
        fail-open: kein Ziel, und der Kauf geht weiter.
        """
        symbol, action, context, event = st.symbol, st.action, st.context, st.event
        should_log = st.should_log
        # Part C (#2176, Piece 2): arm the PortfolioManager gate on the
        # single-tenant fallback path — mirror _execute_tenant_order so the
        # desktop book's cooldown (ADR-R12) and full-book displacement fire
        # here too. This path never called should_open_new_position before,
        # leaving Increment 2's per-symbol cooldown dormant on desktop.
        # Null-safe: no PortfolioManager ⇒ behaviour is identical to before.
        _pc_symbol_to_close = None
        _pc_strat = getattr(self, "active_strategy", None)
        _pc_pm = getattr(_pc_strat, "portfolio_manager", None) if _pc_strat else None
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
                            # #3251: skip unset (None) fields — see the
                            # single-tenant path above. Prevents a None
                            # placeholder from reaching score_opportunity.
                            _pc_v = getattr(context, _pc_k)
                            if _pc_v is not None:
                                _pc_features[_pc_k] = _pc_v
                _pc_opp = _pc_pm.score_opportunity(
                    symbol=symbol,
                    current_price=_pc_curr,
                    rl_action=1,
                    model_confidence=(context.lstm_prediction if context else 0.5),
                    features=_pc_features,
                    # #3619: the dead-band derives the sizer's clean-weight
                    # target from the SAME context fields the sizer read.
                    forecast_vol=(
                        getattr(context, "forecast_vol", None) if context else None
                    ),
                    skew_percentile=(
                        getattr(context, "skew_percentile", None) if context else None
                    ),
                    vote_coverage=(
                        getattr(context, "vote_coverage", None) if context else None
                    ),
                )
                (
                    _pc_open,
                    _pc_reason,
                    _pc_symbol_to_close,
                ) = _pc_pm.should_open_new_position(_pc_opp)
                # #3008 step 1 (observation-only): attach the displacement
                # decision to the logged context so the non-stop exit path
                # is measurable. Same rationale as the tenant path above.
                if context is not None:
                    context.symbol_to_close = _pc_symbol_to_close or ""
                    context.portfolio_reason = _pc_reason or ""
                if not _pc_open:
                    _pc_code = (
                        "blocked:churn"
                        if "cooldown" in (_pc_reason or "").lower()
                        else "blocked:portfolio"
                    )
                    order_executor.logging.info(
                        "[Global] %s 📊 Portfolio declined open: %s",
                        symbol,
                        _pc_reason,
                    )
                    order_executor._rec_outcome(symbol, _pc_code, _pc_reason)
                    # ADR-OBS-01: log the declined decision before the
                    # early return so a portfolio/cooldown-blocked BUY is
                    # not silently absent from the decisions table (every
                    # other block path reaches log_decision at the tail).
                    if should_log and not event.is_simulation:
                        _pc_loop = order_executor.asyncio.get_running_loop()
                        await _pc_loop.run_in_executor(
                            None,
                            self.cloud_logger.log_decision,
                            context,
                        )
                        # #2113: durable capture beside log_decision
                        # (flag-gated, fail-safe, observation-only).
                        order_executor._capture_outcome(context)
                    # #2585: approved BUY declined by the desktop
                    # PortfolioManager — audit the skip.
                    await order_executor._audit_skipped_signal(
                        symbol,
                        action,
                        order_executor.SKIP_REASON_OTHER,
                        f"portfolio declined: {_pc_reason}",
                    )
                    return False
            except Exception:
                # Fail-OPEN: a scoring error must not newly block a BUY the
                # legacy path would have executed. Proceed without
                # displacement (identical to pre-Part-C behaviour).
                order_executor.logging.warning(
                    "[Global] %s displacement gate errored (proceeding without it)",
                    symbol,
                    exc_info=True,
                )
                _pc_symbol_to_close = None
        st.pc_symbol_to_close, st.pc_pm = _pc_symbol_to_close, _pc_pm
        return True

    async def _schritt_desktop_verkaufstor(self, st: _Uebergabe) -> bool:
        """#2713: Verkaufstor (Mindesthaltedauer); ``False`` = SELL wird gehalten (Abbruch).

        Fail-closed: ein Fehler in ``can_sell_position`` hält den SELL. Ein Risiko-Ausstieg
        passiert ohne Frage.
        """
        symbol, action, context, event = st.symbol, st.action, st.context, st.event
        qty = st.qty
        # #2713: the desktop/global path finally gets the SELL-side brake —
        # parity with the tenant path (:~700). Risk exits skip it: a stop-out
        # must never wait behind a min-hold. Consecutive-sell bypass lives
        # inside can_sell_position itself.
        if action == "SELL":
            _ek = order_executor.classify_exit_kind(event)
            _gate_pm = getattr(
                getattr(self, "active_strategy", None),
                "portfolio_manager",
                None,
            )
            if _ek != "risk" and _gate_pm is not None:
                try:
                    res = _gate_pm.can_sell_position(symbol)
                    if isinstance(res, tuple) and len(res) == 2:
                        _ok, _why = res
                    else:
                        _ok, _why = bool(res), "anti-churn check"
                except Exception as _gate_err:
                    # Review #4342: ein Code-Fehler ist keine Haltedauer-Ablehnung -
                    # Stack-Trace auf WARNING (CLAUDE.md 5.6), der SELL bleibt gehalten.
                    order_executor.logging.warning(
                        "[Global] %s SELL gate can_sell_position failed - fail-closed",
                        symbol,
                        exc_info=True,
                    )
                    _ok, _why = (
                        False,
                        f"gate error - fail-closed: {_gate_err}",
                    )
                if not _ok:
                    order_executor.logging.info(
                        "[Global] %s SELL held by can_sell_position: %s",
                        symbol,
                        _why,
                    )
                    await order_executor._audit_skipped_signal(
                        symbol,
                        action,
                        order_executor.SKIP_REASON_OTHER,
                        f"min-hold gate (#2713): {_why}",
                        order_value=abs(qty)
                        * (context.current_price if context else 0.0),
                    )
                    return False
        return True
