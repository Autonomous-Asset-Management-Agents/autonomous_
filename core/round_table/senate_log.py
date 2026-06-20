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
import hashlib
import json
import logging
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Coroutine, Optional, Union

logger = logging.getLogger(__name__)


# --- Background audit-task tracking (#1253) ---------------------------------
# Primary fix: fire-and-forget audit-log writes must keep a STRONG reference,
# otherwise the event loop only holds a weak reference and the task can be
# garbage-collected before it runs (asyncio footgun / RUF006).
# Defense-in-depth: the done-callback surfaces any *unexpected* failure or
# cancellation at WARNING. (The inner writers already self-guard and log, so
# this is a backstop for exceptions raised outside those try-blocks.)
_BACKGROUND_AUDIT_TASKS: "set[asyncio.Task]" = set()


def _on_audit_task_done(task: "asyncio.Task") -> None:
    _BACKGROUND_AUDIT_TASKS.discard(task)
    if task.cancelled():
        logger.warning("Senate audit-log task was cancelled before completion")
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Senate audit-log task failed: %s", exc, exc_info=exc)


def spawn_audit_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Schedule a fire-and-forget audit-log coroutine while retaining a strong
    reference (prevents GC drop) and surfacing failures at WARNING (#1253)."""
    task = asyncio.ensure_future(coro)
    _BACKGROUND_AUDIT_TASKS.add(task)
    task.add_done_callback(_on_audit_task_done)
    return task


# Log-Verzeichnis (konfigurierbar via Env-Variable)


def _resolve_log_dir() -> str:
    """Resolve the senate-protocol log dir (G0a, #1050 — PR-review P0-1, INV-29 sibling).

    Precedence: explicit ``SENATE_LOG_DIR`` > ``AAA_USER_DATA_DIR``-anchored
    (desktop installs: CWD = Program Files is not writable) > legacy
    CWD-relative path UNCHANGED (cloud byte-identical).
    """
    explicit = os.getenv("SENATE_LOG_DIR", "").strip()
    if explicit:
        return explicit
    user_data_dir = os.getenv("AAA_USER_DATA_DIR", "").strip()
    if user_data_dir:
        return os.path.join(user_data_dir, "cloud_fallback_logs")
    return "cloud_fallback_logs"


_LOG_DIR = Path(_resolve_log_dir())
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


@dataclass
class HITLPolicyEvent:
    """Immutable record of a change to the HITL autonomy policy (EU AI Act Art. 14, PR-0a)."""

    timestamp: str
    actor: str
    old_policy: dict
    new_policy: dict


@dataclass
class HITLExecutionEvent:
    """Immutable record of one HITL order-decision outcome, stamped with the active policy hash.

    ``branch`` ∈ {under_limit, risk_off_exempt, queued, approved, rejected, expired,
    iron_dome_rejected}. Optional fields are populated per branch (e.g. ``threshold_breached``
    on a queued order, ``reason`` on a rejection).
    """

    timestamp: str
    symbol: str
    action: str
    branch: str
    policy_hash: str
    order_value: float
    day_notional_after: Optional[float] = None
    approval_id: Optional[str] = None
    threshold_breached: Optional[str] = None
    reason: Optional[str] = None


def _hitl_event_to_dict(event: Union[HITLPolicyEvent, HITLExecutionEvent]) -> dict:
    """Serialise a HITL event to its audit-entry dict, tagged with a discriminator.

    Each dataclass carries ONLY its own fields, so ``asdict`` yields no null-noise (D1).
    """
    if isinstance(event, HITLPolicyEvent):
        return {"event_type": "hitl_policy", **asdict(event)}
    if isinstance(event, HITLExecutionEvent):
        return {"event_type": "hitl_execution", **asdict(event)}
    raise TypeError(f"Unsupported HITL event type: {type(event).__name__!r}")


class IAuditLogger(ABC):
    """Abstract interface for Round-Table session + HITL audit logging."""

    @abstractmethod
    async def log_session(self, session: SenateSession) -> None:
        """Loggt eine Round-Table-Session."""
        ...

    @abstractmethod
    async def log_hitl_event(
        self, event: Union[HITLPolicyEvent, HITLExecutionEvent]
    ) -> None:
        """Persist a HITL policy-change or per-execution audit event (EU AI Act Art. 14)."""
        ...


class LocalJSONAuditLogger(IAuditLogger):
    """
    OSS Community Fallback: Writes Audit Logs strictly to a local JSONL file.
    No Database, No Redis.
    Includes a pre-write Disk-Space Check to prevent trading-loop crashes.
    Implements a simple Hash-Chain for MiFID II 'Glass Box' compliance.
    """

    def __init__(self) -> None:
        self._log_dir = Path(os.getenv("SENATE_LOG_DIR", "oss_audit_logs"))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._disk_check_failed = False
        self._last_hash = "0" * 64
        self._per_loop_locks: dict[int, asyncio.Lock] = {}

    async def log_session(self, session: SenateSession) -> None:
        # Puts minimal, non-blocking logs to stdout
        logger.info(
            "[AUDIT] %s: Score=%.3f | Action=%s | Gatekeeper=%s",
            session.symbol,
            session.consensus_score,
            session.signal_action,
            session.gatekeeper_approved,
        )
        # Disk full guard - schedule off the hot path, but tracked (#1253)
        spawn_audit_task(self._async_log_to_jsonl(session))

    async def _async_log_to_jsonl(self, session: SenateSession) -> None:
        entry = {
            "session_id": session.session_id,
            "symbol": session.symbol,
            "timestamp": session.timestamp,
            "consensus_score": session.consensus_score,
            "gatekeeper_approved": session.gatekeeper_approved,
            "gatekeeper_reason": session.gatekeeper_reason,
            "signal_action": session.signal_action,
            "votes": session.votes,
        }
        await self._write_to_hash_chain(entry)

    async def _write_to_hash_chain(self, entry: dict) -> None:
        """Append ``entry`` to today's audit JSONL under the SHA-256 hash chain.

        Generic — takes a ready dict (NOT a SenateSession) — so ``log_session`` and
        ``log_hitl_event`` share the ONE tamper-evident chain (N7). Disk-space guarded;
        failures are logged, never raised (audit must not crash the trading loop).
        """
        try:
            # Offload CPU/Disk-intensive task to a thread.
            free_space = await asyncio.to_thread(shutil.disk_usage, self._log_dir)
            if free_space.free < 100 * 1024 * 1024:
                if not self._disk_check_failed:
                    logger.warning(
                        "LocalJSONAuditLogger: Disk space < 100MB! Audit logging suspended to prevent crash."
                    )
                    self._disk_check_failed = True
                return
            self._disk_check_failed = False

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = self._log_dir / f"audit_log_{today}.jsonl"

            loop = asyncio.get_running_loop()
            loop_id = id(loop)
            if loop_id not in self._per_loop_locks:
                self._per_loop_locks[loop_id] = asyncio.Lock()
            lock = self._per_loop_locks[loop_id]

            async with lock:
                entry["prev_hash"] = self._last_hash
                entry_str = json.dumps(entry, sort_keys=True)
                self._last_hash = hashlib.sha256(entry_str.encode()).hexdigest()
                entry["hash"] = self._last_hash

                def _write():
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")

                await asyncio.to_thread(_write)

        except Exception as exc:
            logger.warning("LocalJSONAuditLogger write failed: %s", exc)

    async def log_hitl_event(
        self, event: Union[HITLPolicyEvent, HITLExecutionEvent]
    ) -> None:
        """Persist a HITL audit event onto the SAME SHA-256 hash chain as Senate sessions.

        Awaited (NOT fire-and-forget like ``log_session``): HITL events are low-frequency
        and compliance-critical (EU AI Act Art. 14), so the write must complete + be durable.
        """
        await self._write_to_hash_chain(_hitl_event_to_dict(event))


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

        # Optional: Database Sink (async, fire-and-forget — tracked so it is not
        # GC-dropped and failures are surfaced, #1253)
        spawn_audit_task(self._log_to_database(session))

    async def log_hitl_event(
        self, event: Union[HITLPolicyEvent, HITLExecutionEvent]
    ) -> None:
        """HITL audit on the enterprise SenateProtocol path (fail-soft).

        The tamper-evident HITL chain is the ``LocalJSONAuditLogger`` SHA-256 JSONL — the
        HITL order path uses that logger directly for Art-14 evidence (PR-0a-ii-4/-5).
        SenateProtocol only records the event fire-and-forget; exceptions are logged, never
        raised. The enterprise Cloud-SQL HITL sink (``approval_queue``) lands in PR-0a-ii-7.
        """
        try:
            logger.info(
                "[HITL-AUDIT] %s recorded (SenateProtocol path; tamper-evident chain = "
                "LocalJSONAuditLogger).",
                _hitl_event_to_dict(event).get("event_type"),
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - fire-and-forget audit must not raise
            logger.warning("SenateProtocol.log_hitl_event failed: %s", exc)

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
