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
import os
import re
import time

from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from core.cloud_logger import SecretMaskMixin

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
    ``start_time``; OTel nanosecond epoch). If the file is still over
    ``max_bytes``, drops the oldest records until it fits, keeping the newest.
    Unparseable lines are dropped. Rewrites atomically. ``max_bytes=None``
    disables the size cap. Never raises — telemetry must never break the app."""
    path = os.path.join(export_dir, "telemetry.jsonl")
    try:
        if not os.path.exists(path):
            return
        if now_ns is None:
            now_ns = time.time_ns()
        cutoff_ns = now_ns - int(max_age_days * _DAY_NS)
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.readlines()
        kept = []
        for ln in raw:
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
            kept.append(ln)
        if max_bytes is not None:
            total = 0
            start = 0
            for i in range(len(kept) - 1, -1, -1):
                total += len(kept[i].encode("utf-8")) + 1  # +1 for the newline
                if total > max_bytes:
                    start = i + 1
                    break
            kept = kept[start:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            if kept:
                fh.write("\n".join(kept) + "\n")
        os.replace(tmp, path)
    except Exception:
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
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            # Keep the store bounded within a long session too (INF-13 P2 #1456).
            try:
                if (
                    self._max_bytes is not None
                    and os.path.getsize(self._path) > self._max_bytes
                ):
                    prune_store(self._dir, self._max_age_days, self._max_bytes)
            except Exception:
                pass
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

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
