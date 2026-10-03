import importlib
import os
from unittest.mock import patch

import pytest

import settings
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agents import DrawdownGuardAgent, RegimeDetectionAgent
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import ConsensusEngine

pytestmark = pytest.mark.vc4


def test_3404_document_defaults_are_true():
    cfg = settings.get_config()
    assert cfg.REGIME_CONDITIONER_ENABLED is True
    assert cfg.MOMENTUM_FALLBACK_ABSTAIN_ENABLED is True
    assert cfg.NEWS_SENTIMENT_NLP_ENABLED is True


@pytest.mark.asyncio
async def test_3404_distinguish_disabled_from_no_data():
    """
    TDD Step 2: Ein per Konfiguration stillgelegter Agent und ein Agent ohne
    Datenquelle erzeugen heute beide weight=0.0 und lassen sich maschinell
    nicht trennen. Dieser Test faellt fehl (rot), bis der Vertrag umgestellt ist.
    """
    agent = RegimeDetectionAgent()
    state_empty = {"symbol": "AAPL"}

    # 1. Disabled via config
    with patch.dict(os.environ, {"REGIME_DETECTION_AGENT_ENABLED": "false"}):
        importlib.reload(settings)
        res_disabled = await agent.vote(state_empty)

    # 2. Enabled but no data
    with patch.dict(os.environ, {"REGIME_DETECTION_AGENT_ENABLED": "true"}):
        importlib.reload(settings)
        res_nodata = await agent.vote(state_empty)

    # Restore settings
    importlib.reload(settings)

    # The crucial assertion: They must be distinguishable beyond free-text!
    # They should both be abstentions, but with different codes.
    assert hasattr(
        res_disabled, "abstain_reason"
    ), "Missing explicit abstain reason on disabled agent"
    assert hasattr(
        res_nodata, "abstain_reason"
    ), "Missing explicit abstain reason on no-data agent"

    assert res_disabled.abstain_reason != res_nodata.abstain_reason


@pytest.mark.asyncio
async def test_3404_drawdown_guard_no_fake_veto():
    """
    TDD Step 3: Ein ohlc-Dict ohne high und low durch den DrawdownGuardAgent.
    Behauptung: "es entsteht keine Bewertung und kein Veto aus eingesetzten Ersatzwerten".
    Heute rot.
    """
    agent = DrawdownGuardAgent()
    state_no_hl = {"symbol": "AAPL", "ohlc": {"close": 150.0}}  # Missing high/low

    with patch.dict(os.environ, {"DRAWDOWN_GUARD_AGENT_ENABLED": "true"}), patch(
        "core.round_table.agents.get_global_registry", return_value=None
    ):
        importlib.reload(settings)
        res = await agent.vote(state_no_hl)

    importlib.reload(settings)

    # Es MUSS eine Enthaltung sein! (hat abstain_reason)
    assert hasattr(
        res, "abstain_reason"
    ), "DrawdownGuardAgent must explicitly abstain when high/low are missing"
    assert (
        getattr(res, "vetoed", False) is False
    ), "Must not veto based on fake high/low fallback"


def test_3404_consensus_ignores_abstain_signal_candidates():
    engine = ConsensusEngine()

    votes = [
        # Normal directional vote
        VoteResult(
            agent_name="AgentA", symbol="AAPL", score=0.8, weight=1.0, reasoning="A"
        ),
        # Abstaining SignalCandidate (weight > 0 shouldn't matter, it's an abstain!)
        SignalCandidate(
            agent_name="AgentB",
            symbol="AAPL",
            weight=1.0,
            reasoning="B",
            abstain_reason=AbstainReason.NO_DATA,
        ),
        # Directional SignalCandidate
        SignalCandidate(
            agent_name="AgentC", symbol="AAPL", score=0.2, weight=1.0, reasoning="C"
        ),
    ]

    # Expected: (0.8 * 1.0 + 0.2 * 1.0) / 2.0 = 0.5
    res = engine.aggregate(votes)
    assert res == 0.5, f"Expected 0.5, got {res}"


def test_3404_no_ohlc_fallback_literals():
    """
    TDD Step 9: Fitness-Test gegen Rueckfall.
    Kein ohlc.get(..., <Zahl>) mit stillem Ersatzwert im Entscheidungspfad.
    """
    import ast
    from pathlib import Path

    agent_file = (
        Path(__file__).parent.parent.parent / "core" / "round_table" / "agents.py"
    )
    with open(agent_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    violations = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):
            # Check for ohlc.get(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "ohlc"
                ):
                    if len(node.args) > 1:
                        # Second argument is the fallback
                        fallback = node.args[1]
                        if isinstance(fallback, ast.Constant):
                            # It's a literal fallback
                            violations.append(
                                f"Line {node.lineno}: ohlc.get({node.args[0].value!r}, {fallback.value!r})"
                            )
            self.generic_visit(node)

    Visitor().visit(tree)

    assert not violations, "Found silent fallbacks in ohlc.get():\n" + "\n".join(
        violations
    )
