"""Typisierte Vertraege an den Uebergaben der Value Chain (#3378, Epic #3366).

Die Value-Chain-Dokumente beschreiben seit langem Uebergabeformate zwischen den Stufen;
im Code existierte keines davon. Dieses Paket beginnt mit den beiden Uebergaben, an
denen heute Kapital ohne nachweisbare Entscheidung bewegt wird:

* ``OrderIntent``        — H2, VC-2 -> VC-4: was soll bewegt werden, aufgrund welcher Entscheidung
* ``ComplianceDecision`` — H3, VC-4 -> VC-3: was hat die Pruefung entschieden, mit Grund-Code

* ``FillEvent``            — H4, VC-3 -> VC-5: was hat der Broker tatsaechlich ausgefuehrt
* ``ReconciliationRecord`` — H4, VC-3 -> VC-5: was hat ein Abgleichlauf verglichen und gefunden

Weitere folgen in eigenen Sub-Issues: ``SignalCandidate`` in #3404 (ARC-E5).

**Editionsneutral.** Nichts hier liest Konfiguration, kennt einen Broker-Typ oder trifft
eine Enterprise-Annahme — das ist per Test festgehalten (BORA).
"""

from core.contracts.compliance_decision import ComplianceDecision
from core.contracts.fill_event import FillEvent, Side
from core.contracts.order_intent import ExitKind, IntentKind, OrderIntent
from core.contracts.reason_code import ReasonCode
from core.contracts.reconciliation_record import (
    BreakKind,
    ReconciliationBreak,
    ReconciliationRecord,
)

__all__ = [
    "BreakKind",
    "ComplianceDecision",
    "ExitKind",
    "FillEvent",
    "IntentKind",
    "OrderIntent",
    "ReasonCode",
    "ReconciliationBreak",
    "ReconciliationRecord",
    "Side",
]
