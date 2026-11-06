"""Wirkungsanzeige der Bemessung (#5005, Epic #4983): Modellrechnung vor dem Kauf.

Ruft den echten Sizer und liest dessen Spur, statt die Bemessung nachzurechnen: Anzeige und
Sizer sollen nicht auseinanderlaufen. Eine Modellrechnung ohne Einzeltitel-Signale: kein Vol-,
Skew- oder Coverage-Eingang, darum zeigt ``b`` genau 1/N und ``off`` das Minimum.

Nur lesend: ``get_account`` hier und ``get_all_positions`` im Exposure-Deckel, sonst kein
Broker-Aufruf. Die Plätze zählt ``effective_free_slots`` aus dem PM-Stand wie im Orderpfad
(``core/engine/signal_uebergabe.py``). Keine Order, kein Eintrag auf der Audit-Kette.

Ein unbekannter Kontostand wirft (die Route antwortet neutral): Cash 0 als Ersatz erschiene
als bindender Deckel ``cash``, eine erfundene Aussage. Zugriff auf ``core.risk_manager`` über
``_rm``, wie im Sizer selbst. Plan:
``docs/5005-wirkungsanzeige-zeigt-ziel-cash-je-platz-und-bin/implementation_plan.md``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import config
from core import risk_manager as _rm

#: Alle Deckel rechnen in Dollar, der Preis kürzt sich heraus; er muss nur > 0 sein.
REFERENZPREIS = 100.0
#: Ohne Einzeltitel kein ATR. Multiplikator 0 schaltet ``max_loss_per_trade`` ab; der hängt am
#: Stop-Abstand des einzelnen Titels. Der ATR muss nur die Eingangsprüfung des Sizers passieren.
OHNE_STOP_MULTIPLIKATOR = 0.0
NEUTRALER_ATR = 1.0
#: Unterste Stufe der VIX-Leiter (ADR-R08). Wirkt nur im ATR-Rückfall, den die Vorschau nicht
#: anzeigt; steht hier, damit ``resolve_vix`` nicht je Anzeige fail-closed warnt.
NEUTRALER_VIX = 18.0


def bemessung_vorschau(rm: Any, pm: Any) -> Dict[str, Any]:
    """Was eine neue Position bekäme und was sie gerade begrenzt.

    ``available: False`` ohne RiskManager oder Broker, bei einem Halt und im ATR-Rückfall
    (``ENABLE_DYNAMIC_SIZING`` aus), der ohne Einzeltitel-ATR nichts aussagt.
    """
    client = getattr(rm, "client", None)
    if client is None:
        return {"available": False}
    cash = float(client.get_account().cash)
    if not math.isfinite(cash):
        raise ValueError(f"Kontostand nicht endlich: {cash!r}")

    plaetze = _rm.effective_free_slots(
        len(getattr(pm, "_position_scores", {}) or {}),
        positions_confirmed=getattr(pm, "_last_refresh_ok", True),
    )
    spur: Dict[str, Any] = {}
    menge = rm.calculate_position_size(
        stop_loss_atr_multiplier=OHNE_STOP_MULTIPLIKATOR,
        atr=NEUTRALER_ATR,
        market_data={"vix": NEUTRALER_VIX},
        num_stocks_in_strategy=plaetze,
        current_price=REFERENZPREIS,
        account_cash=cash,
        allow_fractional=True,
        sizing_trace=spur,
    )
    if "sizing_mode" not in spur:
        return {"available": False, "zero_reason": spur.get("zero_reason")}

    gewicht = float(spur["sizing_target_weight"])
    return {
        "available": True,
        "sizing_mode": spur["sizing_mode"],
        "target_weight": gewicht,
        "target_value": gewicht * rm.total_capital,
        "order_value": float(menge) * REFERENZPREIS,
        # Dieselbe Teilung wie ``_step_cash``, dort ohne den Nachfrage-Anteil des Titels.
        "cash_per_slot": max(0.0, (cash - config.RISK_CASH_BUFFER_USD) / plaetze),
        "slots": plaetze,
        "binding_limit": spur.get("binding_limit"),
        "binding_cap_value": _als_zahl(spur.get("binding_cap_value")),
        "zero_reason": spur.get("zero_reason"),
    }


def _als_zahl(wert: Optional[str]) -> Optional[float]:
    return None if wert is None else float(wert)
