# core/round_table/__init__.py
# Epic 2.5 — Round Table V2: Democratic Symbol Selection
# Verantwortlichkeit: Public API des round_table Pakets
#
# Exports: run_round_table (Haupt-Einstiegspunkt für LangGraph _run_strategy_node)
#          VotingAgent, VoteResult, ConsensusEngine, ComplianceGatekeeper, SenateProtocol

from core.round_table.agents import (
    DrawdownGuardAgent,
    LSTMSignalAgent,
    MomentumAgent,
    NewsSentimentAgent,
    PatternRecognitionAgent,
    RegimeDetectionAgent,
    RLConfidenceAgent,
    SpecialistAlphaAgent,
    VIXAwareRiskAgent,
    set_specialist_registry,
)
from core.round_table.base_agent import VoteResult, VotingAgent
from core.round_table.consensus import ConsensusEngine
from core.round_table.gatekeeper import ComplianceGatekeeper, GatekeeperDecision
from core.round_table.runner import run_round_table
from core.round_table.senate_log import SenateProtocol, SenateSession

__all__ = [
    "run_round_table",
    "VotingAgent",
    "VoteResult",
    "DrawdownGuardAgent",
    "SpecialistAlphaAgent",
    "RegimeDetectionAgent",
    "MomentumAgent",
    "VIXAwareRiskAgent",
    "LSTMSignalAgent",
    "RLConfidenceAgent",
    "NewsSentimentAgent",
    "PatternRecognitionAgent",
    "ComplianceGatekeeper",
    "GatekeeperDecision",
    "ConsensusEngine",
    "SenateProtocol",
    "SenateSession",
    "set_specialist_registry",
]
