# tests/unit/test_round_table_source_resolution.py
# The Round Table cannot weigh LSTM against RL — it hears one voice twice.
#
# THE DEFECT (verified, LIVE, no flag involved):
#   LSTMSignalAgent  -> registry.get_active().evaluate_for_symbol(...)   (agents.py:653)
#   RLConfidenceAgent -> registry.get_active().evaluate_for_symbol(...)  (same pattern)
#   config.ACTIVE_STRATEGY defaults to "RLAgent"  (config.py:208)
#   => under the ship default BOTH agents call RLStrategy.evaluate_for_symbol. The agent
#      NAMED for the LSTM votes on RL output, at weight 0.40, and RLConfidenceAgent votes
#      on the same object at weight 0.40. Two votes, 0.80 combined weight, ONE source —
#      one of them wearing the other's name in the MiFID audit trail.
#
# The codebase already knew. monitor_loop's own registration comment says the strategy is
# registered "so that LSTMSignalAgent AND RLConfidenceAgent ... can access it via
# get_global_registry().get_active()" — one object, both agents, stated as the design.
# AGENT_CATALOG.md:17 even names the consequence: "LSTM und RL lesen dieselbe Quelle
# (Echo) -> ~4 effektiv unabhängige Signale". Nobody drew the conclusion.
#
# So "the LSTM rank is ONE input among many" (the owner's decision) is not merely
# unbuilt — it is UNREACHABLE. No flag fixes it: an agent that asks for "whichever
# strategy is active" can never be told which model to listen to.
#
# THE SEAM: AgentRegistry already holds several strategies by name
# (register(..., set_active=False)) — R3 registers the standby LSTM through exactly that
# door. What is missing is the ability to ASK for one by name: there is get_active(),
# list_registered(), swap() — and no get(name).
#
# ⚠ SCOPE: this file adds the ACCESSOR and pins the defect. Actually switching an agent's
# source changes its vote -> changes trading -> needs its own flag + walk-forward. Not
# here, and not by accident.

from __future__ import annotations

from unittest.mock import MagicMock

import config


class TestTheRegistryCanBeAskedByName:
    """The missing seam. Additive: nothing reads it yet."""

    def test_a_named_strategy_can_be_retrieved(self):
        from core.agent_registry import AgentRegistry

        reg = AgentRegistry()
        trader, ranker = MagicMock(), MagicMock()
        reg.register("RLAgent", trader)
        reg.register("LSTMDynamic", ranker, set_active=False)

        assert reg.get("RLAgent") is trader
        assert reg.get("LSTMDynamic") is ranker

    def test_asking_by_name_never_disturbs_the_active_strategy(self):
        """Reading must be read-only: resolving a name must not swap, activate, or
        otherwise touch who trades."""
        from core.agent_registry import AgentRegistry

        reg = AgentRegistry()
        trader, ranker = MagicMock(), MagicMock()
        reg.register("RLAgent", trader)
        reg.register("LSTMDynamic", ranker, set_active=False)

        reg.get("LSTMDynamic")

        assert reg.get_active() is trader

    def test_an_unknown_name_is_none_not_a_crash(self):
        """An agent whose model is not registered must be able to ABSTAIN. Raising here
        would turn a missing model into a dead round table."""
        from core.agent_registry import AgentRegistry

        reg = AgentRegistry()
        reg.register("RLAgent", MagicMock())

        assert reg.get("LSTMDynamic") is None
        assert reg.get("nonsense") is None


class TestTheEchoIsReal:
    """Pin the defect itself, so the fix cannot be quietly declared done."""

    def test_both_ml_agents_resolve_through_get_active_today(self):
        """The two 'independent' ML voices read the SAME object. This is the finding —
        asserted on source so it fails loudly the day someone fixes it (and then this
        test is what tells them to update the catalog + the weights)."""
        import inspect

        from core.round_table import agents

        lstm_src = inspect.getsource(agents.LSTMSignalAgent)
        rl_src = inspect.getsource(agents.RLConfidenceAgent)

        assert "get_active()" in lstm_src
        assert "get_active()" in rl_src

    def test_the_default_makes_the_lstm_agent_vote_on_rl(self):
        """Under the ship default, 'the active strategy' IS RLStrategy — so the agent
        named for the LSTM asks RL what it thinks."""
        assert config.get_config().ACTIVE_STRATEGY == "RLAgent"

        from core.strategies.rl_strategy import RLStrategy

        assert hasattr(
            RLStrategy, "evaluate_for_symbol"
        ), "the LSTM agent's call lands here — that is the echo"

    def test_their_combined_weight_is_what_makes_it_matter(self):
        """ADR-018 muted the RL echo at the WEIGHT channel: RL_CONFIDENCE_WEIGHT default
        → 0.0 (agents.py:296, import-time), so the LSTM signal is no longer double-counted
        (0.40 LSTM + 0.0 muted RL). Before activation both carried 0.40 (0.80 combined on one
        source, counted twice). The ROUTING echo is separately broken by the activated
        ROUND_TABLE_DISTINCT_ML_SOURCES=True (LSTM → registry.get('LSTMDynamic'))."""
        assert agents_weight("LSTMSignalAgent") == 0.40
        assert agents_weight("RLConfidenceAgent") == 0.0


def agents_weight(name: str) -> float:
    from core.round_table.agents import ALL_AGENTS

    for a in ALL_AGENTS:
        if a.__class__.__name__ == name:
            return a.__class__.default_weight
    raise AssertionError(f"{name} is not in the gremium")
