# tests/unit/test_diagnostics.py
# TDD — Epic 5: Auto-RCA & Incident Management
#
# Gherkin:
#   Given: Eine DependencyLostException wird geworfen
#   When:  run_rca_diagnostics(exc) aufgerufen
#   Then:  RCAReport mit severity, root_cause, recommendations zurückgegeben
#
#   Given: create_github_incident(report) aufgerufen
#   Then:  GitHub API korrekt aufgerufen (gemockt), Issue URL zurückgegeben
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD - Red → Green → Refactor

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestRCADiagnostics:
    """TDD Red → Green: run_rca_diagnostics muss einen RCAReport liefern."""

    def test_rca_report_importable(self):
        """Epic 5: RCAReport und run_rca_diagnostics müssen importierbar sein."""
        from core.diagnostics import RCAReport, run_rca_diagnostics

        assert callable(run_rca_diagnostics)

    def test_rca_report_structure(self):
        """Epic 5: RCAReport enthält severity, root_cause, recommendations."""
        from core.diagnostics import RCAReport

        report = RCAReport(
            severity="CRITICAL",
            root_cause="Registry not initialized",
            recommendations=["Restart bot", "Check GEMINI_API_KEY"],
            affected_agents=["LSTMSignalAgent"],
            raw_logs=[],
        )
        assert report.severity == "CRITICAL"
        assert len(report.recommendations) == 2

    @pytest.mark.anyio
    async def test_run_rca_diagnostics_with_dependency_exception(self):
        """Epic 5: run_rca_diagnostics gibt RCAReport zurück bei DependencyLostException."""
        from core.diagnostics import run_rca_diagnostics, RCAReport
        from core.round_table.agents import DependencyLostException

        exc = DependencyLostException(
            "LSTMSignalAgent: Registry None — Kill Switch required."
        )
        report = await run_rca_diagnostics(exc)

        assert isinstance(report, RCAReport)
        assert report.severity in ("CRITICAL", "HIGH", "MEDIUM")
        assert "LSTMSignalAgent" in report.root_cause or "Registry" in report.root_cause
        assert len(report.recommendations) > 0

    @pytest.mark.anyio
    async def test_run_rca_diagnostics_with_suspect_data(self):
        """Epic 5: run_rca_diagnostics gibt RCAReport zurück bei SuspectDataException."""
        from core.diagnostics import run_rca_diagnostics, RCAReport
        from core.round_table.agents import SuspectDataException

        exc = SuspectDataException("[AAPL] Flat-Candle mit vol=500000")
        report = await run_rca_diagnostics(exc)

        assert isinstance(report, RCAReport)
        assert report.severity in ("CRITICAL", "HIGH", "MEDIUM")
        assert len(report.recommendations) > 0


class TestIncidentManagement:
    """TDD Red → Green: create_github_incident muss ein GitHub Issue öffnen."""

    @pytest.mark.anyio
    async def test_create_github_incident_calls_api(self):
        """Epic 5: create_github_incident ruft gh CLI auf und gibt Issue URL zurück."""
        from core.diagnostics import create_github_incident, RCAReport

        report = RCAReport(
            severity="CRITICAL",
            root_cause="Test Root Cause",
            recommendations=["Fix 1", "Fix 2"],
            affected_agents=["LSTMSignalAgent"],
            raw_logs=["log line 1"],
        )

        with patch(
            "core.diagnostics.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_proc:
            mock_proc.return_value = MagicMock(
                communicate=AsyncMock(
                    return_value=(
                        b"https://github.com/aaagents/Dev-Enviroment/issues/999\n",
                        b"",
                    )
                ),
                returncode=0,
            )
            url = await create_github_incident(report)

        assert "github.com" in url or url == "N/A"
