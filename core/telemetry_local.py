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


# --- Local file exporter (no egress) -----------------------------------------


class LocalScrubbingSpanExporter(SpanExporter):
    """Scrubs each ReadableSpan and appends it as JSONL to ``<export_dir>/telemetry.jsonl``.
    A file writer — it opens no socket. Never raises out of ``export`` (telemetry must
    never break the app)."""

    def __init__(self, export_dir: str):
        self._dir = export_dir
        self._path = os.path.join(export_dir, "telemetry.jsonl")
        try:
            os.makedirs(export_dir, exist_ok=True)
        except Exception:
            pass

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
