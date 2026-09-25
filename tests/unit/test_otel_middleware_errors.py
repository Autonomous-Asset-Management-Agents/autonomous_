"""#2183: the OTel HTTP span middleware must make FAILURES diagnosable from a
diagnostics export.

Gaps this pins (found while diagnosing #2182 — a live-enable refusal was invisible
in telemetry):
  * a 4xx (e.g. a 403 gate refusal) was recorded as span status OK → invisible.
    Now 4xx AND 5xx set status ERROR + an ``error.type`` attribute.
  * a raised exception was not recorded on the span → empty ``events``. Now the
    middleware calls ``record_exception`` so the reason lands in the export.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import core.otel_middleware as mw


def _run(path: str):
    """Drive one request through the middleware, capturing the (mocked) span."""
    span = MagicMock()

    @contextmanager
    def fake_span(_name, context=None):  # OBS-5 (#2638): dispatch now passes context=
        yield span

    tracer = MagicMock()
    tracer.start_as_current_span = fake_span

    app = FastAPI()
    app.add_middleware(mw.OtelSpanMiddleware)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/forbidden")
    def forbidden():
        raise HTTPException(status_code=403, detail="[Disclaimer] re-accept required")

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    with patch.object(mw.trace, "get_tracer", return_value=tracer):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(path)
    return span, resp


def _attrs(span):
    return {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}


def test_2xx_is_ok_no_error_attr():
    span, resp = _run("/ok")
    assert resp.status_code == 200
    assert _attrs(span).get("http.status_code") == 200
    assert "error.type" not in _attrs(span)
    # status OK
    assert span.set_status.called
    assert mw.StatusCode.ERROR not in [
        c.args[0] for c in span.set_status.call_args_list
    ]


def test_4xx_is_marked_error_and_visible():
    span, resp = _run("/forbidden")
    assert resp.status_code == 403
    assert _attrs(span).get("http.status_code") == 403
    # #2183: a client-error refusal must be VISIBLE — status ERROR + error.type
    assert mw.StatusCode.ERROR in [c.args[0] for c in span.set_status.call_args_list]
    assert _attrs(span).get("error.type") == "client_error"


def test_raised_exception_is_recorded_on_span():
    span, resp = _run("/boom")
    assert resp.status_code == 500
    # #2183: the exception must land in the span's events, not vanish
    assert span.record_exception.called
    assert isinstance(span.record_exception.call_args[0][0], Exception)
    assert mw.StatusCode.ERROR in [c.args[0] for c in span.set_status.call_args_list]
