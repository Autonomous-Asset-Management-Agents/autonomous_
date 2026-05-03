# tests/unit/test_agent_registry.py
# Epic 2.3-Pre / PR-A — TDD Red-Phase
# AgentRegistry: register(), get_active(), swap(), has_pending_swap()
#
# Alle Tests sind zuerst ROT — core/agent_registry.py existiert noch nicht.
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(name="RLAgent"):
    """Erzeugt einen minimalen BaseStrategy-Mock."""
    s = MagicMock()
    s.strategy_name = name
    return s


# ---------------------------------------------------------------------------
# 1. Register & Get
# ---------------------------------------------------------------------------


class TestAgentRegistryRegisterAndGet:
    def test_get_active_returns_none_when_empty(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        assert registry.get_active() is None

    def test_register_sets_active(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        strategy = _make_strategy("RLAgent")
        registry.register("RLAgent", strategy)
        assert registry.get_active() is strategy

    def test_register_multiple_does_not_override_active(self):
        """Registrieren weiterer Strategien ändert NICHT die aktive Strategy."""
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = _make_strategy("RLAgent")
        s2 = _make_strategy("LSTMDynamic")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)
        assert registry.get_active() is s1

    def test_list_registered_returns_all_names(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry.register("RLAgent", _make_strategy("RLAgent"))
        registry.register(
            "LSTMDynamic", _make_strategy("LSTMDynamic"), set_active=False
        )
        names = registry.list_registered()
        assert "RLAgent" in names
        assert "LSTMDynamic" in names


# ---------------------------------------------------------------------------
# 2. Swap (Pending-Flag-Mechanismus)
# ---------------------------------------------------------------------------


class TestAgentRegistrySwap:
    def test_swap_sets_pending_flag(self):
        """swap() setzt _pending_swap ohne sofortigen Wechsel der aktiven Strategy."""
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = _make_strategy("RLAgent")
        s2 = _make_strategy("LSTMDynamic")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)

        result = registry.swap("LSTMDynamic")

        assert result is True
        assert registry.has_pending_swap() is True
        # active_strategy darf sich NOCH NICHT geändert haben
        assert registry.get_active() is s1

    def test_swap_returns_false_for_unknown_name(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry.register("RLAgent", _make_strategy("RLAgent"), set_active=True)
        result = registry.swap("UnknownStrategy")
        assert result is False
        assert registry.has_pending_swap() is False

    def test_has_pending_swap_false_by_default(self):
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        assert registry.has_pending_swap() is False

    def test_commit_swap_changes_active(self):
        """commit_swap() führt den tatsächlichen Wechsel durch (nach Cycle-Ende)."""
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = _make_strategy("RLAgent")
        s2 = _make_strategy("LSTMDynamic")
        registry.register("RLAgent", s1, set_active=True)
        registry.register("LSTMDynamic", s2, set_active=False)
        registry.swap("LSTMDynamic")

        registry.commit_swap()

        assert registry.get_active() is s2
        assert registry.has_pending_swap() is False

    def test_commit_swap_noop_when_no_pending(self):
        """commit_swap() ohne pending swap ändert nichts."""
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        s1 = _make_strategy("RLAgent")
        registry.register("RLAgent", s1, set_active=True)
        registry.commit_swap()
        assert registry.get_active() is s1


# ---------------------------------------------------------------------------
# 3. Thread-Safety
# ---------------------------------------------------------------------------


class TestAgentRegistryThreadSafety:
    def test_get_active_is_threadsafe(self):
        """Concurrent reads geben immer eine valide (oder None) Strategy zurück."""
        import threading
        from core.agent_registry import AgentRegistry

        registry = AgentRegistry()
        strategy = _make_strategy("RLAgent")
        registry.register("RLAgent", strategy, set_active=True)

        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    s = registry.get_active()
                    results.append(s is strategy or s is None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread-safety errors: {errors}"
        assert all(results)
