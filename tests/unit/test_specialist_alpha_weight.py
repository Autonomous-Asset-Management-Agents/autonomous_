# tests/unit/test_specialist_alpha_weight.py
# #1346: the SpecialistAlphaAgent vote weight is config-gated. Default 0.0 keeps it
# DORMANT (byte-identical to today — clamped to 0, excluded from consensus). Setting
# SPECIALIST_ALPHA_WEIGHT restores a real weighted vote (once the registry is enabled +
# the #76 shadow gate clears). The os.environ read lives in config.py/config.oss.py
# (CODING_POLICY §2.10); the finance-core reads it via config.get_config().
import importlib
from types import SimpleNamespace

import config
import core.round_table.agents as agents


def _patch_weight(monkeypatch, value):
    monkeypatch.setattr(
        config, "get_config", lambda: SimpleNamespace(SPECIALIST_ALPHA_WEIGHT=value)
    )


def test_specialist_alpha_weight_helper_reads_config(monkeypatch):
    _patch_weight(monkeypatch, 0.0)
    assert agents._specialist_alpha_weight() == 0.0
    _patch_weight(monkeypatch, 0.55)
    assert agents._specialist_alpha_weight() == 0.55
    _patch_weight(monkeypatch, "garbage")
    assert agents._specialist_alpha_weight() == 0.0  # invalid -> dormant, never crash


def test_specialist_alpha_dormant_by_default(monkeypatch):
    _patch_weight(monkeypatch, 0.0)
    importlib.reload(agents)
    try:
        # default_weight 0.0 AND max_weight 0.0 -> the weight property clamps to 0
        assert agents.SpecialistAlphaAgent.default_weight == 0.0
        assert agents.SpecialistAlphaAgent.max_weight == 0.0
    finally:
        monkeypatch.undo()
        importlib.reload(agents)  # restore module to the default (dormant) state


def test_specialist_alpha_weight_restored_when_configured(monkeypatch):
    _patch_weight(monkeypatch, 0.55)
    importlib.reload(agents)
    try:
        assert agents.SpecialistAlphaAgent.default_weight == 0.55
        assert agents.SpecialistAlphaAgent.max_weight == 2.0  # now effective
    finally:
        monkeypatch.undo()
        importlib.reload(agents)  # restore module to the default (dormant) state


def test_redis_cannot_activate_dormant_specialist(monkeypatch):
    """R3 (#1950): Redis kann den dormanten Voter NICHT aktivieren.

    Dormant ist max_weight == 0.0 (agents.py class body) und VotingAgent.weight
    klemmt JEDEN Redis-Wert aus agent_weights_v2 hart auf [min_weight, max_weight]
    (base_agent.py clamp) -> selbst 0.55 aus Redis wird zu 0.0. Aktivierung ist
    AUSSCHLIESSLICH via SPECIALIST_ALPHA_WEIGHT in der Env zum Prozess-Start
    (import-zeitliche Aufloesung, agents.py `_SPECIALIST_ALPHA_WEIGHT`).
    """
    from unittest.mock import MagicMock

    import core.round_table.base_agent as base_agent

    # #2839: default weight is 0.40 now (desktop master) — dormancy is forced
    # explicitly; the claim under test (Redis clamp) is unchanged.
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "default_weight", 0.0)
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "max_weight", 0.0)
    assert agents.SpecialistAlphaAgent.max_weight == 0.0
    redis_client = MagicMock()
    redis_client.get_sync_redis.return_value.hget.return_value = b"0.55"
    monkeypatch.setattr(base_agent, "RedisClient", redis_client)
    agent = agents.SpecialistAlphaAgent()
    assert agent.weight == 0.0  # clamped to [0.0, 0.0] — Redis injection inert
