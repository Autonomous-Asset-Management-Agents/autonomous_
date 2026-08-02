# core/telemetry_local.py
# INF-13 P1 (#1371) — local, PII-scrubbed, NO-EGRESS telemetry for the desktop edition.
#
# The desktop bundle ships opentelemetry-api/sdk only (no exporter) — so OTel is dead on
# desktop today. This module provides a LOCAL SpanExporter that scrubs every span on-box
# and appends it to <USER_DATA_DIR>/telemetry/*.jsonl. Zero network, zero consent: nothing
# leaves the device. Cloud (K_SERVICE) is untouched — this module is only attached on the
# DEPLOYMENT_MODE=LOCAL branch of init_telemetry().
#
# Scrubbing is the load-bearing legal line (§25 TDDDG / Art.6 DSGVO — data must be clean
# before it is even written). We reuse the engine's SecretMaskMixin for secret values and
# strip OS-username paths; the Resource is built minimal (no host/process detectors → no
# process.owner = the OS username).
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque

from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from core.cloud_logger import SecretMaskMixin

logger = logging.getLogger(__name__)

# --- Scrubbing ---------------------------------------------------------------

_USER_PATH_WIN = re.compile(r"(?i)([A-Za-z]:[\\/]+Users[\\/]+)[^\\/]+")
_USER_PATH_NIX = re.compile(r"(?i)((?:/home|/Users)/)[^/]+")


class _Scrubber(SecretMaskMixin):
    """Thin concrete SecretMaskMixin so we can pull the configured secret values."""


_scrubber = _Scrubber()


def _secret_values() -> list:
    try:
        return [s for s in _scrubber._get_secrets() if s]
    except Exception:
        return []


def scrub_text(value) -> str:
    """Redact PII from a string BEFORE it is written: configured secrets + OS-username
    paths (Windows ``C:\\Users\\<name>`` and Unix ``/home/<name>`` / ``/Users/<name>``).
    ``None`` -> ``""``. Pure; never raises."""
    if value is None:
        return ""
    s = str(value)
    for secret in _secret_values():
        if secret and secret in s:
            s = s.replace(secret, "[redacted-secret]")
    s = _USER_PATH_WIN.sub(r"\1[user]", s)
    s = _USER_PATH_NIX.sub(r"\1[user]", s)
    return s


def _scrub_attrs(attrs) -> dict:
    out = {}
    for k, v in dict(attrs or {}).items():
        out[k] = scrub_text(v) if isinstance(v, str) else v
    return out


# --- Resource (minimal — no PII / no host/process detectors) -----------------


def minimal_resource(service_name: str, version: str) -> Resource:
    """A deliberately minimal Resource: service identity only. No host/os/process
    detectors → never emits ``process.owner`` (the OS username), ``host.name`` or
    ``process.command_line``."""
    return Resource.create({SERVICE_NAME: service_name, SERVICE_VERSION: version})


# --- Retention / rotation (INF-13 P2 #1456) ----------------------------------

_DAY_NS = 86_400 * 10**9
DEFAULT_RETENTION_DAYS = 7
DEFAULT_RETENTION_BYTES = 50 * 1024 * 1024  # 50 MB


def prune_store(
    export_dir: str,
    max_age_days: float = DEFAULT_RETENTION_DAYS,
    max_bytes=DEFAULT_RETENTION_BYTES,
    now_ns: int = None,
) -> None:
    """Bound ``<export_dir>/telemetry.jsonl`` by age and size so a long-lived
    desktop install never accumulates an unbounded buffer (Art. 5(1)(e) storage
    limitation; egress is separate and activation-gated, #1457).

    Drops records older than ``max_age_days`` (by ``end_time``, falling back to
    ``start_time``; OTel nanosecond epoch) and, if still over ``max_bytes``, the
    oldest records until it fits — keeping the newest. Unparseable lines are
    dropped. Rewrites atomically. ``max_bytes=None`` disables the size cap.

    Streams line-by-line into a byte-bounded ``deque`` so repairing a large legacy
    store never materialises the whole file in RAM (a ``readlines()`` of the 114 MB
    store peaked ~580 MB at boot). ``errors="replace"``: a single non-UTF-8 byte
    must NOT abort the prune (that silently killed retention → unbounded growth).

    Also age-prunes the rotated sibling ``telemetry.1.jsonl`` by mtime, so age
    retention covers ``telemetry*.jsonl`` — not just the active file (Art. 5(1)(e)).
    Never raises — telemetry must never break the app."""
    path = os.path.join(export_dir, "telemetry.jsonl")
    try:
        if now_ns is None:
            now_ns = time.time_ns()
        cutoff_ns = now_ns - int(max_age_days * _DAY_NS)
        if os.path.exists(path):
            kept = deque()  # (line, byte_len) of the newest records within budget
            kept_bytes = 0
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for ln in fh:  # streamed — never loads the whole file at once
                    ln = ln.rstrip("\n")
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except Exception:
                        continue  # drop unparseable
                    ts = rec.get("end_time")
                    if not isinstance(ts, (int, float)):
                        ts = rec.get("start_time")
                    if isinstance(ts, (int, float)) and ts < cutoff_ns:
                        continue  # too old
                    blen = len(ln.encode("utf-8")) + 1  # +1 for the newline
                    kept.append((ln, blen))
                    kept_bytes += blen
                    if max_bytes is not None:
                        # keep the NEWEST records whose cumulative size fits; never
                        # drop the sole remaining line even if it alone exceeds the cap
                        while kept_bytes > max_bytes and len(kept) > 1:
                            kept_bytes -= kept.popleft()[1]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                if kept:
                    fh.write("\n".join(ln for ln, _ in kept) + "\n")
            os.replace(tmp, path)
        # Age-prune the rotated sibling by mtime: a telemetry.1.jsonl last written
        # more than max_age_days ago holds only expired records → drop it (O(1)).
        rotated = os.path.join(export_dir, "telemetry.1.jsonl")
        if os.path.exists(rotated):
            cutoff_s = now_ns / 1e9 - max_age_days * 86_400
            if os.path.getmtime(rotated) < cutoff_s:
                os.remove(rotated)
    except Exception as exc:
        # Never raise out (telemetry must not break the app) — but LOG it. A silent
        # return here previously hid a UnicodeDecodeError that disabled retention and
        # let telemetry.jsonl grow unbounded to 114 MB.
        logger.warning("prune_store: retention pass failed (non-fatal): %s", exc)
        return


# --- Local file exporter (no egress) -----------------------------------------


class LocalScrubbingSpanExporter(SpanExporter):
    """Scrubs each ReadableSpan and appends it as JSONL to ``<export_dir>/telemetry.jsonl``.
    A file writer — it opens no socket. Never raises out of ``export`` (telemetry must
    never break the app). On construction it prunes the store (INF-13 P2 #1456) so a
    long-lived install stays bounded by age/size."""

    def __init__(
        self,
        export_dir: str,
        max_age_days: float = DEFAULT_RETENTION_DAYS,
        max_bytes=DEFAULT_RETENTION_BYTES,
    ):
        self._dir = export_dir
        self._path = os.path.join(export_dir, "telemetry.jsonl")
        self._max_age_days = max_age_days
        self._max_bytes = max_bytes
        # SimpleSpanProcessor.on_end calls export() with NO lock, and spans end on
        # multiple threads (HTTP on the event loop; model.inference / risk / order /
        # crash-hook spans on background threads). Serialise rotate+append so a
        # rotation cannot delete another thread's just-rotated file and concurrent
        # appends cannot interleave into a torn (non-UTF-8) line.
        self._lock = threading.Lock()
        try:
            os.makedirs(export_dir, exist_ok=True)
        except Exception:
            pass
        prune_store(export_dir, max_age_days, max_bytes)  # bound on startup

    def _span_to_record(self, span) -> dict:
        status = None
        try:
            status = span.status.status_code.name if span.status else None
        except Exception:
            status = None
        events = []
        for ev in getattr(span, "events", None) or []:
            events.append(
                {"name": scrub_text(ev.name), "attributes": _scrub_attrs(ev.attributes)}
            )
        return {
            "name": scrub_text(span.name),
            "status": status,
            "start_time": getattr(span, "start_time", None),
            "end_time": getattr(span, "end_time", None),
            "attributes": _scrub_attrs(span.attributes),
            "events": events,
        }

    def export(self, spans) -> SpanExportResult:
        try:
            lines = [
                json.dumps(self._span_to_record(s), ensure_ascii=False) for s in spans
            ]
            if not lines:
                return SpanExportResult.SUCCESS
            # Size-bound in O(1) BEFORE the append. The old code called prune_store
            # (an O(file-size) readlines+json.loads+rewrite) here on EVERY span-end;
            # under SimpleSpanProcessor that runs synchronously on the ending thread
            # (the asyncio event loop for HTTP spans), so once the file hit the cap it
            # starved the loop (py-spy: ~98 % of event-loop busy CPU in prune_store).
            # Rotation is O(1) and never reads the file. Age retention runs on startup.
            # rotate+append under the lock: rotation must not delete another thread's
            # just-rotated file, and concurrent appends must not interleave a torn line.
            with self._lock:
                self._rotate_if_over_cap()
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def _rotate_if_over_cap(self) -> None:
        """O(1) size bound: when the active store exceeds ``max_bytes``, move it aside
        to a ``telemetry.1.jsonl`` sibling and start a fresh file. The ``.jsonl`` suffix
        is REQUIRED so the diagnostics export (telemetry-export.cjs bundles every
        ``*.jsonl``) still includes the rotated data. Keeps exactly one rotation.
        Callers hold ``self._lock``. Never raises — telemetry must never break the app,
        but a persistent failure IS logged (a silently-failing rotation would let the
        active file grow unbounded again — the exact blind spot this module fixes)."""
        try:
            if self._max_bytes is None or not os.path.exists(self._path):
                return
            if os.path.getsize(self._path) <= self._max_bytes:
                return
            rotated = os.path.join(self._dir, "telemetry.1.jsonl")
            if os.path.exists(rotated):
                os.remove(rotated)
            os.replace(self._path, rotated)
        except Exception as exc:
            logger.warning("telemetry rotation failed (non-fatal): %s", exc)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# --- Crash hooks -------------------------------------------------------------


def install_crash_hooks(tracer) -> None:
    """Turn uncaught Python exceptions into crash spans (then chain to the default
    handler so behaviour is unchanged). ``faulthandler`` captures hard crashes to a
    sibling file. Never raises."""
    import sys
    import threading

    def _emit(exc_type, exc, tb):
        try:
            with tracer.start_as_current_span("crash.uncaught_exception") as span:
                span.set_attribute("exception.type", getattr(exc_type, "__name__", "?"))
                span.record_exception(exc)
        except Exception:
            pass

    def _hook(exc_type, exc, tb):
        _emit(exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        _emit(args.exc_type, args.exc_value, args.exc_traceback)

    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass
