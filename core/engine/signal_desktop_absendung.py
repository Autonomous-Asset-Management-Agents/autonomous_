# core/engine/signal_desktop_absendung.py
# ARC-E6 H-1j (#4239) — Absendungs-Schritte des Desktop-Zweigs, ausgezogen aus
# OrderExecutorMixin._process_signal_event (core/engine/order_executor.py).
"""Die Schritte der Signal-Übergabe, die auf dem Desktop-Zweig einen Auftrag absendebereit machen.

Der Dirigent ``OrderExecutorMixin._process_signal_event`` bleibt in ``order_executor.py``.
Hierher gezogen sind die Blöcke seines freigegebenen Zweigs (``if approved:``), die keinen
anderen Schritt rufen, und der Null-Menge-Zweig: der Auftrag mit dem Kill-Switch-Tor, die
Kaufkraft-Vorprüfung der Verdrängung, der Verdrängungs-SELL und die Null-Menge-Meldung
(ADR-OBS-01). ``_schritt_absenden`` und ``_schritt_nachbuchen`` ruft weiter der Dirigent
selbst. Die Werte, die von Schritt zu Schritt wandern, trägt der Zustand ``_Uebergabe``
(Kern). Die Kaufkraft-Vorprüfung liefert ``bool``; der Dirigent schreibt an ihre Stelle
``if not await self._schritt_desktop_kaufkraft(st): return`` (Abbruch-Ergebnis, Muster G-1b).
Ein Abbruch verlässt damit weiter die ganze Übergabe und überspringt den Abschluss
``log_decision``/``_capture_outcome``.

Die Rümpfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten Abbildung
wie in G-1a, G-1b und H-1d bis H-1i (Entscheidung #4183 §3, Weg b): Jeder Name, der im Kern
gebunden ist, wird als ``order_executor.<name>`` gelesen — sonst griffe ein Patch am Kern ins
Leere, darunter ``kill_switch``, ``config`` und das Tor
``order_executor.OrderExecutorMixin._sende_durchs_tor``. Ein Span steht in keinem Schritt.

Einzige nicht-wörtliche Stelle: Das ``try … except Exception as swap_e`` um Vorprüfung und
SELL ist geteilt. Jeder der beiden Schritte hat sein eigenes, mit derselben WARNING
``[Global] Failed to displace``. Scheitert die Vorprüfung, setzt sie ``st.pc_oq = 0.0``, und
der SELL-Schritt tut nichts — wie zuvor, als die Ausnahme den SELL übersprang. Der Kauf läuft
in beiden Fällen weiter (fail-open der Verdrängung).

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4239-h-1j-a-signal-abergabe-absendungs-schritte-a-sig/implementation_plan.md``.
"""

from . import order_executor, verdraengung
from .order_executor import _Uebergabe


class SignalDesktopAbsendungMixin:
    """Absendungs-Schritte von ``_process_signal_event``; über ``AusfuehrungMixin`` zusammengesetzt."""

    async def _schritt_desktop_auftrag(self, st: _Uebergabe) -> None:
        """Auftrag und Kill-Switch-Tor; setzt ``st.side_enum``, ``st.req``, ``st.schutz_exit``.

        Fail-closed: Ein Halt wirft weiter, der äußere Fehlerfänger des Dirigenten fängt ihn.
        Nur ein Schutz-Exit (#3380) passiert.
        """
        symbol, action, context = st.symbol, st.action, st.context
        side_enum = (
            order_executor.OrderSide.BUY
            if action == "BUY"
            else order_executor.OrderSide.SELL
        )
        # Defend against MagicMock from tests breaking Pydantic validation
        client_order_id = getattr(context, "client_order_id", None) if context else None
        # #3387: siehe oben — leerer String ist kein Schluessel.
        client_order_id = (
            client_order_id
            if isinstance(client_order_id, str) and client_order_id
            else order_executor._derived_coid(context)
        )
        req = order_executor.MarketOrderRequest(
            symbol=symbol,
            qty=st.qty,
            side=side_enum,
            time_in_force=order_executor.TimeInForce.DAY,
            client_order_id=client_order_id,
        )

        # --- FIX: Kill Switch Gate ---
        # #3380: dieselbe enge Freistellung wie im Mandantenpfad.
        # Sie fehlte hier — und das ist der Pfad, den der Desktop
        # nimmt: Ein gehaltener Desktop konnte seinen Stop-Loss
        # nicht ausfuehren. Nur ein SELL mit ``triggered_by_stop``
        # passiert; BUY, Rotation und Trim bleiben geblockt.
        _schutz_exit = order_executor.halt_exempt_protective_exit(context, action)
        if _schutz_exit:
            order_executor.logging.warning(
                "[Halt] %s: Schutz-Exit trotz Kill-Switch-Halt "
                "ausgefuehrt (stop_type=%s) — Freistellung nach "
                "#3380, nicht blockiert.",
                symbol,
                getattr(context, "stop_type", "") or "unbekannt",
            )
        try:
            if not _schutz_exit:
                order_executor.kill_switch.check_halt(st.uid or "global")
        except Exception:
            # #2113 (flag-gated, PURE OBSERVATION): durable
            # blocked:kill_switch — re-raise keeps the gate
            # byte-identical (caught by the outer handler,
            # then the log_decision tail captures the row).
            if order_executor._capture_enabled():
                order_executor._rec_outcome(
                    symbol,
                    "blocked:kill_switch",
                    "kill-switch halt — order blocked",
                    decision_id=getattr(context, "decision_id", None),
                )
            raise
        st.side_enum, st.req, st.schutz_exit = side_enum, req, _schutz_exit

    async def _schritt_desktop_kaufkraft(self, st: _Uebergabe) -> bool:
        """Kaufkraft-Vorprüfung der Verdrängung (#2176 Part C, #2712); ``False`` = Abbruch.

        Setzt ``st.pc_oq`` und ``st.fb_buy_first``. Reicht die Kaufkraft nicht, endet die
        ganze Übergabe (fail-closed beim Befund). Ein Fehler bleibt fail-open: WARNING,
        ``st.pc_oq = 0.0``, kein SELL, und der Kauf geht weiter.
        """
        symbol, action = st.symbol, st.action
        st.pc_oq, st.fb_buy_first = 0.0, False
        if st.pc_symbol_to_close:
            try:
                # Pre-check for Global/Fallback Account
                _pc_account = await order_executor.asyncio.to_thread(
                    st.trade_client.get_account
                )
                _pc_multiplier = getattr(_pc_account, "multiplier", "1")
                _pc_bp = float(getattr(_pc_account, "buying_power", 0.0) or 0.0)

                _pc_old = await order_executor.asyncio.to_thread(
                    st.trade_client.get_open_position,
                    st.pc_symbol_to_close,
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
                        else float(_pc_old.get("current_price", 0.0))
                    )
                if _pc_oq > 0:
                    sell_value = _pc_oq * _pc_price
                    multiplier_val = float(_pc_multiplier) if _pc_multiplier else 1.0
                    # #2712 Inc 2: buy-first = STRICT BP.
                    _fb_buy_first = bool(
                        getattr(
                            order_executor.config,
                            "DISPLACEMENT_BUY_FIRST",
                            True,
                        )
                    )
                    if multiplier_val == 1.0:
                        buying_power_available = _pc_bp
                    else:
                        buying_power_available = _pc_bp + (sell_value * multiplier_val)
                    buying_power_available = verdraengung.displacement_available_bp(
                        buy_first=_fb_buy_first,
                        buying_power=_pc_bp,
                        sell_value=sell_value,
                        multiplier=multiplier_val,
                    )

                    required_buying_power = st.qty * st.curr
                    if buying_power_available < required_buying_power:
                        order_executor.logging.warning(
                            f"[Global] ⚠️ Displacement pre-check failed. "
                            f"Required BP: {required_buying_power}, Available BP (post-sell estimate): {buying_power_available}. "
                            f"Aborting swap for {st.pc_symbol_to_close} -> {symbol}."
                        )
                        try:
                            if st.fb_redis:
                                await order_executor._safe_publish(
                                    st.fb_redis,
                                    f"explainability:{st.uid or 'global'}",
                                    order_executor.json.dumps(
                                        {
                                            "type": "trade_rejected",
                                            "title": f"Precheck Blocked: {symbol}",
                                            "message": f"Insufficient settled buying power (multiplier={_pc_multiplier}). Available: {buying_power_available}, Required: {required_buying_power}",
                                            "timestamp": order_executor.CompositionRoot.get_instance()
                                            .clock_port.now(order_executor.timezone.utc)
                                            .isoformat(),
                                        }
                                    ),
                                )
                        except Exception as redis_e:
                            order_executor.logging.warning("PubSub error: %s", redis_e)
                        order_executor._rec_outcome(
                            symbol,
                            "blocked:precheck",
                            "Insufficient settled buying power.",
                        )
                        # #2585: approved BUY dropped at the
                        # buying-power precheck — audit the skip.
                        await order_executor._audit_skipped_signal(
                            symbol,
                            action,
                            order_executor.SKIP_REASON_INSUFFICIENT_BUYING_POWER,
                            f"required={required_buying_power:.2f}"
                            f" available="
                            f"{buying_power_available:.2f}",
                            order_value=required_buying_power,
                        )
                        return False

                    st.pc_oq, st.fb_buy_first = _pc_oq, _fb_buy_first
            except Exception as swap_e:
                order_executor.logging.warning(
                    "[Global] Failed to displace %s: %s",
                    st.pc_symbol_to_close,
                    swap_e,
                    exc_info=True,
                )
                st.pc_oq = 0.0
        return True

    async def _schritt_desktop_verdraengung_sell(self, st: _Uebergabe) -> None:
        """Verdrängungs-SELL (#2176 Part C): aufgeschoben (#2712 buy-first) oder sofort.

        Läuft nur mit Ziel und ``st.pc_oq > 0``. Der sofortige SELL geht durchs Tor, live mit
        eigenem ``check_halt`` davor (#2252). Ein Fehler bleibt fail-open: WARNING, und der
        Kauf geht weiter. Schreibt die vier Felder, die ``_schritt_absenden`` liest.
        """
        symbol, context = st.symbol, st.context
        _pc_oq, _fb_buy_first = st.pc_oq, st.fb_buy_first
        _fallback_displacement_sell_order_id = st.fallback_displacement_sell_order_id
        _fallback_displacement_sell_qty = st.fallback_displacement_sell_qty
        _fb_deferred_close_symbol = st.fb_deferred_close_symbol
        _fb_deferred_close_qty = st.fb_deferred_close_qty
        if st.pc_symbol_to_close and _pc_oq > 0:
            try:
                if _fb_buy_first:
                    # #2712 Inc 2: DEFER the SELL until
                    # the BUY is confirmed (no recovery
                    # roundtrip on a failed BUY).
                    _fb_deferred_close_symbol = st.pc_symbol_to_close
                    _fb_deferred_close_qty = _pc_oq
                else:
                    # #3387: wie oben — bisher ohne jeden
                    # Wiedererkennungswert.
                    _pc_sell_req = order_executor.MarketOrderRequest(
                        symbol=st.pc_symbol_to_close,
                        qty=_pc_oq,
                        side=order_executor.OrderSide.SELL,
                        time_in_force=order_executor.TimeInForce.DAY,
                        client_order_id=order_executor._derived_coid(
                            context, "displacement"
                        ),
                    )
                    if getattr(order_executor.config, "SHADOW_MODE", False):
                        await order_executor.OrderExecutorMixin._sende_durchs_tor(
                            client=order_executor.DryRunOrderProxy("global"),
                            request=_pc_sell_req,
                            symbol=st.pc_symbol_to_close,
                            side_enum=order_executor.OrderSide.SELL,
                            qty=_pc_oq,
                            user_id=st.uid or "global",
                            decision_id=getattr(context, "decision_id", "") or "",
                            intent_kind="displacement",
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
                        order_executor.kill_switch.check_halt(st.uid or "global")
                        # #3447: durchs Tor; das ``check_halt``
                        # darueber bleibt (SELL vor BUY, #2252).
                        _fb_sell_order = (
                            await order_executor.OrderExecutorMixin._sende_durchs_tor(
                                client=st.trade_client,
                                request=_pc_sell_req,
                                symbol=st.pc_symbol_to_close,
                                side_enum=order_executor.OrderSide.SELL,
                                qty=_pc_oq,
                                user_id=st.uid or "global",
                                decision_id=getattr(context, "decision_id", "") or "",
                                intent_kind="displacement",
                            )
                        )
                        _fallback_displacement_sell_order_id = _fb_sell_order.id
                        _fallback_displacement_sell_qty = _pc_oq
                        # #2712 Inc1: leg visible to compliance.
                        verdraengung.register_displacement_leg(
                            getattr(
                                self,
                                "compliance_guardian",
                                None,
                            ),
                            symbol=st.pc_symbol_to_close,
                            side="sell",
                            qty=_pc_oq,
                            price=0.0,
                            user_id=st.uid or "global",
                            held_qty=_pc_oq,
                            kind="displacement_sell",
                        )
                    if st.pc_pm is not None:
                        st.pc_pm.record_trade(st.pc_symbol_to_close, "sell")
                        await order_executor.persist_pm_state_to_redis(
                            st.pc_pm,
                            st.pc_symbol_to_close,
                            st.fb_redis,
                        )
                    await order_executor.asyncio.sleep(0.5)
            except Exception as swap_e:
                order_executor.logging.warning(
                    "[Global] Failed to displace %s: %s",
                    st.pc_symbol_to_close,
                    swap_e,
                    exc_info=True,
                )
        st.fallback_displacement_sell_order_id = _fallback_displacement_sell_order_id
        st.fallback_displacement_sell_qty = _fallback_displacement_sell_qty
        st.fb_deferred_close_symbol = _fb_deferred_close_symbol
        st.fb_deferred_close_qty = _fb_deferred_close_qty

    async def _schritt_desktop_null_menge(self, st: _Uebergabe) -> None:
        """ADR-OBS-01: eine auf 0 bemessene Order wird gemeldet, nicht abgesendet."""
        symbol, action = st.symbol, st.action
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
        order_executor.logging.warning(
            "[%s] %s dropped: position size resolved to 0 "
            "(risk limits / exposure cap / available cash) — no order "
            "submitted.",
            symbol,
            action,
        )
        order_executor._rec_outcome(
            symbol,
            "blocked:risk",
            "position size resolved to 0 (risk/cash)",
        )
        # #2585: the ADR-OBS-01 WARNING above made this drop visible
        # in the LOGS — but the audit chain still ended at "Approved".
        # Seal the skip onto the chain too. A SELL lands here when no
        # sellable quantity resolved (flat / fetch failed), a BUY when
        # risk/cash sizing returned 0 — name the honest cause.
        # #2811: name the traced cause; the three-candidate list only
        # when the sizer recorded nothing (unknown must stay unknown).
        _zr = st.sizing_trace.get("zero_reason")
        await order_executor._audit_skipped_signal(
            symbol,
            action,
            order_executor.SKIP_REASON_SIZING_ZERO,
            (
                (
                    f"position size resolved to 0 — {_zr}"
                    if _zr
                    else "position size resolved to 0 "
                    "(risk limits / exposure cap / available cash)"
                )
                if action == "BUY"
                else "no sellable quantity (flat or fetch failed)"
            ),
        )
