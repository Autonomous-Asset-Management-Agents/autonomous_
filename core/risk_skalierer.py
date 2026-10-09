"""#4271 (H-3i) — Skalierer und Audit-Spiegel der Risikoverwaltung.

Schnitt-Entscheidung #4185: ``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3,
Abschnitt H-3i. Wortgleich aus ``core/risk_manager.py`` gezogen: der fail-closed VIX-Pfad
(ADR-R09, ``resolve_vix``), die VIX-Leiter (ADR-R08), die Größenfaktoren für Vol-Targeting,
Skew und Coverage und die beiden MiFID-II-Audit-Spiegel.

Reines Modul: kein ``self``, kein Import aus ``core.risk_*``. Der Kern exportiert jeden Namen
wieder (``from core.risk_skalierer import …``); Importeure von außen und die ``_rm.``-Lesestellen
in ``risk_bemessung.py`` und ``risk_vorpruefung.py`` lösen auf dasselbe Funktionsobjekt auf.
Patch-Ziel am Modulobjekt ``core.risk_manager`` ist keiner dieser Namen: Ein Patch dort träfe
die Aufrufe in diesem Modul nicht (``test_risk_manager_patch_ziele.py``).
"""

import logging
import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

# ADR-R09: Missing/invalid VIX resolves FAIL-CLOSED (maximum de-risk / block new  # noqa: E501
# buys), NOT to a benign default. Basis: CODING_POLICY §5.6 + the #2672
# position-context provenance pattern. A blind volatility gauge during a feed
# outage must NEVER read as "calm" — the safe posture for a regulated money-path  # noqa: E501
# is to stop ADDING risk (EU AI Act Art. 14 safe-state, MiFID II RTS 6), while
# SELL / de-risk paths always stay open ("only reduce, never enlarge").
#
# ADR-R09: Fallback-Wert fuer unbestaetigten VIX (Fail-Closed) = 45.0
# Basis: Interne Risikoentscheidung (Fail-Safe-Architektur nach EU AI Act Art. 14 / MiFID II RTS 6).
# Begruendung: 45.0 lands in the ADR-R08 crash band (> 40), i.e. the maximum de-risk. Documented single source of truth so the three VIX consumers cannot drift again (#2980).
# Jaehrliche Pruefung.
VIX_FAILCLOSED_SENTINEL = 45.0

# Mirrors the "[position context UNCONFIRMED] " marker (runner.py, #2672): a
# self-describing audit prefix stamped on any decision whose VIX was substituted,  # noqa: E501
# never observed. confirmed=False means "unknown", NEVER "calm".
VIX_UNCONFIRMED_MARKER = "[VIX UNCONFIRMED] "


def resolve_vix(
    market_data: Optional[Dict[str, Any]],
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


def vol_targeting_scaler(
    forecast_vol: Optional[float], log_missing: bool = True
) -> float:
    """#1953 TRD-2: config-gated inverse-vol risk-parity multiplier (dark feature).  # noqa: E501

    Returns EXACTLY 1.0 unless ``VOL_TARGETING_SIZING_ENABLED`` is set (DEFAULT ON —
    owner waiver 2026-08-14; OFF => the vol-blind behaviour, BORA; #3261). With
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

    scaler = vol_size_scaler_from_forecast(forecast_vol, target, lo, hi)
    # #3619: VIX_SIZE_INFLUENCE (registry, 0..1, ships 1.0 = byte-identical) blends
    # the vol-targeting effect linearly toward 1.0: 0 = every name 1/N, 1 = full
    # effect. Applied HERE — the one place both the sizer and the audit mirror read
    # (applied == logged). Never a direction effect; the caps below still apply.
    try:
        influence = float(getattr(_cfg, "VIX_SIZE_INFLUENCE", 1.0))
    except (TypeError, ValueError):
        logging.warning(
            "VIX_SIZE_INFLUENCE unreadable -> full vol-targeting effect (1.0)",
            exc_info=True,
        )
        influence = 1.0
    influence = max(0.0, min(1.0, influence))
    return 1.0 + influence * (scaler - 1.0)


def skew_size_tilt(
    rr_percentile: Optional[float], log_missing: bool = True
) -> float:  # noqa: E501
    """#3199: config-gated bounded size tilt from the 25Δ RR skew percentile.

    Returns EXACTLY 1.0 unless ``SKEW_SIZE_TILT_ENABLED`` is set (default OFF =>  # noqa: E501
    byte-identical, BORA) AND ``SKEW_SIZE_TILT_CAP`` > 0. With the flag ON a valid  # noqa: E501
    percentile ``p`` ∈ [0, 1] (bullish 25Δ risk-reversal upside skew high, crash  # noqa: E501
    skew low) becomes a bounded multiplicative factor::

        tilt = clip(1 + 2*cap*(p - 0.5), 1 - cap, 1 + cap)

    p=0.5 (neutral) -> 1.0; p=1 -> 1+cap; p=0 -> 1-cap. This mirrors the #1953
    ``vol_targeting_scaler`` (IV LEVEL -> size) around the IV SKEW dimension and,  # noqa: E501
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
    """#3210: config-gated prudence size discount from directional-vote coverage.  # noqa: E501

    Returns EXACTLY 1.0 unless ``COVERAGE_SIZING_STRENGTH`` (lambda) > 0 (default  # noqa: E501
    0.0 => byte-identical, BORA). With lambda > 0 a coverage fraction ``c`` ∈ (0, 1]  # noqa: E501
    — the share of the ARMED directional vote weight that actually voted this round  # noqa: E501
    — becomes a bounded, CONCAVE, WEAKENED discount::

        discount = 1 - lambda * (1 - sqrt(c))

    c=1 (full coverage) -> 1.0; thinner coverage -> < 1.0. ``lambda`` attenuates the  # noqa: E501
    theoretical sqrt because coverage is a SUSPECTED, not proven, risk indicator.  # noqa: E501
    Applied BEFORE every hard cap in the sizer (like the #1953 vol scaler / #3199  # noqa: E501
    skew tilt): it can only resize DOWN within the safety ceilings, never > 1.0. A  # noqa: E501
    missing/invalid coverage is a FAIL-OPEN no-op logged at WARNING (CLAUDE.md §5.6)  # noqa: E501
    — never a guessed discount. PURE (reads only config + arg) so the sizer and the  # noqa: E501
    MiFID II audit mirror call it independently and never drift.

    # ADR-R17: Coverage-Vorsichtsabschlag (Prudenz / Unsicherheit, KEIN Alpha).
    # Basis: weniger unabhängige Richtungsstimmen => größerer Standardfehler der  # noqa: E501
    #   Konsens-Schätzung (Condorcet-Jury / Bagging) => risiko-angemessen kleiner  # noqa: E501
    #   setzen (fractional-Kelly-Logik). KEIN Performance-Anspruch.
    # Rationale: empirisch heute nicht testbar (Datenzulänglichkeits-Gate, §9); der  # noqa: E501
    #   Abschlag wird über lambda abgeschwächt (default 0.0 = aus), weil Coverage ein  # noqa: E501
    #   VERMUTETER Indikator ist. Dokumentiert als ADR-R (nicht RTR-0-Alpha-Gate) in  # noqa: E501
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


def apply_sizing_mode_audit(
    context: Any, sizing_trace: Optional[Dict[str, Any]]
) -> None:
    """#3284 Punkt 3: mirror WHICH sizing procedure formed the size into the decision
    audit trail (MiFID II Art. 17 „applied == logged"), from the sizer's ``sizing_trace``.

    Writes ``DecisionContext.sizing_mode`` ("conviction" | "a" | "b") and
    ``sizing_target_weight`` (the target base weight before caps). Since clean-weight "b"
    is the default, ``risk_size_scaler`` alone is mode-ambiguous — this closes that gap.
    Additive and defensive: no trace / no context / broken sink ⇒ no-op, never raises into
    the order path. The DB writer persists both via decisions.sizing_mode/sizing_target_weight
    and drops them harmlessly on an un-migrated schema.
    """
    if context is None or not sizing_trace:
        return
    try:
        mode = sizing_trace.get("sizing_mode")
        if mode is not None:
            context.sizing_mode = str(mode)
        tw = sizing_trace.get("sizing_target_weight")
        if tw is not None:
            context.sizing_target_weight = float(tw)
        # #3619: WHICH cap bound the size and its dollar value (None = the target
        # set the size). Backing columns decisions.sizing_binding_limit/sizing_cap_value.
        bl = sizing_trace.get("binding_limit")
        if bl is not None:
            context.sizing_binding_limit = str(bl)
        cv = sizing_trace.get("binding_cap_value")
        if cv is not None:
            context.sizing_cap_value = float(cv)
    except Exception as exc:  # noqa: BLE001 — audit mirror must never break sizing
        logging.warning("sizing-mode audit mirror failed: %s", exc)
