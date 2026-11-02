# risk_konto.py
# --- Konto, Halt, Liquidation: Tages-Drawdown, Circuit Breaker, Erholung (#4269, H-3g) ---

"""Konto und Halt des ``RiskManager`` — ``update_account_equity`` samt Halt und Liquidation als ``KontoHaltMixin``.

Umgezogen aus ``core/risk_manager.py`` (#4269, H-3g). Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2 und §3.

Zugriffsregel (Entscheidung §3): Namen, die Tests am Modulobjekt ``core.risk_manager``
patchen, liest dieses Modul **nur** als ``_rm.<name>`` — hier ``_rm.CLOUD_LOGGING_AVAILABLE``
und ``_rm.cloud_log_risk_event``. Ein freier Name liefe am Patch still vorbei. Der Kern wird
erst am Dateiende importiert, nach der Klasse: Der Kern importiert dieses Modul für seine
Basisklasse (Zirkelimport). Das Mixin hat kein ``__init__``; den Zustand (``trading_halted``,
``trading_reduced``, ``peak_daily_equity``, ``daily_drawdown_limit``, ``last_halt_time``,
``halt_trigger_count``, ``_portfolio_stop_triggered``, ``_eigener_halt``, ``client``,
``clock``, ``user_id``) liest und setzt es über ``self``.

Die Importe von ``core.engine.order_executor``, ``core.gateway`` und ``core.kill_switch``
bleiben spät, im Rumpf der Methoden: ``order_executor`` importiert den ``RiskManager`` auf
Modulebene, ein Import oben schlösse den Zyklus ``risk_manager → risk_konto →
order_executor → risk_manager``.
"""

import logging
import uuid


class KontoHaltMixin:
    """Konto des ``RiskManager``: Tages-Drawdown, Warnstufe, Circuit Breaker, Erholung."""

    def _liquidate_through_gateway(self, equity: float) -> None:
        """Schliesst den Bestand Position fuer Position ueber das Tor (#3383).

        Der Import steht in der Funktion: ``order_executor`` importiert den RiskManager
        auf Modulebene: ein Import oben ergaebe einen Zyklus.
        """
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        from core.engine.order_executor import gateway_for
        from core.gateway import liquidate_positions

        holen = getattr(self.client, "get_all_positions", None)
        if not callable(holen):
            logging.error(
                "   Breaker-Liquidation: Broker kennt get_all_positions nicht — "
                "es wird NICHTS geschlossen. Der Halt steht, der Bestand bleibt."
            )
            return

        positions = list(holen() or [])
        if not positions:
            return

        liquidate_positions(
            gateway_for(self.client),
            positions,
            decision_id=f"breaker-{uuid.uuid4()}",
            intent_kind="breaker",
            request_factory=lambda symbol, qty: MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            ),
            halted=True,
        )

    def _halt(self):
        """#3485: der Halt dieses RiskManagers — hereingereicht oder der globale.

        Der globale wird bei jedem Aufruf frisch aufgeloest (nicht im Konstruktor), damit er
        derselbe bleibt, den der Rest der Engine sieht."""
        if self._eigener_halt is not None:
            return self._eigener_halt
        import core.kill_switch as _ks

        return _ks.kill_switch

    def update_account_equity(
        self, current_equity: float, allow_unlock: bool = True
    ):  # noqa: E501
        """Monitors for daily drawdown with PROGRESSIVE HALT & intelligent unlocking.  # noqa: E501

        #2978 (Epic #1891): ``allow_unlock`` defaults to ``True`` so the Sim path  # noqa: E501
        (simulation_runner) and all existing callers/tests stay byte-identical.
        The live-wiring path (trading_loop._update_live_account_equity) passes
        ``allow_unlock=False`` to suppress the adaptive intraday auto-unlock, so a  # noqa: E501
        real-money account-wide breaker can only move trading_halted False→True,  # noqa: E501
        never back — an intraday re-enable is a capital decision that stays behind  # noqa: E501
        a human gate (EU AI Act Art. 14 / MiFID II RTS 6). Session-restart /
        NY-day rollover (reset_daily_limit) remain the live re-enable paths.
        """
        current_equity = float(current_equity)

        self._konto_portfolio_stop(current_equity)
        drawdown, drawdown_percent, drawdown_ratio = self._konto_drawdown(
            current_equity
        )
        self._konto_warnstufe(
            current_equity, drawdown, drawdown_percent, drawdown_ratio
        )
        self._konto_breaker(current_equity, drawdown, drawdown_percent)

        # === INTELLIGENT UNLOCK: Progressive Recovery ===
        # #2978: suppressed entirely on the live-wiring path (allow_unlock=False) —  # noqa: E501
        # halt-only guarantee; no silent intraday re-enable of a real-money breaker.  # noqa: E501
        if self.trading_halted and allow_unlock:
            self._konto_erholung(current_equity, drawdown, drawdown_percent)

    def _konto_portfolio_stop(self, current_equity):
        """H-3h Schritt 1: Portfolio-Stop ab Sitzungsbeginn (ADR-R07).

        Setzt den Halt und liquidiert nicht; ob er liquidieren soll, ist #4302.
        """
        # === PORTFOLIO STOP LOSS (from session start): halt if down 7% from when trading started ===  # noqa: E501
        if self.session_start_equity > 0 and self.portfolio_stop_loss_pct > 0:
            drawdown_from_start = self.session_start_equity - current_equity
            pct_down = drawdown_from_start / self.session_start_equity
            if pct_down >= self.portfolio_stop_loss_pct:
                if not self._portfolio_stop_triggered:
                    self._portfolio_stop_triggered = True
                    self.trading_halted = True
                    self._halt().trip(
                        reason=f"Portfolio stop loss ({pct_down * 100:.1f}%)",
                        user_id=self.user_id,
                    )
                    logging.critical(
                        f"🔴 PORTFOLIO STOP LOSS - Max loss from session start reached. "  # noqa: E501
                        f"Equity ${current_equity:,.2f} is {pct_down * 100:.1f}% below session start ${self.session_start_equity:,.2f} (limit {self.portfolio_stop_loss_pct * 100:.0f}%). Halting new trades.",  # noqa: E501
                        stacklevel=2,
                    )
                if _rm.CLOUD_LOGGING_AVAILABLE:
                    _rm.cloud_log_risk_event(
                        event_type="portfolio_stop_loss",
                        severity="critical",
                        message=f"Portfolio stop loss: {pct_down * 100:.1f}% down from session start",  # noqa: E501
                        trigger_value=drawdown_from_start,
                        threshold_value=self.session_start_equity
                        * self.portfolio_stop_loss_pct,
                        equity=current_equity,
                    )
                # Keep halted; do not allow unlock for portfolio stop (restart session to reset)  # noqa: E501

    def _konto_drawdown(self, current_equity):
        """H-3h Schritt 2: Spitzenstand nachführen, Drawdown gegen den Spitzenstand.

        Liefert ``(drawdown, drawdown_percent, drawdown_ratio)``.
        """
        # Update peak equity (for recovery detection)
        if current_equity > self.peak_daily_equity:
            self.peak_daily_equity = current_equity

        drawdown = self.peak_daily_equity - current_equity
        drawdown_percent = (
            (drawdown / self.peak_daily_equity) * 100
            if self.peak_daily_equity > 0
            else 0
        )
        drawdown_ratio = (
            drawdown / self.daily_drawdown_limit
            if self.daily_drawdown_limit > 0
            else 0  # noqa: E501
        )
        return drawdown, drawdown_percent, drawdown_ratio

    def _konto_warnstufe(
        self, current_equity, drawdown, drawdown_percent, drawdown_ratio
    ):
        """H-3h Schritt 3: Warnstufe (ADR-R03), über 60 % halbe Größe, bis 50 % zurück.

        Liest ``trading_halted``, das der Portfolio-Stop im selben Aufruf gesetzt haben kann.
        """
        # === TIER 1: WARNING PHASE (ADR-R03) — Drawdown > 60% des Tages-Limits → Position Reduce ===  # noqa: E501
        # Begründung: 60%-Schwelle gibt ~2/3 des Risk-Budgets als Frühwarnung; 50% Positionsgröße  # noqa: E501
        # (siehe calculate_position_size > reduction_scaler) reduziert weiteres Exposure halbiert.  # noqa: E501
        # Recovery-Schwelle: 50% des Limits — symmetrisch, kein Hysterese-Problem.  # noqa: E501
        if (
            drawdown_ratio > 0.60
            and not self.trading_reduced
            and not self.trading_halted
        ):
            self.trading_reduced = True
            logging.warning(
                f"⚠️  WARNING PHASE: Drawdown at {drawdown_percent:.2f}% - Reducing position sizes to 50%",  # noqa: E501
                stacklevel=2,
            )
            if _rm.CLOUD_LOGGING_AVAILABLE:
                _rm.cloud_log_risk_event(
                    event_type="warning",
                    severity="warning",
                    message=f"WARNING PHASE: Drawdown at {drawdown_percent:.2f}% - Reducing position sizes to 50%",  # noqa: E501
                    trigger_value=drawdown,
                    threshold_value=self.daily_drawdown_limit * 0.60,
                    equity=current_equity,
                )
        elif (
            drawdown_ratio <= 0.50
            and self.trading_reduced
            and not self.trading_halted  # noqa: E501
        ):
            self.trading_reduced = False
            logging.info(
                f"✓ Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing",  # noqa: E501
                stacklevel=2,
            )
            if _rm.CLOUD_LOGGING_AVAILABLE:
                _rm.cloud_log_risk_event(
                    event_type="recovery",
                    severity="info",
                    message=f"Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing",  # noqa: E501
                    trigger_value=drawdown,
                    threshold_value=self.daily_drawdown_limit * 0.50,
                    equity=current_equity,
                )

    def _konto_breaker(self, current_equity, drawdown, drawdown_percent):
        """H-3h Schritt 4: Circuit Breaker, Halt vor Liquidation (#3380, #3383).

        Halt, Zähler, Logs und Cloud-Ereignis, danach die Liquidation durch das Tor.
        """
        # === TIER 2: CIRCUIT BREAKER (100% of limit) - Halt new trades ===
        if drawdown > self.daily_drawdown_limit:
            if not self.trading_halted:
                self.trading_halted = True
                self._halt().trip(
                    reason=f"Daily drawdown limit exceeded ({drawdown_percent:.2f}%)",  # noqa: E501
                    user_id=self.user_id,
                )
                self.last_halt_time = self.clock.now().replace(tzinfo=None)
                self.halt_trigger_count += 1
                self.unlock_recovery_percent = 0.50  # Reset unlock threshold
                logging.critical(
                    f"🔴 CIRCUIT BREAKER #{self.halt_trigger_count} TRIGGERED - Halting new trades",  # noqa: E501
                    stacklevel=2,
                )
                logging.critical(
                    f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) > Limit ${self.daily_drawdown_limit:,.2f}",  # noqa: E501
                    stacklevel=2,
                )

                # Cloud log the circuit breaker
                if _rm.CLOUD_LOGGING_AVAILABLE:
                    _rm.cloud_log_risk_event(
                        event_type="circuit_breaker",
                        severity="critical",
                        message=f"CIRCUIT BREAKER #{self.halt_trigger_count} TRIGGERED - Halting new trades. Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%)",  # noqa: E501
                        trigger_value=drawdown,
                        threshold_value=self.daily_drawdown_limit,
                        equity=current_equity,
                        details={
                            "halt_count": self.halt_trigger_count,
                            "drawdown_percent": drawdown_percent,
                        },
                    )

                # #3383: war `close_all_positions(cancel_orders=True)` — ein Aufruf
                # ohne Symbol und ohne Menge. Ein Datensatz dazu koennte nur "alles"
                # sagen; im Audit waere hinterher nicht belegbar, WAS der Breaker
                # geschlossen hat. Jetzt je Position ein OrderIntent mit Grund-Code
                # `breaker` durch das Tor. Freigestellt, weil der Breaker gerade selbst
                # den Halt gesetzt hat und sich sonst aussperren wuerde.
                try:
                    self._liquidate_through_gateway(current_equity)
                except Exception as e:
                    logging.error("   Liquidation attempt failed: %s", e, stacklevel=2)

    def _konto_erholung(self, current_equity, drawdown, drawdown_percent):
        """H-3h Schritt 5: Erholung, die Schwelle sinkt mit der Haltdauer (50/30/20 %).

        Läuft nur, wenn der Dirigent ``trading_halted and allow_unlock`` prüft (#2978).
        """
        # Dynamically adjust unlock threshold based on halt duration
        if self.last_halt_time:
            halt_duration = (
                self.clock.now().replace(tzinfo=None) - self.last_halt_time
            ).total_seconds() / 3600
            # After 2 hours, lower unlock threshold from 50% to 30%
            if halt_duration > 2:
                self.unlock_recovery_percent = 0.30
            # After 4 hours, lower to 20%
            if halt_duration > 4:
                self.unlock_recovery_percent = 0.20

        unlock_threshold = (
            self.daily_drawdown_limit * self.unlock_recovery_percent
        )  # noqa: E501

        if drawdown <= unlock_threshold and not getattr(
            self, "_portfolio_stop_triggered", False
        ):
            self.trading_halted = False
            self._halt().reset(user_id=self.user_id)
            self.last_halt_time = None
            self.trading_reduced = False
            self.halt_trigger_count = 0
            logging.info("✅ CIRCUIT BREAKER RESET - Trading RESUMED", stacklevel=2)
            logging.info(
                f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) - "  # noqa: E501
                f"Below {self.unlock_recovery_percent:.0%} recovery threshold",  # noqa: E501
                stacklevel=2,
            )

            # Cloud log the unlock
            if _rm.CLOUD_LOGGING_AVAILABLE:
                _rm.cloud_log_risk_event(
                    event_type="unlock",
                    severity="info",
                    message=f"CIRCUIT BREAKER RESET - Trading RESUMED. Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) - Below {self.unlock_recovery_percent:.0%} recovery threshold",  # noqa: E501
                    trigger_value=drawdown,
                    threshold_value=unlock_threshold,
                    equity=current_equity,
                )

    def reset_daily_limit(self, current_equity: float):
        """Resets the daily drawdown limit (usually called at start of day)."""
        # #2980 §5.6: None equity must not crash the daily reset (TypeError) →  # noqa: E501
        # 0.0 safe default (fail-closed: the drawdown limit collapses to 0, no
        # new risk) with a WARNING.
        if current_equity is None:
            logging.warning(
                "RiskManager.reset_daily_limit: current_equity is None → 0.0 "  # noqa: E501
                "safe default (fail-closed) (§5.6)."
            )
            current_equity = 0.0
        self.initial_daily_equity = float(current_equity)
        self.daily_drawdown_limit = (
            self.initial_daily_equity * self.daily_drawdown_limit_percent
        )
        self.trading_halted = False

    # TODO(PR-D): Complex f-string, review manually:         # logging.info(f"RM daily limit reset. Init Equity: ${self.initial_daily_equity:,.2f}")  # noqa: E501
    # logging.info(f"RM daily limit reset. Init Equity: ${self.initial_daily_equity:,.2f}")  # noqa: E501


from core import risk_manager as _rm  # noqa: E402
