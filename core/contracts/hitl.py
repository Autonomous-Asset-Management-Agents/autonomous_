"""HITL: Freigabe-Warteschlange und Freigabe-Richtlinie (EU AI Act Art. 14) (#3825).

Die Modelle standen bis #3825 (ARC-E6 G-3) in ``core/engine/api_routes.py`` und sind
unveraendert hierher gewandert: Felder, Namen und Validierung gleich. Das
OpenAPI-Schema prueft ``tests/unit/test_contracts_ort.py`` gegen eine Momentaufnahme.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class HitlQueueItemDTO(BaseModel):
    """One order awaiting human approval (the pending-queue item the UI renders)."""  # noqa: E501

    approval_id: str
    user_id: str
    symbol: str
    action: str
    qty: float
    price: float
    conviction: float
    target_weight: float
    created_at: str


class HitlPendingResponse(BaseModel):
    items: List[HitlQueueItemDTO]


class HitlPolicyDTO(BaseModel):
    """The full HITL policy (GET response). ``HITL_ENABLED`` is shown read-only."""  # noqa: E501

    HITL_ENABLED: bool
    HITL_MAX_VALUE_PER_TRADE: float
    HITL_MAX_VALUE_PER_DAY: float
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    HITL_EXPIRY_SECONDS: int


class HitlPolicyUpdateDTO(BaseModel):
    """The runtime-adjustable limits (POST body). ``extra="forbid"`` ⇒ a POST that includes  # noqa: E501
    ``HITL_ENABLED`` (or any unknown key) is rejected with HTTP 422 — enabling HITL is the  # noqa: E501
    env+redeploy step (C2/M5), never an API toggle, so the flag is structurally unsettable.  # noqa: E501
    """

    model_config = ConfigDict(extra="forbid")

    HITL_MAX_VALUE_PER_TRADE: float = Field(ge=0)
    HITL_MAX_VALUE_PER_DAY: float = Field(ge=0)
    HITL_AUTONOMOUS_UNLIMITED: bool
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool
    # ge=1 .. le=86_400 (24h): an approval window must be bounded — a near-infinite expiry would  # noqa: E501
    # quietly defeat the "a human must act in bounded time" posture.
    HITL_EXPIRY_SECONDS: int = Field(ge=1, le=86_400)


class HitlApproveRequest(BaseModel):
    approval_id: str


class HitlRejectRequest(BaseModel):
    approval_id: str
    reason: Optional[str] = None


class HitlActionResponse(BaseModel):
    success: bool
    approval_id: Optional[str] = None
    detail: Optional[str] = None
