# tests/unit/test_execution_block_telemetry.py
# #2069 (INF-13) — execution/compliance block visibility in the diagnose-export.
#
# The block reasons already exist (in-memory outcome store + compliance
# reason_code) but never reach the OTel telemetry.jsonl store that the desktop
# diagnose-export reads. `emit_block_span` is the single scrubbed sink-helper:
# ONE short "execution.block" span per block, emitted at two choke-points
# (executor outcome store + ComplianceGuardian finally). Flag-gated
# (EXECUTION_BLOCK_TELEMETRY_ENABLED, default OFF = byte-identical), fail-safe
# (NEVER raises into the trading path), scrubbed by the existing
# LocalScrubbingSpanExporter (DSGVO/TDDDG, analog #1833).
import importlib.util
import json
from pathlib import Path

import pytest

import config
import core.telemetry as tele
from core.round_table import execution_outcomes as eo

_ROOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        config, "EXECUTION_BLOCK_TELEMETRY_ENABLED", True, raising=False
    )


@pytest.fixture
def flag_off(monkeypatch):
    # #2839: default is now ON — the OFF path is forced explicitly to stay tested.
    monkeypatch.setattr(
        config, "EXECUTION_BLOCK_TELEMETRY_ENABLED", False, raising=False
    )


@pytest.fixture
def span_capture(monkeypatch):
    """Isolated in-memory OTel pipeline; core.telemetry.get_tracer routed to it."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tele, "get_tracer", lambda name: provider.get_tracer(name))
    return exporter


# --- R1/R2: flag gate -----------------------------------------------------------


def test_flag_off_emits_no_span(flag_off, span_capture):
    # OFF path stays byte-identical (rollback lever); OFF is forced since #2839
    # flipped the default ON (= desktop launcher profile).
    eo.emit_block_span("AAPL", "blocked:risk", "size 0")
    assert len(span_capture.get_finished_spans()) == 0


def test_flag_on_emits_exactly_one_block_span(flag_on, span_capture):
    eo.emit_block_span("AAPL", "blocked:risk", "size 0")
    spans = span_capture.get_finished_spans()
    assert len(spans) == 1
    (sp,) = spans
    assert sp.name == "execution.block"
    assert sp.attributes["block.reason_code"] == "blocked:risk"
    assert sp.attributes["block.symbol"] == "AAPL"
    assert sp.attributes["block.detail"] == "size 0"


def test_empty_detail_attribute_is_omitted(flag_on, span_capture):
    eo.emit_block_span("NVDA", "compliance:max_order_value")
    (sp,) = span_capture.get_finished_spans()
    assert "block.detail" not in sp.attributes
    assert sp.attributes["block.reason_code"] == "compliance:max_order_value"


# --- R3: fail-safe — observation must never touch execution ---------------------


def test_tracer_failure_never_raises(flag_on, monkeypatch):
    def _boom(name):
        raise RuntimeError("otel down")

    monkeypatch.setattr(tele, "get_tracer", _boom)
    assert eo.emit_block_span("AAPL", "blocked:risk") is None  # swallowed


# --- R14: scrubbing round-trip through the REAL local exporter (DSGVO/TDDDG) ----


def test_block_span_is_scrubbed_in_local_store(flag_on, tmp_path, monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from core.telemetry_local import LocalScrubbingSpanExporter, minimal_resource

    tele_dir = tmp_path / "telemetry"
    provider = TracerProvider(resource=minimal_resource("aaa-desktop", "v1"))
    provider.add_span_processor(
        SimpleSpanProcessor(LocalScrubbingSpanExporter(str(tele_dir)))
    )
    monkeypatch.setattr(tele, "get_tracer", lambda name: provider.get_tracer(name))

    eo.emit_block_span("AAPL", "blocked:risk", detail=r"C:\Users\georg\data\x.log")
    provider.force_flush()

    blob = (tele_dir / "telemetry.jsonl").read_text(encoding="utf-8")
    assert "georg" not in blob  # OS-username path scrubbed before write
    rec = json.loads(blob.splitlines()[0])
    assert rec["name"] == "execution.block"
    assert rec["attributes"]["block.reason_code"] == "blocked:risk"
    assert rec["attributes"]["block.symbol"] == "AAPL"


# --- Config flag: default OFF + BORA parity config.py <-> config.oss.py ---------


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_2069", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_config_flag_default_on():
    # #2839: default ON = shipped desktop launcher profile; spans stay LOCAL
    # (egress separately gated by TELEMETRY_EGRESS_ENABLED=False).
    assert config.EXECUTION_BLOCK_TELEMETRY_ENABLED is True


def test_config_oss_flag_parity_default_on():
    cfg = _load_oss_config()
    assert hasattr(cfg, "EXECUTION_BLOCK_TELEMETRY_ENABLED"), (
        "config.oss.py must carry EXECUTION_BLOCK_TELEMETRY_ENABLED "
        "(BORA dual-edition parity)"
    )
    assert cfg.EXECUTION_BLOCK_TELEMETRY_ENABLED is True
