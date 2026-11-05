"""Abgleich: Aufhebung der Abgleich-Sperre und Abgleich-Telemetrie nach dem Live⇄Paper-Wechsel (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ReconciliationReleaseRequest(BaseModel):
    """Koerper der Aufhebung. Nur ein Grund — der Urheber kommt nie aus dem Koerper."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class SwitchReconcileRequest(BaseModel):
    """#2277 INC-3 (§4b R4): the console's post-switch reconcile telemetry — turned into a
    ``health_reconcile`` span so a failed Live⇄Paper reconcile is reconstructable from the export.

    Every field is optional so the fail-open frontend post never 422s (a telemetry post must never
    block the switch)."""

    switch_id: Optional[str] = None
    attempts: int = 0
    settled_status: Optional[str] = None
    paper_trading: Optional[bool] = None
