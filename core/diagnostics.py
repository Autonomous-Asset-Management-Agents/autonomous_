# core/diagnostics.py
# Epic 5: Auto-RCA & Incident Management — Fail-Fast Architecture
#
# Wenn der Emergency Kill Switch ausgelöst wird (DependencyLostException oder
# SuspectDataException), führt dieses Modul einen automatischen Diagnostic-Trace
# durch und öffnet ein GitHub Issue mit dem RCA-Befund.
#
# Policy Ref: docs/CODING_POLICY.md §11.5 TDD

from __future__ import annotations

import asyncio
import logging
import os
import textwrap
from dataclasses import dataclass, field
from typing import List

from core.round_table.agents import DependencyLostException, SuspectDataException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RCAReport — Struktur des Diagnose-Berichts
# ---------------------------------------------------------------------------


@dataclass
class RCAReport:
    """Structured Root Cause Analysis report produced after Kill Switch activation."""

    severity: str  # CRITICAL | HIGH | MEDIUM
    root_cause: str  # Kurze Ursachen-Beschreibung
    recommendations: List[str]  # Handlungsempfehlungen
    affected_agents: List[str]  # Betroffene Agenten
    raw_logs: List[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        recs = "\n".join(f"- {r}" for r in self.recommendations)
        agents = ", ".join(self.affected_agents) if self.affected_agents else "—"
        logs = "\n".join(self.raw_logs[-20:]) if self.raw_logs else "—"
        return textwrap.dedent(
            f"""
            ## 🚨 Auto-RCA Incident Report

            **Severity**: {self.severity}
            **Root Cause**: {self.root_cause}
            **Affected Agents**: {agents}

            ### Recommendations
            {recs}

            ### Last Log Lines
            ```
            {logs}
            ```

            *Generated automatically by core/diagnostics.py*
        """
        ).strip()


# ---------------------------------------------------------------------------
# run_rca_diagnostics — Diagnostic-Trace nach Kill Switch
# ---------------------------------------------------------------------------


async def run_rca_diagnostics(exc: Exception) -> RCAReport:
    """
    Führt einen Instant-Diagnostic-Trace aus, wenn der Kill Switch ausgelöst wurde.
    Analysiert die Exception und klassifiziert Schwere + Root Cause.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    recommendations: List[str] = []
    affected_agents: List[str] = []
    severity = "CRITICAL"

    if isinstance(exc, DependencyLostException):
        root_cause = f"Kritische Laufzeit-Abhängigkeit verloren: {exc_msg}"
        if "Registry" in exc_msg or "registry" in exc_msg:
            recommendations += [
                "AgentRegistry auf korrekte Initialisierung in base.py prüfen.",
                "Cloud Run Service neustarten.",
                "Sicherstellen, dass validate_dependencies() beim Start kein Fehler wirft.",
            ]
            # Extrahiere betroffene Agenten aus der Exception-Message
            for agent in [
                "LSTMSignalAgent",
                "RLConfidenceAgent",
                "SpecialistAlphaAgent",
            ]:
                if agent in exc_msg:
                    affected_agents.append(agent)
        else:
            recommendations += [
                "API-Keys im Secret Manager prüfen (GEMINI_API_KEY, Alpaca Keys).",
                "Health-Endpoint der externen API prüfen.",
            ]

    elif isinstance(exc, SuspectDataException):
        root_cause = f"Korrupte Marktdaten (Flat-Candle) im Alpaca Feed: {exc_msg}"
        recommendations += [
            "Alpaca REST API direkt testen (/v2/stocks/{symbol}/bars).",
            "Sicherstellen, dass daily_bar aus snapshot_obj korrekt extrahiert wird.",
            "Prüfen ob Marktdaten für den Zeitraum verfügbar sind (Pre-Market / After-Hours).",
        ]

    else:
        root_cause = f"Unbekannter Fehler ({exc_type}): {exc_msg}"
        severity = "HIGH"
        recommendations += [
            f"Exception-Typ {exc_type} manuell analysieren.",
            "Bot-Logs der letzten 50 Zeilen prüfen.",
        ]

    # Lade die letzten Log-Zeilen aus dem GCP Cloud Logging (wenn verfügbar)
    raw_logs = await _fetch_recent_logs()

    logger.error(
        "🔍 Auto-RCA abgeschlossen | Severity: %s | Root Cause: %s",
        severity,
        root_cause,
    )

    return RCAReport(
        severity=severity,
        root_cause=root_cause,
        recommendations=recommendations,
        affected_agents=affected_agents,
        raw_logs=raw_logs,
    )


async def _fetch_recent_logs(limit: int = 30) -> List[str]:
    """
    Versucht, die letzten Cloud Run Log-Zeilen via gcloud CLI zu laden.
    Kein Crash bei Fehler — Logs sind optional für den RCA-Bericht.
    """
    try:
        service = os.getenv("K_SERVICE", "aaa-backend")
        proc = await asyncio.create_subprocess_exec(
            "gcloud",
            "logging",
            "read",
            f"resource.labels.service_name={service}",
            f"--limit={limit}",
            "--format=value(textPayload)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return [
            line for line in stdout.decode(errors="ignore").splitlines() if line.strip()
        ]
    except Exception as log_err:
        logger.debug("_fetch_recent_logs failed (non-critical): %s", log_err)
        return []


# ---------------------------------------------------------------------------
# create_github_incident — Automatisches GitHub Issue erstellen
# ---------------------------------------------------------------------------


async def create_github_incident(report: RCAReport) -> str:
    """
    Öffnet automatisch ein GitHub Issue mit dem RCA-Bericht.
    Nutzt die gh CLI (muss im Container vorhanden sein).
    Gibt die Issue-URL zurück oder 'N/A' bei Fehler.
    """
    title = f"🚨 [{report.severity}] Bot Kill Switch ausgelöst — Auto-RCA Incident"
    body = report.to_markdown()

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--label",
            "incident",
            "--label",
            "bug",
            "--label",
            "priority-critical",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        url = stdout.decode(errors="ignore").strip()
        logger.error("🚨 GitHub Incident Issue erstellt: %s", url)
        return url
    except Exception as gh_err:
        logger.error("create_github_incident fehlgeschlagen: %s", gh_err)
        return "N/A"
