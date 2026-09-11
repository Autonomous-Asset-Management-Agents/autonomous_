"""#3132 — Portfoliostruktur: Grenzen klemmen und Aenderungen beschreiben.

Reine Funktionen ohne Seiteneffekte. Sie sitzen bewusst SERVERSEITIG: die
Schieberegler in der Console sind Bequemlichkeit, nicht die Absicherung — ein
direkter API-Aufruf muss dieselben Grenzen sehen.

**Warum 20 und nicht 25.** Der Kauf-Betrag pro Position ist der Equal-Weight-Slot
``1/N`` (``risk_manager.effective_max_positions()``). Er muss ``>=
MIN_POSITION_PERCENT`` (0,05) bleiben, sonst bindet der Min-Clamp und das Buch
kann sich nicht fuellen. Bei N = 25 waere ein Slot 4 % — die Einstellung waere
oberhalb von 20 wirkungslos. Herleitung: ``docs/6_runbooks/POSITION_BOOK_CAP_TUNING.md``.
Untergrenze 10: ``test_full_universe_trading.py::test_the_slot_count_never_shrinks_the_default_book``.

**Warum alles als Text herauskommt.** Der WORM-Eintrag geht auf dieselbe
SHA-256-Kette wie ``LiveEnableEvent``, und deren Vorlage muss byte-identisch
sein, wenn der JS-Verifizierer (``audit-chain.cjs``) sie nachrechnet. Ein Float
kann zwischen ``json.dumps`` und ``JSON.stringify`` abweichen und wuerde den
Live-Handel dauerhaft fail-closed setzen.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# Grenzen — jede mit einer Quelle, keine erfunden.
POSITIONS_BOUNDS: Tuple[int, int] = (10, 20)  # POSITION_BOOK_CAP_TUNING.md
HOLD_DAYS_BOUNDS: Tuple[float, float] = (5.0, 20.0)  # Vorgabe #3132
VOL_TARGET_BOUNDS: Tuple[float, float] = (0.001, 0.05)  # TradingControlsCard.tsx:22-23

# Der heutige Stand. Wer nichts anfasst, bekommt exakt dieses Verhalten.
DEFAULT_POSITIONS = 10
DEFAULT_HOLD_DAYS = 20.0
DEFAULT_VOL_TARGET = 0.015

# setup.json wird unter den EXAKTEN Engine-Flag-Namen geschrieben (Durchreichen, #2863).
ENGINE_KEYS = {
    "positions": "FULL_UNIVERSE_MAX_POSITIONS",
    "hold_days": "SMART_EXIT_MIN_HOLD_DAYS",
    "vol_target": "VOL_TARGET_DAILY_VOL",
}


def _num(value: Any, default: float) -> float:
    """Eine brauchbare Zahl oder der Default — wirft nie, raet nie.

    ``bool`` wird ausgeschlossen: ``True`` ist in Python eine 1 und waere hier
    eine stumme Fehlinterpretation eines offensichtlichen Aufruferfehlers.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    v = float(value)
    return default if not math.isfinite(v) else v


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def clamp_portfolio_shape(
    positions: Any = None,
    hold_days: Any = None,
    vol_target: Any = None,
) -> Dict[str, Any]:
    """Die drei Werte auf ihre zulaessigen Bereiche klemmen.

    Geklemmt, nicht abgelehnt: ein Wert ausserhalb des Bereichs ist eine
    Bedienungsfrage, kein Angriff. Unbrauchbare Eingaben (None, Text, NaN, bool)
    fallen auf den heutigen Default zurueck.
    """
    p_lo, p_hi = POSITIONS_BOUNDS
    h_lo, h_hi = HOLD_DAYS_BOUNDS
    v_lo, v_hi = VOL_TARGET_BOUNDS
    return {
        "positions": int(_clamp(int(_num(positions, DEFAULT_POSITIONS)), p_lo, p_hi)),
        "hold_days": float(_clamp(_num(hold_days, DEFAULT_HOLD_DAYS), h_lo, h_hi)),
        "vol_target": float(_clamp(_num(vol_target, DEFAULT_VOL_TARGET), v_lo, v_hi)),
    }


def _as_text(field: str, value: Any) -> str:
    """Einheitliche Textform — die Kette darf keine Float-Repraesentation sehen."""
    if field == "positions":
        return str(int(value))
    if field == "hold_days":
        return f"{float(value):.1f}"
    return f"{float(value):.4f}"


def describe_changes(
    current: Dict[str, Any], new: Dict[str, Any]
) -> List[Dict[str, str]]:
    """Nur die tatsaechlich veraenderten Werte, als reine Textfelder.

    Leere Liste heisst: nichts zu tun. Der Aufrufer schreibt dann KEINEN
    WORM-Eintrag — eine Kette voller Nicht-Aenderungen waere Rauschen, das die
    echten Entscheidungen verdeckt.
    """
    out: List[Dict[str, str]] = []
    for field in ("positions", "hold_days", "vol_target"):
        before, after = current.get(field), new.get(field)
        if before is None or after is None:
            continue
        b, a = _as_text(field, before), _as_text(field, after)
        if b != a:
            out.append({"key": ENGINE_KEYS[field], "from": b, "to": a})
    return out


def to_setup_state(shape: Dict[str, Any]) -> Dict[str, str]:
    """Die geklemmten Werte unter den Engine-Flag-Namen, als Text fuer setup.json."""
    return {
        ENGINE_KEYS[field]: _as_text(field, shape[field])
        for field in ("positions", "hold_days", "vol_target")
        if field in shape
    }


def current_shape(cfg: Optional[Any] = None) -> Dict[str, Any]:
    """Der aktuell wirksame Stand, zur Aufrufzeit gelesen (nie eingefroren).

    ``FULL_UNIVERSE_MAX_POSITIONS`` ist der wirksame Knopf, solange
    ``FULL_UNIVERSE_TRADING_ENABLED`` gesetzt ist (Default True) — nicht das
    feste ``MAX_POSITIONS``, das als test-erzwungene Untergrenze dient.
    """
    if cfg is None:
        try:
            from config import get_config

            cfg = get_config()
        except Exception:  # noqa: BLE001 — unlesbare Config => heutiger Stand
            cfg = None
    g = (
        (lambda name, d: getattr(cfg, name, d))
        if cfg is not None
        else (lambda _n, d: d)
    )
    return clamp_portfolio_shape(
        positions=g("FULL_UNIVERSE_MAX_POSITIONS", DEFAULT_POSITIONS),
        hold_days=g("SMART_EXIT_MIN_HOLD_DAYS", DEFAULT_HOLD_DAYS),
        vol_target=g("VOL_TARGET_DAILY_VOL", DEFAULT_VOL_TARGET),
    )
