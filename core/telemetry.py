# core/telemetry.py
# Task #361 — Backend Boundary Instrumentation (OTel SDK Initialization)
#
# Usage: import this module as the FIRST import in every backend entrypoint,
# then call init_telemetry() before anything else.
#
#   from core.telemetry import init_telemetry, get_tracer
#   init_telemetry()

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level guard — ensures init_telemetry() is truly idempotent
# ---------------------------------------------------------------------------
_TELEMETRY_INITIALIZED: bool = False

# Try to import OTel — all imports are guarded so the rest of the app
# works even in environments without the packages installed.
try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_PACKAGES_AVAILABLE = True
except ImportError:
    _OTEL_PACKAGES_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    metrics = None  # type: ignore[assignment]

# INF-13 #1371: the Cloud Trace exporter is a SEPARATE, cloud-only dependency — the  # noqa: E501
# desktop bundle ships opentelemetry api/sdk only. Guard it INDEPENDENTLY so its absence  # noqa: E501
# does NOT disable the whole SDK on desktop. (Previously this import lived in the main  # noqa: E501
# guard above, so on desktop the ImportError flipped _OTEL_PACKAGES_AVAILABLE=False and  # noqa: E501
# OTel was dead — no local capture possible.)
try:
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

    _CLOUD_TRACE_AVAILABLE = True
except ImportError:
    _CLOUD_TRACE_AVAILABLE = False
    CloudTraceSpanExporter = None  # type: ignore[assignment]


def get_service_version() -> str:
    """Return the running app/service version for telemetry, health and heartbeat.

    Precedence:
      1. AAA_APP_VERSION - the desktop app version (desktop/package.json via
         Electron app.getVersion()), stamped into the engine env by the shell (#1940).
      2. GIT_COMMIT - the cloud build SHA (set only in Cloud Run), kept as build
         provenance for the cloud edition.
      3. "unknown" - neither is set.

    An empty AAA_APP_VERSION is treated as unset so it never shadows GIT_COMMIT.
    """
    return os.environ.get("AAA_APP_VERSION") or os.environ.get("GIT_COMMIT", "unknown")


def _local_telemetry_dir() -> str:
    """The on-device telemetry buffer dir: ``<USER_DATA_DIR>/telemetry/`` (desktop).  # noqa: E501

    Reads ``config.get_config().USER_DATA_DIR`` (which itself honours
    ``AAA_USER_DATA_DIR``); falls back to ``~/.aaa`` so it never raises.
    """
    base = None
    try:
        import config

        base = config.get_config().USER_DATA_DIR
    except Exception:
        base = None
    if not base:
        base = os.environ.get("AAA_USER_DATA_DIR") or os.path.join(
            os.path.expanduser("~"), ".aaa"
        )
    return os.path.join(base, "telemetry")


def _retention_from_config():
    """Local-store retention ``(max_age_days, max_bytes)`` from config, with the  # noqa: E501
    P2 (#1456) defaults (7 days / 50 MB). Reads via ``get_config()`` (OSS
    SimpleNamespace) then the module attribute (cloud config.py), so it works
    across both editions. Never raises."""
    days, mb = 7.0, 50.0
    try:
        import config

        cfg = config.get_config()
        days = float(
            getattr(cfg, "TELEMETRY_RETENTION_DAYS", None)
            or getattr(config, "TELEMETRY_RETENTION_DAYS", days)
        )
        mb = float(
            getattr(cfg, "TELEMETRY_RETENTION_MB", None)
            or getattr(config, "TELEMETRY_RETENTION_MB", mb)
        )
    except Exception:
        pass
    return days, int(mb * 1024 * 1024)


def init_telemetry(service_name: str = "aaa-backend") -> None:
    """Idempotent OTel SDK initialisation.

    Initialises the TracerProvider with the GCP Cloud Trace exporter.
    Safe to call multiple times — only the first call has any effect.
    Never raises: any failure is logged as a warning so the app still starts.

    Args:
        service_name: The ``service.name`` resource attribute.  Defaults to
            ``"aaa-backend"`` which matches the Cloud Run service name.
    """
    global _TELEMETRY_INITIALIZED
    if _TELEMETRY_INITIALIZED:
        return

    if not _OTEL_PACKAGES_AVAILABLE:
        logger.warning("opentelemetry packages not available — tracing disabled.")
        _TELEMETRY_INITIALIZED = True
        return

    try:
        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: get_service_version(),
                # Standard Cloud Run attributes (picked up automatically by
                # GcpResourceDetector on Cloud Run, but we set them explicitly
                # so local dev also gets meaningful labels).
                "environment": os.environ.get("K_SERVICE", "local"),
            }
        )

        provider = TracerProvider(resource=resource)

        # Only attach the Cloud Trace exporter when running on Cloud Run
        # (K_SERVICE is set by GCP) AND not in a CI environment.
        #
        # The self-hosted GKE runner inherits K_SERVICE from the node at a
        # level that cannot be reliably overridden by GitHub Actions step env
        # blocks.  GITHUB_ACTIONS=true is injected by the GitHub Actions
        # runner itself and is the canonical way to detect CI context.
        # CI=true is included as a fallback for other CI systems.
        _in_ci = (
            os.environ.get("GITHUB_ACTIONS") == "true"
            or os.environ.get("CI", "").lower() == "true"
        )
        if os.environ.get("K_SERVICE") and not _in_ci and _CLOUD_TRACE_AVAILABLE:
            try:
                exporter = CloudTraceSpanExporter()
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info(
                    "OTel CloudTraceSpanExporter registered (service=%s version=%s)",  # noqa: E501
                    service_name,
                    get_service_version(),
                )
            except Exception as export_err:
                logger.warning(
                    "Could not attach CloudTraceSpanExporter — spans will not "  # noqa: E501
                    "be exported: %s",
                    export_err,
                )
        elif os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
            # Desktop edition (INF-13 P1 #1371): a LOCAL, PII-scrubbed, NO-EGRESS file  # noqa: E501
            # exporter under <USER_DATA_DIR>/telemetry/. Nothing leaves the device; no  # noqa: E501
            # consent needed. Only taken when K_SERVICE is unset → cloud is unaffected.  # noqa: E501
            try:
                from opentelemetry.sdk.trace.export import SimpleSpanProcessor

                from core.telemetry_local import LocalScrubbingSpanExporter

                tele_dir = _local_telemetry_dir()
                _max_age, _max_bytes = _retention_from_config()
                provider.add_span_processor(
                    SimpleSpanProcessor(
                        LocalScrubbingSpanExporter(
                            tele_dir,
                            max_age_days=_max_age,
                            max_bytes=_max_bytes,
                        )
                    )
                )
                logger.info(
                    "OTel local file exporter registered (desktop, dir=%s)",
                    tele_dir,
                )
            except Exception as local_err:
                logger.warning(
                    "Could not attach the local span exporter: %s", local_err
                )
        else:
            logger.debug(
                "OTel initialised without exporter (K_SERVICE not set or CI environment)."  # noqa: E501
            )

        trace.set_tracer_provider(provider)

        # Desktop crash hooks (INF-13 #1371): uncaught exceptions -> a local crash span.  # noqa: E501
        if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
            try:
                from core.telemetry_local import install_crash_hooks

                install_crash_hooks(trace.get_tracer("crash"))
            except Exception as hook_err:
                logger.debug("crash hooks not installed: %s", hook_err)

        # Set up MeterProvider (no-export in local/CI — metrics stay in-process)  # noqa: E501
        try:
            meter_provider = MeterProvider(resource=resource)
            metrics.set_meter_provider(meter_provider)
        except Exception as metric_err:
            logger.debug("OTel MeterProvider setup skipped: %s", metric_err)

    except Exception as err:
        logger.warning("OTel initialisation failed (tracing disabled): %s", err)
    finally:
        _TELEMETRY_INITIALIZED = True


def get_tracer(name: str):
    """Return a named OTel tracer.

    Returns a no-op tracer if OTel packages are unavailable or
    ``init_telemetry()`` has not been called yet.
    """
    if not _OTEL_PACKAGES_AVAILABLE:
        # Return a minimal no-op-compatible object
        class _NoopSpan:
            def set_attribute(self, *a, **kw):
                pass

            def set_status(self, *a, **kw):
                pass

            def record_exception(self, *a, **kw):
                pass

        class _NoopContextManager:
            def __enter__(self):
                return _NoopSpan()

            def __exit__(self, *a, **kw):
                pass

        class _NoopTracer:
            def start_as_current_span(self, *a, **kw):
                return _NoopContextManager()

        return _NoopTracer()
    return trace.get_tracer(name)


def get_meter(name: str = "aaa-engine"):
    """Return a named OTel meter for metrics (counters, histograms).

    Returns a no-op meter if OTel packages are unavailable, so callers
    never need to guard against None.

    Usage::

        meter = get_meter("aaa-engine")
        fallback_counter = meter.create_counter(
            "agent.fallback",
            description="Incremented when an agent returns a fallback value",
        )
        fallback_counter.add(1, {"agent": "lstm", "reason": "model_not_loaded"})  # noqa: E501
    """
    if not _OTEL_PACKAGES_AVAILABLE or metrics is None:

        class _NoopCounter:
            def add(self, *a, **kw):
                pass

        class _NoopHistogram:
            def record(self, *a, **kw):
                pass

        class _NoopMeter:
            def create_counter(self, *a, **kw):
                return _NoopCounter()

            def create_histogram(self, *a, **kw):
                return _NoopHistogram()

        return _NoopMeter()
    return metrics.get_meter(name)
