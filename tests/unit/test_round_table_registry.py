# tests/unit/test_round_table_registry.py
import os
import tempfile

import allure
import pytest

from core.round_table.base_agent import VoteResult, VotingAgent


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestAgentRegistry:
    def test_register_decorator(self):
        from core.round_table.registry import PluginRegistry, register_agent

        registry = PluginRegistry()

        @register_agent("TestPluginAgent", registry=registry)
        class TestPluginAgent(VotingAgent):
            default_weight = 1.0

            async def vote(self, state):
                return VoteResult(
                    "TestPluginAgent", state["symbol"], 1.0, 1.0, "Test", False
                )

        agents = registry.get_active_agents()
        assert len(agents) == 1
        assert isinstance(agents[0], TestPluginAgent)

    def test_load_plugins_from_directory(self):
        from core.round_table.registry import _global_registry

        registry = _global_registry

        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "my_plugin.py")
            with open(plugin_path, "w") as f:
                f.write(
                    """
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("DynAgent")
class DynAgent(VotingAgent):
    default_weight = 0.5
    async def vote(self, state):
        return VoteResult("DynAgent", state["symbol"], 0.8, 0.5, "Dyn", False)
"""
                )
            # By default, without ALLOW_UNTRUSTED_PLUGINS=true, it should log a warning and skip loading
            os.environ["ALLOW_UNTRUSTED_PLUGINS"] = "false"
            registry.load_plugins_from_directory(tmpdir)
            agents = registry.get_active_agents()
            assert not any(a.__class__.__name__ == "DynAgent" for a in agents)

            # Now allow untrusted plugins
            os.environ["ALLOW_UNTRUSTED_PLUGINS"] = "true"
            registry.load_plugins_from_directory(tmpdir)
            agents = registry.get_active_agents()
            assert any(a.__class__.__name__ == "DynAgent" for a in agents)
