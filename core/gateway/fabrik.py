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
from typing import Any, Callable, Optional

from core.contracts import ComplianceDecision
from core.gateway.order_gateway import OrderGateway


def record_gateway_decision(decision: ComplianceDecision) -> None:
    """Senke fuer die Entscheidungen des Gateways.

    Heute eine Logzeile — die dauerhafte, rueckfuehrbare Ablage entsteht mit dem
    ``FillEvent``-Vertrag und der Identitaetskette (#3386, #2783). Bis dahin ist die
    Zeile der Beleg, dass jede Order durch das Tor eine Entscheidung hat; ein stiller
    Pfad waere schlimmer als ein unvollstaendig abgelegter.
    """
    logging.info(
        "[Gateway] decision_id=%s approved=%s reason=%s halted=%s %s",
        decision.decision_id,
        decision.approved,
        decision.reason_code.value,
        decision.halted,
        decision.detail,
    )


def _halt_aus_kill_switch(user_id: Optional[str] = None) -> bool:
    from core.kill_switch import kill_switch

    return bool(kill_switch.is_halted(user_id))


def gateway_for(
    broker: Any,
    *,
    is_halted: Optional[Callable[..., bool]] = None,
    record: Optional[Callable[[ComplianceDecision], None]] = None,
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
        record=record or (lambda d: record_gateway_decision(d)),
    )
