# core/otel_middleware.py
# Task #361 — Backend Boundary Instrumentation (OTel SDK Initialization)
#
# FastAPI middleware that creates an OTel span for every HTTP request and
# stamps it with the mandatory attributes from the Gherkin spec in #361:
#
#   - user.id              ← X-User-Id request header
#   - http.method          ← GET / POST / …
#   - http.route           ← URL path
#   - http.status_code     ← response status
#   - response.body_length ← Content-Length of the response
#   - service.version      ← GIT_COMMIT env var (via core.telemetry)

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guard — middleware is a no-op if OTel is not installed or not initialised
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    OTEL_AVAILABLE = True
    # OBS-5 (#2638): a single W3C propagator instance for ingress context extraction.
    _PROPAGATOR = TraceContextTextMapPropagator()
except ImportError:
    OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]
    _PROPAGATOR = None  # type: ignore[assignment]

from core.telemetry import get_service_version  # always available

# ADR-OBS-01 / PR F: anonymous api-hit counting. Imported fail-safe so the OTel
# middleware never depends on it at import time.
try:
    from starlette.routing import Match as _RouteMatch

    from core.usage_counters import bump_api_hit as _bump_api_hit

    _USAGE_AVAILABLE = True
except Exception:  # noqa: BLE001 — usage analytics must never block the middleware
    _USAGE_AVAILABLE = False


def _resolve_route_template(request: Request):
    """Return the MATCHED route TEMPLATE (e.g. "/portfolio-summary"), never the raw
    path with IDs/query. ``None`` if no route matches (e.g. a 404). Fail-safe."""
    try:
        scope = request.scope
        route = scope.get("route")
        template = getattr(route, "path", None)
        if template:
            return template
        # BaseHTTPMiddleware may not have ``scope['route']`` populated yet — match
        # against the registered routes ourselves to recover the template.
        for candidate in request.app.router.routes:
            match, _ = candidate.matches(scope)
            if match == _RouteMatch.FULL:
                return getattr(candidate, "path", None)
    except Exception:  # noqa: BLE001 — resolution must never break the request
        return None
    return None


def _record_api_usage(request: Request) -> None:
    """Fail-safe anonymous api-hit count for one request (PURE OBSERVATION).

    Counts by ROUTE TEMPLATE only (bounded to registered routes); NEVER the raw
    path/query/IDs. Runs on EVERY request — swallows every error so it can never
    break, slow, or alter a request/response."""
    if not _USAGE_AVAILABLE:
        return
    try:
        template = _resolve_route_template(request)
        if template:
            _bump_api_hit(template)
    except Exception:  # noqa: BLE001 — a counter failure must never break a request
        pass


class OtelSpanMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that wraps every HTTP request in an OTel span.

    Attached span attributes (Gherkin spec #361):
        - ``user.id``              — from ``X-User-Id`` header
        - ``http.method``          — HTTP verb
        - ``http.route``           — request path
        - ``http.status_code``     — integer status code
        - ``response.body_length`` — Content-Length (bytes) or -1 if unknown
        - ``service.version``      — Git SHA from ``GIT_COMMIT`` env var

    The middleware is completely safe in environments where OTel is disabled:
    when ``OTEL_AVAILABLE`` is ``False`` it simply passes the request through
    without touching any span.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # PR F: anonymous api-hit count on EVERY request (before the OTel guard so
        # it runs even when OTel is disabled). PURE OBSERVATION — fail-safe.
        _record_api_usage(request)

        if not OTEL_AVAILABLE:
            return await call_next(request)

        tracer = trace.get_tracer(__name__)
        route = request.url.path
        span_name = f"{request.method} {route}"

        # OBS-5 (#2638): continue an incoming W3C ``traceparent`` so the trace
        # UI-click -> engine-handler is one connected trace. A missing/malformed
        # header yields an empty context -> a new root (unchanged behaviour).
        # ``extract`` never raises. Starlette lowercases header keys, matching the
        # propagator's default getter.
        parent_ctx = _PROPAGATOR.extract(dict(request.headers))

        with tracer.start_as_current_span(span_name, context=parent_ctx) as span:
            try:
                # --- mandatory attributes (ticket #361 Gherkin) ---
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.route", route)
                span.set_attribute("service.version", get_service_version())

                # user.id comes from the upstream proxy via X-User-Id header
                user_id = request.headers.get("X-User-Id", "")
                if user_id:
                    span.set_attribute("user.id", user_id)

                response: Response = await call_next(request)

                span.set_attribute("http.status_code", response.status_code)

                # response.body_length
                content_length = response.headers.get("content-length", "")
                try:
                    span.set_attribute("response.body_length", int(content_length))
                except (ValueError, TypeError):
                    span.set_attribute("response.body_length", -1)

                # #2183: mark BOTH client (4xx) and server (5xx) errors so a
                # REFUSAL (e.g. a 403 disclaimer/entitlement gate) is VISIBLE in the
                # local diagnostics export. A 4xx was previously recorded as
                # StatusCode.OK — invisible — which made the live-enable refusal
                # (#2182) undiagnosable from telemetry. ``error.type`` makes them
                # filterable. (Deliberate deviation from the "4xx is not a server
                # fault" convention: this stream is a support/diagnostics aid.)
                if response.status_code >= 400:
                    span.set_attribute(
                        "error.type",
                        (
                            "server_error"
                            if response.status_code >= 500
                            else "client_error"
                        ),
                    )
                    span.set_status(StatusCode.ERROR)
                else:
                    span.set_status(StatusCode.OK)

                return response

            except Exception as exc:
                # Never let the span crash the request. #2183: record the exception
                # on the span so the reason lands in the export's ``events`` (they
                # were previously empty on error spans).
                try:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                except Exception:
                    pass
                raise
