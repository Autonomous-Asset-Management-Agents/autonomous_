"""Einstellungen: Handelseinstellungen und Abweichungen vom Default am Live-Schalter (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SettingsDeviationItem(BaseModel):
    """One default-deviation the operator decided about — str-only (WORM verbatim)."""

    key: str = Field(min_length=1)
    current: str
    default: str


class SettingsDeviationAckRequest(BaseModel):
    """#3156 (UXC-1 S3) — the operator's keep/reset decision at the live switch."""

    decision: str = Field(min_length=1)
    deviations: List[SettingsDeviationItem] = Field(default_factory=list)
    nonce: str = Field(min_length=1)
    switch_id: Optional[str] = None


class SettingsDeviationAckResponse(BaseModel):
    success: bool
    decision: str
    detail: str


class TradingSettingsRequest(BaseModel):
    """#3155 (UXC-1 S2) — a deliberate settings change plus the operator's acknowledgement.

    ``settings`` holds partial registry keys (``core.trading_settings.REGISTRY``).
    Values are CLAMPED server-side, never rejected — but an UNKNOWN key (incl. any
    compliance guardrail like ``HARD_STOP_LOSS_PCT``) is a 422, never silently ignored.
    """

    settings: Dict[str, Any] = Field(default_factory=dict)
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class TradingSettingsResponse(BaseModel):
    success: bool
    applied: dict
    changes: list
    detail: str
