# tests/unit/test_otel_middleware_traceparent.py
# INF-13 OBS-5 (#2638): the HTTP middleware must CONTINUE an incoming W3C
# ``traceparent`` so the trace UI-click -> engine-handler -> trading-action is
# one connected trace. Today OtelSpanMiddleware.dispatch starts the request span
# without ``context=extract(headers)`` -> every request is a new root.
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import core.otel_middleware as mw

_TP = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_PARENT_ID = "b7ad6b7169203331"


def _client_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Override the process-global (set-once) provider for this test so the
    # middleware's trace.get_tracer() records into our in-memory exporter.
    trace_api._TRACER_PROVIDER = provider
    app = FastAPI()
    app.add_middleware(mw.OtelSpanMiddleware)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False), exporter


def _req_span(exporter):
    spans = [s for s in exporter.get_finished_spans() if s.name == "GET /ok"]
    assert spans, "no request span was exported"
    return spans[0]


def test_incoming_traceparent_is_continued():
    client, exporter = _client_and_exporter()
    client.get("/ok", headers={"traceparent": _TP})
    span = _req_span(exporter)
    assert format(span.context.trace_id, "032x") == _TRACE_ID
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == _PARENT_ID


def test_missing_traceparent_starts_new_root():
    client, exporter = _client_and_exporter()
    client.get("/ok")
    span = _req_span(exporter)
    assert span.parent is None
    assert format(span.context.trace_id, "032x") != _TRACE_ID


def test_malformed_traceparent_does_not_raise():
    client, exporter = _client_and_exporter()
    resp = client.get("/ok", headers={"traceparent": "not-a-valid-traceparent"})
    assert resp.status_code == 200
    span = _req_span(exporter)
    assert span.parent is None  # unparseable header -> ignored -> new root


def test_cors_preflight_allows_traceparent():
    # The browser CORS preflight must whitelist ``traceparent`` or it strips the
    # header on cross-origin (console) requests, breaking the egress half.
    from core.engine.api_routes import app

    client = TestClient(app)
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8082",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "traceparent",
        },
    )
    allow = resp.headers.get("access-control-allow-headers", "").lower()
    assert "traceparent" in allow
