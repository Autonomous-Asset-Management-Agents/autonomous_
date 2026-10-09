# core/engine/bemessung_helfer.py
# #4232 (ARC-E6 H-1c) — Bemessungs-Helfer des Kaufs, wörtlich aus order_executor.py
"""Bemessungs-Helfer — Earnings-Veto, Top-up-Deckel, Regime-Drossel (#4232, H-1c).

Wörtlich aus ``order_executor.py`` umgezogen, Schnitt-Entscheidung #4183
(``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1c). Der Kern
re-exportiert jeden Namen; Leser und Patches auf ``order_executor.<name>`` treffen
dasselbe Objekt (Entscheidung §3, Weg b).

Dieses Modul importiert den Kern nicht, auch nicht funktionslokal: Keine der drei
Funktionen liest einen Namen, der nur am Kern gepatcht wird.
"""

import logging
from datetime import timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

# Module logger for NEW code (review #3480 FINDING-02); the legacy call sites in this
# file still use the root logger and are out of scope here.
logger = logging.getLogger(__name__)


def _earnings_guard_veto(symbol: str, action: str) -> Optional[Tuple[str, str]]:
    """#3349 — shared Earnings-Proximity-Guard seam for BOTH money paths.

    Returns ``(outcome_tag, reason)`` when a NEW BUY must be skipped, else ``None``.
    Dark default (``EARNINGS_GUARD_ENABLED=False``) ⇒ ``None`` without reading the
    cache. ``now`` comes from the ENGINE clock (sim-safe). SELL / risk exits are never
    looked at. Any error ⇒ fail-open (the BUY proceeds), logged at WARNING.
    """
    if action != "BUY":
        return None
    try:
        import config as _cfg

        cfg = _cfg.get_config()
        if not getattr(cfg, "EARNINGS_GUARD_ENABLED", False):
            return None
        from core.engine.earnings_guard import earnings_guard_block
        from core.sim.clock import engine_now

        return earnings_guard_block(
            symbol,
            engine_now(timezone.utc).date(),
            int(getattr(cfg, "EARNINGS_GUARD_POST_DAYS", 2)),
            int(getattr(cfg, "EARNINGS_GUARD_PRE_DAYS", 0)),
        )
    except Exception as exc:  # noqa: BLE001 — the guard never breaks the order path
        logger.warning(
            "[%s] EarningsGuard inert (%s) — BUY proceeds.",
            symbol,
            exc,
            exc_info=True,
        )
        return None


def _topup_gap_capped_qty(
    pm: Any,
    symbol: str,
    qty: float,
    price: float,
    context: Any,
    sizing_trace: Optional[Dict[str, Any]] = None,
) -> float:
    """#3963: cap a BUY for a HELD name at the gap to its top-up target.

    ADR-R18 (#3963): a top-up buys at most ``target - held`` (the same target the
    dead-band sees, regime factor included). Basis: the sizer prices every BUY as a
    FULL target position and never subtracts the holding (risk_manager clean-weight
    branch), so a released top-up overshot by up to one tranche (ABNB/MRK, 16.-18.09.).
    Rationale: one target authority for admission AND size. Applied AFTER the regime
    throttle (the gap already uses the throttled target, so the factor is not applied
    twice). A new name (not held), a missing PM or a non-PortfolioManager stand-in leaves
    ``qty`` untouched. Unconfirmed holdings => gap 0 => no buy (fail-closed).
    """
    if not qty or qty <= 0 or not price or price <= 0 or pm is None:
        return qty
    try:
        from core.portfolio_manager import PortfolioManager

        if not isinstance(pm, PortfolioManager):
            return qty
        opportunity = SimpleNamespace(
            symbol=symbol,
            model_confidence=float(getattr(context, "conviction_score", 0.5) or 0.5),
            forecast_vol=getattr(context, "forecast_vol", None),
            skew_percentile=getattr(context, "skew_percentile", None),
            vote_coverage=getattr(context, "vote_coverage", None),
        )
        gap_value = pm.topup_gap_value(opportunity)
    except Exception as exc:  # noqa: BLE001 — never break the order path
        logging.warning(
            "[%s] top-up gap unavailable (%s) — held name not topped up (#3963).",
            symbol,
            exc,
            exc_info=True,
        )
        return 0.0 if symbol in (getattr(pm, "_position_scores", {}) or {}) else qty
    if gap_value is None:
        return qty
    gap_qty = max(0.0, float(gap_value) / float(price))
    if gap_qty < qty:
        if sizing_trace is not None:
            try:
                sizing_trace["binding_limit"] = "topup_gap"
                sizing_trace["topup_gap_value"] = f"{float(gap_value):.2f}"
            except Exception:  # noqa: BLE001 — observation only, never alters the cap
                # Review #3981 POLICY-01: never swallowed silently (§5.6).
                logging.warning(
                    "[%s] topup_gap: sizing-trace audit entry not recorded — the cap "
                    "still applies (#3963).",
                    symbol,
                    exc_info=True,
                )
        logging.info(
            "[%s] top-up capped at the gap to target: %.4f -> %.4f shares ($%.2f) (#3963).",
            symbol,
            qty,
            gap_qty,
            float(gap_value),
        )
        return gap_qty
    return qty


def _regime_throttled_size(
    symbol: str, action: str, size: float, context: Any
) -> float:
    """#3361 (Epic #2963): scale a NEW BUY by the regime throttle factor.

    Dark default: ``REGIME_THROTTLE_ENABLED=False`` ⇒ returns ``size`` untouched before
    anything is imported ⇒ byte-identical. Only ever SHRINKS (factor in [0.1, 1.0]);
    never sells, never blocks, never touches SELL / risk exits. Reads the daily cache
    only — the order path never fetches. ``now`` is the ENGINE clock, so a replay never
    sees today's cache. Any error ⇒ the unthrottled size (fail-open, WARNING §5.6).
    """
    if action != "BUY" or not size or size <= 0:
        return size
    try:
        from core.engine.regime_signal import active_throttle_reading

        # One gate shared with the top-up dead-band (portfolio_manager): dark ⇒ None.
        reading = active_throttle_reading()
        if not reading:
            return size
        factor = float(reading.get("factor", 1.0))
        if factor >= 1.0:
            return size
        logging.warning(
            "[%s] RegimeThrottle: risk-off reading %.1f >= threshold %.1f (as of %s) — "
            "BUY size x%.2f.",
            symbol,
            reading.get("score") or 0.0,
            reading.get("threshold") or 0.0,
            reading.get("asof"),
            factor,
        )
        try:  # audit mirror — observation only, never alters the flow
            setattr(context, "regime_throttle_factor", factor)  # noqa: B010
        except Exception:  # noqa: BLE001
            pass
        return size * factor
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a side-feed must never break the order path
        logging.warning(
            "[%s] RegimeThrottle inert (%s) — BUY size unchanged.",
            symbol,
            exc,
            exc_info=True,
        )
        return size
