"""Abrechnung: Strategiewechsel, Tarif-Checkout und Lizenz-Aktivierung (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from pydantic import BaseModel


class SwapRequest(BaseModel):
    strategy_name: str
    shadow_mode: bool = False
    force: bool = (
        False  # Bypass Position Lock (Shadow-Mode empfohlen bei force=True)  # noqa: E501
    )


class CheckoutRequest(BaseModel):
    tier: str  # canonical Tier string, e.g. "PRO" / "PROFESSIONAL"


class ActivateRequest(BaseModel):
    token: str  # the signed Ed25519 license key the user pastes after checkout
