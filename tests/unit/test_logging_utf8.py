"""INC-9: the engine must never crash — or drop a log line — on a non-ASCII record.

Observed incident (runner.py:298): the Round-Table logs an arrow (``→``) and other
non-ASCII glyphs (``€``, umlauts). On Windows the console stream defaults to cp1252,
whose strict codec raises ``UnicodeEncodeError`` on ``→``. Python's logging catches
that inside ``Handler.emit`` and prints a ``--- Logging error ---`` traceback to
stderr instead of the line — the diagnostic is lost exactly when it matters.

The fix forces UTF-8 (with ``backslashreplace`` as a last-resort escape hatch) on the
process stdio in ``setup_logging()``, so every edition (config.py and config.oss.py
both route through it) writes non-ASCII log records losslessly.
"""

import io
import logging
import sys

from core.structured_logging import setup_logging

ARROW = "→"  # → — the exact glyph that trips cp1252


def _cp1252_stream():
    """A text stream backed by bytes with the Windows-console default codec."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_setup_logging_forces_utf8_on_stdio():
    """After init the process stdio must be UTF-8 so no strict cp1252 codec remains."""
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _cp1252_stream(), _cp1252_stream()
    try:
        setup_logging()
        assert (sys.stdout.encoding or "").lower() == "utf-8"
        assert (sys.stderr.encoding or "").lower() == "utf-8"
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err


def test_non_ascii_log_record_is_written_and_does_not_error():
    """Logging an arrow neither raises nor triggers logging's error handler."""
    orig_out, orig_err = sys.stdout, sys.stderr
    out = _cp1252_stream()
    sys.stdout, sys.stderr = out, _cp1252_stream()
    errors = []
    orig_handle_error = logging.Handler.handleError
    logging.Handler.handleError = lambda self, record: errors.append(record)
    try:
        setup_logging()
        logging.getLogger("inc9").info("entitlement %s active", ARROW)
        for h in logging.getLogger().handlers:
            h.flush()
    finally:
        logging.Handler.handleError = orig_handle_error
        sys.stdout, sys.stderr = orig_out, orig_err

    assert errors == [], "logging invoked handleError — the record was lost"
    written = out.buffer.getvalue().decode("utf-8")
    assert ARROW in written or "\\u2192" in written
