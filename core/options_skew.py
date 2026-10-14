"""#3095 (b) — Options-Schiefe (25Δ Risk-Reversal) als DIREKTIONALES Aufwärtssignal.

Der IV-Betrag (VIXAware, #3038) ist richtungslos — er kann „erwartete Kurssteigerung"
nicht ausdrücken. Die **Schiefe** kann es: das 25-Delta-Risk-Reversal
``RR = IV(25Δ Call) − IV(25Δ Put)`` ist positiv, wenn der Optionsmarkt für
Aufwärts-Absicherung mehr zahlt als für Abwärts (bullische Nachfrage).

Dieses Modul enthält die **reine** Kennzahl-Logik (Auswahl + RR + Perzentil-Rang),
getrennt vom Alpaca-Kettenabruf (Step-2), damit sie ohne Netz testbar ist und
``implied_vol.py`` (ATM-Betrag) fokussiert bleibt (Decision: Option A, eigenes Modul).

Kontrakt-Form (editions-neutral, entkoppelt von Alpaca-Objekten):
    ``{"right": "C"|"P", "delta": float, "iv": float}``
Der Erzeuger (Step-2) mappt Kettensnaps (``snap.greeks.delta``,
``snap.implied_volatility``, Call/Put aus dem OCC-Symbol) auf diese Form.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

_TARGET_DELTA = 0.25


def _usable(c: dict) -> bool:
    try:
        iv = float(c.get("iv"))
        d = float(c.get("delta"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(iv) and iv > 0 and math.isfinite(d)


def select_25delta(
    contracts: Sequence[dict], target_delta: float = _TARGET_DELTA
) -> Optional[Tuple[float, float]]:
    """Return ``(call_iv, put_iv)`` of the ~25Δ wings, or None if a side is missing.

    The call closest to +target_delta and the put closest to −target_delta (by the
    absolute delta), among usable contracts (finite positive IV, finite delta).
    """
    calls = [c for c in contracts if _usable(c) and str(c.get("right")).upper() == "C"]
    puts = [c for c in contracts if _usable(c) and str(c.get("right")).upper() == "P"]
    if not calls or not puts:
        return None
    call = min(calls, key=lambda c: abs(abs(float(c["delta"])) - target_delta))
    put = min(puts, key=lambda c: abs(abs(float(c["delta"])) - target_delta))
    return float(call["iv"]), float(put["iv"])


def risk_reversal_from_chain(
    contracts: Sequence[dict], target_delta: float = _TARGET_DELTA
) -> Optional[float]:
    """25Δ Risk-Reversal ``IV(Call) − IV(Put)``. None when the wings can't be picked.

    Positive = the market pays up for UPSIDE (bullish skew); negative = downside fear.
    """
    wings = select_25delta(contracts, target_delta)
    if wings is None:
        return None
    call_iv, put_iv = wings
    rr = call_iv - put_iv
    return rr if math.isfinite(rr) else None


def rr_percentile(value: float, reference: Sequence[float]) -> Optional[float]:
    """Percentile rank of ``value`` in ``reference`` in [0,1] (fraction strictly below).

    None when the reference has fewer than 2 usable, distinct values. Mirrors the
    #3038 previous-day-reference convention (AC-7): rank against COMPLETED cycles.
    """
    vals: List[float] = [
        float(v) for v in reference if isinstance(v, (int, float)) and math.isfinite(v)
    ]
    if len(vals) < 2 or len(set(vals)) < 2:
        return None
    below = sum(1 for v in vals if v < value)
    return below / len(vals)


# --- Step-2: Erzeuger (Alpaca-Kettenabruf + State-Kanäle), gespiegelt an implied_vol ---
import datetime as _dt  # noqa: E402
import logging as _logging  # noqa: E402
import os as _os  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

import config  # noqa: E402

_logger = _logging.getLogger(__name__)

_CHANNELS = ("risk_reversal", "risk_reversal_reference", "risk_reversal_reference_date")
_LEER = dict.fromkeys(_CHANNELS)

# Wing band: OTM enough to reach the ~25Δ strikes (wider than the ATM band in
# implied_vol.py). Verfall wie beim ATM-Fetch (30-Tage-Fenster).
_WING_BAND = 0.30
_REST_MIN_TAGE = 20
_REST_MAX_TAGE = 45


def _enabled() -> bool:
    """#3095 (b): the single config seam (CODING_POLICY §2.10). Fail-safe OFF."""
    try:
        return bool(getattr(config.get_config(), "UPSIDE_SKEW_AGENT_ENABLED", False))
    except Exception as exc:  # noqa: BLE001 — a broken config must not switch on
        _logger.exception("OptionsSkew: Flag nicht lesbar (%s) — bleibt AUS.", exc)
        return False


def _sim_mode() -> bool:
    try:
        return bool(getattr(config.get_config(), "SIM_MODE", False))
    except Exception:  # noqa: BLE001
        return False


def _default_path() -> "_Path":
    base = _os.environ.get("AAA_USER_DATA_DIR", "").strip()
    return _Path(base) / "risk_reversal.json" if base else _Path("risk_reversal.json")


_STORE = None


def _default_store():
    """Reuse the value-agnostic ImpliedVolStore (JSON {date:{symbol:value}}) with a
    separate file — no store re-implementation, no Alembic (BORA)."""
    global _STORE
    if _STORE is None:
        from core.implied_vol import ImpliedVolStore

        _STORE = ImpliedVolStore(path=_default_path())
    return _STORE


def _default_client():
    from core.keychain import load_secrets_from_keychain

    load_secrets_from_keychain()
    from alpaca.data.historical.option import OptionHistoricalDataClient

    return OptionHistoricalDataClient(
        _os.environ.get("ALPACA_API_KEY"), _os.environ.get("ALPACA_SECRET_KEY")
    )


def _snap_to_contract(occ: str, snap) -> Optional[dict]:
    """Map one Alpaca chain snapshot to the pure contract dict. None if unusable.

    Right = the C/P char of the OCC symbol (ROOT + YYMMDD + C|P + strike8) at [-9];
    delta from ``snap.greeks.delta``; iv from ``snap.implied_volatility``.
    """
    iv = getattr(snap, "implied_volatility", None)
    greeks = getattr(snap, "greeks", None)
    delta = getattr(greeks, "delta", None) if greeks is not None else None
    if iv is None or delta is None:
        return None
    try:
        right = str(occ)[-9].upper()
    except (IndexError, TypeError):
        return None
    if right not in ("C", "P"):
        return None
    return {"right": right, "delta": delta, "iv": iv}


def fetch_risk_reversal(
    symbol: str, spot: float, tag: "_dt.date", client=None
) -> Optional[float]:
    """25Δ Risk-Reversal for a symbol from the Alpaca option chain. None on any gap.

    Fetches a WIDER strike band than the ATM fetch (to reach the wings), reads
    ``greeks.delta`` + ``implied_volatility`` per contract, maps to contract dicts
    and delegates to the pure ``risk_reversal_from_chain``. A failure → None (the
    agent then abstains, never guesses)."""
    if not spot or spot <= 0 or not math.isfinite(spot):
        return None
    try:
        from alpaca.data.requests import OptionChainRequest

        cl = client if client is not None else _default_client()
        kette = cl.get_option_chain(
            OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date_gte=tag + _dt.timedelta(days=_REST_MIN_TAGE),
                expiration_date_lte=tag + _dt.timedelta(days=_REST_MAX_TAGE),
                strike_price_gte=round(spot * (1 - _WING_BAND), 2),
                strike_price_lte=round(spot * (1 + _WING_BAND), 2),
            )
        )
    except Exception as exc:  # noqa: BLE001 — Ausfall ⇒ Enthaltung, kein Abbruch
        _logger.exception(
            "OptionsSkew: Kettenabruf für %s fehlgeschlagen (%s).", symbol, exc
        )
        return None

    contracts = []
    for occ, snap in (kette or {}).items():
        c = _snap_to_contract(occ, snap)
        if c is not None:
            contracts.append(c)
    return risk_reversal_from_chain(contracts)


def options_skew_state_keys(
    symbol: str, spot: float, tag: "Optional[_dt.date]" = None, store=None, client=None
) -> dict:
    """The three ``SymbolEvalState`` channels for one symbol (mirrors
    ``implied_vol.implied_vol_state_keys``). Flag OFF ⇒ all None and NO network.

    Sim mode has no risk-reversal corpus yet ⇒ all None (agent abstains) — honest,
    never a fabricated skew. The reference is the previous COMPLETED session (AC-7).
    """
    if not _enabled():
        return dict(_LEER)
    if _sim_mode():
        return dict(_LEER)
    tag = tag or _dt.date.today()
    st = store if store is not None else _default_store()
    rr = st.get(symbol, tag)
    if rr is None:
        rr = fetch_risk_reversal(symbol, spot, tag, client=client)
        if rr is not None:
            st.put(symbol, tag, rr)
    referenz, referenz_datum = st.reference(tag)
    return {
        "risk_reversal": rr,
        "risk_reversal_reference": referenz,
        "risk_reversal_reference_date": referenz_datum,
    }
