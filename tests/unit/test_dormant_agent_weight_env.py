"""#3145(3)/#3146: config-gated weights for the FundamentalsAgent / ValuationAgent.

ACTIVATED at 0.35 as the shipped default (owner decision 2026-09-03) — these were the
formerly-dormant (0.0) fundamental agents; the env/config seam is unchanged, only the
default value moved.
"""

from types import SimpleNamespace
from unittest.mock import patch

import core.round_table.agents as ag


def test_default_weight_is_active_035_and_wired():
    assert ag._FUNDAMENTALS_AGENT_WEIGHT == 0.35
    assert ag._VALUATION_AGENT_WEIGHT == 0.35
    assert ag.FundamentalsAgent().default_weight == ag._FUNDAMENTALS_AGENT_WEIGHT
    assert ag.ValuationAgent().default_weight == ag._VALUATION_AGENT_WEIGHT


def test_env_config_weight_is_honored_and_clamped():
    with patch(
        "config.get_config",
        return_value=SimpleNamespace(FUNDAMENTALS_AGENT_WEIGHT=0.35),
    ):
        assert ag._consensus_weight("FUNDAMENTALS_AGENT_WEIGHT", 0.0) == 0.35
    with patch(
        "config.get_config", return_value=SimpleNamespace(VALUATION_AGENT_WEIGHT=7.0)
    ):
        assert (
            ag._consensus_weight("VALUATION_AGENT_WEIGHT", 0.0) == 1.0
        )  # clamp typo-guard
