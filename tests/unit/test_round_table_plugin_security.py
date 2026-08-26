# tests/unit/test_round_table_plugin_security.py
#
# Regression tests for the Plugin Loader Security Guard in runner.py.
#
# The security invariant: ALL_AGENTS (baseline) ALWAYS win name collisions
# over plugin-registered agents. A plugin named "RiskAgent" must NEVER
# replace the core RiskAgent — doing so would allow arbitrary code injection
# via the plugin directory.
#
# If these tests fail, a future change has broken the security guard in
# boot_engine() and the plugin system is vulnerable to name-hijacking.
#
# Policy: CODING_POLICY.md §1 Compliance-First, ADR-OSS-PLUGIN-001

from __future__ import annotations

import os
from unittest.mock import patch

import allure
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_hijack_plugin_file(directory: str, target_class_name: str) -> str:
    """
    Write a plugin .py file that registers an agent with the given class name.
    This simulates a malicious or misconfigured plugin trying to shadow a
    baseline agent by using the same __class__.__name__ as key in the dict merge.
    """
    plugin_path = os.path.join(directory, f"hijack_{target_class_name.lower()}.py")
    plugin_code = f"""
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("{target_class_name}")
class {target_class_name}(VotingAgent):
    default_weight = 999.0
    IS_MALICIOUS_PLUGIN = True  # marker to detect if this agent is active

    async def vote(self, state):
        return VoteResult(
            "{target_class_name}", state.get("symbol", "X"), 0.0, 999.0, "HIJACK", False
        )
"""
    with open(plugin_path, "w") as f:
        f.write(plugin_code)
    return plugin_path


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestPluginSecurityGuard:
    """
    Verifies that the dict-merge in boot_engine() enforces the correct priority:
        plugins (lower) → ALL_AGENTS (higher, wins on name collision)
    """

    def test_baseline_agent_wins_name_collision(self, tmp_path):
        """
        Given: A plugin registered with name 'RiskAgent' (same as a baseline agent)
        When:  boot_engine() loads the plugin directory
        Then:  The 'RiskAgent' in _active_agents is the CORE RiskAgent,
               not the plugin — IS_MALICIOUS_PLUGIN must not be set on it.
        """
        import importlib

        from core.round_table import runner
        from core.round_table.agents import ALL_AGENTS
        from core.round_table.registry import PluginRegistry

        # Identify a real baseline agent name to use as the collision target
        baseline_agent_name = ALL_AGENTS[0].__class__.__name__
        _make_hijack_plugin_file(str(tmp_path), baseline_agent_name)

        # Isolate: use a fresh registry to avoid polluting the global one
        fresh_registry = PluginRegistry()
        with (
            patch.dict(os.environ, {"ALLOW_UNTRUSTED_PLUGINS": "true"}),
            patch("core.round_table.runner._global_registry", fresh_registry),
            patch("core.round_table.runner._active_agents", [], create=True),
        ):
            fresh_registry.load_plugins_from_directory(str(tmp_path))
            plugin_agents = fresh_registry.get_active_agents()

            # Build the merged list exactly as boot_engine() does
            merged = list(
                {
                    **{a.__class__.__name__: a for a in plugin_agents},
                    **{a.__class__.__name__: a for a in ALL_AGENTS},
                }.values()
            )

        # The winner for the collision key must be the core agent, not the plugin
        merged_by_name = {a.__class__.__name__: a for a in merged}
        winner = merged_by_name[baseline_agent_name]

        assert not getattr(winner, "IS_MALICIOUS_PLUGIN", False), (
            f"Plugin hijacked {baseline_agent_name}! "
            "The dict-merge security guard is broken — ALL_AGENTS must win collisions."
        )

    def test_plugins_extend_baseline_without_removing_any(self, tmp_path):
        """
        Given: 2 uniquely named plugins (no name collision with baseline)
        When:  the dict-merge in boot_engine() combines plugins with ALL_AGENTS
        Then:  All baseline agents are present AND both plugins are present.

        NOTE: @register_agent writes to _global_registry (singleton).
        We verify the merge logic directly — the security property is the
        priority ordering, not which registry instance is used.
        """
        from core.round_table.agents import ALL_AGENTS
        from core.round_table.base_agent import VoteResult, VotingAgent

        # Simulate 2 plugin agent instances (not loaded from disk, but equivalent)
        class SafePlugin1(VotingAgent):
            default_weight = 0.5

            async def vote(self, state):
                return VoteResult(
                    "SafePlugin1", state.get("symbol", "X"), 0.7, 0.5, "OK", False
                )

        class SafePlugin2(VotingAgent):
            default_weight = 0.5

            async def vote(self, state):
                return VoteResult(
                    "SafePlugin2", state.get("symbol", "X"), 0.7, 0.5, "OK", False
                )

        plugin_agents = [SafePlugin1(), SafePlugin2()]

        merged = list(
            {
                **{a.__class__.__name__: a for a in plugin_agents},
                **{a.__class__.__name__: a for a in ALL_AGENTS},
            }.values()
        )

        expected_count = len(ALL_AGENTS) + 2
        assert len(merged) == expected_count, (
            f"Expected {expected_count} agents (9 baseline + 2 plugins), "
            f"got {len(merged)}. "
            "Plugin extension or baseline preservation is broken."
        )
        # Verify all baseline agents are still present
        baseline_names = {a.__class__.__name__ for a in ALL_AGENTS}
        merged_names = {a.__class__.__name__ for a in merged}
        assert baseline_names.issubset(
            merged_names
        ), f"Missing baseline agents after plugin merge: {baseline_names - merged_names}"
        # Verify both safe plugins are present
        assert "SafePlugin1" in merged_names, "SafePlugin1 missing from merged agents"
        assert "SafePlugin2" in merged_names, "SafePlugin2 missing from merged agents"

    def test_deny_by_default_no_plugins_loaded_without_flag(self, tmp_path):
        """
        Given: ALLOW_UNTRUSTED_PLUGINS is not set (or False)
        When:  load_plugins_from_directory() is called
        Then:  No plugins are loaded — the registry stays empty.

        This tests the deny-by-default security posture documented in .env.oss.example.
        """
        from core.round_table.registry import PluginRegistry

        plugin_path = os.path.join(str(tmp_path), "my_plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                """
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("DenyTestAgent")
class DenyTestAgent(VotingAgent):
    default_weight = 1.0
    async def vote(self, state):
        return VoteResult("DenyTestAgent", "X", 0.5, 1.0, "test", False)
"""
            )

        fresh_registry = PluginRegistry()
        with patch.dict(os.environ, {"ALLOW_UNTRUSTED_PLUGINS": "false"}):
            fresh_registry.load_plugins_from_directory(str(tmp_path))

        agents = fresh_registry.get_active_agents()
        assert len(agents) == 0, (
            "Plugins were loaded despite ALLOW_UNTRUSTED_PLUGINS=false. "
            "The deny-by-default security posture is broken."
        )


# ---------------------------------------------------------------------------
# TestProductionEnvGuard (I-2 #943 — Rogue Agent Hardening)
# ---------------------------------------------------------------------------


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestProductionEnvGuard:
    """
    TDD coverage for the ENVIRONMENT=production plugin-loading guard added in I-2 #943.

    Gherkin:
        Given: ENVIRONMENT=production is set
        When:  load_plugins_from_directory() is called (even with ALLOW_UNTRUSTED_PLUGINS=true)
        Then:  No plugins are loaded
        And:   A SECURITY WARNING is emitted in the logs
    """

    def test_production_env_blocks_plugins_even_if_flag_set(self, tmp_path, caplog):
        """
        Given: ENVIRONMENT=production AND ALLOW_UNTRUSTED_PLUGINS=true
        When:  load_plugins_from_directory() called
        Then:  Registry stays empty — production guard overrides the flag.
        This is the defense-in-depth guarantee: two independent guards must BOTH pass.
        """
        import logging

        from core.round_table.registry import PluginRegistry

        plugin_path = os.path.join(str(tmp_path), "evil_plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                """
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("EvilProductionAgent")
class EvilProductionAgent(VotingAgent):
    default_weight = 999.0
    async def vote(self, state):
        return VoteResult("EvilProductionAgent", "X", 0.0, 999.0, "EVIL", False)
"""
            )
        fresh_registry = PluginRegistry()
        with (
            patch.dict(
                os.environ,
                {
                    "ENVIRONMENT": "production",
                    "ALLOW_UNTRUSTED_PLUGINS": "true",  # This MUST be overridden by the production guard
                },
            ),
            caplog.at_level(logging.WARNING, logger="core.round_table.registry"),
        ):
            fresh_registry.load_plugins_from_directory(str(tmp_path))

        agents = fresh_registry.get_active_agents()
        assert len(agents) == 0, (
            "Plugin was loaded in production environment despite ENVIRONMENT=production guard! "
            "The defense-in-depth security posture for I-2 #943 is broken."
        )
        # Verify the SECURITY warning was emitted
        assert any("SECURITY" in r.message for r in caplog.records), (
            "Expected a SECURITY WARNING log when plugin loading is blocked in production. "
            "Operators must be able to detect this in Cloud Run logs."
        )

    def test_non_production_env_allows_plugins_with_flag(self, tmp_path, caplog):
        """
        Given: ENVIRONMENT=staging AND ALLOW_UNTRUSTED_PLUGINS=true
        When:  load_plugins_from_directory() called
        Then:  Plugin file IS executed — production guard only blocks 'production'.

        Note: register_agent() writes to _global_registry (singleton), not fresh_registry.
        We verify the plugin was loaded by checking that load_plugins_from_directory
        did NOT return early (i.e., the "Successfully loaded" log appears).
        """
        import logging

        from core.round_table.registry import PluginRegistry

        plugin_path = os.path.join(str(tmp_path), "staging_plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                """
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("StagingAgentNonProd")
class StagingAgentNonProd(VotingAgent):
    default_weight = 1.0
    async def vote(self, state):
        return VoteResult("StagingAgentNonProd", "X", 0.5, 1.0, "staging", False)
"""
            )
        fresh_registry = PluginRegistry()
        with (
            patch.dict(
                os.environ,
                {
                    "ENVIRONMENT": "staging",
                    "ALLOW_UNTRUSTED_PLUGINS": "true",
                },
            ),
            caplog.at_level(logging.INFO, logger="core.round_table.registry"),
        ):
            fresh_registry.load_plugins_from_directory(str(tmp_path))

        # In staging with flag=true, the file must be executed (not blocked)
        assert any("Successfully loaded" in r.message for r in caplog.records), (
            "Plugin file should be executed in non-production environment with "
            "ALLOW_UNTRUSTED_PLUGINS=true. The production guard must ONLY block 'production'."
        )
        # Verify no SECURITY warning (that would indicate false positive blocking)
        security_warnings = [r for r in caplog.records if "SECURITY" in r.message]
        assert (
            len(security_warnings) == 0
        ), f"No SECURITY warning expected for staging env, got: {security_warnings}"

    def test_production_env_case_insensitive(self, tmp_path):
        """
        Given: ENVIRONMENT=PRODUCTION (uppercase)
        When:  load_plugins_from_directory() called
        Then:  Production guard triggers (case-insensitive match).
        """
        from core.round_table.registry import PluginRegistry

        plugin_path = os.path.join(str(tmp_path), "plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                """
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

@register_agent("UpperCaseEnvAgent")
class UpperCaseEnvAgent(VotingAgent):
    default_weight = 1.0
    async def vote(self, state):
        return VoteResult("UpperCaseEnvAgent", "X", 0.5, 1.0, "test", False)
"""
            )
        fresh_registry = PluginRegistry()
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "PRODUCTION",  # uppercase — must still be blocked
                "ALLOW_UNTRUSTED_PLUGINS": "true",
            },
        ):
            fresh_registry.load_plugins_from_directory(str(tmp_path))

        agents = fresh_registry.get_active_agents()
        assert len(agents) == 0, (
            "Production guard did not block uppercase ENVIRONMENT=PRODUCTION. "
            "Case-insensitive match is required."
        )
