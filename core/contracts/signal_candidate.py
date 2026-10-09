from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AbstainReason(str, Enum):
    NO_DATA = "NO_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    DEPENDENCY_LOST = "DEPENDENCY_LOST"
    WARMUP = "WARMUP"
    DISABLED = "DISABLED"
    GUARD_VETO = "GUARD_VETO"


class SignalCandidate(BaseModel):
    agent_name: str
    symbol: str
    weight: float
    score: Optional[float] = None
    abstain_reason: Optional[AbstainReason] = None
    reasoning: str
    data_source: Optional[str] = None
    vetoed: bool = False

    @model_validator(mode="after")
    def validate_either_score_or_abstain(self) -> "SignalCandidate":
        has_score = self.score is not None
        has_abs = self.abstain_reason is not None

        if has_score and has_abs:
            raise ValueError("either score or abstain_reason, not both")

        if not has_score and not has_abs:
            raise ValueError("either score or abstain_reason must be set")

        return self
