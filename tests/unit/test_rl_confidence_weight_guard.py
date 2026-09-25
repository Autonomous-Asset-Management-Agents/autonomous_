"""RTR-0 (#1947) / Epic #1958 — RLConfidenceAgent vote weight is config-gated.

The RL vote is direction-blind: its score is ``0.5 + (|pred|/2)*0.5`` on a BUY action, so a
strongly-BEARISH prediction (large ``|pred|``) still yields a max-conviction BUY (observed
live: PLD pred=-2.24 -> score 1.000). ``RL_CONFIDENCE_WEIGHT`` lets the desktop MUTE that
broken signal (=0.0) as an interim guard until the sign-fix + attribution land. The default
0.40 keeps the historical hardcoded weight byte-identical (BORA).
"""

from types import SimpleNamespace
from unittest.mock import patch

import config as app_config
from core.round_table import agents as rt_agents


def _cfg(value):
    return SimpleNamespace(RL_CONFIDENCE_WEIGHT=value)


def test_config_default_is_00_rl_muted():
    # Round-table-v2 activation: the shipped default is now 0.0, MUTING the direction-blind
    # RL vote until the sign-fix + RTR-0 attribution land. Present in BOTH editions
    # (config.py / config.oss.py, byte-identical). Set RL_CONFIDENCE_WEIGHT>0 via env to arm.
    assert float(app_config.get_config().RL_CONFIDENCE_WEIGHT) == 0.0


def test_reader_returns_configured_value():
    with patch.object(rt_agents.config, "get_config", return_value=_cfg(0.0)):
        assert rt_agents._rl_confidence_weight() == 0.0
    with patch.object(rt_agents.config, "get_config", return_value=_cfg(0.25)):
        assert rt_agents._rl_confidence_weight() == 0.25


def test_reader_clamps_negative_and_falls_back():
    # Negative -> clamped to 0.0 (never a negative vote weight).
    with patch.object(rt_agents.config, "get_config", return_value=_cfg(-1.0)):
        assert rt_agents._rl_confidence_weight() == 0.0
    # Missing attribute -> getattr fallback to the 0.40 default (CI-prevention: no AttributeError).
    with patch.object(rt_agents.config, "get_config", return_value=SimpleNamespace()):
        assert rt_agents._rl_confidence_weight() == 0.40
    # Non-numeric -> 0.40 default (fail-safe, never crashes the vote).
    with patch.object(rt_agents.config, "get_config", return_value=_cfg("nope")):
        assert rt_agents._rl_confidence_weight() == 0.40


def test_rl_agent_weight_wired_to_module_constant():
    # The class default_weight is resolved once at import from the config-gated constant,
    # mirroring SpecialistAlphaAgent. At the shipped default that is 0.40 (byte-identical).
    assert rt_agents.RLConfidenceAgent.default_weight == rt_agents._RL_CONFIDENCE_WEIGHT
    if rt_agents._RL_CONFIDENCE_WEIGHT <= 0.0:
        # Muted -> min/max clamp to 0 so the RL vote is fully excluded from consensus.
        assert rt_agents.RLConfidenceAgent.max_weight == 0.0
        assert rt_agents.RLConfidenceAgent.min_weight == 0.0
    else:
        # Armed -> historical band preserved (0.15 .. 1.50).
        assert rt_agents.RLConfidenceAgent.min_weight == 0.15
        assert rt_agents.RLConfidenceAgent.max_weight == 1.50
