# tests/unit/test_round_table_di.py
import pytest
import os
from unittest.mock import patch
from core.round_table.runner import boot_engine
from core.round_table.senate_log import SenateProtocol, DummyAuditLogger
from core.round_table.agents import ALL_AGENTS


class TestRoundTableDI:
    def test_boot_engine_oss(self):
        # Given an environment without an ENTERPRISE_LICENSE_KEY
        if "ENTERPRISE_LICENSE_KEY" in os.environ:
            del os.environ["ENTERPRISE_LICENSE_KEY"]

        # When boot_engine is called
        boot_engine(None)

        # Then the DummyAuditLogger is injected
        import core.round_table.runner as runner

        assert isinstance(runner._senate, DummyAuditLogger)
        # And we use OSS plugins or fallback to ALL_AGENTS
        assert runner._active_agents == ALL_AGENTS  # Fallback logic in test env

    def test_boot_engine_enterprise(self):
        # Given an environment WITH an ENTERPRISE_LICENSE_KEY
        os.environ["ENTERPRISE_LICENSE_KEY"] = "true"

        try:
            # When boot_engine is called
            boot_engine("true")

            # Then the SenateProtocol is injected
            import core.round_table.runner as runner

            assert isinstance(runner._senate, SenateProtocol)
            # And ALL_AGENTS are loaded
            assert runner._active_agents == ALL_AGENTS
        finally:
            if "ENTERPRISE_LICENSE_KEY" in os.environ:
                del os.environ["ENTERPRISE_LICENSE_KEY"]
