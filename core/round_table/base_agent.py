# core/round_table/base_agent.py
# Epic 2.5 — Round Table V2: VoteResult Dataclass + VotingAgent Basisklasse
#
# Design-Entscheidung:
#   - @dataclass(slots=True): minimiert Speicher-Overhead bei 50*9=450 Objekten/Zyklus
#   - ABC: erzwingt async def vote() in allen Subklassen
#
# Policy: CODING_POLICY.md §11.5 TDD — Interface-First

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from core.redis_client import RedisClient
except ImportError:
    RedisClient = None

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VoteResult:
    """
    Leichter Container für das Abstimmungsergebnis eines einzelnen Agents.

    __slots__ (via @dataclass(slots=True)) minimiert den Serialisierungs-Overhead
    wenn 450 Objekte/Zyklus (50 Symbole × 9 Agents) durch den Redis Checkpointer
    fließen.

    Policy: score muss in [0.0, 1.0] liegen.
    """

    agent_name: str
    symbol: str
    score: float  # 0.0 = Strong Avoid, 1.0 = Strong Buy
    weight: float  # Agent-spezifisches Gewicht für ConsensusEngine
    reasoning: str  # MiFID II / EU AI Act Audit-Trail
    vetoed: bool = False  # vom ComplianceGatekeeper gesetzt


class VotingAgent(ABC):
    """
    Abstrakte Basisklasse für alle Round-Table Voting-Agents.

    Jeder Agent:
    - Ist vollständig async (kein blocking I/O in vote())
    - Arbeitet ausschließlich auf SymbolEvalState-Skalaren
    - Produziert einen score ∈ [0.0, 1.0] mit Reasoning für MiFID-Audit

    class weight: float muss in der Subklasse als Klassen-Attribut definiert werden.
    """

    default_weight: float = 0.0  # Override in Subklasse
    min_weight: float = 0.0  # Override in Subklasse
    max_weight: float = 100.0  # Override in Subklasse

    @property
    def weight(self) -> float:
        """
        Dynamically fetch the agent's current weight.
        Tries to read 'agent_weights_v2' from Redis. If missing or invalid, falls back to default_weight.
        Always clamps the final weight to [min_weight, max_weight].
        """
        val = self.default_weight
        if RedisClient is not None:
            try:
                r = RedisClient.get_sync_redis()
                raw = r.hget("agent_weights_v2", self.__class__.__name__)
                if raw is not None:
                    val = float(raw)
            except Exception:
                pass

        return max(self.min_weight, min(self.max_weight, val))

    @abstractmethod
    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        """
        Bewertet ein Symbol und gibt ein VoteResult zurück.

        Args:
            state: SymbolEvalState mit OHLC-Skalaren (kein DataFrame erlaubt)

        Returns:
            VoteResult mit score ∈ [0.0, 1.0]
        """
        ...

    def _clamp(self, score: float) -> float:
        """Sicherstellen dass score in [0.0, 1.0] liegt."""
        return max(0.0, min(1.0, score))


class AsyncAIAgent(VotingAgent):
    """
    Basisklasse für ML-Agents mit blockierender PyTorch-Inferenz.
    Erzwingt die Ausführung in einem ThreadPoolExecutor, um den LangGraph-Event-Loop
    nicht zu blockieren.
    """

    @abstractmethod
    def _run_inference(self, state: "SymbolEvalState") -> VoteResult:
        """
        Synchronous inference logic (e.g. PyTorch forward pass).
        This will be executed in a separate thread.
        """
        ...

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        """
        Wraps the synchronous `_run_inference` method using asyncio.to_thread
        to avoid blocking the main event loop.
        """
        import asyncio

        return await asyncio.to_thread(self._run_inference, state)
