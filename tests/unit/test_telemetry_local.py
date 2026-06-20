# tests/unit/test_telemetry_local.py
# INF-13 P1 (#1371): local, PII-scrubbed, no-egress crash/stability capture.
# The desktop edition has no OTel exporter (requirements.oss.txt ships api/sdk only),
# so OTel is dead on desktop today. P1 attaches a LOCAL file exporter that scrubs spans
# on-box and writes them to <USER_DATA_DIR>/telemetry/ — zero network, zero consent.
import json
import os

import pytest

from core.telemetry_local import (
    LocalScrubbingSpanExporter,
    minimal_resource,
    scrub_text,
)


def test_scrub_text_redacts_windows_user_path():
    s = r"Traceback at C:\Users\müller\AppData\Local\app\engine.py line 42"
    out = scrub_text(s)
    assert "müller" not in out
    assert "[redacted]" in out.lower() or "[user]" in out.lower()


def test_scrub_text_redacts_unix_home_path():
    out = scrub_text("/home/georg/.config/aaa/secrets")
    assert "georg" not in out


def test_scrub_text_redacts_known_secret(monkeypatch):
    # SecretMaskMixin pulls secrets from config; a value present in config must be masked.
    import config
    import core.telemetry_local

    # Reset cache to prevent test pollution
    core.telemetry_local._scrubber._secrets_cache = []
    core.telemetry_local._scrubber._last_cache_update = 0.0

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: type("C", (), {"ALPACA_API_KEY": "SK_LIVE_DEADBEEF_TOKEN"})(),
    )
    out = scrub_text("auth failed with key SK_LIVE_DEADBEEF_TOKEN at boot")
    assert "SK_LIVE_DEADBEEF_TOKEN" not in out


def test_minimal_resource_has_no_pii_attributes():
    res = minimal_resource("aaa-desktop", "abc123")
    attrs = dict(res.attributes)
    # The OS username / hostname / command line must never be in the resource.
    for leaky in ("process.owner", "host.name", "process.command_line"):
        assert leaky not in attrs
    assert attrs.get("service.name") == "aaa-desktop"


def test_local_exporter_writes_scrubbed_jsonl(tmp_path):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    tele_dir = tmp_path / "telemetry"
    exporter = LocalScrubbingSpanExporter(str(tele_dir))
    provider = TracerProvider(resource=minimal_resource("aaa-desktop", "v1"))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("boot.failure") as span:
        span.set_attribute("path", r"C:\Users\georg\app\x.py")
        span.set_attribute("note", "key SK_LIVE_X at line 1")

    provider.force_flush()

    files = list(tele_dir.glob("*.jsonl"))
    assert files, "a telemetry JSONL file must be written locally"
    blob = files[0].read_text(encoding="utf-8")
    assert "boot.failure" in blob
    assert "georg" not in blob  # path scrubbed
    rec = json.loads(blob.splitlines()[0])
    assert rec["name"] == "boot.failure"


def test_local_exporter_makes_no_network_call(tmp_path, monkeypatch):
    # The exporter is a file writer — assert it never opens a socket.
    import socket

    def _boom(*a, **k):
        raise AssertionError("telemetry exporter must not open a socket (no egress)")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    exporter = LocalScrubbingSpanExporter(str(tmp_path / "telemetry"))
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(resource=minimal_resource("aaa-desktop", "v1"))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.get_tracer("t").start_span("x").end()
    provider.force_flush()


def test_init_telemetry_desktop_attaches_local_exporter(tmp_path, monkeypatch):
    # End-to-end wiring: DEPLOYMENT_MODE=LOCAL + no K_SERVICE -> init_telemetry attaches
    # the local file exporter; a span lands in <dir>/telemetry/.
    from opentelemetry import trace

    import core.telemetry as tele

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(
        tele, "_local_telemetry_dir", lambda: str(tmp_path / "telemetry")
    )
    monkeypatch.setattr(tele, "_TELEMETRY_INITIALIZED", False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)

    tele.init_telemetry(service_name="aaa-desktop")
    with tele.get_tracer("test").start_as_current_span("boot.failure"):
        pass
    tele.trace.get_tracer_provider().force_flush()

    files = list((tmp_path / "telemetry").glob("*.jsonl"))
    assert files, "desktop init must attach the local file exporter"
    assert "boot.failure" in files[0].read_text(encoding="utf-8")
