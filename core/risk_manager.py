# risk_manager.py
# --- FULL FILE: Includes Cash Constraints & AI Rule Evaluation ---
# --- With Cloud Logging for risk events ---

import logging
import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

# Import the AI Rules handler
from core.ai_rules import AILearnedRules
from core.telemetry import get_tracer

tracer = get_tracer(__name__)

# ADR-R09: Missing/invalid VIX resolves FAIL-CLOSED (maximum de-risk / block new  # noqa: E501
# buys), NOT to a benign default. Basis: CODING_POLICY §5.6 + the #2672
# position-context provenance pattern. A blind volatility gauge during a feed
# outage must NEVER read as "calm" — the safe posture for a regulated money-path  # noqa: E501
# is to stop ADDING risk (EU AI Act Art. 14 safe-state, MiFID II RTS 6), while
# SELL / de-risk paths always stay open ("only reduce, never enlarge").
#
# 45.0 lands in the ADR-R08 crash band (> 40 → vix_risk_scaler 0.3), i.e. the
# maximum de-risk the sizing ladder offers. Documented single source of truth so  # noqa: E501
# the three VIX consumers (sizing, block-gate, audit) cannot drift again (#2980).  # noqa: E501
VIX_FAILCLOSED_SENTINEL = 45.0

# Mirrors the "[position context UNCONFIRMED] " marker (runner.py, #2672): a
# self-describing audit prefix stamped on any decision whose VIX was substituted,  # noqa: E501
# never observed. confirmed=False means "unknown", NEVER "calm".
VIX_UNCONFIRMED_MARKER = "[VIX UNCONFIRMED] "


def resolve_vix(
    market_data: Optional[Dict[str, Any]]
) -> Tuple[float, bool, str]:  # noqa: E501
    """Central Missing-VIX resolver — single source of truth (#2980, ADR-R09).  # noqa: E501

    Returns ``(vix_value, confirmed, source)``:
    - a finite ``vix > 0`` → ``(float(vix), True, "confirmed")`` (lossless happy path);  # noqa: E501
    - missing key → ``(SENTINEL, False, "unconfirmed")`` + WARNING;
    - non-numeric → ``(SENTINEL, False, "invalid")`` + WARNING;
    - non-finite / ``<= 0`` → ``(SENTINEL, False, "invalid")`` + WARNING.

    ``confirmed=False`` means the volatility gauge is UNKNOWN, never "calm".
    Consumers MUST fail closed: sizing → max de-risk; block-gate → new buys
    blocked (protective rules fire); audit → ``vix_confirmed=False`` + the
    ``[VIX UNCONFIRMED]`` marker. SELL / de-risk paths stay open regardless.
    """
    raw = (market_data or {}).get("vix")
    if raw is None:
        logging.warning(
            "[VIX] resolve_vix: VIX missing — FAIL-CLOSED (max de-risk / block new "  # noqa: E501
            "buys). Consumers must treat volatility as UNKNOWN, not calm (§5.6)."  # noqa: E501
        )
        return VIX_FAILCLOSED_SENTINEL, False, "unconfirmed"
    # bool is an int subclass — reject it explicitly (True/False is not a VIX).  # noqa: E501
    if isinstance(raw, bool):
        logging.warning(
            "[VIX] resolve_vix: VIX invalid (%r) — FAIL-CLOSED.", raw
        )  # noqa: E501
        return VIX_FAILCLOSED_SENTINEL, False, "invalid"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logging.warning(
            "[VIX] resolve_vix: VIX invalid (%r) — FAIL-CLOSED.", raw
        )  # noqa: E501
        return VIX_FAILCLOSED_SENTINEL, False, "invalid"
    if not math.isfinite(val) or val <= 0:
        logging.warning(
            "[VIX] resolve_vix: VIX non-finite/<=0 (%r) — FAIL-CLOSED.", raw
        )
        return VIX_FAILCLOSED_SENTINEL, False, "invalid"
    return val, True, "confirmed"


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


def _bull_exposure_cfg() -> Tuple[bool, float]:
    """#3099 (H2): (enabled, calm_scaler) for the bounded bull-regime risk-on step.  # noqa: E501

    Fail-safe: any config read error → (False, 1.0), i.e. the pure ADR-R08 down-only  # noqa: E501
    ladder, byte-identical. The scaler value is only consulted when enabled.
    """
    try:
        import config as _cfg

        enabled = bool(getattr(_cfg, "BULL_EXPOSURE_ENABLED", False))
        calm = float(getattr(_cfg, "BULL_EXPOSURE_CALM_SCALER", 1.0))
        return enabled, calm
    except Exception:  # noqa: BLE001 — a config read must never break sizing
        logging.exception("Fehler beim Lesen der Bull Exposure Config")
        return False, 1.0


def vix_ladder_scaler(
    vix: float, bull_enabled: bool = False, calm_scaler: float = 1.0
) -> float:
    """ADR-R08 VIX risk ladder + #3099 (H2) bounded calm-regime risk-on step.

    Down-only ABOVE 18 (unchanged): >40→0.3, >35→0.4, >25→0.65, >18→0.9 — so any  # noqa: E501
    volatility rise de-risks immediately (fast forward-looking gauge). AT/BELOW 18  # noqa: E501
    (calm regime): 1.0, OR ``calm_scaler`` (>1.0) when ``bull_enabled`` — the ONLY  # noqa: E501
    >1.0 step. It NEVER breaches the aggregate MAX_TOTAL_EXPOSURE_PCT cap, the
    per-name caps or RISK_FORBID_LEVERAGE: those apply downstream in the sizing
    funnel and remain the hard ceiling (no leverage, ever).
    """
    if vix > 40:
        return 0.3
    if vix > 35:
        return 0.4
    if vix > 25:
        return 0.65
    if vix > 18:
        return 0.9
    return float(calm_scaler) if bull_enabled else 1.0


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
        return max(1, int(getattr(cfg, "FULL_UNIVERSE_MAX_POSITIONS", 10)))
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


def vol_targeting_scaler(
    forecast_vol: Optional[float], log_missing: bool = True
) -> float:
    """#1953 TRD-2: config-gated inverse-vol risk-parity multiplier (dark feature).  # noqa: E501

    Returns EXACTLY 1.0 unless ``VOL_TARGETING_SIZING_ENABLED`` is set (default
    OFF => the sizer is byte-identical to the vol-blind behaviour, BORA). With
    the flag ON, a valid HAR-RV ``forecast_vol`` becomes
    ``clip(VOL_TARGET_DAILY_VOL / forecast_vol, LO, HI)`` — calm names size up,  # noqa: E501
    volatile names size down (constant risk contribution). A missing/invalid
    forecast (the desktop neutral state, plan §1) is a FAIL-SAFE no-op logged at  # noqa: E501
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
                "Vol-targeting sizing ON but forecast_vol=%r missing/invalid — "  # noqa: E501
                "scaler 1.0 (fail-safe no-op, no silent over-sizing).",
                forecast_vol,
            )
        return 1.0
    target = float(getattr(_cfg, "VOL_TARGET_DAILY_VOL", 0.015))
    lo = float(getattr(_cfg, "VOL_SIZE_SCALER_LO", 0.5))
    hi = float(getattr(_cfg, "VOL_SIZE_SCALER_HI", 1.5))
    from core.ml.vol_model import vol_size_scaler_from_forecast

    return vol_size_scaler_from_forecast(forecast_vol, target, lo, hi)


def skew_size_tilt(
    rr_percentile: Optional[float], log_missing: bool = True
) -> float:  # noqa: E501
    """#3199: config-gated bounded size tilt from the 25Δ RR skew percentile.

    Returns EXACTLY 1.0 unless ``SKEW_SIZE_TILT_ENABLED`` is set (default OFF =>
    byte-identical, BORA) AND ``SKEW_SIZE_TILT_CAP`` > 0. With the flag ON a valid
    percentile ``p`` ∈ [0, 1] (bullish 25Δ risk-reversal upside skew high, crash
    skew low) becomes a bounded multiplicative factor::

        tilt = clip(1 + 2*cap*(p - 0.5), 1 - cap, 1 + cap)

    p=0.5 (neutral) -> 1.0; p=1 -> 1+cap; p=0 -> 1-cap. This mirrors the #1953
    ``vol_targeting_scaler`` (IV LEVEL -> size) around the IV SKEW dimension and,
    like it, applies BEFORE every hard cap in the sizer (risk-parity may only
    resize WITHIN the safety ceilings). A missing/invalid percentile is a
    FAIL-OPEN no-op logged at WARNING (CLAUDE.md §5.6) — never a guessed tilt.
    PURE (reads only config + arg) so the sizer and the MiFID II audit mirror
    can call it independently and never drift (#3199 audit-trail guarantee).
    """
    try:
        import config as _cfg

        enabled = bool(getattr(_cfg, "SKEW_SIZE_TILT_ENABLED", False))
    except ImportError:
        return 1.0
    if not enabled:
        return 1.0
    cap = float(getattr(_cfg, "SKEW_SIZE_TILT_CAP", 0.0))
    if cap <= 0.0:
        return 1.0
    valid = (
        rr_percentile is not None
        and isinstance(rr_percentile, (int, float))
        and not isinstance(rr_percentile, bool)
        and pd.notna(rr_percentile)
        and 0.0 <= rr_percentile <= 1.0
    )
    if not valid:
        if log_missing:
            logging.warning(
                "Skew size tilt ON but rr_percentile=%r missing/invalid — "  # noqa: E501
                "tilt 1.0 (fail-open no-op, no guessed sizing).",
                rr_percentile,
            )
        return 1.0
    tilt = 1.0 + 2.0 * cap * (float(rr_percentile) - 0.5)
    return max(1.0 - cap, min(1.0 + cap, tilt))


def coverage_size_discount(
    coverage: Optional[float], log_missing: bool = True
) -> float:  # noqa: E501
    """#3210: config-gated prudence size discount from directional-vote coverage.

    Returns EXACTLY 1.0 unless ``COVERAGE_SIZING_STRENGTH`` (lambda) > 0 (default
    0.0 => byte-identical, BORA). With lambda > 0 a coverage fraction ``c`` ∈ (0, 1]
    — the share of the ARMED directional vote weight that actually voted this round
    — becomes a bounded, CONCAVE, WEAKENED discount::

        discount = 1 - lambda * (1 - sqrt(c))

    c=1 (full coverage) -> 1.0; thinner coverage -> < 1.0. ``lambda`` attenuates the
    theoretical sqrt because coverage is a SUSPECTED, not proven, risk indicator.
    Applied BEFORE every hard cap in the sizer (like the #1953 vol scaler / #3199
    skew tilt): it can only resize DOWN within the safety ceilings, never > 1.0. A
    missing/invalid coverage is a FAIL-OPEN no-op logged at WARNING (CLAUDE.md §5.6)
    — never a guessed discount. PURE (reads only config + arg) so the sizer and the
    MiFID II audit mirror call it independently and never drift.

    # ADR-R17: Coverage-Vorsichtsabschlag (Prudenz / Unsicherheit, KEIN Alpha).
    # Basis: weniger unabhängige Richtungsstimmen => größerer Standardfehler der
    #   Konsens-Schätzung (Condorcet-Jury / Bagging) => risiko-angemessen kleiner
    #   setzen (fractional-Kelly-Logik). KEIN Performance-Anspruch.
    # Rationale: empirisch heute nicht testbar (Datenzulänglichkeits-Gate, §9); der
    #   Abschlag wird über lambda abgeschwächt (default 0.0 = aus), weil Coverage ein
    #   VERMUTETER Indikator ist. Dokumentiert als ADR-R (nicht RTR-0-Alpha-Gate) in
    #   docs/1_architecture_and_adr/richtung_risiko_groesse.md +
    #   docs/3_mlops_and_models/STRATEGY_VALIDATION.md §7. Rücknahme: =0.0.
    """
    try:
        import config as _cfg

        lam = float(getattr(_cfg, "COVERAGE_SIZING_STRENGTH", 0.0))
    except (ImportError, TypeError, ValueError):
        return 1.0
    if lam <= 0.0:
        return 1.0
    valid = (
        coverage is not None
        and isinstance(coverage, (int, float))
        and not isinstance(coverage, bool)
        and pd.notna(coverage)
        and 0.0 < coverage <= 1.0
    )
    if not valid:
        if log_missing:
            logging.warning(
                "Coverage sizing ON but coverage=%r missing/invalid — "  # noqa: E501
                "discount 1.0 (fail-open no-op, no guessed sizing).",
                coverage,
            )
        return 1.0
    discount = 1.0 - lam * (1.0 - math.sqrt(float(coverage)))
    return max(0.0, min(1.0, discount))


def apply_vol_targeting_audit(
    context: Any,
    forecast_vol: Optional[float],
    rr_percentile: Optional[float] = None,
    coverage: Optional[float] = None,
) -> float:  # noqa: E501
    """#1953/#3199: mirror the EFFECTIVE combined size scaler into the decision
    audit trail (MiFID II Art. 17 traceability — WHICH inputs changed the size).  # noqa: E501

    Writes ``DecisionContext.risk_size_scaler`` (existing audit sink — DB column  # noqa: E501
    ``decisions.risk_size_scaler``, models.py:63) only when the applied scaler
    deviates from 1.0; flag OFF / no-op leaves the context untouched
    (byte-identical decisions payload). Never raises; returns the scaler.

    #3199: the effective multiplier applied by ``calculate_position_size`` is
    ``vol_targeting_scaler · skew_size_tilt`` — the SKEW tilt is folded in here
    with the SAME pure helpers the sizer uses, so the logged value equals the
    APPLIED value (applied == logged; no MiFID II divergence). Both helpers
    return 1.0 when their flag is OFF, so a single-feature deployment records
    exactly that feature's scaler and the dark default stays byte-identical.
    """
    scaler = (
        vol_targeting_scaler(forecast_vol, log_missing=False)
        * skew_size_tilt(rr_percentile, log_missing=False)
        * coverage_size_discount(coverage, log_missing=False)  # #3210
    )
    if context is not None and scaler != 1.0:
        try:
            context.risk_size_scaler = scaler
        except Exception as exc:  # audit mirror must never break sizing
            logging.warning(
                "size-scaler audit mirror failed (risk_size_scaler=%s): %s",
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
        # Basis: Internes Risikopolicy v1.2 — abgeleitet aus Backtests 2023-2024  # noqa: E501
        # Begründung: 17.5% erlaubt ~3 Sigma-Intraday-Schwankungen ohne Halt;
        # < 10% wäre bei volatilen Märkten (VIX > 30) zu restriktiv und würde legitime  # noqa: E501
        # Rebounds abschneiden. Tier-System (Warnung @ 60%, Halt @ 100%) abgefedert.  # noqa: E501
        daily_drawdown_limit_percent=0.175,
        user_id: str = None,
    ):
        self.client = client
        self.user_id = user_id
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
        from datetime import datetime

        current_equity = float(current_equity)

        # === PORTFOLIO STOP LOSS (from session start): halt if down 7% from when trading started ===  # noqa: E501
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
                        f"🔴 PORTFOLIO STOP LOSS - Max loss from session start reached. "  # noqa: E501
                        f"Equity ${current_equity:,.2f} is {pct_down * 100:.1f}% below session start ${self.session_start_equity:,.2f} (limit {self.portfolio_stop_loss_pct * 100:.0f}%). Halting new trades."  # noqa: E501
                    )
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
                        event_type="portfolio_stop_loss",
                        severity="critical",
                        message=f"Portfolio stop loss: {pct_down * 100:.1f}% down from session start",  # noqa: E501
                        trigger_value=drawdown_from_start,
                        threshold_value=self.session_start_equity
                        * self.portfolio_stop_loss_pct,
                        equity=current_equity,
                    )
                # Keep halted; do not allow unlock for portfolio stop (restart session to reset)  # noqa: E501

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
                f"⚠️  WARNING PHASE: Drawdown at {drawdown_percent:.2f}% - Reducing position sizes to 50%"  # noqa: E501
            )
            if CLOUD_LOGGING_AVAILABLE:
                cloud_log_risk_event(
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
                f"✓ Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing"  # noqa: E501
            )
            if CLOUD_LOGGING_AVAILABLE:
                cloud_log_risk_event(
                    event_type="recovery",
                    severity="info",
                    message=f"Drawdown recovered to {drawdown_percent:.2f}% - Resuming normal position sizing",  # noqa: E501
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
                    reason=f"Daily drawdown limit exceeded ({drawdown_percent:.2f}%)",  # noqa: E501
                    user_id=self.user_id,
                )
                self.last_halt_time = datetime.now()
                self.halt_trigger_count += 1
                self.unlock_recovery_percent = 0.50  # Reset unlock threshold
                logging.critical(
                    f"🔴 CIRCUIT BREAKER #{self.halt_trigger_count} TRIGGERED - Halting new trades"  # noqa: E501
                )
                logging.critical(
                    f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) > Limit ${self.daily_drawdown_limit:,.2f}"  # noqa: E501
                )

                # Cloud log the circuit breaker
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
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

                # Attempt to liquidate EXISTING positions only (don't short sell)  # noqa: E501
                try:
                    if hasattr(self.client, "close_all_positions"):
                        self.client.close_all_positions(cancel_orders=True)
                except Exception as e:
                    logging.error("   Liquidation attempt failed: %s", e)

        # === INTELLIGENT UNLOCK: Progressive Recovery ===
        # #2978: suppressed entirely on the live-wiring path (allow_unlock=False) —  # noqa: E501
        # halt-only guarantee; no silent intraday re-enable of a real-money breaker.  # noqa: E501
        if self.trading_halted and allow_unlock:
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

            unlock_threshold = (
                self.daily_drawdown_limit * self.unlock_recovery_percent
            )  # noqa: E501

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
                    f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%) - "  # noqa: E501
                    f"Below {self.unlock_recovery_percent:.0%} recovery threshold"  # noqa: E501
                )

                # Cloud log the unlock
                if CLOUD_LOGGING_AVAILABLE:
                    cloud_log_risk_event(
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
        rr_percentile: Optional[float] = None,
        coverage: Optional[float] = None,
        sizing_trace: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Calculates position size with DYNAMIC CONVICTION-BASED SCALING.
        - conviction_score: 0.0-1.0, higher = bigger position (5% to 25% of portfolio)  # noqa: E501
        - trading_reduced=True → 50% position size
        - trading_halted=True → 0 shares (no new positions)
        - allow_fractional=True → Returns fractional shares (e.g., 0.5 shares)  # noqa: E501
        - forecast_vol: #1953 HAR-RV forward-vol (daily stdev) for the flag-gated  # noqa: E501
          vol-targeting factor; None (default) or flag OFF → exact old behaviour  # noqa: E501
        - sizing_trace (#2811): OPTIONAL per-call sink. When a dict is passed, the sizer  # noqa: E501
          records WHICH limit bound (`binding_limit`) and, on a zero, WHY
          (`zero_reason`) — the cause behind 85.5 % of the chain's skipped orders,  # noqa: E501
          previously logged to a log file the pythonw desktop engine does not have.  # noqa: E501
          Pure observation: never changes the returned size (pinned bit-for-bit by  # noqa: E501
          tests/unit/test_sizing_zero_reason.py against golden values generated from  # noqa: E501
          the unchanged code), and a broken sink never raises into the order path.  # noqa: E501
          The reason is recorded AT THE CLAMP, not at the funnel exit — every clamp  # noqa: E501
          (cash/caps/conviction) leaves through the same `< min_fractional` return,  # noqa: E501
          so labelling the exit would call every cause "below_order_floor".
        """

        def _note(key: str, value: str) -> None:
            # Observation must never break sizing: a read-only/broken sink is ignored.  # noqa: E501
            if sizing_trace is None:
                return
            try:
                sizing_trace[key] = value
            except Exception:  # noqa: BLE001 — see docstring; deliberate
                pass

        if atr is None or pd.isna(atr) or atr <= 0:
            _note("zero_reason", "invalid_atr")
            return 0.0

        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===  # noqa: E501
        from core.kill_switch import kill_switch

        if self.trading_halted or kill_switch.is_halted(self.user_id):
            _note("zero_reason", "trading_halted")
            return 0.0

        # 1. Dynamic Volatility Risk Scaling (AGGRESSIVE REDUCTION)
        # ADR-R08: VIX-basierte Risikosteuerung (Volatility Regime Scaling)
        # Schwellenwerte abgeleitet aus CBOE VIX-Perzentilverteilung 2010-2024:
        #   VIX > 40 → 99. Perzentil (Crash-Regime, z.B. COVID März 2020): Risk -70%  # noqa: E501
        #   VIX > 35 → 97. Perzentil (Stress): Risk -60%
        #   VIX > 25 → 85. Perzentil (Erhöht, Pre-Earnings-Season): Risk -35%  # noqa: E501
        #   VIX > 18 → 55. Perzentil (Leicht erhöht): Risk -10%
        #   VIX <= 18 → Normal: kein Scaling
        # Begründung: Lineare VIX-Skalierung würde Cliff-Effekte erzeugen; diese Stufen  # noqa: E501
        # sind bewusst grob, um häufiges Regime-Wechseln (Churn) zu vermeiden.
        # #2980 (ADR-R09): resolve VIX through the central fail-closed helper.
        # Missing/invalid VIX → VIX_FAILCLOSED_SENTINEL (crash band) → 0.3 scaler  # noqa: E501
        # (max de-risk), NOT the old benign 20.0 (0.9, a 10% trim). A blind gauge  # noqa: E501
        # must size as if the market were in crisis, never as if it were calm.
        current_vix, _vix_confirmed, _vix_source = resolve_vix(market_data)

        # #3099 (H2): bounded calm-regime risk-on step (dark flag). Flag OFF =
        # pure down-only ladder (byte-identical). The >1.0 step is capped downstream  # noqa: E501
        # by MAX_TOTAL_EXPOSURE_PCT + per-name caps + RISK_FORBID_LEVERAGE.
        _bull_en, _bull_scaler = _bull_exposure_cfg()
        vix_risk_scaler = vix_ladder_scaler(
            current_vix, bull_enabled=_bull_en, calm_scaler=_bull_scaler
        )

        # 2. Confidence Scaling
        confidence_scaler = 1.0
        if confidence == "high":
            confidence_scaler = 1.5
        elif confidence == "low":
            confidence_scaler = 0.5

        # NEW: PROGRESSIVE REDUCTION scaling
        reduction_scaler = 1.0
        if self.trading_reduced:
            reduction_scaler = (
                0.50  # 50% position size when warning phase active  # noqa: E501
            )

        # #1953 TRD-2: inverse-vol risk-parity factor (VOL_TARGETING_SIZING_ENABLED,  # noqa: E501
        # default OFF => vol_scaler == 1.0 exactly, byte-identical). Applied here —  # noqa: E501
        # BEFORE every hard cap below (max-% / cash / exposure / compliance), so  # noqa: E501
        # risk parity can only resize WITHIN the existing safety ceilings, never  # noqa: E501
        # bypass one. Separate factor: the size_scaler semantics stay untouched.  # noqa: E501
        vol_scaler = vol_targeting_scaler(forecast_vol)

        # #3199: bounded 25Δ RR skew tilt (SKEW_SIZE_TILT_ENABLED, default OFF =>
        # skew_tilt == 1.0 exactly, byte-identical). Applied here — like vol_scaler,
        # BEFORE every hard cap below — so the asymmetry tilt can only resize WITHIN
        # the safety ceilings, never bypass one. Folded into the SAME audit mirror
        # (apply_vol_targeting_audit) so context.risk_size_scaler logs vol*skew
        # (applied == logged; MiFID II Art. 17).
        skew_tilt = skew_size_tilt(rr_percentile)

        # #3210: coverage prudence discount (COVERAGE_SIZING_STRENGTH, default 0.0
        # => 1.0 exactly, byte-identical). Applied here — like vol_scaler / skew_tilt,
        # BEFORE every hard cap below — so the prudence discount can only resize DOWN
        # within the safety ceilings, never bypass one. Folded into the SAME audit
        # mirror (apply_vol_targeting_audit) so context.risk_size_scaler logs
        # vol*skew*coverage (applied == logged; MiFID II Art. 17). PRUDENCE, not alpha.
        coverage_discount = coverage_size_discount(coverage)

        final_risk_scaler = (
            vix_risk_scaler
            * confidence_scaler
            * size_scaler
            * reduction_scaler
            * vol_scaler
            * skew_tilt
            * coverage_discount
        )

        # 3. DYNAMIC CONVICTION-BASED POSITION SIZING
        # Position scales from MIN to MAX based on conviction (wide range so not "strictly 10k each")  # noqa: E501
        try:
            import config as _cfg

            dynamic_enabled = getattr(_cfg, "ENABLE_DYNAMIC_SIZING", True)
            # Fallback MUST equal the config default (#2784) — 0.02 silently halved the  # noqa: E501
            # relative entry floor whenever _cfg was a partial namespace.
            min_pos_pct = getattr(_cfg, "MIN_POSITION_PERCENT", 0.05)
            max_pos_pct_sizing = getattr(
                _cfg, "MAX_POSITION_PERCENT_SIZING", 0.30
            )  # noqa: E501
            max_pos_cap_pct = getattr(_cfg, "MAX_POSITION_PERCENT", 0.25)
        except ImportError:
            dynamic_enabled = True
            min_pos_pct = 0.02
            max_pos_pct_sizing = 0.30
            max_pos_cap_pct = 0.25

        # Track for INFO log: what limited the size (cap vs cash)
        _sizing_conv, _sizing_target_pct, _sizing_target_value = (
            None,
            None,
            None,
        )  # noqa: E501

        if dynamic_enabled and current_price > 0:
            # Clamp conviction to 0-1 range
            conv = max(0.0, min(1.0, conviction_score))
            # Linear interpolation: low conviction = small (e.g. 2%), high = large (e.g. 30%)  # noqa: E501
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
                f"Dynamic sizing: conviction={conv:.2f} -> {target_position_pct * 100:.1f}% = ${target_position_value:.2f}"  # noqa: E501
            )
        else:
            # Fallback to old risk-based calculation
            if num_stocks_in_strategy <= 0:
                num_stocks_in_strategy = 1
            capital_to_risk_total = (
                self.total_capital * self.risk_per_trade_percent
            )  # noqa: E501
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
                _note("binding_limit", "position_cap")

        # 5. CASH CONSTRAINT — Kein Order über verfügbares Cash
        # ADR-R09: Slippage-Buffer = $50
        # Begründung: Alpaca Paper/Live hat bid/ask-Spread + Commissions; $50 Puffer deckt  # noqa: E501
        # bei typischen Aktienkursen ($10-$500) 0.01%-0.5% Slippage ab.
        # Bei sehr teuren Aktien (> $1000, z.B. NVDA) ggf. auf $100 erhöhen.
        # ADR-R10: Hebel-Verbot — eine Order darf den freien Cash nie übersteigen.  # noqa: E501
        # Basis: FINRA Reg-T erlaubt 2x Kaufkraft; Alpaca räumt sie Paper- wie Live-Konten  # noqa: E501
        # ein. Nichts im Code hat sie bisher abgelehnt.
        #
        # DER FEHLER: `account_cash > 0` hat diesen ganzen Block bewacht. Ein Konto, das  # noqa: E501
        # bereits im Minus steht (cash < 0, d.h. geliehen), übersprang den Cash-Deckel damit  # noqa: E501
        # KOMPLETT — der eine Zustand, in dem "nicht bezahlbar" sicher feststeht, war der  # noqa: E501
        # eine ungeprüfte. Die Klammer `max_shares_by_cash < 0 -> 0` unten war genau dafür  # noqa: E501
        # geschrieben und bis hierhin unerreichbar.
        #
        # ⚠ DEFAULT AN, UND ES ÄNDERT LIVE-VERHALTEN. Ein früherer Entwurf behauptete hier,  # noqa: E501
        # die Regel sei "ein No-op, solange MAX_TOTAL_EXPOSURE_PCT greift, weil cash < 0  # noqa: E501
        # gleichbedeutend mit Exposure > 100 % ist". Das ist WIDERLEGT (adversarialer Review,  # noqa: E501
        # ausgeführt): der Deckel misst gegen `self.total_capital` — einen Schnappschuss aus  # noqa: E501
        # der Konstruktion (:52 ist der einzige Schreibzugriff im ganzen Repo; ein  # noqa: E501
        # `update_total_capital` existiert nur im PortfolioManager) — während `cash` live ist.  # noqa: E501
        # Gemessenes Gegenbeispiel: RM bei 100k gebaut, Equity fällt auf 60k, Positionen 60k,  # noqa: E501
        # Cash 0.00, Client da, keine Exception → der Deckel gewährt 35.000 Spielraum:  # noqa: E501
        #     Flag AUS -> 99.99 Shares ($9.999)   Flag AN -> 0.0
        # Die Regel bindet also, WÄHREND der Deckel funktioniert. Das Blockieren ist dort  # noqa: E501
        # richtig (man kauft nicht für 9.999 $ mit 0 $ Cash) — aber es ist ein echter  # noqa: E501
        # Eingriff, kein No-op, und wird hier nicht schöngeredet.
        #
        # WAS DEN DEFAULT TRÄGT, ist allein die Einseitigkeit: die Regel kann eine Order nur  # noqa: E501
        # VERKLEINERN, nie vergrößern. Über 53.760 adversariale Kombinationen (Client an/aus ×  # noqa: E501
        # Positions-Sets inkl. negativer market_value × cash von -1e9 bis 1e6 inkl. ±inf/NaN ×  # noqa: E501
        # slots × Preise × ATR × Conviction × allow_fractional) verletzungsfrei; jede Stufe  # noqa: E501
        # NACH diesem Block ist monoton nicht-fallend in num_shares. Ihr schlimmster Fall ist  # noqa: E501
        # ein verpasster Trade, nie ein zusätzliches Risiko.
        # Beweis + Grid: tests/unit/test_risk_forbid_leverage.py (Sicherheitsaxiom).  # noqa: E501
        #
        # ⚠ NEBENWIRKUNG, absichtlich behalten: `rl_execution.py:765-767` fängt einen  # noqa: E501
        # get_account-Fehler, loggt und setzt `cash = 0.0` — und fällt DURCH zum Sizer (beide  # noqa: E501
        # return-Wächter liegen innerhalb des try). Dort heißt cash=0.0 also auch  # noqa: E501
        # "Broker nicht erreichbar". Mit Flag AN wird daraus "kein neuer Kauf" statt bisher  # noqa: E501
        # "kaufe blind, gesized auf einen Kapital-Schnappschuss, ohne das Konto lesen zu  # noqa: E501
        # können". Das ist fail-CLOSED und damit CLAUDE.md 5.6 / BUG-AI-105 konform — aber es  # noqa: E501
        # IST eine Verhaltensänderung, und sie steht hier, weil ein früherer Entwurf sie  # noqa: E501
        # bestritten hat.
        #
        # NaN: `float("nan")` entkommt der Regel (jeder Vergleich ist False). Erreichbar nur,  # noqa: E501
        # wenn der Broker wörtlich "nan" liefert. Bekannt, nicht abgedeckt.
        # Der Import löst auf BEIDEN Editionen auf — auf der Desktop-Edition als  # noqa: E501
        # modul-level Name (config.oss.py:256), auf der Enterprise-Edition über das  # noqa: E501
        # PEP-562 `__getattr__` (config.py:1030), das auf `_config_state` proxyt.  # noqa: E501
        # Verifiziert durch Ausführung, nicht angenommen: ein früherer Entwurf behauptete  # noqa: E501
        # hier einen ImportError auf Enterprise — falsch, `from config import
        # RISK_FORBID_LEVERAGE` liefert dort True. Das `except` ist reine Gürtel-und-  # noqa: E501
        # Hosenträger für ein gänzlich fehlendes config-Modul, kein Editions-Unterschied.  # noqa: E501
        try:
            from config import RISK_FORBID_LEVERAGE

            forbid_leverage = bool(RISK_FORBID_LEVERAGE)
        except (ImportError, AttributeError):
            forbid_leverage = True

        limited_by_cash = False
        if current_price > 0 and (account_cash > 0 or forbid_leverage):
            # Diversified cash allocation: split available cash across the strategy's target  # noqa: E501
            # universe (equal-weight slots) instead of letting the first BUYs of a cycle consume  # noqa: E501
            # it all — N concurrent BUYs then collectively fit the budget rather than the tail  # noqa: E501
            # being silently dropped at the broker buying-power gate. slots=1 (single-symbol /  # noqa: E501
            # default) preserves the original full-cash behaviour.
            # ADR-R09 (revised 2026-07-27): the slippage/rounding buffer is RISK_CASH_BUFFER_USD (default  # noqa: E501
            # $1 = Alpaca's minimum), not a flat $50. The flat $50 was 25% of a $200 account and — split  # noqa: E501
            # across the slots — zeroed every small-account order below the dust floor. The real slippage  # noqa: E501
            # cushion is the 5% MAX_TOTAL_EXPOSURE headroom (§5b), not this buffer. Module read on both  # noqa: E501
            # editions (config.oss.py module-level / config.py PEP-562); fail-safe $1 if config is absent.  # noqa: E501
            slots = max(1, int(num_stocks_in_strategy or 1))
            try:
                from config import RISK_CASH_BUFFER_USD

                _cash_buffer = float(RISK_CASH_BUFFER_USD)
            except (ImportError, AttributeError):
                _cash_buffer = 1.0
            # ADR-R11 (#3135): proportional slot-cash. The equal (cash-buffer)/slots split  # noqa: E501
            # caps every concurrent BUY at the SAME dollar amount, erasing the IV/conviction  # noqa: E501
            # size differentiation #3094 (VIXAware-IV -> forecast_vol -> vol-targeting) and the  # noqa: E501
            # conviction sizer produce upstream (measured live: 8 buys, risk_size_scaler  # noqa: E501
            # 0.50-1.089, all filled ~$1,005). When PROPORTIONAL_SLOT_CASH_ENABLED is on, scale  # noqa: E501
            # the slot by this position's own target value relative to the largest a single  # noqa: E501
            # position could target (total_capital * MAX_POSITION_PERCENT_SIZING). The fraction  # noqa: E501
            # is bounded to [0,1] so the cap can only SHRINK vs the equal split => the  # noqa: E501
            # no-leverage safety axiom (sum of concurrent budgets <= cash) holds a fortiori  # noqa: E501
            # (tests/unit/test_risk_forbid_leverage.py grid). Flag OFF or non-dynamic sizing  # noqa: E501
            # (_sizing_target_value is None) => 1.0 => byte-identical equal split. No gross  # noqa: E501
            # renormalisation (idle cash left when convictions < max) — consistent with #3094.  # noqa: E501
            # Basis: MiFID II Art. 17 sizing governance. Annual review.
            _demand_fraction = 1.0
            if _sizing_target_value is not None and max_pos_pct_sizing > 0:
                try:
                    from config import (  # noqa: E501
                        PROPORTIONAL_SLOT_CASH_ENABLED as _prop_slot,
                    )
                except (ImportError, AttributeError):
                    _prop_slot = False
                if _prop_slot:
                    _ref_value = self.total_capital * max_pos_pct_sizing
                    if _ref_value > 0:
                        _demand_fraction = max(
                            0.0, min(1.0, _sizing_target_value / _ref_value)
                        )
            max_shares_by_cash = (
                ((account_cash - _cash_buffer) / slots) * _demand_fraction
            ) / current_price
            if max_shares_by_cash < 0:
                max_shares_by_cash = 0
            if num_shares > max_shares_by_cash:
                if (
                    forbid_leverage and num_shares > 0 and max_shares_by_cash <= 0
                ):  # noqa: E501
                    # ADR-R10 / CLAUDE.md 5.6: the refusal MUST say so. The INFO log below is  # noqa: E501
                    # gated on `num_shares > 0` and therefore never fires for exactly the case  # noqa: E501
                    # this rule creates — a zeroed order. Without this line the guard would be  # noqa: E501
                    # a new SILENT producer of zeros, in a system whose orders already die  # noqa: E501
                    # unexplained (order_executor.py:1394 `if qty > 0:` has no else; its  # noqa: E501
                    # sibling at :631 logs the same event at DEBUG). Refusing a trade without  # noqa: E501
                    # saying why is how 1013 orders vanished on 2026-07-16.
                    logging.warning(
                        "[ADR-R10] Order refused: no leverage. cash=%.2f over %d slot(s) "  # noqa: E501
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
                # The single most common zero on small accounts: (cash − buffer) / slots  # noqa: E501
                # under one fractional share. Recorded HERE so the funnel exit below  # noqa: E501
                # cannot claim it as "below_order_floor".
                _note("binding_limit", "cash")
                if num_shares <= 0 or (
                    current_price > 0 and num_shares < 1.0 / current_price
                ):
                    _note("zero_reason", "insufficient_cash")

        # INFO log: show conviction -> target% -> value and which cap applied (helps debug "strictly 10k" issue)  # noqa: E501
        if _sizing_conv is not None and current_price > 0 and num_shares > 0:
            final_value = num_shares * current_price
            cap_reason = "capped by max%"
            if limited_by_cash:
                cap_reason = "capped by cash"
            elif limited_by_cap:
                cap_reason = "capped by max%"
            logging.info(
                f"Position sizing: conviction={_sizing_conv:.2f} -> target {_sizing_target_pct * 100:.1f}% (${_sizing_target_value:,.0f}) -> "  # noqa: E501
                f"final ${final_value:,.0f} ({num_shares:.2f} shares) [{cap_reason}]"  # noqa: E501
            )

        # 5b. TOTAL EXPOSURE CAP (optional): sum of position values <= total_capital * MAX_TOTAL_EXPOSURE_PCT  # noqa: E501
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
                        _note("binding_limit", "total_exposure_cap")
                        if num_shares <= 0 or (
                            current_price > 0
                            and num_shares < 1.0 / current_price  # noqa: E501
                        ):
                            _note("zero_reason", "total_exposure_cap")
                        # ADR-R12 (#2960): a clamped residue below the entry
                        # floor is dropped, not bought — headroom leftovers of  # noqa: E501
                        # $1-$5 became standalone dust positions. ONLY inside
                        # this clamp branch: a normal cash-/pct-bound order
                        # never reaches it (the #2784 small-account objection).
                        # Default 0.0 = OFF = today's behavior byte-identical.
                        try:
                            from config import (  # noqa: E501
                                EXPOSURE_CLAMP_MIN_NOTIONAL_USD as _clamp_floor,
                            )

                            _entry_floor = float(_clamp_floor)
                        except (
                            ImportError,
                            AttributeError,
                            TypeError,
                            ValueError,
                        ) as _exc:
                            # §5.6: a fallback substitution logs at WARNING,
                            # never silently (review apeldorn, PR #2961).
                            logging.warning(
                                "EXPOSURE_CLAMP_MIN_NOTIONAL_USD unreadable (%s) "  # noqa: E501
                                "— clamp entry floor OFF this sizing pass.",
                                _exc,
                            )
                            _entry_floor = 0.0  # fail-safe: floor OFF
                        if (
                            _entry_floor > 0.0
                            and current_price > 0
                            and 0.0
                            < (num_shares * current_price)
                            < _entry_floor  # noqa: E501
                        ):
                            logging.info(
                                "ADR-R12 clamp entry floor: dropping $%.2f "
                                "headroom residue (< $%.2f floor)",
                                num_shares * current_price,
                                _entry_floor,
                            )
                            num_shares = 0.0
                            _note("zero_reason", "exposure_clamp_entry_floor")
                except Exception as e:
                    # BUG-AI-105: fail CLOSED, not open. If the aggregate
                    # exposure cannot be verified (e.g. the broker positions
                    # query fails), do NOT silently skip the cap and let the
                    # order breach MAX_TOTAL_EXPOSURE_PCT - add no new exposure
                    # this sizing pass (CLAUDE.md 5.6).
                    logging.warning(
                        "Total-exposure cap check failed "
                        "(get_all_positions/valuation error): %s "
                        "- failing CLOSED: no new exposure added this sizing pass.",  # noqa: E501
                        e,
                        exc_info=True,
                    )
                    num_shares = 0.0
                    _note("zero_reason", "exposure_check_failed")
        except ImportError as _e:
            # CLAUDE.md §5.6: config is a core module — an ImportError here is a real anomaly.  # noqa: E501
            # The aggregate MAX_TOTAL_EXPOSURE_PCT cap is skipped this pass (per-order cash/max-%  # noqa: E501
            # caps above still apply); log rather than swallow silently.
            logging.warning(
                "config import failed (%s) — MAX_TOTAL_EXPOSURE_PCT aggregate cap NOT applied "  # noqa: E501
                "this sizing pass; per-order caps still apply.",
                _e,
            )

        # 5c. KELLY FRACTION CAP (optional): scale position size to avoid over-betting in hot streaks  # noqa: E501
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
        # A single order's notional (qty × price) must never exceed COMPLIANCE_MAX_ORDER_VALUE  # noqa: E501
        # (default 10,000 EUR; ESMA Position-Limit Guidelines / MiFID II Art. 57) — the SAME hard  # noqa: E501
        # limit the ComplianceGuardian enforces post-sizing (core/compliance.py _check_risk_limits:  # noqa: E501
        # ``value > max_order_value``). Without capping here, a risk-sized order above the limit is  # noqa: E501
        # built and then HARD-BLOCKED downstream → NO trade ever executes (observed: every desktop  # noqa: E501
        # BUY 🛡️ BLOCKED "Order exceeds Max Order Value"). Sizing to fit lets the order pass.  # noqa: E501
        # Applied LAST (after the multiplicative Kelly step) so nothing can re-inflate it.  # noqa: E501
        if current_price > 0:
            try:
                # module-level read (same pattern as the MAX_TOTAL_EXPOSURE_PCT / KELLY caps  # noqa: E501
                # above); in practice identical to compliance.py's get_config() value.  # noqa: E501
                _max_order_value = float(
                    getattr(
                        __import__(
                            "config", fromlist=["COMPLIANCE_MAX_ORDER_VALUE"]
                        ),  # noqa: E501
                        "COMPLIANCE_MAX_ORDER_VALUE",
                        0.0,
                    )
                    or 0.0
                )
            except Exception as _e:
                # CLAUDE.md §5.6: a fallback that changes money behaviour MUST log at WARNING.  # noqa: E501
                # 0.0 disables the sizing-time compliance cap (ComplianceGuardian still HARD-blocks  # noqa: E501
                # downstream); surface that the cap went inactive this pass.
                logging.warning(
                    "COMPLIANCE_MAX_ORDER_VALUE unreadable (%s) — per-order compliance size-cap "  # noqa: E501
                    "NOT applied this sizing pass (ComplianceGuardian still enforces downstream).",  # noqa: E501
                    _e,
                )
                _max_order_value = 0.0
            if _max_order_value > 0:
                # 0.9999 = a hair of headroom: the Guardian rejects on a STRICT ``>``, so the  # noqa: E501
                # capped notional must stay just UNDER the limit (float / fee tolerance).  # noqa: E501
                max_shares_by_compliance = (
                    _max_order_value * 0.9999
                ) / current_price  # noqa: E501
                if num_shares > max_shares_by_compliance:
                    num_shares = max_shares_by_compliance
                    _note("binding_limit", "compliance_order_value")

        # ADR-R10: Mindestpositionswert = $1 (Alpaca Fractional Shares Minimum)
        # Basis: Alpaca API-Dokumentation — kleinste handelbare Einheit bei Fractional Shares  # noqa: E501
        # Begründung: Unter $1 würde Alpaca die Order ablehnen (API Error 422);  # noqa: E501
        # 0.001-Shares-Fallback greift bei current_price=0 (Datenfehler) als Sicherheitsnetz.  # noqa: E501
        if current_price > 0:
            min_fractional_shares = 1.0 / current_price
            if num_shares < min_fractional_shares:
                # FUNNEL EXIT (#2811): every upstream clamp leaves through here. Only  # noqa: E501
                # claim the label when no clamp recorded a cause — otherwise this exit  # noqa: E501
                # would rename a cash/exposure zero to "below_order_floor".
                if (
                    sizing_trace is not None and "zero_reason" not in sizing_trace
                ):  # noqa: E501
                    _note("zero_reason", "below_order_floor")
                return 0.0
        elif (
            num_shares < 0.001
        ):  # Fallback: Datenfehler-Schutz (current_price nicht verfügbar)
            if sizing_trace is not None and "zero_reason" not in sizing_trace:
                _note("zero_reason", "invalid_price")
            return 0.0

        # Round to 6 decimal places (Alpaca supports up to 9)
        num_shares = round(num_shares, 6)

        # If fractional not allowed, convert to int
        if not allow_fractional:
            num_shares = int(num_shares)
            if num_shares < 1:
                if (
                    sizing_trace is not None and "zero_reason" not in sizing_trace
                ):  # noqa: E501
                    _note("zero_reason", "below_one_whole_share")
                return 0.0

        # ADR-R11 (revised 2026-07-27): Dust-Filter Threshold = MIN_ORDER_VALUE_USD (default $1).  # noqa: E501
        # Basis: Alpaca supports fractional/notional orders at $0 commission — the old $50 rationale  # noqa: E501
        # (spread + commission drag on sub-$50 orders) is void for the liquid trading universe. $1 is  # noqa: E501
        # Alpaca's real notional minimum (orders < $1 → API 422; also guarded at :858-861). A flat $50  # noqa: E501
        # zeroed EVERY order on a funded small account (observed live: €220 account, all BUYs blocked).  # noqa: E501
        # DUST-FILTER (EXC-1): Block nominal values < MIN_ORDER_VALUE_USD
        if current_price > 0 and num_shares > 0:
            nominal_value = num_shares * current_price
            try:
                from config import MIN_ORDER_VALUE_USD

                min_value = float(MIN_ORDER_VALUE_USD)
            except (ImportError, AttributeError):
                min_value = 1.0  # Fail-safe = Alpaca's $1 minimum (config defines it explicitly)  # noqa: E501

            if nominal_value < min_value:
                logging.info(
                    f"Dust-Filter: Rejected trade. Nominal value ${nominal_value:.2f} "  # noqa: E501
                    f"is below minimum of ${min_value:.2f}"
                )
                if (
                    sizing_trace is not None and "zero_reason" not in sizing_trace
                ):  # noqa: E501
                    _note("zero_reason", "below_order_floor")
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
        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===  # noqa: E501
        from core.kill_switch import kill_switch

        if self.trading_halted or kill_switch.is_halted(self.user_id):
            return (
                False,
                "System or User HALTED by Kill Switch / Risk Manager",
                {},
            )  # noqa: E501

        # #2980 (ADR-R09): fail-closed VIX gate. When the volatility gauge is
        # UNKNOWN, protective rules keyed on `vix_gt` can no longer fire (the old  # noqa: E501
        # 0.0 default silently disarmed every "block buys when VIX > N" rule).
        # We therefore block NEW BUYS outright while VIX is unconfirmed — but
        # SELL / de-risk paths stay open ("only reduce, never enlarge"): a book
        # must always be reducible even when it cannot be grown.
        _vix_value, vix_confirmed, _vix_source = resolve_vix(market_data)
        if not vix_confirmed and side.lower() == "buy":
            logging.warning(
                "[VIX] evaluate_new_trade(%s): VIX unconfirmed — new BUY blocked "  # noqa: E501
                "(fail-closed §5.6). SELL / de-risk unaffected.",
                symbol,
            )
            return (
                False,
                "Blocked (fail-closed, §5.6): VIX unconfirmed — new buys blocked",  # noqa: E501
                {},
            )
        # For a confirmed VIX use the real value in the rule loop; for an
        # UNCONFIRMED SELL keep the benign 0.0 read so the fail-closed sentinel
        # (45.0) does not NEWLY trip a `vix_gt` block on a de-risk order.
        loop_vix = _vix_value if vix_confirmed else 0.0

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
                    if (
                        trigger.get("side") and trigger["side"].lower() != side.lower()
                    ):  # noqa: E501
                        rule_matches = False
                    if trigger.get("strategy") and trigger[
                        "strategy"
                    ] not in market_data.get("strategy_name", ""):
                        rule_matches = False

                    # VIX Checks — #2980: use the resolver-provided value
                    # (confirmed real VIX, or benign 0.0 for an unconfirmed SELL;  # noqa: E501
                    # unconfirmed BUYs already returned blocked above).
                    current_vix = loop_vix
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
                    # abort evaluation of the rest — but it must NOT be silent  # noqa: E501
                    # (#1236, CLAUDE.md §5.6).
                    logging.warning(
                        "AI rule evaluation failed (rule=%s): %s — skipping this rule",  # noqa: E501
                        rule.get("id", rule.get("reason", "<unknown>")),
                        e,
                        exc_info=True,
                    )

        return True, "Approved", action_mods
