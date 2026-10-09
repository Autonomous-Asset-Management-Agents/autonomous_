# risk_manager.py
# --- FULL FILE: Includes Cash Constraints & AI Rule Evaluation ---
# --- With Cloud Logging for risk events ---

import logging
from typing import Optional

# Import the AI Rules handler
from core.ai_rules import AILearnedRules
from core.ports.clock_port import ClockPort
from core.risk_bemessung import BemessungMixin
from core.risk_deckel import DeckelMixin
from core.risk_konto import KontoHaltMixin
from core.risk_skalierer import (  # noqa: F401 — #4271 (H-3i): Re-Export
    VIX_FAILCLOSED_SENTINEL,
    VIX_UNCONFIRMED_MARKER,
    _bull_exposure_cfg,
    apply_sizing_mode_audit,
    apply_vol_targeting_audit,
    coverage_size_discount,
    resolve_vix,
    skew_size_tilt,
    vix_ladder_scaler,
    vol_targeting_scaler,
)
from core.risk_vorpruefung import VorpruefungMixin
from core.telemetry import get_tracer

tracer = get_tracer(__name__)

# Cloud logging imports (graceful fallback if not configured)
try:
    import config
    from core.cloud_logger import log_risk_event as cloud_log_risk_event

    CLOUD_LOGGING_AVAILABLE = getattr(
        config, "DB_AVAILABLE", False
    ) and getattr(  # noqa: E501
        config, "CLOUD_LOGGING_ENABLED", True
    )
except ImportError:
    CLOUD_LOGGING_AVAILABLE = False

    def cloud_log_risk_event(*args, **kwargs):
        pass


def effective_max_positions() -> int:
    """The number of concurrent positions the portfolio may hold — the correct  # noqa: E501
    equal-weight slot count for cash sizing.

    #2153: ``calculate_position_size`` divides cash into ``num_stocks_in_strategy``  # noqa: E501
    equal-weight slots. The book is HARD-CAPPED at ``max_positions`` holdings
    (``portfolio_manager.should_open_new_position``: open on room, else displace),  # noqa: E501
    so the cash MUST be split by that cap — NOT by ``len(live_universe)`` (the ~500  # noqa: E501
    name SCAN universe), which under-sized every order ~50x in production/full-universe  # noqa: E501
    and forced the fractional-share churn. This mirrors the author's own sizing test  # noqa: E501
    (``slots=10 == MAX_POSITIONS``) and the max_positions read in order_executor.py.  # noqa: E501
    """
    from config import get_config

    cfg = get_config()
    if getattr(cfg, "FULL_UNIVERSE_TRADING_ENABLED", False):
        return max(1, int(getattr(cfg, "FULL_UNIVERSE_MAX_POSITIONS", 20)))
    return max(1, int(getattr(cfg, "MAX_POSITIONS", 10)))


def is_immaterial_entry(
    order_value: float,
    equity: float,
    mat_pct: float,
    max_positions: Optional[int] = None,
) -> bool:
    """True <=> eine BUY-Order liegt UNTER dem #2935-Materialitaets-Schwellwert.  # noqa: E501

    #2981: EINE Materiality-Entscheidung, an EINER Stelle definiert, von allen drei  # noqa: E501
    Money-Path-Call-Sites (Tenant, HITL-Drain, Desktop/[Global]-Fallback) in
    ``order_executor.py`` aufgerufen. Der Nenner der Ziel-Allokation ist IMMER
    ``equity`` (NICHT ``cash``): ``target_alloc = equity / effective_max_positions()``.  # noqa: E501

    Rationale (Divisor = equity): der Schwellwert misst, ob eine Order relevant im  # noqa: E501
    Verhaeltnis zu EINER Ziel-Position im Gesamtbuch ist; die Ziel-Position ist
    ``Gesamtvermoegen / Slot-Zahl``, unabhaengig davon, wie viel gerade als Cash frei  # noqa: E501
    liegt. ``cash`` als Nenner koppelt die Schwelle faelschlich an die
    Investitionsquote (fast-voll => Schwelle ~ 0 => Gate wirkungslos, fail-open).  # noqa: E501

    ``mat_pct <= 0`` (Gate aus) oder ``order_value <= 0`` (nichts zu pruefen) => False.  # noqa: E501
    Nicht-numerischer / fehlender Input => fail-safe False (kein faelschliches Blocken).  # noqa: E501
    ``max_positions`` optional; ``None`` => ``effective_max_positions()``.
    """
    try:
        order_value = float(order_value)
        equity = float(equity)
        mat_pct = float(mat_pct)
    except (TypeError, ValueError):
        return False
    if mat_pct <= 0 or order_value <= 0:
        return False
    if max_positions is None:
        slots = effective_max_positions()
    else:
        try:
            slots = int(max_positions)
        except (TypeError, ValueError):
            slots = effective_max_positions()
    target_alloc = equity / max(1, slots)
    return order_value < (mat_pct * target_alloc)


def effective_free_slots(
    open_positions: int, positions_confirmed: bool = True
) -> int:  # noqa: E501
    """O3 — the cash-sizing divisor that reflects the REMAINING free slots, not the fixed book cap.  # noqa: E501

    ``calculate_position_size`` splits free cash into ``num_stocks_in_strategy`` equal-weight slots.  # noqa: E501
    Passing the fixed ``effective_max_positions()`` (=10) means cash is ALWAYS divided by 10 — so a  # noqa: E501
    mostly-invested book with little free cash sizes every new buy at ``cash/10`` = dust, and the  # noqa: E501
    account can never build a fresh full-size position from a near-invested state.  # noqa: E501

    With ``CASH_AWARE_SLOTS_ENABLED`` ON, the divisor becomes the number of remaining free slots  # noqa: E501
    (``cap - held``) — the free cash is reserved only across the slots still to fill, so freed cash  # noqa: E501
    (via exit/displacement) funds a proper-sized position. A book with 1..cap-1 free slots divides by  # noqa: E501
    that count (the genuine last free slot → divisor 1, i.e. the whole cash budget for it, by design).  # noqa: E501
    OFF → the fixed ``effective_max_positions()`` divisor, byte-identical to today. The concentration  # noqa: E501
    MAX rail (MAX_POSITION_PERCENT / ESMA order-value cap) is unchanged and still bounds the top.  # noqa: E501

    O3-SAFETY (adversarial review): a **full or over-cap** book has ZERO free slots (``cap - held <= 0``).  # noqa: E501
    The naive ``max(1, cap - held)`` collapsed that to divisor **1**, which makes the cash constraint  # noqa: E501
    route ~100% of free cash into a single new/displacement buy — the *maximum* per-order concentration,  # noqa: E501
    the OPPOSITE of the risk-averse "reserve across the remaining slots" intent and ~cap× the flag-OFF  # noqa: E501
    ``cash/cap`` size. So when there are no free slots we fall back to the conservative fixed **book-cap**  # noqa: E501
    divisor (= today's small ``cash/cap``), never all-in.

    ``positions_confirmed`` — the held count is only trustworthy if the last position read CONFIRMED it.  # noqa: E501
    ``PortfolioManager.refresh_positions`` swallows a transient broker-read error and returns the *un-pruned*  # noqa: E501
    ``_position_scores`` map (a STALE over-count), which would collapse the divisor and oversize the buy.  # noqa: E501
    Callers pass ``pm._last_refresh_ok``; when it is False (or the count is otherwise unconfirmed) we  # noqa: E501
    fail-safe to the fixed book-cap divisor rather than trust a possibly-stale, possibly-inflated count.  # noqa: E501

    Mode-neutral (no paper/live branch). BORA: ``CASH_AWARE_SLOTS_ENABLED`` mirrored in both editions.  # noqa: E501
    """
    from config import get_config

    cfg = get_config()
    cap = effective_max_positions()
    if not getattr(cfg, "CASH_AWARE_SLOTS_ENABLED", False):
        return cap
    if not positions_confirmed:
        # Held count not confirmed-fresh (broker read failed → stale/possibly-inflated) → conservative.  # noqa: E501
        return cap
    try:
        held = max(0, int(open_positions or 0))
    except (TypeError, ValueError):
        held = 0
    remaining = cap - held
    if remaining <= 0:
        # Full / over-cap / stale-over-count → conservative fixed book-cap split, never all-in.  # noqa: E501
        return cap
    return remaining


class RiskManager(KontoHaltMixin, BemessungMixin, DeckelMixin, VorpruefungMixin):
    """
    Advanced risk manager incorporating AI rules and hard cash constraints.
    """

    def __init__(
        self,
        client,
        total_capital,
        risk_per_trade_percent=None,
        # ADR-R01: Daily Drawdown Limit = 17.5% des Tageskapitals
        # Basis: Internes Risikopolicy v1.2 — abgeleitet aus Backtests 2023-2024  # noqa: E501
        # Begründung: 17.5% erlaubt ~3 Sigma-Intraday-Schwankungen ohne Halt;
        # < 10% wäre bei volatilen Märkten (VIX > 30) zu restriktiv und würde legitime  # noqa: E501
        # Rebounds abschneiden. Tier-System (Warnung @ 60%, Halt @ 100%) abgefedert.  # noqa: E501
        daily_drawdown_limit_percent=0.175,
        user_id: str = None,
        kill_switch=None,
        *,
        clock: "ClockPort",
    ):
        self.clock = clock
        self.client = client
        self.user_id = user_id
        # #3485: wessen Halt dieser RiskManager ausloest, aufhebt und fragt. Ohne Angabe der
        # globale Kill-Switch der Engine (alle bisherigen Aufrufer). Die In-App-Simulation
        # reicht einen LokalerHalt herein — sonst hielte ihr Verlust die echte Engine an und
        # ihre Erholung hoebe einen echten Halt auf.
        self._eigener_halt = kill_switch
        # #2980 §5.6: None capital must not crash construction (TypeError). Fall to  # noqa: E501
        # a documented safe value (0.0 → zero capital sizes zero positions =
        # fail-closed) with a WARNING, never a silent substitution.
        if total_capital is None:
            logging.warning(
                "RiskManager: total_capital is None → 0.0 safe default "
                "(fail-closed: no position can be sized) (§5.6)."
            )
            total_capital = 0.0
        self.total_capital = float(total_capital)

        # ADR-R02: Risk per Trade = 2% des Gesamtkapitals (Fallback-Default)
        # Basis: Van Tharp "Trade Your Way to Financial Freedom" — Standard Fixed-Fractional  # noqa: E501
        # Begründung: 2% begrenzt Maximum Consecutive Losses auf ~32 Verluste bis Ruin (50%-Kapital);  # noqa: E501
        # Überschreiben via config.RISK_PER_TRADE_PERCENT empfohlen je nach Strategie-Volatilität.  # noqa: E501
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
        self.peak_daily_equity = (
            self.total_capital
        )  # Track peak for unlock mechanism  # noqa: E501
        self.initial_daily_equity = self.total_capital
        self.trading_halted = False

        # PROGRESSIVE HALT SYSTEM: Intermediate state — Positionsgröße reduzieren statt hart blocken  # noqa: E501
        # ADR-R03: Zweistufiges Halt-System (Warnung → Halt) statt Binary-Switch  # noqa: E501
        # Begründung: Hard-Halt bei erstem Überschreiten führt zu verpassten Recovery-Rallyes;  # noqa: E501
        # Reduce-Phase (60% des Limits) gibt dem Markt Zeit zur Stabilisierung.
        self.trading_reduced = False
        self.halt_trigger_count = 0

        # ADR-R04: Unlock-Schwelle = 50% Recovery vom Drawdown-Peak (initial)
        # Begründung: 50% verhindert sofortiges Re-Entry nach minimalem Bounce (Bull-Trap-Schutz).  # noqa: E501
        # Wird nach Zeit adaptiv gesenkt (2h → 30%, 4h → 20%) — siehe update_account_equity().  # noqa: E501
        self.unlock_recovery_percent = 0.50
        self.last_halt_time = None

        # Load AI Rules
        self.ai_rules_singleton = AILearnedRules()

        # ADR-R05: Default Stop-Loss-Multiplier = 3.0x ATR
        # Basis: Chandelier Exit (Le Beau) — Standard für trendfolgende Systeme  # noqa: E501
        # Begründung: 3x ATR deckt ~95% der normalen Intraday-Schwankungen ab;
        # < 2x ATR führt zu exzessivem Whipsaw bei moderater Volatilität.
        # Kann durch AI-Rules (evaluate_new_trade) dynamisch überschrieben werden.  # noqa: E501
        self.default_sl_multiplier = 3.0

        # ADR-R06: Max Loss per Trade = 1.5% des Gesamtkapitals
        # Basis: Internes Risikopolicy v1.2 — Einzeltrade-Verlustbegrenzer
        # Begründung: Kombiniert mit 2% Risk-per-Trade (ADR-R02) entsteht eine Doppel-Absicherung;  # noqa: E501
        # 1.5% = ~75% des Risk-per-Trade-Budgets als absolute Obergrenze (konservativ bei hoher ATR).  # noqa: E501
        self.max_loss_per_trade_percent = 0.015
        self.max_loss_per_trade = (
            self.total_capital * self.max_loss_per_trade_percent
        )  # noqa: E501

        # DEF-1 (#3217): Order-Deckel-Override aus der Iron-Dome-Policy.
        # ``None`` = keine Policy angewandt → Schritt 7 liest wie bisher das
        # Config-Modul (byte-identisch zum Verhalten vor #3217). Sobald
        # ``apply_policy`` läuft — bei jedem ``/start``, ADR-SEC-06 §1 —, setzt
        # ``reload_policy`` den Wert und Sizer und ComplianceGuardian können
        # nicht mehr auseinanderlaufen.
        self._policy_max_order_value = None

        # ADR-R07: Portfolio Stop-Loss = 7% vom Session-Start-Kapital (Fallback)  # noqa: E501
        # Basis: Internes Risikopolicy v1.2 + ESMA Guideline für algorithmische Systeme  # noqa: E501
        # Begründung: 7% = ~2x Daily-Drawdown-Limit — fängt systematische Fehler ab,  # noqa: E501
        # die den Daily-Drawdown-Check umgehen (z.B. Overnight-Gaps, News-Crashes).  # noqa: E501
        # Konfigurierbar via config.PORTFOLIO_STOP_LOSS_PCT; einmal ausgelöst → Session-Restart nötig.  # noqa: E501
        try:
            from config import PORTFOLIO_STOP_LOSS_PCT

            self.portfolio_stop_loss_pct = (
                float(PORTFOLIO_STOP_LOSS_PCT) / 100.0
            )  # noqa: E501
        except ImportError:
            self.portfolio_stop_loss_pct = 0.07
        self.session_start_equity = float(total_capital)
        self._portfolio_stop_triggered = (
            False  # Once True, no new trades until session restart
        )

        logging.info("Risk Manager initialized.")
        # TODO(PR-D): Complex f-string, review manually:         logging.info(f"Total Capital: ${self.total_capital:,.2f}")  # noqa: E501
        logging.info(f"Total Capital: ${self.total_capital:,.2f}")
        logging.info(
            f"Daily Drawdown Limit: ${self.daily_drawdown_limit:,.2f} ({self.daily_drawdown_limit_percent:.1%})"  # noqa: E501
        )
        logging.info(
            f"Max Loss Per Trade: ${self.max_loss_per_trade:,.2f} ({self.max_loss_per_trade_percent:.1%})"  # noqa: E501
        )
        logging.info(
            f"Portfolio Stop Loss: {self.portfolio_stop_loss_pct * 100:.0f}% from session start (max loss cap)"  # noqa: E501
        )
        logging.info(
            "Progressive Halt System: ENABLED (Graceful degradation instead of hard stop)"  # noqa: E501
        )

    def reload_policy(self, config_value=None):
        """ADR-SEC-06 (#1596): re-read the effective Iron Dome policy and apply it in place.  # noqa: E501

        Lets a policy change take effect without a restart (ADR §5a). Values are clamped to  # noqa: E501
        the immutable hard-floor; a missing/invalid source fails closed to the strict default.  # noqa: E501
        """
        from core.governance.iron_dome_policy import load_policy

        policy = load_policy(config_value)
        self.portfolio_stop_loss_pct = policy.portfolio_stop_loss_pct
        self.daily_drawdown_limit_percent = policy.daily_drawdown_pct
        self.daily_drawdown_limit = (
            self.total_capital * self.daily_drawdown_limit_percent
        )

        # DEF-1 (#3217): der Order-Deckel gehört zu den Werten, die diese Methode  # noqa: E501
        # übernehmen MUSS. Vorher setzte nur ComplianceGuardian.reload_policy ihn —  # noqa: E501
        # der Sizer las weiter das Config-Modul, sodass eine Policy-Änderung
        # entweder verpuffte (anheben) oder Handelsstillstand erzeugte (senken:
        # der Sizer baute weiter zu große Orders, die der Guardian hart blockte —  # noqa: E501
        # der Ausfall vom 14.07.2026).
        self._policy_max_order_value = policy.max_order_value

        # CLAUDE.md §5.6: eine Abweichung vom Config-Wert ändert das Geldverhalten  # noqa: E501
        # und muss sichtbar sein — sonst wundert sich ein Operator, warum sein
        # gesetztes COMPLIANCE_MAX_ORDER_VALUE nach dem Start nicht mehr greift.  # noqa: E501
        try:
            _cfg_value = float(
                getattr(
                    __import__(
                        "config", fromlist=["COMPLIANCE_MAX_ORDER_VALUE"]
                    ),  # noqa: E501
                    "COMPLIANCE_MAX_ORDER_VALUE",
                    0.0,
                )
                or 0.0
            )
        except Exception:  # noqa: BLE001, E501
            _cfg_value = 0.0
        if _cfg_value > 0 and policy.max_order_value != _cfg_value:
            logging.warning(
                "Iron-Dome-Policy setzt max_order_value auf %s — der Config-Wert "  # noqa: E501
                "COMPLIANCE_MAX_ORDER_VALUE (%s) greift ab jetzt nicht mehr "
                "(Sizer und ComplianceGuardian folgen der Policy).",
                policy.max_order_value,
                _cfg_value,
            )
