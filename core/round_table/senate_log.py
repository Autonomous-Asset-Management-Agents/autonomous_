# core/round_table/senate_log.py
# Epic 2.5 — Round Table V2: Senate Protocol (Audit Log)
#
# Async fire-and-forget Logging via Redis Streams (primär).
# JSONL-Fallback wenn Redis nicht erreichbar.
# Optional: Cloud SQL-Sink für MiFID II / EU AI Act Compliance.
#
# Policy: Non-blocking — darf den LangGraph _run_strategy_node Pfad NICHT verzögern.

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Log-Verzeichnis (konfigurierbar via Env-Variable)
_LOG_DIR = Path(os.getenv("SENATE_LOG_DIR", "cloud_fallback_logs"))
_REDIS_STREAM_KEY = "senate_protocol_stream"

# Modul-Level Import für Testbarkeit (patchbar via 'core.round_table.senate_log.RedisClient')
try:
    from core.redis_client import RedisClient
except ImportError:  # pragma: no cover
    RedisClient = None  # type: ignore[assignment]


@dataclass
class SenateSession:
    """
    Vollständiger Datensatz einer Round-Table-Session (eine Symbol-Evaluierung).
    Wird als JSONL-Eintrag persistiert.
    """

    session_id: str
    symbol: str
    timestamp: str
    votes: list[dict]  # serialisierte VoteResult-Objekte
    consensus_score: float
    gatekeeper_approved: bool
    gatekeeper_reason: str
    signal_action: Optional[str] = None
    # --- Epic 4.3: ML / compliance enrichment fields (optional) ---
    market_regime: Optional[str] = None  # e.g. "bull", "bear", "sideways"
    escalations: Optional[list] = None  # list of escalation event strings
    specialist_summaries: Optional[dict] = None  # per-symbol specialist report snippets
    ml_scores: Optional[dict] = None  # per-agent LightGBM scores (Epic 4.x)


class IAuditLogger(ABC):
    """Abstract interface for Round-Table session logging."""

    @abstractmethod
    async def log_session(self, session: SenateSession) -> None:
        """Loggt eine Round-Table-Session."""
        ...


class DummyAuditLogger(IAuditLogger):
    """
    OSS Community Fallback: Writes Audit Logs strictly to stdout.
    No Database, No Redis, purely ephemeral for local environments.
    """

    async def log_session(self, session: SenateSession) -> None:
        # Puts minimal, non-blocking logs to stdout
        logger.info(
            f"[AUDIT] {session.symbol}: Score={session.consensus_score:.3f} | "
            f"Action={session.signal_action} | "
            f"Gatekeeper={session.gatekeeper_approved}"
        )


class SenateProtocol(IAuditLogger):
    """
    Async-Logger für Round-Table-Sessions.

    Architektur (Architect Blueprint):
        Primär: Redis Streams XADD (fire-and-forget, non-blocking)
        Fallback: JSONL-Datei (append-only)
        Optional: Cloud SQL-Sink (async, fire-and-forget)

    Wird im run_round_table() als letzter Schritt aufgerufen.
    Der Main-Thread wartet NICHT auf den Log-Abschluss.
    """

    def __init__(self) -> None:
        self._log_dir = _LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)

    async def log_session(self, session: SenateSession) -> None:
        """
        Loggt eine Round-Table-Session.
        Fire-and-forget: Exceptions werden geloggt, aber nicht propagiert.

        Args:
            session: SenateSession mit vollständiger Abstimmungshistorie
        """
        # Primär: Redis Streams (non-blocking)
        redis_ok = await self._log_to_redis_stream(session)

        # Fallback: JSONL-Datei
        if not redis_ok:
            await self._log_to_jsonl(session)

        # Optional: Database Sink (async, fire-and-forget — Exception darf nicht propagieren)
        asyncio.ensure_future(self._log_to_database(session))

    async def _log_to_redis_stream(self, session: SenateSession) -> bool:
        """
        Schreibt die Session via XADD in einen Redis Stream.
        Gibt True zurück wenn erfolgreich, False bei Fehler (Fallback aktiviert).
        """
        try:
            if RedisClient is None:
                return False

            redis = RedisClient.get_sync_redis()
            if redis is None:
                return False

            # Redis XADD: Felder müssen Strings sein
            redis.xadd(
                _REDIS_STREAM_KEY,
                {
                    "session_id": session.session_id,
                    "symbol": session.symbol,
                    "timestamp": session.timestamp,
                    "consensus_score": str(session.consensus_score),
                    "gatekeeper_approved": str(session.gatekeeper_approved),
                    "gatekeeper_reason": session.gatekeeper_reason,
                    "signal_action": session.signal_action or "NONE",
                    "vote_count": str(len(session.votes)),
                },
                maxlen=10_000,  # Rolling Window — älteste Entries werden entfernt
                approximate=True,
            )
            logger.debug(
                "SenateProtocol: Session %s für %s in Redis Stream geloggt",
                session.session_id,
                session.symbol,
            )
            return True
        except Exception as exc:
            logger.debug("SenateProtocol: Redis Stream nicht verfügbar: %s", exc)
            return False

    async def _log_to_jsonl(self, session: SenateSession) -> None:
        """
        JSONL-Fallback: Schreibt eine Session als einzelne JSON-Zeile.
        Datei: cloud_fallback_logs/senate_protocol_YYYY-MM-DD.jsonl
        """
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = self._log_dir / f"senate_protocol_{today}.jsonl"

            entry = {
                "session_id": session.session_id,
                "symbol": session.symbol,
                "timestamp": session.timestamp,
                "consensus_score": session.consensus_score,
                "gatekeeper_approved": session.gatekeeper_approved,
                "gatekeeper_reason": session.gatekeeper_reason,
                "signal_action": session.signal_action,
                "votes": session.votes,
                # Epic 4.3 enrichment fields (None → omitted from output)
                "market_regime": session.market_regime,
                "escalations": session.escalations,
                "specialist_summaries": session.specialist_summaries,
                "ml_scores": session.ml_scores,
            }

            # Append-only, thread-safe genug für single Cloud Run Instance
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            logger.debug(
                "SenateProtocol: Session %s für %s in JSONL geloggt (%s)",
                session.session_id,
                session.symbol,
                log_file,
            )
        except Exception as exc:
            logger.error("SenateProtocol: JSONL-Fallback fehlgeschlagen: %s", exc)

    async def _log_to_database(self, session: SenateSession) -> None:
        """
        Cloud SQL Sink — persistiert jede RoundTable-Auswertung (fire-and-forget).
        Schreibt alle Entscheidungen: BUY, SELL, HOLD und NONE (nicht gekauft).
        Pro Agent: Name, Score, Gewicht, Reasoning, Veto-Flag.
        """
        try:
            from core.cloud_logger import get_cloud_logger

            cl = get_cloud_logger()
            if cl is not None:
                cl.log_senate_session(
                    session_id=session.session_id,
                    symbol=session.symbol,
                    consensus_score=session.consensus_score,
                    signal_action=session.signal_action,
                    gatekeeper_approved=session.gatekeeper_approved,
                    gatekeeper_reason=session.gatekeeper_reason,
                    votes=session.votes,  # [{agent_name, score, weight, reasoning, vetoed}, ...]
                    vote_count=len(session.votes),
                )
        except Exception as exc:
            logger.debug("SenateProtocol: Cloud SQL Sink nicht verfügbar: %s", exc)


def make_session_id() -> str:
    """Erzeugt eine eindeutige Session-ID (UUID4)."""
    return str(uuid.uuid4())
