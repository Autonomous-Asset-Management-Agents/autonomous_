# core/structured_logging.py
import logging
import os
import sys
import contextvars
from datetime import datetime, timezone
from pythonjsonlogger import json as jsonlogger

# Context variables for correlation and trace IDs (thread-safe and async-safe)
correlation_id_var = contextvars.ContextVar("correlation_id", default=None)
trace_id_var = contextvars.ContextVar("trace_id", default=None)


class GcpJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter for Google Cloud Logging.
    Maps Python log levels to GCP 'severity' and adds metadata.
    """

    def add_fields(self, log_record, record, message_dict):
        super(GcpJsonFormatter, self).add_fields(log_record, record, message_dict)

        # 1. Map severity for GCP
        # GCP expects 'severity' level, not 'levelname'
        log_record["severity"] = record.levelname

        # 2. Add timestamp in ISO format
        if not log_record.get("timestamp"):
            log_record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # 3. Add source info
        log_record["module"] = record.module
        log_record["filename"] = record.filename
        log_record["lineno"] = record.lineno

        # 4. Add correlation and trace IDs if present
        c_id = correlation_id_var.get()
        if c_id:
            log_record["correlation_id"] = c_id

        t_id = trace_id_var.get()
        if t_id:
            log_record["trace_id"] = t_id

        # 5. Clean up redundant fields to save space/cost
        if "levelname" in log_record:
            del log_record["levelname"]


def setup_logging():
    """
    Initializes logging based on the environment.
    Uses JSON formatting in production (or when LOG_FORMAT=json).
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Check if we should use structured logging
    # Cloud Run usually sets K_SERVICE, but we can also use a dedicated env var
    use_json = (
        os.getenv("LOG_FORMAT", "").lower() == "json"
        or os.getenv("K_SERVICE") is not None
    )

    root_logger = logging.getLogger()

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if use_json:
        # GCP format: severity, message, timestamp are key
        formatter = GcpJsonFormatter(
            "%(timestamp)s %(severity)s %(module)s %(message)s"
        )
        handler.setFormatter(formatter)
    else:
        # Dev-friendly text format
        log_format = (
            "%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
        )
        handler.setFormatter(logging.Formatter(log_format))

    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Suppress noisy logs
    logging.getLogger("alpaca").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("h5py").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    if use_json:
        logging.info("Structured JSON logging initialized for Cloud Logging.")
    else:
        logging.info("Standard text logging initialized.")


def set_correlation_id(c_id: str):
    """Set the correlation ID for the current context."""
    correlation_id_var.set(c_id)


def set_trace_id(t_id: str):
    """Set the trace ID for the current context."""
    trace_id_var.set(t_id)
