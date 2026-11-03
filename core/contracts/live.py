"""Scharfschalten: Art.-14-Freigabe und -Abschaltung des Live-Handels (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from typing import Optional

from pydantic import BaseModel, Field


class LiveEnableRequest(BaseModel):
    """Operator's deliberate Art.-14 acknowledgement + a unique nonce (replay-distinct)."""  # noqa: E501

    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class LiveEnableResponse(BaseModel):
    success: bool
    action: str
    detail: Optional[str] = None
