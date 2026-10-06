"""Portfolio: Sektor-Grenzen (Vier-Augen) und Portfolio-Form (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from typing import Optional

from pydantic import BaseModel, Field


class PortfolioConstraintsProposeRequest(BaseModel):
    """Manual propose path (the EOD assessment of #2652 stages its own pending)."""

    caps: dict
    actor: str = Field(min_length=1)


class PortfolioConstraintsApproveRequest(BaseModel):
    token: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class PortfolioShapeRequest(BaseModel):
    """#3132 — a deliberate portfolio-structure change plus the operator's acknowledgement.

    Values are CLAMPED server-side, never rejected: a value outside the range is an
    operating question, not an attack. The console's sliders are convenience — a direct API
    call must see the same bounds (``core.portfolio_shape``).
    """

    positions: Optional[float] = None
    hold_days: Optional[float] = None
    vol_target: Optional[float] = None
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class PortfolioShapeResponse(BaseModel):
    success: bool
    applied: dict
    changes: list
    detail: str
