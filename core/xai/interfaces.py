# core/xai/interfaces.py
# XAI-1 / XAI-T1 (#1330): the agent-core routing contract + edition-gated data read-seams.
# Modelled on IAuditLogger (core/round_table/senate_log.py:151). NOTE: IAuditLogger is
# WRITE-only — these READ seams are new (epic #569 Rev 2). Concrete impls land in XAI-T3..T6.
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class IDomainProvider(ABC):
    """Routing contract: the agent-core dispatches a request to exactly one of these.
    A concrete handler (XAI-T3..T6) wraps a data read-seam below and returns an answer.
    """

    @abstractmethod
    async def answer(self, request: Any) -> Any: ...


class ISenateLogReader(ABC):
    """Trading-History (Glass Box) READ seam — new; IAuditLogger is write-only.
    OSS reads the LocalJSONAuditLogger JSONL; Enterprise reads SenateProtocol/DB."""

    @abstractmethod
    async def read_decisions(
        self, *, symbol: Optional[str] = None, limit: int = 20
    ) -> list[dict]: ...


class ISpecialistReportSource(ABC):
    """Stock-Research seam — reads SpecialistReports (core/specialist/). Gated by RPAR-1."""

    @abstractmethod
    async def get_report(self, symbol: str) -> Optional[dict]: ...


class IExplainabilitySource(ABC):
    """Strategy/SHAP seam — Enterprise: Vertex-AI SHAP; OSS: degraded local explanation."""

    @abstractmethod
    async def get_feature_importance(self, decision_id: str) -> Optional[dict]: ...


class IFaqSource(ABC):
    """Support seam — OSS: static FAQ bundle; Enterprise: vector DB."""

    @abstractmethod
    async def search(self, query: str, *, top_k: int = 3) -> list[dict]: ...
