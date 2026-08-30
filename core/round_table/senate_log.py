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
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Coroutine, Optional, Union

logger = logging.getLogger(__name__)


# --- ADR-OBS-01 / PR A.2: audit-write instrumentation (PURE OBSERVATION) -----
# Fail-safe counters around the tamper-evident audit-persist path. ``_bump_audit``
# swallows every error so a counter failure can NEVER raise into the audit write
# (a broken counter must never block or change an audit write). Read-only snapshot
# via ``get_audit_counters`` for /engine-diagnostics.
_AUDIT_COUNTERS: "dict[str, int]" = {"write_ok": 0, "write_fail": 0}


def _bump_audit(field: str) -> None:
    """Fail-safe audit-write counter mutation — swallows EVERY error."""
    try:
        _AUDIT_COUNTERS[field] = _AUDIT_COUNTERS.get(field, 0) + 1
    except Exception:  # noqa: BLE001 — a broken counter must never break an audit write
        pass


def get_audit_counters() -> "dict[str, int]":
    """Read-only snapshot of the audit-write counters."""
    return dict(_AUDIT_COUNTERS)


def reset_audit_counters() -> None:
    """Test/daily-reset helper — zeroes the audit-write counters."""
    _AUDIT_COUNTERS.update({"write_ok": 0, "write_fail": 0})


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
    iron_dome_rejected, skipped}. Optional fields are populated per branch (e.g.
    ``threshold_breached`` on a queued order, ``reason`` on a rejection). ``skipped``
    (#2585) records the NON-execution of an approved signal — written by
    ``order_executor._audit_skipped_signal`` with ``reason = "SKIPPED: <code>"`` where
    ``<code>`` is one of the closed taxonomy {insufficient_buying_power,
    below_order_floor, sizing_zero, execution_error, other}.
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


@dataclass
class LiveEnableEvent:
    """Immutable record of a deliberate live-trading enablement/revocation (LIVE-1 T4, #1427).

    EU AI Act Art. 14: written to the tamper-evident SHA-256 chain BEFORE the desktop shell is
    permitted to boot the engine live (audit-before-enable). All fields are ``str`` — NO floats —
    so the SHA-256 preimage (``json.dumps(entry, sort_keys=True)``) is byte-identical when the JS
    verifier (``audit-chain.cjs`` ``verifyAuditChain``) recomputes it; float repr can diverge
    between ``json.dumps`` and ``JSON.stringify`` and would permanently fail-close live trading.
    ``action`` ∈ {enable, disable}; a later ``disable`` revokes an earlier ``enable``.

    ``switch_id`` (#2277 INC-1) is the UI→API→boot→revoke correlation id — a ``str`` (never a float),
    so the SHA-256 preimage stays byte-stable for the JS verifier. It defaults to the ``nonce`` at the
    write boundary (``hitl_gate.log_live_enablement_event``) so the record is never ``null``; a
    compensating ``disable`` inherits the *enable's* switch_id, joining a degrade back to its arming.
    """

    timestamp: str
    actor: str
    action: str
    acknowledgment: str
    nonce: str
    switch_id: Optional[str] = None


@dataclass
class EulaAcceptanceEvent:
    """Immutable record of a first-run EULA + Risk-Disclosure acceptance (GTM-1 T3, #1466).

    Sealed onto the SAME tamper-evident SHA-256 chain as the other audit events, on the first
    engine boot after the desktop wizard recorded the operator's deliberate acceptance. All fields
    are ``str`` (float-free), so the preimage is byte-stable (mirrors LiveEnableEvent, LIVE-1 T4).
    """

    timestamp: str
    actor: str
    document: str
    version: str
    text_sha256: str
    app_version: str


@dataclass
class ManualCancelEvent:
    """Immutable record of an operator's manual HITL cancel of a working order (#2137).

    EU AI Act Art. 14 human-oversight intervention: the operator pulled a live/pending order by
    hand from the console (always available, independent of any autonomous mode). Sealed onto the
    SAME tamper-evident SHA-256 chain as the other audit events; all fields are ``str`` (float-free)
    so the SHA-256 preimage stays byte-stable for the JS verifier (mirrors LiveEnableEvent, T4).
    """

    timestamp: str
    actor: str
    order_id: str


@dataclass
class TradingControlEvent:
    """Immutable record of the operator's effective trading-control settings (#2863 Inc 4).

    The two operator-tunable risk controls — the volatility-sizing master switch
    (``VOL_TARGETING_SIZING_ENABLED``) and its daily-vol target (``VOL_TARGET_DAILY_VOL``) —
    are set by the operator in the desktop console (no advice — the operator's own judgement)
    and take effect at engine boot via env injection (#2865). Sealed onto the SAME
    tamper-evident SHA-256 chain as the other audit events so every run's governing risk-control
    state is on-chain (compliance evidence, not investment advice).

    All fields are ``str`` (float-free) so the SHA-256 preimage is byte-stable for the JS verifier
    (``audit-chain.cjs``) — a float repr can diverge between ``json.dumps`` and ``JSON.stringify``.
    ``vol_target_daily_vol`` therefore carries the canonical shortest round-trip string of the
    effective float (e.g. ``"0.015"``); ``vol_targeting_sizing_enabled`` is ``"true"``/``"false"``.
    """

    timestamp: str
    actor: str
    vol_targeting_sizing_enabled: str
    vol_target_daily_vol: str
    app_version: str


_HITLEvent = Union[
    HITLPolicyEvent,
    HITLExecutionEvent,
    LiveEnableEvent,
    EulaAcceptanceEvent,
    ManualCancelEvent,
    TradingControlEvent,
]


def _hitl_event_to_dict(event: _HITLEvent) -> dict:
    """Serialise a HITL / live-enablement event to its audit-entry dict, tagged with a discriminator.

    Each dataclass carries ONLY its own fields, so ``asdict`` yields no null-noise (D1).
    """
    if isinstance(event, HITLPolicyEvent):
        return {"event_type": "hitl_policy", **asdict(event)}
    if isinstance(event, HITLExecutionEvent):
        return {"event_type": "hitl_execution", **asdict(event)}
    if isinstance(event, LiveEnableEvent):
        return {"event_type": "live_enablement", **asdict(event)}
    if isinstance(event, EulaAcceptanceEvent):
        return {"event_type": "eula_acceptance", **asdict(event)}
    if isinstance(event, ManualCancelEvent):
        return {"event_type": "manual_order_cancel", **asdict(event)}
    if isinstance(event, TradingControlEvent):
        return {"event_type": "trading_control", **asdict(event)}
    raise TypeError(f"Unsupported HITL event type: {type(event).__name__!r}")


class IAuditLogger(ABC):
    """Abstract interface for Round-Table session + HITL audit logging."""

    @abstractmethod
    async def log_session(self, session: SenateSession) -> None:
        """Loggt eine Round-Table-Session."""
        ...

    @abstractmethod
    async def log_hitl_event(self, event: _HITLEvent) -> None:
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
        self._last_hash = self._recover_last_hash()
        self._per_loop_locks: dict[int, asyncio.Lock] = {}

    _DATE_FILE_RE = re.compile(r"^audit_log_\d{4}-\d{2}-\d{2}\.jsonl$")

    # Lesefenster fuer die Hash-Wiederaufnahme (#2956). Gestaffelt statt einmalig gross: der
    # Normalfall (letzte Zeile = juengster Datensatz) ist nach 64 KB erledigt, nur eine
    # pathologisch lange Zeile zwingt zum Nachfassen. Die Obergrenze ist bewusst endlich — ein
    # unbegrenzter Rueckfall waere wieder der Fehler, den dieser Code behebt.
    _TAIL_WINDOWS_BYTES = (64 * 1024, 1024 * 1024, 16 * 1024 * 1024)

    @classmethod
    def _last_hash_in_tail(cls, log_file: Path) -> "str | None":
        """Juengster gueltiger Hash einer Logdatei — gelesen vom DATEIENDE.

        Frueher las diese Stelle die Datei per ``read_text()`` VOLLSTAENDIG. Am 19.08. war die
        Tagesdatei 2,59 GB gross (alle Sim-Laeufe schreiben in dasselbe Verzeichnis), was im
        Konstruktor einen ``MemoryError`` ausloeste und drei Sweep-Laeufe toetete; derselbe
        Konstruktor laeuft im Produktivpfad der WORM-Kette. Gesucht ist immer nur die LETZTE
        Hash-Zeile — dafuer genuegt das Dateiende.

        ``None`` heisst „in dieser Datei nichts gefunden", nicht „Fehler": der Aufrufer geht dann
        zur naechstaelteren Datei, genau wie bisher.
        """
        size = log_file.stat().st_size
        if size == 0:
            return None
        for window in cls._TAIL_WINDOWS_BYTES:
            start = max(0, size - window)
            with log_file.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read()
            # errors="replace": ein Byte-Fenster kann ein UTF-8-Zeichen zerschneiden. Der Schaden
            # liegt am ANFANG des Puffers — also in der Zeile, die direkt darunter verworfen wird.
            lines = chunk.decode("utf-8", errors="replace").splitlines()
            if start > 0 and lines:
                lines = lines[1:]  # angeschnitten ⇒ kein vollstaendiger Datensatz
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(rec.get("hash"), str) and len(rec["hash"]) == 64:
                    return rec["hash"]
            if start == 0:
                return None  # ganze Datei gesehen, nichts gefunden
        logger.warning(
            "SenateLog: kein Hash in den letzten %d Bytes von %s (Datei %d Bytes) — Kette wird "
            "aus einer aelteren Datei fortgesetzt. Ungewoehnlich lange Zeilen? (#2956)",
            cls._TAIL_WINDOWS_BYTES[-1],
            log_file.name,
            size,
        )
        return None

    def _recover_last_hash(self) -> str:
        """Resume the hash chain from the last record in the newest daily log file.

        Fail-safe: any error (OSError, JSONDecodeError, empty file, non-conforming
        filenames) falls back to the null-hash — identical to a fresh chain. This
        method is read-only and never blocks or raises.

        #2956: „never raises" war eine Zusage ohne Deckung — ``MemoryError`` ist kein ``OSError``
        und lief durch. Der Fang ist jetzt breit und LAUT (WARNING, CODING_POLICY §5.6): ein
        stiller Ketten-Neustart ist audit-relevant und darf nicht unbemerkt bleiben.
        """
        try:
            # ADR: strict date-filename regex matching JS parity
            files = sorted(
                f
                for f in self._log_dir.iterdir()
                if f.is_file() and self._DATE_FILE_RE.match(f.name)
            )
            if not files:
                return "0" * 64

            # Archon v3 Fix: Iterate backwards through ALL files in case today's file is empty (e.g. midnight restart)
            for log_file in reversed(files):
                try:
                    found = self._last_hash_in_tail(log_file)
                except OSError:
                    continue
                if found:
                    return found
            return "0" * 64
        except OSError:
            return "0" * 64
        except (
            Exception
        ):  # noqa: BLE001 — u. a. MemoryError; der Start darf nie daran kippen
            logger.warning(
                "SenateLog: Hash-Wiederaufnahme fehlgeschlagen — Kette startet neu bei 0. "
                "AUDIT-RELEVANT: die Verkettung zum Vortag fehlt (#2956).",
                exc_info=True,
            )
            return "0" * 64

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
                if entry.get("event_type") == "live_enablement":
                    # C4 (#2369): Ed25519-sign the record hash so a raw file append can no
                    # longer forge a live-enable - the desktop shell verifies the sig
                    # (unsigned/invalid -> fail-closed to PAPER once AAA_WORM_PUBKEYS is
                    # provisioned). Signing the hash string keeps Python<->JS byte-parity trivial.
                    from core.round_table.worm_signing import sign_worm_hash

                    entry["sig"] = sign_worm_hash(self._last_hash)

                def _write():
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")

                await asyncio.to_thread(_write)
                # PR A.2: fail-safe — the audit entry was durably persisted. DOUBLE-guarded
                # (call site + inside _bump) so a broken counter can never turn a successful
                # write into a raised exception (which would be misread as a write failure).
                try:
                    _bump_audit("write_ok")
                except (
                    Exception
                ):  # noqa: BLE001 — observation must never break the write
                    pass

        except Exception as exc:
            # PR A.2: fail-safe — the audit persist failed (disk/IO). Counted first
            # (double-guarded), then the pre-existing swallow keeps the trading loop uncrashed.
            try:
                _bump_audit("write_fail")
            except (
                Exception
            ):  # noqa: BLE001 — observation must never escape the audit path
                pass
            logger.warning("LocalJSONAuditLogger write failed: %s", exc)

    async def log_hitl_event(self, event: _HITLEvent) -> None:
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

    async def log_hitl_event(self, event: _HITLEvent) -> None:
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
