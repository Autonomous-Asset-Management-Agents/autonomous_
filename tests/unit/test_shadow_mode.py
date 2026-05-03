# tests/unit/test_shadow_mode.py
# Epic 2.3-Pre / PR-C — TDD Red-Phase
# Issue H: Shadow Mode — swap() mit shadow_mode=True Parameter
#
# Tests ROT bis shadow_mode in AgentRegistry und config.py implementiert ist.

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry_with_strategies():
    from core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    s1 = MagicMock(strategy_name="RLAgent")
    s2 = MagicMock(strategy_name="LSTMDynamic")
    registry.register("RLAgent", s1, set_active=True)
    registry.register("LSTMDynamic", s2, set_active=False)
    return registry, s1, s2


# ---------------------------------------------------------------------------
# 1. shadow_mode=True im swap()
# ---------------------------------------------------------------------------


class TestShadowMode:
    def test_swap_with_shadow_mode_sets_pending_flag(self):
        """swap(name, shadow_mode=True) setzt Pending-Flag wie normaler Swap."""
        registry, s1, _ = _make_registry_with_strategies()

        result = registry.swap("LSTMDynamic", shadow_mode=True)

        assert result is True
        assert registry.has_pending_swap() is True
        # Active darf sich nicht geändert haben
        assert registry.get_active() is s1

    def test_swap_with_shadow_mode_marks_as_shadow(self):
        """swap(name, shadow_mode=True) markiert den Swap als Shadow-Mode."""
        registry, _, _ = _make_registry_with_strategies()

        registry.swap("LSTMDynamic", shadow_mode=True)

        assert registry.is_shadow_mode() is True

    def test_swap_without_shadow_mode_is_not_shadow(self):
        """Normaler swap() ohne shadow_mode=True → is_shadow_mode() ist False."""
        registry, _, _ = _make_registry_with_strategies()

        registry.swap("LSTMDynamic")

        assert registry.is_shadow_mode() is False


# ---------------------------------------------------------------------------
# 2. SHADOW_MODE_HOURS Config
# ---------------------------------------------------------------------------


class TestShadowModeConfig:
    def test_shadow_mode_hours_exists_in_config(self):
        """SHADOW_MODE_HOURS muss in config.py definiert sein (default: 24)."""
        import config

        hours = getattr(config, "SHADOW_MODE_HOURS", None)
        assert hours is not None, "SHADOW_MODE_HOURS fehlt in config.py"
        assert isinstance(hours, (int, float))
        assert hours > 0
