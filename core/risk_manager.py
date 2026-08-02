# risk_manager.py
# --- FULL FILE: Includes Cash Constraints & AI Rule Evaluation ---
# --- With Cloud Logging for risk events ---

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

# Import the AI Rules handler
from core.ai_rules import AILearnedRules
from core.exceptions import RiskLimitExceeded
from core.telemetry import get_tracer

tracer = get_tracer(__name__)

# Cloud logging imports (graceful fallback if not configured)
try:
    import config
    from core.cloud_logger import log_risk_event as cloud_log_risk_event

    CLOUD_LOGGING_AVAILABLE = getattr(config, "DB_AVAILABLE", False) and getattr(
        config, "CLOUD_LOGGING_ENABLED", True
    )
except ImportError:
    CLOUD_LOGGING_AVAILABLE = False

    def cloud_log_risk_event(*args, **kwargs):
        pass


def effective_max_positions() -> int:
    """The number of concurrent positions the portfolio may hold — the correct
    equal-weight slot count for cash sizing.

    #2153: ``calculate_position_size`` divides cash into ``num_stocks_in_strategy``
    equal-weight slots. The book is HARD-CAPPED at ``max_positions`` holdings
    (``portfolio_manager.should_open_new_position``: open on room, else displace),
    so the cash MUST be split by that cap — NOT by ``len(live_universe)`` (the ~500
    name SCAN universe), which under-sized every order ~50x in production/full-universe
    and forced the fractional-share churn. This mirrors the author's own sizing test
    (``slots=10 == MAX_POSITIONS``) and the max_positions read in order_executor.py.
    """
    from config import get_config

    cfg = get_config()
    if getattr(cfg, "FULL_UNIVERSE_TRADING_ENABLED", False):
        return max(1, int(getattr(cfg, "FULL_UNIVERSE_MAX_POSITIONS", 10)))
    return max(1, int(getattr(cfg, "MAX_POSITIONS", 10)))


def effective_free_slots(open_positions: int, positions_confirmed: bool = True) -> int:
    """O3 — the cash-sizing divisor that reflects the REMAINING free slots, not the fixed book cap.

    ``calculate_position_size`` splits free cash into ``num_stocks_in_strategy`` equal-weight slots.
    Passing the fixed ``effective_max_positions()`` (=10) means cash is ALWAYS divided by 10 — so a
    mostly-invested book with little free cash sizes every new buy at ``cash/10`` = dust, and the
    account can never build a fresh full-size position from a near-invested state.

    With ``CASH_AWARE_SLOTS_ENABLED`` ON, the divisor becomes the number of remaining free slots
    (``cap - held``) — the free cash is reserved only across the slots still to fill, so freed cash
    (via exit/displacement) funds a proper-sized position. A book with 1..cap-1 free slots divides by
    that count (the genuine last free slot → divisor 1, i.e. the whole cash budget for it, by design).
    OFF → the fixed ``effective_max_positions()`` divisor, byte-identical to today. The concentration
    MAX rail (MAX_POSITION_PERCENT / ESMA order-value cap) is unchanged and still bounds the top.

    O3-SAFETY (adversarial review): a **full or over-cap** book has ZERO free slots (``cap - held <= 0``).
    The naive ``max(1, cap - held)`` collapsed that to divisor **1**, which makes the cash constraint
    route ~100% of free cash into a single new/displacement buy — the *maximum* per-order concentration,
    the OPPOSITE of the risk-averse "reserve across the remaining slots" intent and ~cap× the flag-OFF
    ``cash/cap`` size. So when there are no free slots we fall back to the conservative fixed **book-cap**
    divisor (= today's small ``cash/cap``), never all-in.

    ``positions_confirmed`` — the held count is only trustworthy if the last position read CONFIRMED it.
    ``PortfolioManager.refresh_positions`` swallows a transient broker-read error and returns the *un-pruned*
    ``_position_scores`` map (a STALE over-count), which would collapse the divisor and oversize the buy.
    Callers pass ``pm._last_refresh_ok``; when it is False (or the count is otherwise unconfirmed) we
    fail-safe to the fixed book-cap divisor rather than trust a possibly-stale, possibly-inflated count.

    Mode-neutral (no paper/live branch). BORA: ``CASH_AWARE_SLOTS_ENABLED`` mirrored in both editions.
    """
    from config import get_config

    cfg = get_config()
    cap = effective_max_positions()
    if not getattr(cfg, "CASH_AWARE_SLOTS_ENABLED", False):
        return cap
    if not positions_confirmed:
        # Held count not confirmed-fresh (broker read failed → stale/possibly-inflated) → conservative.
        return cap
    try:
        held = max(0, int(open_positions or 0))
    except (TypeError, ValueError):
        held = 0
    remaining = cap - held
    if remaining <= 0:
        # Full / over-cap / stale-over-count → conservative fixed book-cap split, never all-in.
        return cap
    return remaining


def vol_targeting_scaler(
    forecast_vol: Optional[float], log_missing: bool = True
) -> float:
    """#1953 TRD-2: config-gated inverse-vol risk-parity multiplier (dark feature).

    Returns EXACTLY 1.0 unless ``VOL_TARGETING_SIZING_ENABLED`` is set (default
    OFF => the sizer is byte-identical to the vol-blind behaviour, BORA). With
    the flag ON, a valid HAR-RV ``forecast_vol`` becomes
    ``clip(VOL_TARGET_DAILY_VOL / forecast_vol, LO, HI)`` — calm names size up,
    volatile names size down (constant risk contribution). A missing/invalid
    forecast (the desktop neutral state, plan §1) is a FAIL-SAFE no-op logged at
    WARNING (CLAUDE.md §5.6) — never a silent over-size. Resolves on both
    editions via the module read (config.oss.py module-level / config.py
    PEP-562 ``__getattr__`` — same pattern as RISK_FORBID_LEVERAGE).
    """
    try:
        import config as _cfg

        enabled = bool(getattr(_cfg, "VOL_TARGETING_SIZING_ENABLED", False))
    except ImportError:
        return 1.0
    if not enabled:
        return 1.0
    valid = (
        forecast_vol is not None
        and isinstance(forecast_vol, (int, float))
        and not isinstance(forecast_vol, bool)
        and pd.notna(forecast_vol)
        and forecast_vol > 0
        and forecast_vol != float("inf")
    )
    if not valid:
        if log_missing:
            logging.warning(
                "Vol-targeting sizing ON but forecast_vol=%r missing/invalid — "
                "scaler 1.0 (fail-safe no-op, no silent over-sizing).",
                forecast_vol,
            )
        return 1.0
    target = float(getattr(_cfg, "VOL_TARGET_DAILY_VOL", 0.015))
    lo = float(getattr(_cfg, "VOL_SIZE_SCALER_LO", 0.5))
    hi = float(getattr(_cfg, "VOL_SIZE_SCALER_HI", 1.5))
    from core.ml.vol_model import vol_size_scaler_from_forecast

    return vol_size_scaler_from_forecast(forecast_vol, target, lo, hi)


def apply_vol_targeting_audit(context: Any, forecast_vol: Optional[float]) -> float:
    """#1953: mirror the EFFECTIVE vol-targeting scaler into the decision audit
    trail (MiFID II Art. 17 traceability — WHICH vol value changed the size how).

    Writes ``DecisionContext.risk_size_scaler`` (existing audit sink — DB column
    ``decisions.risk_size_scaler``, models.py:63) only when the applied scaler
    deviates from 1.0; flag OFF / no-op leaves the context untouched
    (byte-identical decisions payload). Never raises; returns the scaler.
    """
    scaler = vol_targeting_scaler(forecast_vol, log_missing=False)
    if context is not None and scaler != 1.0:
        try:
            context.risk_size_scaler = scaler
        except Exception as exc:  # audit mirror must never break sizing
            logging.warning(
                "vol-targeting audit mirror failed (risk_size_scaler=%s): %s",
                scaler,
                exc,
            )
    return scaler


class RiskManager:
    """
    Advanced risk manager incorporating AI rules and hard cash constraints.
    """

    def __init__(
        self,
        client,
        total_capital,
        risk_per_trade_percent=None,
        # ADR-R01: Daily Drawdown Limit = 17.5% des Tageskapitals
        # Basis: Internes Risikopolicy v1.2 — abgeleitet aus Backtests 2023-2024
        # Begründung: 17.5% erlaubt ~3 Sigma-Intraday-Schwankungen ohne Halt;
        # < 10% wäre bei volatilen Märkten (VIX > 30) zu restriktiv und würde legitime
        # Rebounds abschneiden. Tier-System (Warnung @ 60%, Halt @ 100%) abgefedert.
        daily_drawdown_limit_percent=0.175,
        user_id: str = None,
    ):
        self.client = client
        self.user_id = user_id
        self.total_capital = float(total_capital)

        # ADR-R02: Risk per Trade = 2% des Gesamtkapitals (Fallback-Default)
        # Basis: Van Tharp "Trade Your Way to Financial Freedom" — Standard Fixed-Fractional
        # Begründung: 2% begrenzt Maximum Consecutive Losses auf ~32 Verluste bis Ruin (50%-Kapital);
        # Überschreiben via config.RISK_PER_TRADE_PERCENT empfohlen je nach Strategie-Volatilität.
        if risk_per_trade_percent is None:
            try:
                from config import RISK_PER_TRADE_PERCENT

                risk_per_trade_percent = RISK_PER_TRADE_PERCENT
            except ImportError:
                risk_per_trade_percent = 0.02
        self.risk_per_trade_percent = risk_per_trade_percent
        self.daily_drawdown_limit_percent = daily_drawdown_limit_percent
        self.daily_drawdown_limit = (
            self.total_capital * self.daily_drawdown_limit_percent
        )
        self.peak_daily_equity = self.total_capital  # Track peak for unlock mechanism
        self.initial_daily_equity = self.total_capital
        self.trading_halted = False

        # PROGRESSIVE HALT SYSTEM: Intermediate state — Positionsgröße reduzieren statt hart blocken
        # ADR-R03: Zweistufiges Halt-System (Warnung → Halt) statt Binary-Switch
        # Begründung: Hard-Halt bei erstem Überschreiten führt zu verpassten Recovery-Rallyes;
        # Reduce-Phase (60% des Limits) gibt dem Markt Zeit zur Stabilisierung.
        self.trading_reduced = False
        self.halt_trigger_count = 0

        # ADR-R04: Unlock-Schwelle = 50% Recovery vom Drawdown-Peak (initial)
        # Begründung: 50% verhindert sofortiges Re-Entry nach minimalem Bounce (Bull-Trap-Schutz).
        # Wird nach Zeit adaptiv gesenkt (2h → 30%, 4h → 20%) — siehe update_account_equity().
        self.unlock_recovery_percent = 0.50
        self.last_halt_time = None

        # Load AI Rules
        self.ai_rules_singleton = AILearnedRules()

        # ADR-R05: Default Stop-Loss-Multiplier = 3.0x ATR
        # Basis: Chandelier Exit (Le Beau) — Standard für trendfolgende Systeme
        # Begründung: 3x ATR deckt ~95% der normalen Intraday-Schwankungen ab;
        # < 2x ATR führt zu exzessivem Whipsaw bei moderater Volatilität.
        # Kann durch AI-Rules (evaluate_new_trade) dynamisch überschrieben werden.
        self.default_sl_multiplier = 3.0

        # ADR-R06: Max Loss per Trade = 1.5% des Gesamtkapitals
        # Basis: Internes Risikopolicy v1.2 — Einzeltrade-Verlustbegrenzer
        # Begründung: Kombiniert mit 2% Risk-per-Trade (ADR-R02) entsteht eine Doppel-Absicherung;
        # 1.5% = ~75% des Risk-per-Trade-Budgets als absolute Obergrenze (konservativ bei hoher ATR).
        self.max_loss_per_trade_percent = 0.015
        self.max_loss_per_trade = self.total_capital * self.max_loss_per_trade_percent

        # ADR-R07: Portfolio Stop-Loss = 7% vom Session-Start-Kapital (Fallback)
        # Basis: Internes Risikopolicy v1.2 + ESMA Guideline für algorithmische Systeme
        # Begründung: 7% = ~2x Daily-Drawdown-Limit — fängt systematische Fehler ab,
        # die den Daily-Drawdown-Check umgehen (z.B. Overnight-Gaps, News-Crashes).
        # Konfigurierbar via config.PORTFOLIO_STOP_LOSS_PCT; einmal ausgelöst → Session-Restart nötig.
        try:
            from config import PORTFOLIO_STOP_LOSS_PCT

            self.portfolio_stop_loss_pct = float(PORTFOLIO_STOP_LOSS_PCT) / 100.0
        except ImportError:
            self.portfolio_stop_loss_pct = 0.07
        self.session_start_equity = float(total_capital)
        self._portfolio_stop_triggered = (
            False  # Once True, no new trades until session restart
        )

        logging.info("Risk Manager initialized.")
        # TODO(PR-D): Complex f-string, review manually:         logging.info(f"Total Capital: ${self.total_capital:,.2f}")
        logging.info(f"Total Capital: ${self.total_capital:,.2f}")
        logging.info(
            f"Daily Drawdown Limit: ${self.daily_drawdown_limit:,.2f} ({self.daily_drawdown_limit_percent:.1%})"
        )
        logging.info(
            f"Max Loss Per Trade: ${self.max_loss_per_trade:,.2f} ({self.max_loss_per_trade_percent:.1%})"
        )
        logging.info(
            f"Portfolio Stop Loss: {self.portfolio_stop_loss_pct * 100:.0f}% from session start (max loss cap)"
        )
        logging.info(
            "Progressive Halt System: ENABLED (Graceful degradation instead of hard stop)"
        )

    def reload_policy(self, config_value=None):
        """ADR-SEC-06 (#1596): re-read the effective Iron Dome policy and apply it in place.

        Lets a policy change take effect without a restart (ADR §5a). Values are clamped to
        the immutable hard-floor; a missing/invalid source fails closed to the strict default.
        """
        from core.governance.iron_dome_policy import load_policy

        policy = load_policy(config_value)
        self.portfolio_stop_loss_pct = policy.portfolio_stop_loss_pct
        self.daily_drawdown_limit_percent = policy.daily_drawdown_pct
        self.daily_drawdown_limit = (
            self.total_capital * self.daily_drawdown_limit_percent
        )

    def update_account_equity(self, current_equity: float):
        """Monitors for daily drawdown with PROGRESSIVE HALT & intelligent unlocking."""
        from datetime import datetime

        current_equity = float(current_equity)

        # === PORTFOLIO STOP LOSS (from session start): halt if down 7% from when trading started ===
        if self.session_start_equity > 0 and self.portfolio_stop_loss_pct > 0:
            drawdown_from_start = self.session_start_equity - current_equity
            pct_down = drawdown_from_start / self.session_start_equity
            if pct_down >= self.portfolio_stop_loss_pct:
                if not self._portfolio_stop_triggered:
                    self._portfolio_stop_triggered = True
                    self.trading_halted = True
                    from core.kill_switch import kill_switch

                    kill_switch.trip(
                        reason=f"Portfolio stop loss ({pct_down * 100:.1f}%)",
                        user_id=self.user_id,
                    )
                    logging.critical(
                        f"🔴 PORTFOLIO STOP LOSS - Max loss from session start reached. "
                        f"Equity ${current_equity:,.2f} is {pct_down * 100:.1f}% below session start ${self.session_start_equity:,.2f} (limit {self.portfolio_stop_loss_pct * 100:.0f}%). Halting new trades."
                    )
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
                        event_type="portfolio_stop_loss",
                        severity="critical",
                        message=f"Portfolio stop loss: {pct_down * 100:.1f}% down from session start",
                        trigger_value=drawdown_from_start,
                        threshold_value=self.session_start_equity
                        * self.portfolio_stop_loss_pct,
                        equity=current_equity,
                    )
                # Keep halted; do not allow unlock for portfolio stop (restart session to reset)

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
            drawdown / self.daily_drawdown_limit if self.daily_drawdown_limit > 0 else 0
        )

        # === TIER 1: WARNING PHASE (ADR-R03) — Drawdown > 60% des Tages-Limits → Position Reduce ===
        # Begründung: 60%-Schwelle gibt ~2/3 des Risk-Budgets als Frühwarnung; 50% Positionsgröße
        # (siehe calculate_position_size > reduction_scaler) reduziert weiteres Exposure halbiert.
        # Recovery-Schwelle: 50% des Limits — symmetrisch, kein Hysterese-Problem.
        if (
            drawdown_ratio > 0.60
            and not self.trading_reduced
            and not self.trading_halted
        ):
            self.trading_reduced = True
            logging.warning(
                f"⚠️  WARNING PHASE: Drawdown at {drawdown_percent:.2f}% - Reducing position sizes to 50%"
            )
            if CLOUD_LOGGING_AVAILABLE:
                cloud_log_risk_event(
                    event_type="warning",
                    severity="warning",
                    message=f"WARNING PHASE: Drawdown at {drawdown_percent:.2f}% - Reducing position sizes to 50%",
                    trigger_value=drawdown,
                    threshold_value=self.daily_drawdown_limit * 0.60,
                    equity=current_equity,
                )
        elif (
            drawdown_ratio <= 0.50 and self.trading_reduced and not self.trading_halted
        ):
            self.trading_reduced = False
            logging.info(
                f"✓ Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing"
            )
            if CLOUD_LOGGING_AVAILABLE:
                cloud_log_risk_event(
                    event_type="recovery",
                    severity="info",
                    message=f"Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing",
                    trigger_value=drawdown,
                    threshold_value=self.daily_drawdown_limit * 0.50,
                    equity=current_equity,
                )

        # === TIER 2: CIRCUIT BREAKER (100% of limit) - Halt new trades ===
        if drawdown > self.daily_drawdown_limit:
            if not self.trading_halted:
                self.trading_halted = True
                from core.kill_switch import kill_switch

                kill_switch.trip(
                    reason=f"Daily drawdown limit exceeded ({drawdown_percent:.2f}%)",
                    user_id=self.user_id,
                )
                self.last_halt_time = datetime.now()
                self.halt_trigger_count += 1
                self.unlock_recovery_percent = 0.50  # Reset unlock threshold
                logging.critical(
                    f"🔴 CIRCUIT BREAKER #{self.halt_trigger_count} TRIGGERED - Halting new trades"
                )
                logging.critical(
                    f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) > Limit ${self.daily_drawdown_limit:,.2f}"
                )

                # Cloud log the circuit breaker
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
                        event_type="circuit_breaker",
                        severity="critical",
                        message=f"CIRCUIT BREAKER #{self.halt_trigger_count} TRIGGERED - Halting new trades. Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%)",
                        trigger_value=drawdown,
                        threshold_value=self.daily_drawdown_limit,
                        equity=current_equity,
                        details={
                            "halt_count": self.halt_trigger_count,
                            "drawdown_percent": drawdown_percent,
                        },
                    )

                # Attempt to liquidate EXISTING positions only (don't short sell)
                try:
                    if hasattr(self.client, "close_all_positions"):
                        self.client.close_all_positions(cancel_orders=True)
                except Exception as e:
                    logging.error("   Liquidation attempt failed: %s", e)

        # === INTELLIGENT UNLOCK: Progressive Recovery ===
        if self.trading_halted:
            # Dynamically adjust unlock threshold based on halt duration
            if self.last_halt_time:
                halt_duration = (
                    datetime.now() - self.last_halt_time
                ).total_seconds() / 3600
                # After 2 hours, lower unlock threshold from 50% to 30%
                if halt_duration > 2:
                    self.unlock_recovery_percent = 0.30
                # After 4 hours, lower to 20%
                if halt_duration > 4:
                    self.unlock_recovery_percent = 0.20

            unlock_threshold = self.daily_drawdown_limit * self.unlock_recovery_percent

            if drawdown <= unlock_threshold and not getattr(
                self, "_portfolio_stop_triggered", False
            ):
                self.trading_halted = False
                from core.kill_switch import kill_switch

                kill_switch.reset(user_id=self.user_id)
                self.last_halt_time = None
                self.trading_reduced = False
                self.halt_trigger_count = 0
                logging.info("✅ CIRCUIT BREAKER RESET - Trading RESUMED")
                logging.info(
                    f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) - "
                    f"Below {self.unlock_recovery_percent:.0%} recovery threshold"
                )

                # Cloud log the unlock
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
                        event_type="unlock",
                        severity="info",
                        message=f"CIRCUIT BREAKER RESET - Trading RESUMED. Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) - Below {self.unlock_recovery_percent:.0%} recovery threshold",
                        trigger_value=drawdown,
                        threshold_value=unlock_threshold,
                        equity=current_equity,
                    )

    def reset_daily_limit(self, current_equity: float):
        """Resets the daily drawdown limit (usually called at start of day)."""
        self.initial_daily_equity = float(current_equity)
        self.daily_drawdown_limit = (
            self.initial_daily_equity * self.daily_drawdown_limit_percent
        )
        self.trading_halted = False

    # TODO(PR-D): Complex f-string, review manually:         # logging.info(f"RM daily limit reset. Init Equity: ${self.initial_daily_equity:,.2f}")
    # logging.info(f"RM daily limit reset. Init Equity: ${self.initial_daily_equity:,.2f}")

    def calculate_position_size(
        self,
        stop_loss_atr_multiplier: float,
        atr: float,
        confidence: str = "medium",
        size_scaler: float = 1.0,
        market_data: Optional[Dict[str, Any]] = None,
        num_stocks_in_strategy: int = 1,
        current_price: float = 0.0,
        account_cash: float = 0.0,
        allow_fractional: bool = True,
        conviction_score: float = 0.5,
        forecast_vol: Optional[float] = None,
    ) -> float:
        """
        Calculates position size with DYNAMIC CONVICTION-BASED SCALING.
        - conviction_score: 0.0-1.0, higher = bigger position (5% to 25% of portfolio)
        - trading_reduced=True → 50% position size
        - trading_halted=True → 0 shares (no new positions)
        - allow_fractional=True → Returns fractional shares (e.g., 0.5 shares)
        - forecast_vol: #1953 HAR-RV forward-vol (daily stdev) for the flag-gated
          vol-targeting factor; None (default) or flag OFF → exact old behaviour
        """
        if atr is None or pd.isna(atr) or atr <= 0:
            return 0.0

        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===
        from core.kill_switch import kill_switch

        if self.trading_halted or kill_switch.is_halted(self.user_id):
            return 0.0

        # 1. Dynamic Volatility Risk Scaling (AGGRESSIVE REDUCTION)
        # ADR-R08: VIX-basierte Risikosteuerung (Volatility Regime Scaling)
        # Schwellenwerte abgeleitet aus CBOE VIX-Perzentilverteilung 2010-2024:
        #   VIX > 40 → 99. Perzentil (Crash-Regime, z.B. COVID März 2020): Risk -70%
        #   VIX > 35 → 97. Perzentil (Stress): Risk -60%
        #   VIX > 25 → 85. Perzentil (Erhöht, Pre-Earnings-Season): Risk -35%
        #   VIX > 18 → 55. Perzentil (Leicht erhöht): Risk -10%
        #   VIX <= 18 → Normal: kein Scaling
        # Begründung: Lineare VIX-Skalierung würde Cliff-Effekte erzeugen; diese Stufen
        # sind bewusst grob, um häufiges Regime-Wechseln (Churn) zu vermeiden.
        current_vix = 20.0
        if market_data and market_data.get("vix") is not None:
            current_vix = float(market_data["vix"])

        vix_risk_scaler = 1.0
        if current_vix > 40:
            vix_risk_scaler = 0.3
        elif current_vix > 35:
            vix_risk_scaler = 0.4
        elif current_vix > 25:
            vix_risk_scaler = 0.65
        elif current_vix > 18:
            vix_risk_scaler = 0.9

        # 2. Confidence Scaling
        confidence_scaler = 1.0
        if confidence == "high":
            confidence_scaler = 1.5
        elif confidence == "low":
            confidence_scaler = 0.5

        # NEW: PROGRESSIVE REDUCTION scaling
        reduction_scaler = 1.0
        if self.trading_reduced:
            reduction_scaler = 0.50  # 50% position size when warning phase active

        # #1953 TRD-2: inverse-vol risk-parity factor (VOL_TARGETING_SIZING_ENABLED,
        # default OFF => vol_scaler == 1.0 exactly, byte-identical). Applied here —
        # BEFORE every hard cap below (max-% / cash / exposure / compliance), so
        # risk parity can only resize WITHIN the existing safety ceilings, never
        # bypass one. Separate factor: the size_scaler semantics stay untouched.
        vol_scaler = vol_targeting_scaler(forecast_vol)

        final_risk_scaler = (
            vix_risk_scaler
            * confidence_scaler
            * size_scaler
            * reduction_scaler
            * vol_scaler
        )

        # 3. DYNAMIC CONVICTION-BASED POSITION SIZING
        # Position scales from MIN to MAX based on conviction (wide range so not "strictly 10k each")
        try:
            import config as _cfg

            dynamic_enabled = getattr(_cfg, "ENABLE_DYNAMIC_SIZING", True)
            min_pos_pct = getattr(_cfg, "MIN_POSITION_PERCENT", 0.02)
            max_pos_pct_sizing = getattr(_cfg, "MAX_POSITION_PERCENT_SIZING", 0.30)
            max_pos_cap_pct = getattr(_cfg, "MAX_POSITION_PERCENT", 0.25)
        except ImportError:
            dynamic_enabled = True
            min_pos_pct = 0.02
            max_pos_pct_sizing = 0.30
            max_pos_cap_pct = 0.25

        # Track for INFO log: what limited the size (cap vs cash)
        _sizing_conv, _sizing_target_pct, _sizing_target_value = None, None, None

        if dynamic_enabled and current_price > 0:
            # Clamp conviction to 0-1 range
            conv = max(0.0, min(1.0, conviction_score))
            # Linear interpolation: low conviction = small (e.g. 2%), high = large (e.g. 30%)
            target_position_pct = (
                min_pos_pct + (max_pos_pct_sizing - min_pos_pct) * conv
            )
            target_position_pct = min(
                target_position_pct, max_pos_cap_pct
            )  # Cap by MAX_POSITION_PERCENT
            target_position_value = (
                self.total_capital * target_position_pct * final_risk_scaler
            )
            num_shares = target_position_value / current_price
            _sizing_conv, _sizing_target_pct, _sizing_target_value = (
                conv,
                target_position_pct,
                target_position_value,
            )
            logging.debug(
                f"Dynamic sizing: conviction={conv:.2f} -> {target_position_pct * 100:.1f}% = ${target_position_value:.2f}"
            )
        else:
            # Fallback to old risk-based calculation
            if num_stocks_in_strategy <= 0:
                num_stocks_in_strategy = 1
            capital_to_risk_total = self.total_capital * self.risk_per_trade_percent
            capital_to_risk_per_stock = (
                capital_to_risk_total / num_stocks_in_strategy
            ) * final_risk_scaler
            dollar_risk_per_share = atr * stop_loss_atr_multiplier
            if dollar_risk_per_share <= 0:
                return 0
            num_shares = capital_to_risk_per_stock / dollar_risk_per_share

        # 4. Capital Constraint (configurable via config.MAX_POSITION_PERCENT)
        limited_by_cap = False
        if current_price > 0:
            try:
                from config import MAX_POSITION_PERCENT

                max_position_value_pct = MAX_POSITION_PERCENT
            except ImportError:
                max_position_value_pct = 0.25  # Default 25%
            max_position_value = self.total_capital * max_position_value_pct
            max_shares_by_cap = max_position_value / current_price
            if num_shares > max_shares_by_cap:
                num_shares = max_shares_by_cap
                limited_by_cap = True

        # 5. CASH CONSTRAINT — Kein Order über verfügbares Cash
        # ADR-R09: Slippage-Buffer = $50
        # Begründung: Alpaca Paper/Live hat bid/ask-Spread + Commissions; $50 Puffer deckt
        # bei typischen Aktienkursen ($10-$500) 0.01%-0.5% Slippage ab.
        # Bei sehr teuren Aktien (> $1000, z.B. NVDA) ggf. auf $100 erhöhen.
        # ADR-R10: Hebel-Verbot — eine Order darf den freien Cash nie übersteigen.
        # Basis: FINRA Reg-T erlaubt 2x Kaufkraft; Alpaca räumt sie Paper- wie Live-Konten
        # ein. Nichts im Code hat sie bisher abgelehnt.
        #
        # DER FEHLER: `account_cash > 0` hat diesen ganzen Block bewacht. Ein Konto, das
        # bereits im Minus steht (cash < 0, d.h. geliehen), übersprang den Cash-Deckel damit
        # KOMPLETT — der eine Zustand, in dem "nicht bezahlbar" sicher feststeht, war der
        # eine ungeprüfte. Die Klammer `max_shares_by_cash < 0 -> 0` unten war genau dafür
        # geschrieben und bis hierhin unerreichbar.
        #
        # ⚠ DEFAULT AN, UND ES ÄNDERT LIVE-VERHALTEN. Ein früherer Entwurf behauptete hier,
        # die Regel sei "ein No-op, solange MAX_TOTAL_EXPOSURE_PCT greift, weil cash < 0
        # gleichbedeutend mit Exposure > 100 % ist". Das ist WIDERLEGT (adversarialer Review,
        # ausgeführt): der Deckel misst gegen `self.total_capital` — einen Schnappschuss aus
        # der Konstruktion (:52 ist der einzige Schreibzugriff im ganzen Repo; ein
        # `update_total_capital` existiert nur im PortfolioManager) — während `cash` live ist.
        # Gemessenes Gegenbeispiel: RM bei 100k gebaut, Equity fällt auf 60k, Positionen 60k,
        # Cash 0.00, Client da, keine Exception → der Deckel gewährt 35.000 Spielraum:
        #     Flag AUS -> 99.99 Shares ($9.999)   Flag AN -> 0.0
        # Die Regel bindet also, WÄHREND der Deckel funktioniert. Das Blockieren ist dort
        # richtig (man kauft nicht für 9.999 $ mit 0 $ Cash) — aber es ist ein echter
        # Eingriff, kein No-op, und wird hier nicht schöngeredet.
        #
        # WAS DEN DEFAULT TRÄGT, ist allein die Einseitigkeit: die Regel kann eine Order nur
        # VERKLEINERN, nie vergrößern. Über 53.760 adversariale Kombinationen (Client an/aus ×
        # Positions-Sets inkl. negativer market_value × cash von -1e9 bis 1e6 inkl. ±inf/NaN ×
        # slots × Preise × ATR × Conviction × allow_fractional) verletzungsfrei; jede Stufe
        # NACH diesem Block ist monoton nicht-fallend in num_shares. Ihr schlimmster Fall ist
        # ein verpasster Trade, nie ein zusätzliches Risiko.
        # Beweis + Grid: tests/unit/test_risk_forbid_leverage.py (Sicherheitsaxiom).
        #
        # ⚠ NEBENWIRKUNG, absichtlich behalten: `rl_execution.py:765-767` fängt einen
        # get_account-Fehler, loggt und setzt `cash = 0.0` — und fällt DURCH zum Sizer (beide
        # return-Wächter liegen innerhalb des try). Dort heißt cash=0.0 also auch
        # "Broker nicht erreichbar". Mit Flag AN wird daraus "kein neuer Kauf" statt bisher
        # "kaufe blind, gesized auf einen Kapital-Schnappschuss, ohne das Konto lesen zu
        # können". Das ist fail-CLOSED und damit CLAUDE.md 5.6 / BUG-AI-105 konform — aber es
        # IST eine Verhaltensänderung, und sie steht hier, weil ein früherer Entwurf sie
        # bestritten hat.
        #
        # NaN: `float("nan")` entkommt der Regel (jeder Vergleich ist False). Erreichbar nur,
        # wenn der Broker wörtlich "nan" liefert. Bekannt, nicht abgedeckt.
        # Der Import löst auf BEIDEN Editionen auf — auf der Desktop-Edition als
        # modul-level Name (config.oss.py:256), auf der Enterprise-Edition über das
        # PEP-562 `__getattr__` (config.py:1030), das auf `_config_state` proxyt.
        # Verifiziert durch Ausführung, nicht angenommen: ein früherer Entwurf behauptete
        # hier einen ImportError auf Enterprise — falsch, `from config import
        # RISK_FORBID_LEVERAGE` liefert dort True. Das `except` ist reine Gürtel-und-
        # Hosenträger für ein gänzlich fehlendes config-Modul, kein Editions-Unterschied.
        try:
            from config import RISK_FORBID_LEVERAGE

            forbid_leverage = bool(RISK_FORBID_LEVERAGE)
        except (ImportError, AttributeError):
            forbid_leverage = True

        limited_by_cash = False
        if current_price > 0 and (account_cash > 0 or forbid_leverage):
            # Diversified cash allocation: split available cash across the strategy's target
            # universe (equal-weight slots) instead of letting the first BUYs of a cycle consume
            # it all — N concurrent BUYs then collectively fit the budget rather than the tail
            # being silently dropped at the broker buying-power gate. slots=1 (single-symbol /
            # default) preserves the original full-cash behaviour.
            # ADR-R09 (revised 2026-07-27): the slippage/rounding buffer is RISK_CASH_BUFFER_USD (default
            # $1 = Alpaca's minimum), not a flat $50. The flat $50 was 25% of a $200 account and — split
            # across the slots — zeroed every small-account order below the dust floor. The real slippage
            # cushion is the 5% MAX_TOTAL_EXPOSURE headroom (§5b), not this buffer. Module read on both
            # editions (config.oss.py module-level / config.py PEP-562); fail-safe $1 if config is absent.
            slots = max(1, int(num_stocks_in_strategy or 1))
            try:
                from config import RISK_CASH_BUFFER_USD

                _cash_buffer = float(RISK_CASH_BUFFER_USD)
            except (ImportError, AttributeError):
                _cash_buffer = 1.0
            max_shares_by_cash = ((account_cash - _cash_buffer) / slots) / current_price
            if max_shares_by_cash < 0:
                max_shares_by_cash = 0
            if num_shares > max_shares_by_cash:
                if forbid_leverage and num_shares > 0 and max_shares_by_cash <= 0:
                    # ADR-R10 / CLAUDE.md 5.6: the refusal MUST say so. The INFO log below is
                    # gated on `num_shares > 0` and therefore never fires for exactly the case
                    # this rule creates — a zeroed order. Without this line the guard would be
                    # a new SILENT producer of zeros, in a system whose orders already die
                    # unexplained (order_executor.py:1394 `if qty > 0:` has no else; its
                    # sibling at :631 logs the same event at DEBUG). Refusing a trade without
                    # saying why is how 1013 orders vanished on 2026-07-16.
                    logging.warning(
                        "[ADR-R10] Order refused: no leverage. cash=%.2f over %d slot(s) "
                        "buys %.4f shares at %.2f — requested %.4f. Set "
                        "RISK_FORBID_LEVERAGE=false to allow margin.",
                        account_cash,
                        slots,
                        max_shares_by_cash,
                        current_price,
                        num_shares,
                    )
                num_shares = max_shares_by_cash
                limited_by_cash = True

        # INFO log: show conviction -> target% -> value and which cap applied (helps debug "strictly 10k" issue)
        if _sizing_conv is not None and current_price > 0 and num_shares > 0:
            final_value = num_shares * current_price
            cap_reason = "capped by max%"
            if limited_by_cash:
                cap_reason = "capped by cash"
            elif limited_by_cap:
                cap_reason = "capped by max%"
            logging.info(
                f"Position sizing: conviction={_sizing_conv:.2f} -> target {_sizing_target_pct * 100:.1f}% (${_sizing_target_value:,.0f}) -> "
                f"final ${final_value:,.0f} ({num_shares:.2f} shares) [{cap_reason}]"
            )

        # 5b. TOTAL EXPOSURE CAP (optional): sum of position values <= total_capital * MAX_TOTAL_EXPOSURE_PCT
        try:
            max_exposure_pct = getattr(
                __import__("config", fromlist=["MAX_TOTAL_EXPOSURE_PCT"]),
                "MAX_TOTAL_EXPOSURE_PCT",
                None,
            )
            if (
                max_exposure_pct is not None
                and current_price > 0
                and hasattr(self, "client")
                and self.client is not None
            ):
                try:
                    # BUG-AI-105 (part 2): this broker re-fetch is INTENTIONAL
                    # per sizing call - do NOT replace it with a cached / once-
                    # per-cycle snapshot. The cap must see positions opened
                    # earlier in the SAME cycle: otherwise N BUYs sized off one
                    # stale snapshot would each see full headroom and together
                    # breach MAX_TOTAL_EXPOSURE_PCT (re-opening the fail-open).
                    # The N+1 is the price of a correct aggregate cap.
                    positions = self.client.get_all_positions()
                    total_position_value = 0.0
                    for p in positions:
                        mv = (
                            p.get("market_value", None)
                            if isinstance(p, dict)
                            else getattr(p, "market_value", None)
                        )
                        if mv is not None:
                            total_position_value += float(mv)
                    max_new_exposure = (
                        self.total_capital * float(max_exposure_pct)
                        - total_position_value
                    )
                    if max_new_exposure < (num_shares * current_price):
                        num_shares = max(0.0, max_new_exposure / current_price)
                except Exception as e:
                    # BUG-AI-105: fail CLOSED, not open. If the aggregate
                    # exposure cannot be verified (e.g. the broker positions
                    # query fails), do NOT silently skip the cap and let the
                    # order breach MAX_TOTAL_EXPOSURE_PCT - add no new exposure
                    # this sizing pass (CLAUDE.md 5.6).
                    logging.warning(
                        "Total-exposure cap check failed "
                        "(get_all_positions/valuation error): %s "
                        "- failing CLOSED: no new exposure added this sizing pass.",
                        e,
                        exc_info=True,
                    )
                    num_shares = 0.0
        except ImportError as _e:
            # CLAUDE.md §5.6: config is a core module — an ImportError here is a real anomaly.
            # The aggregate MAX_TOTAL_EXPOSURE_PCT cap is skipped this pass (per-order cash/max-%
            # caps above still apply); log rather than swallow silently.
            logging.warning(
                "config import failed (%s) — MAX_TOTAL_EXPOSURE_PCT aggregate cap NOT applied "
                "this sizing pass; per-order caps still apply.",
                _e,
            )

        # 5c. KELLY FRACTION CAP (optional): scale position size to avoid over-betting in hot streaks
        try:
            kelly_cap = getattr(
                __import__("config", fromlist=["KELLY_FRACTION_CAP"]),
                "KELLY_FRACTION_CAP",
                None,
            )
            if kelly_cap is not None and num_shares > 0:
                num_shares = num_shares * float(kelly_cap)
        except ImportError:
            pass

        # 6. STOP-LOSS CONSTRAINT (Limit max loss per trade)
        if stop_loss_atr_multiplier > 0 and atr > 0 and current_price > 0:
            dollar_loss_at_sl = num_shares * atr * stop_loss_atr_multiplier

            if dollar_loss_at_sl > self.max_loss_per_trade:
                max_shares_by_sl = self.max_loss_per_trade / (
                    atr * stop_loss_atr_multiplier
                )
                if max_shares_by_sl < num_shares:
                    num_shares = max(0, max_shares_by_sl)

        # 7. COMPLIANCE MAX-ORDER-VALUE CAP (ADR-C01)
        # A single order's notional (qty × price) must never exceed COMPLIANCE_MAX_ORDER_VALUE
        # (default 10,000 EUR; ESMA Position-Limit Guidelines / MiFID II Art. 57) — the SAME hard
        # limit the ComplianceGuardian enforces post-sizing (core/compliance.py _check_risk_limits:
        # ``value > max_order_value``). Without capping here, a risk-sized order above the limit is
        # built and then HARD-BLOCKED downstream → NO trade ever executes (observed: every desktop
        # BUY 🛡️ BLOCKED "Order exceeds Max Order Value"). Sizing to fit lets the order pass.
        # Applied LAST (after the multiplicative Kelly step) so nothing can re-inflate it.
        if current_price > 0:
            try:
                # module-level read (same pattern as the MAX_TOTAL_EXPOSURE_PCT / KELLY caps
                # above); in practice identical to compliance.py's get_config() value.
                _max_order_value = float(
                    getattr(
                        __import__("config", fromlist=["COMPLIANCE_MAX_ORDER_VALUE"]),
                        "COMPLIANCE_MAX_ORDER_VALUE",
                        0.0,
                    )
                    or 0.0
                )
            except Exception as _e:
                # CLAUDE.md §5.6: a fallback that changes money behaviour MUST log at WARNING.
                # 0.0 disables the sizing-time compliance cap (ComplianceGuardian still HARD-blocks
                # downstream); surface that the cap went inactive this pass.
                logging.warning(
                    "COMPLIANCE_MAX_ORDER_VALUE unreadable (%s) — per-order compliance size-cap "
                    "NOT applied this sizing pass (ComplianceGuardian still enforces downstream).",
                    _e,
                )
                _max_order_value = 0.0
            if _max_order_value > 0:
                # 0.9999 = a hair of headroom: the Guardian rejects on a STRICT ``>``, so the
                # capped notional must stay just UNDER the limit (float / fee tolerance).
                max_shares_by_compliance = (_max_order_value * 0.9999) / current_price
                if num_shares > max_shares_by_compliance:
                    num_shares = max_shares_by_compliance

        # ADR-R10: Mindestpositionswert = $1 (Alpaca Fractional Shares Minimum)
        # Basis: Alpaca API-Dokumentation — kleinste handelbare Einheit bei Fractional Shares
        # Begründung: Unter $1 würde Alpaca die Order ablehnen (API Error 422);
        # 0.001-Shares-Fallback greift bei current_price=0 (Datenfehler) als Sicherheitsnetz.
        if current_price > 0:
            min_fractional_shares = 1.0 / current_price
            if num_shares < min_fractional_shares:
                return 0.0
        elif (
            num_shares < 0.001
        ):  # Fallback: Datenfehler-Schutz (current_price nicht verfügbar)
            return 0.0

        # Round to 6 decimal places (Alpaca supports up to 9)
        num_shares = round(num_shares, 6)

        # If fractional not allowed, convert to int
        if not allow_fractional:
            num_shares = int(num_shares)
            if num_shares < 1:
                return 0.0

        # ADR-R11 (revised 2026-07-27): Dust-Filter Threshold = MIN_ORDER_VALUE_USD (default $1).
        # Basis: Alpaca supports fractional/notional orders at $0 commission — the old $50 rationale
        # (spread + commission drag on sub-$50 orders) is void for the liquid trading universe. $1 is
        # Alpaca's real notional minimum (orders < $1 → API 422; also guarded at :858-861). A flat $50
        # zeroed EVERY order on a funded small account (observed live: €220 account, all BUYs blocked).
        # DUST-FILTER (EXC-1): Block nominal values < MIN_ORDER_VALUE_USD
        if current_price > 0 and num_shares > 0:
            nominal_value = num_shares * current_price
            try:
                from config import MIN_ORDER_VALUE_USD

                min_value = float(MIN_ORDER_VALUE_USD)
            except (ImportError, AttributeError):
                min_value = 1.0  # Fail-safe = Alpaca's $1 minimum (config defines it explicitly)

            if nominal_value < min_value:
                logging.info(
                    f"Dust-Filter: Rejected trade. Nominal value ${nominal_value:.2f} "
                    f"is below minimum of ${min_value:.2f}"
                )
                return 0.0

        return float(num_shares)

    def evaluate_new_trade(
        self,
        symbol: str,
        side: str,
        market_data: Dict[str, Any],
        current_sl_multiplier: float,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates a trade against AI rules.
        Returns: (is_allowed, reason, action_mods)
        """
        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===
        from core.kill_switch import kill_switch

        if self.trading_halted or kill_switch.is_halted(self.user_id):
            return False, "System or User HALTED by Kill Switch / Risk Manager", {}

        active_rules = self.ai_rules_singleton.get_rules()
        action_mods: Dict[str, Any] = {
            "size_scaler": 1.0,
            "sl_multiplier": current_sl_multiplier,
        }

        features = market_data.get("indicators", {}).get("features", {})

        with tracer.start_as_current_span("risk.evaluate_trade") as span:
            span.set_attribute("symbol", symbol)
            span.set_attribute("trade.side", side)
            span.set_attribute("risk.approved", True)

            for rule in active_rules:
                try:
                    trigger = rule.get("trigger", {})
                    action = rule.get("action", "")
                    status = rule.get("status", "active")

                    # Skip proactive signals (handled in engine.py)
                    if action == "proactive_signal":
                        continue

                    rule_matches = True

                    # --- Match Logic ---
                    if trigger.get("side") and trigger["side"].lower() != side.lower():
                        rule_matches = False
                    if trigger.get("strategy") and trigger[
                        "strategy"
                    ] not in market_data.get("strategy_name", ""):
                        rule_matches = False

                    # VIX Checks
                    current_vix = float(market_data.get("vix", 0.0))
                    if (
                        trigger.get("vix_gt") is not None
                        and current_vix <= trigger["vix_gt"]
                    ):
                        rule_matches = False
                    if (
                        trigger.get("vix_lt") is not None
                        and current_vix >= trigger["vix_lt"]
                    ):
                        rule_matches = False

                    # Dynamic Feature Checks (RSI, ADX, etc.)
                    for key, val in trigger.items():
                        if key.startswith("indicators.features."):
                            parts = key.split(".")
                            feature_name = parts[-2]
                            condition = parts[-1]
                            curr_val = float(features.get(feature_name, 0.0))

                            if condition == "gt" and curr_val <= float(val):
                                rule_matches = False
                                break
                            elif condition == "lt" and curr_val >= float(val):
                                rule_matches = False
                                break

                    # --- Execute Action ---
                    if rule_matches:
                        if status == "probation":
                            continue  # Skip probation rules

                        if action == "block_trade":
                            span.set_attribute("risk.approved", False)
                            span.set_attribute(
                                "risk.reason",
                                f"Blocked by AI Rule: {rule.get('reason')}",
                            )
                            return (
                                False,
                                f"Blocked by AI Rule: {rule.get('reason')}",
                                {},
                            )

                        elif action == "reduce_size":
                            scaler = float(rule.get("value", 0.5))
                            if scaler < action_mods["size_scaler"]:
                                action_mods["size_scaler"] = scaler

                        elif action == "tighten_sl":
                            multiplier = float(rule.get("value", 1.5))
                            if multiplier < action_mods["sl_multiplier"]:
                                action_mods["sl_multiplier"] = multiplier

                        elif action == "increase_size":
                            scaler = float(rule.get("value", 1.5))
                            if scaler > action_mods["size_scaler"]:
                                action_mods["size_scaler"] = scaler

                        elif action == "widen_sl":
                            multiplier = float(rule.get("value", 3.0))
                            if multiplier > action_mods["sl_multiplier"]:
                                action_mods["sl_multiplier"] = multiplier

                except Exception as e:
                    # Fail-open per rule: a single malformed AI rule must not
                    # abort evaluation of the rest — but it must NOT be silent
                    # (#1236, CLAUDE.md §5.6).
                    logging.warning(
                        "AI rule evaluation failed (rule=%s): %s — skipping this rule",
                        rule.get("id", rule.get("reason", "<unknown>")),
                        e,
                        exc_info=True,
                    )

        return True, "Approved", action_mods
