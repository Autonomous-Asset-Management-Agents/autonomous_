"""Die eine Stelle, an der das ``OrderGateway`` gebaut wird (#3447, Schritt 3).

Bisher stand die Fabrik in ``core/engine/order_executor.py``. Der Strategiepfad
(``core/strategies/base.py``) darf ``core/engine/`` aber nicht importieren: Die Engine
importiert die Strategien (``core/engine/base.py``), und ``import core.engine.order_executor``
laedt 138 ``core``-Module, ``core.gateway`` 10. Also liegt die Fabrik jetzt beim Tor.

Der Executor baut sein Tor weiter ueber diese Fabrik, reicht aber **seine** Halt-Abfrage und
**seine** Senke herein — Tests ersetzen sie dort am Modul des Executors.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Mapping, Optional

from core.contracts import ComplianceDecision, ReasonCode
from core.contracts.verkaufsgrund import Verkaufsgrund, unbekannter_grund
from core.gateway.order_gateway import OrderGateway
from core.ports.audit_ablage_port import hole_ablage

# #4126 (Plan §9c, §11): Das Tor kennt die Ablage nur ueber den Port. Die Composition Root
# meldet sie im Port-Modul an; das Tor holt sie zur Aufrufzeit. Der Zaehler liegt hier, weil
# das Tor ``senate_log._bump_audit`` nicht importieren darf; ``/engine-diagnostics`` liest ihn.
_ablage_zaehler = {"nicht_verdrahtet": 0}
_nicht_verdrahtet_gemeldet = False


def get_ablage_zaehler() -> "dict[str, int]":
    return dict(_ablage_zaehler)


def reset_ablage_zaehler() -> None:
    global _nicht_verdrahtet_gemeldet
    _ablage_zaehler["nicht_verdrahtet"] = 0
    _nicht_verdrahtet_gemeldet = False
    with _GRUENDE_LOCK:
        _GRUENDE_ABGELEGT.clear()


def _ablage_daten(
    decision: ComplianceDecision, intent: Any, storno: Optional[Mapping[str, Any]]
) -> "dict[str, Any]":
    daten: "dict[str, Any]" = {
        "decision_id": decision.decision_id,
        "approved": decision.approved,
        "reason_code": decision.reason_code.value,
        "halted": decision.halted,
        "detail": decision.detail,
    }
    if intent is not None:
        daten.update(
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            intent_kind=intent.intent_kind,
            is_protective_exit=intent.is_protective_exit,
        )
    elif storno:
        daten.update(
            symbol=storno.get("symbol"), storno_order_id=storno.get("order_id")
        )
    return daten


def _an_die_ablage(
    decision: ComplianceDecision, intent: Any, storno: Optional[Mapping[str, Any]]
) -> None:
    """Gibt die Entscheidung an den Port. Eigene Funktion: Ihre WARNINGs sind keine
    ``[Gateway]``-Zeilen (das Sicherheitsnetz liest die Zeilen von ``record_gateway_decision``).
    """
    global _nicht_verdrahtet_gemeldet
    port = hole_ablage()
    if port is None:
        _ablage_zaehler["nicht_verdrahtet"] += 1
        if not _nicht_verdrahtet_gemeldet:
            _nicht_verdrahtet_gemeldet = True
            logging.warning(
                "[Gateway] Audit-Ablage nicht verdrahtet — Tor-Entscheidungen stehen nur in "
                "der Logzeile (#4126). Die Composition Root meldet sie beim Engine-Start an."
            )
        return
    try:
        port.lege_ab("gateway_decision", _ablage_daten(decision, intent, storno))
        if _ist_ausgefuehrter_verkauf(decision, intent) and _erster_grund(
            decision.decision_id
        ):
            port.lege_ab("sell_reason", _verkaufsgrund_daten(decision, intent))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — ein Ablage-Fehler darf das Tor nie stoeren
        logging.warning("[Gateway] Audit-Ablage fehlgeschlagen: %s", exc)


#: #4125 (Plan §13c): Wege mit festem ``intent_kind`` — die Klasse ist die Art selbst.
_KLASSE_AUS_ART = frozenset({"displacement", "panic", "breaker", "strategy_switch"})

# #4125 (Plan §13c, „genau ein Eintrag je Verkauf“): Ein Folgeversuch derselben Entscheidung —
# der Markt-Rest nach einem nicht gefuellten Limit-Exit — legt keinen zweiten Grund ab.
# Begrenzt: die juengsten 2000 Entscheidungen; aeltere kommen nicht mehr als Folgeversuch.
_GRUENDE_ABGELEGT: "deque[str]" = deque(maxlen=2000)
_GRUENDE_LOCK = threading.Lock()


def _erster_grund(decision_id: str) -> bool:
    with _GRUENDE_LOCK:
        if decision_id in _GRUENDE_ABGELEGT:
            return False
        _GRUENDE_ABGELEGT.append(decision_id)
        return True


def _ist_ausgefuehrter_verkauf(decision: ComplianceDecision, intent: Any) -> bool:
    """#4125 (Plan §13c): Verkauf, freigegeben, kein Systemfehler, kein Stop-Platzieren.

    Ein Broker-Stop mit ``stop_price`` ist erst ein Auftrag; verkauft wird bei seinem Fill
    (Teil 4). Eine Ablehnung oder ein gescheiterter Broker-Aufruf ist kein Verkauf; die
    Market-Order danach traegt dieselbe ``decision_id`` und legt genau einen Eintrag ab.
    """
    return (
        intent is not None
        and getattr(intent, "side", None) == "sell"
        and decision.approved
        and decision.reason_code is not ReasonCode.SYSTEM_ERROR
        and getattr(intent, "stop_price", None) is None
    )


def _verkaufsgrund_daten(decision: ComplianceDecision, intent: Any) -> "dict[str, Any]":
    """``sell_reason``: Klasse und Werte aus dem Intent, sonst aus der Art, sonst ``unknown``.

    Der Zeitpunkt steht nur, wenn der Entstehungsort ihn kennt; die Kettenzeile traegt ohnehin
    den Zeitstempel der Ablage (``ClockPort``, Plan §9d).
    """
    grund = getattr(intent, "verkaufsgrund", None)
    if grund is None and intent.intent_kind in _KLASSE_AUS_ART:
        grund = Verkaufsgrund(klasse=intent.intent_kind)
    if grund is None:
        grund = unbekannter_grund(
            f"Tor: {intent.symbol} {intent.intent_kind} decision_id={decision.decision_id}"
        )
    daten: "dict[str, Any]" = {
        **grund.werte,
        "decision_id": decision.decision_id,
        "klasse": grund.klasse,
        "symbol": intent.symbol,
        "qty": intent.qty,
        "approved": True,
    }
    if grund.zeitpunkt is not None:
        daten["zeitpunkt"] = grund.zeitpunkt.isoformat()
    return daten


def _order_angaben(intent: Any, storno: Optional[Mapping[str, Any]]) -> str:
    """Symbol, Seite, Menge und Art des Intents — bzw. Symbol und Order-ID eines Stornos."""
    if intent is not None:
        return (
            f"symbol={intent.symbol} side={intent.side} qty={intent.qty} "
            f"kind={intent.intent_kind}"
        )
    if storno:
        return f"symbol={storno.get('symbol')} storno={storno.get('order_id')}"
    return ""


def record_gateway_decision(
    decision: ComplianceDecision,
    *,
    intent: Any = None,
    storno: Optional[Mapping[str, Any]] = None,
) -> None:
    """Senke fuer die Entscheidungen des Gateways.

    Die Logzeile bleibt der Beleg, dass jede Order durch das Tor eine Entscheidung hat.
    #4126 (Plan §9c): Dazu legt die angemeldete Audit-Ablage jede Entscheidung dauerhaft ab
    (Hash-Kette und ``mifid_decision_log``); ohne Anmeldung zaehlt ``nicht_verdrahtet``.

    #4126 (Plan §10b): Das Tor reicht den Intent als Schluesselwort mit, ein Storno Symbol
    und Order-ID. Die Zeile nennt damit, WELCHE Order entschieden wurde.
    """
    # Genau fuenf Argumente: das Sicherheitsnetz (tests/unit/_risk_netz.py) liest sie
    # positionsweise. Die Order-Angaben stehen deshalb vor dem Detailtext im letzten.
    logging.info(
        "[Gateway] decision_id=%s approved=%s reason=%s halted=%s %s",
        decision.decision_id,
        decision.approved,
        decision.reason_code.value,
        decision.halted,
        f"{_order_angaben(intent, storno)} {decision.detail}".strip(),
    )
    _an_die_ablage(decision, intent, storno)


def _halt_aus_kill_switch(user_id: Optional[str] = None) -> bool:
    from core.kill_switch import kill_switch

    return bool(kill_switch.is_halted(user_id))


def gateway_for(
    broker: Any,
    *,
    is_halted: Optional[Callable[..., bool]] = None,
    record: Optional[Callable[..., None]] = None,
) -> OrderGateway:
    """Baut das Tor fuer einen Broker-Zugang.

    Ohne Angaben fragt es den vorhandenen Kill-Switch und schreibt in
    ``record_gateway_decision`` — beides **zur Aufrufzeit** nachgeschlagen, damit ein Test
    die Senke am Modul ersetzen kann. Sobald der ``BrokerPort`` aus #3398 steht, wird aus
    dieser Fabrik die Composition Root.
    """
    return OrderGateway(
        broker=broker,
        is_halted=is_halted or _halt_aus_kill_switch,
        record=record or (lambda d, **kw: record_gateway_decision(d, **kw)),
    )
