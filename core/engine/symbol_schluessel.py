# core/engine/symbol_schluessel.py
# #4244 (H-2c, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: flag-gesteuerte Zustandsschluessel je Symbol (SymbolEvalState).
# Zyklusfrei: kein Import aus core.engine.trading_loop (Entscheidung #4184 §2); der Kern
# importiert die fuenf Erzeuger wieder. Die Altstellen loggen weiter ueber den
# Root-Logger (Entscheidung §6 "Logger").

import logging

import config


def _regime_conditioner_state_keys(market_data) -> dict:
    """#1949 (RTR-2): flag-gated "vix"/"regime" values for the SymbolEvalState seam.

    Both are now DECLARED LangGraph channels (core/orchestration/graph.py) because
    langgraph==1.0.10 silently drops undeclared input keys — the old top-level "vix"
    the loop emitted never reached any agent. To keep the dark ship byte-identical,
    the producer emits None for BOTH channels while REGIME_CONDITIONER_ENABLED is
    OFF (default): agents then observe exactly the old dropped-key behaviour
    (VIXAwareRiskAgent keeps abstaining). ON → the per-cycle MarketRegimeModel
    output (monitor_loop → current_market_data) reaches RegimeDetectionAgent (and
    VIXAwareRiskAgent — plan #1949 §12.5, documented joint consumption). Scalars
    only (§5.9). Invalid config fails safe to OFF.
    """
    try:
        # config.get_config() (not the from-import) — the same patchable seam the
        # finance-core helpers use (CODING_POLICY §2.10).
        enabled = bool(
            getattr(config.get_config(), "REGIME_CONDITIONER_ENABLED", False)
        )
    except (TypeError, ValueError):
        enabled = False
    if not enabled or not market_data:
        return {"vix": None, "regime": None}
    return {"vix": market_data.get("vix"), "regime": market_data.get("regime")}


#: #2672: the five DECLARED position-context channels of SymbolEvalState.
_POSITION_CONTEXT_CHANNELS = (
    "in_position",
    "position_qty",
    "position_avg_price",
    "unrealized_pnl",
    "position_context_confirmed",
)


def _implied_vol_state_keys(symbol, spot) -> dict:
    """#3038 (TRD-10): flag-gated producer for the three implied-volatility channels.

    House pattern #1949 (``_regime_conditioner_state_keys``): OFF (default) emits
    None for ALL three, without network I/O — agents observe exactly today's
    dropped-key behaviour, byte-identical.

    Thin by design. The fetch, the day cache and the previous-session reference
    live in ``core/implied_vol``; this is only the seam that hangs them into the
    per-symbol state. Never raises: an external dependency in the trading path
    must not be able to stop a cycle. On any failure the channels stay None and
    VIXAwareRiskAgent abstains (AC-5/AC-8) — no vote instead of a guessed one.

    Measured cost (RESULTS_ABRUFKOSTEN.md): 0.15 s per symbol filtered, 4.5 s for
    30 candidates = 0.5 % of a 900 s cycle, and only on the first cycle of a day.
    """
    try:
        from core.implied_vol import implied_vol_state_keys

        return implied_vol_state_keys(symbol, spot)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.warning(
            "[ImpliedVol] producer failed for %s (%s) - channels stay None, "
            "VIXAwareRiskAgent abstains.",
            symbol,
            exc,
        )
        return {
            "implied_vol": None,
            "implied_vol_reference": None,
            "implied_vol_reference_date": None,
        }


def _risk_reversal_state_keys(symbol, spot) -> dict:
    """#3095 (b): flag-gated producer for the three risk-reversal channels.

    Same house pattern as ``_implied_vol_state_keys``: OFF (default) emits None for
    ALL three, without network I/O — byte-identical. Thin seam; fetch/cache/reference
    live in ``core/options_skew``. Never raises: on any failure the channels stay
    None and UpsideSkewAgent abstains (no vote instead of a guessed one).
    """
    try:
        from core.options_skew import options_skew_state_keys

        return options_skew_state_keys(symbol, spot)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.exception(
            "[OptionsSkew] producer failed for %s (%s) - channels stay None, "
            "UpsideSkewAgent abstains.",
            symbol,
            exc,
        )
        return {
            "risk_reversal": None,
            "risk_reversal_reference": None,
            "risk_reversal_reference_date": None,
        }


def _quality_state_keys(symbol, spot, as_of) -> dict:
    """#3275: flag-gated producer for the three composite-quality channels.

    Same house pattern as ``_implied_vol_state_keys`` / ``_risk_reversal_state_keys``:
    OFF (default) emits None for ALL three, without work — byte-identical. Thin seam;
    the composite math, store and previous-session reference live in
    ``core/quality_score``. Never raises: on any failure the channels stay None and
    QualityAgent abstains (no vote instead of a guessed one).

    ``as_of`` is the evaluation datetime (state["current_time"]); the composite is read
    from the PIT feed AS OF that date (no look-ahead), so unlike IV/RR this works under
    SIM without an external corpus.
    """
    try:
        from core.quality_score import quality_state_keys

        tag = as_of.date() if hasattr(as_of, "date") else as_of
        return quality_state_keys(symbol, spot, tag)
    except Exception as exc:  # noqa: BLE001 — the cycle must never die on this
        logging.warning(
            "[Quality] producer failed for %s (%s) - channels stay None, "
            "QualityAgent abstains.",
            symbol,
            exc,
        )
        return {
            "quality_score": None,
            "quality_reference": None,
            "quality_reference_date": None,
        }


def _position_context_state_keys(positions_by_symbol, confirmed, symbol) -> dict:
    """#2672: flag-gated producer for the five position-context channels.

    House pattern #1949 (``_regime_conditioner_state_keys``): the channels are
    DECLARED in SymbolEvalState (langgraph==1.0.10 drops undeclared input keys),
    so the dark ship emits None for ALL of them while
    ROUND_TABLE_POSITION_CONTEXT_ENABLED is OFF (default) — agents and runner
    observe exactly today's dropped-key behaviour, byte-identical.

    ON + confirmed book: real values (a symbol absent from the book is genuinely
    flat -> False/0.0). ON + UNCONFIRMED book: position fields stay None
    ("unknown") — fail-closed, never fabricate "flat" from a broker outage.
    Invalid config fails safe to OFF.
    """
    try:
        enabled = bool(
            getattr(config.get_config(), "ROUND_TABLE_POSITION_CONTEXT_ENABLED", False)
        )
    except Exception:  # noqa: BLE001 — broken config seam must fail safe to OFF
        enabled = False
    if not enabled:
        return dict.fromkeys(_POSITION_CONTEXT_CHANNELS)
    if not confirmed:
        return {
            "in_position": None,
            "position_qty": None,
            "position_avg_price": None,
            "unrealized_pnl": None,
            "position_context_confirmed": False,
        }
    entry = (positions_by_symbol or {}).get(symbol)
    if entry is None:
        return {
            "in_position": False,
            "position_qty": 0.0,
            "position_avg_price": 0.0,
            "unrealized_pnl": 0.0,
            "position_context_confirmed": True,
        }
    return {
        "in_position": float(entry.get("qty", 0.0) or 0.0) > 0.0,
        "position_qty": float(entry.get("qty", 0.0) or 0.0),
        "position_avg_price": float(entry.get("avg_entry", 0.0) or 0.0),
        "unrealized_pnl": float(entry.get("unrealized_pnl", 0.0) or 0.0),
        "position_context_confirmed": True,
    }
