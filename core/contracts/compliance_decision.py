"""ComplianceDecision — der Vertrag an der Uebergabe H3 (VC-4 -> VC-3) (#3378, ARC-E1.2).

Heute ist die Entscheidung ein Wahrheitswert: ``core/compliance.py:244`` deklariert
``def check_order(self, order: Dict) -> bool``, und die Aufrufer werten genau das aus
(``order_executor.py:1555``, ``:2887``) — der Grund bleibt eine Logzeile. Damit gibt es
keinen Datensatz, den eine Order tragen koennte, und CH-2 aus #3366 hat keinen
Gegenstand.

Dieser Vertrag macht die Entscheidung zu einem Ding: mit ``decision_id`` zur
Rueckfuehrbarkeit, einem Grund-Code aus geschlossener Liste und dem Halt-Zustand.

``frozen=True``: Eine getroffene Entscheidung wird nicht nachtraeglich umgeschrieben.
"""

from pydantic import BaseModel, ConfigDict, Field

from core.contracts.reason_code import ReasonCode


class ComplianceDecision(BaseModel):
    """Was die Pruefung entschieden hat — und warum."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(min_length=1)
    approved: bool
    reason_code: ReasonCode

    # Freitext fuer Menschen. Ausdruecklich NICHT der Ort fuer maschinelle Auswertung —
    # dafuer ist der Grund-Code da. Die heutige Logzeile traegt den Symbolnamen mit;
    # hier bleibt sie erhalten, ohne die Auswertbarkeit zu beruehren.
    detail: str = ""

    # Der Halt-Zustand zum Zeitpunkt der Pruefung (CH-2).
    halted: bool
