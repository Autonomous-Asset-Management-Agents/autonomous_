"""Secrets must never reach a log line.

The observed incident: Polygon returned a plain 404 for a delisted ticker. ``requests``
puts the *full request URL* — including ``?apiKey=<key>`` — into the text of the
``HTTPError`` it raises, and the feed logged that exception with ``%r``. No crash
needed: a 404 in normal operation is enough to print a customer's API key into a log
they may hand to support.

These tests exercise the real call path with a real ``requests.HTTPError`` and assert on
what ``caplog`` actually captured. Never put a real key in this file — ``SECRET123`` and
friends are dummies.
"""

import io
import logging

import pytest
import requests

from core.log_redaction import SecretRedactingFilter, redact_secrets
from core.report.company_feed import fetch_raw_company
from core.report.financials_feed import fetch_financials_vX

DUMMY_KEY = "SECRET123"
POLY_URL = "https://api.polygon.io/v3/reference/tickers/SEE"


def _http_error(key: str = DUMMY_KEY, status: int = 404) -> requests.HTTPError:
    """A real HTTPError carrying the key in its URL, exactly as requests builds it."""
    response = requests.Response()
    response.status_code = status
    response.url = f"{POLY_URL}?apiKey={key}"
    response.reason = "Not Found"
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover - always raises
        return exc
    raise AssertionError("raise_for_status did not raise")


def test_real_httperror_carries_the_key_in_its_text() -> None:
    """Anchors the premise: without redaction the key IS in the exception text."""
    exc = _http_error()
    assert DUMMY_KEY in repr(exc)


class _RaisingResponse:
    """Minimal Response stand-in whose raise_for_status leaks like the real one."""

    status_code = 404

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def raise_for_status(self) -> None:
        raise self._exc

    def json(self) -> dict:
        return {}


class _Session:
    def __init__(self, response: object = None, exc: Exception = None) -> None:
        self._response = response
        self._exc = exc

    def get(self, *_args, **_kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


# --------------------------------------------------------------------------- #
# Leak class 1: HTTP error (raise_for_status) — the observed incident
# --------------------------------------------------------------------------- #


def test_company_feed_http_error_does_not_log_the_key(caplog) -> None:
    session = _Session(response=_RaisingResponse(_http_error()))
    with caplog.at_level(logging.WARNING):
        assert fetch_raw_company("SEE", api_key=DUMMY_KEY, session=session) is None
    assert DUMMY_KEY not in caplog.text
    # Redaction that destroys the diagnosis gets ripped out at the first incident.
    assert "SEE" in caplog.text
    assert "404" in caplog.text


def test_financials_feed_http_error_does_not_log_the_key(caplog) -> None:
    session = _Session(response=_RaisingResponse(_http_error()))
    with caplog.at_level(logging.WARNING):
        assert (
            fetch_financials_vX("SEE", "2026-07-16", api_key=DUMMY_KEY, session=session)
            is None
        )
    assert DUMMY_KEY not in caplog.text
    assert "SEE" in caplog.text
    assert "404" in caplog.text


# --------------------------------------------------------------------------- #
# Leak class 2: request failed (transport error before a response exists)
# --------------------------------------------------------------------------- #


def test_company_feed_request_failure_does_not_log_the_key(caplog) -> None:
    exc = requests.ConnectionError(
        f"HTTPSConnectionPool: Max retries exceeded with url: "
        f"/v3/reference/tickers/SEE?apiKey={DUMMY_KEY}"
    )
    session = _Session(exc=exc)
    with caplog.at_level(logging.WARNING):
        assert fetch_raw_company("SEE", api_key=DUMMY_KEY, session=session) is None
    assert DUMMY_KEY not in caplog.text
    assert "SEE" in caplog.text


def test_financials_feed_request_failure_does_not_log_the_key(caplog) -> None:
    exc = requests.ConnectionError(f"Max retries exceeded: ?apiKey={DUMMY_KEY}")
    session = _Session(exc=exc)
    with caplog.at_level(logging.WARNING):
        assert (
            fetch_financials_vX("SEE", "2026-07-16", api_key=DUMMY_KEY, session=session)
            is None
        )
    assert DUMMY_KEY not in caplog.text


# --------------------------------------------------------------------------- #
# Leak class 3: bad JSON (200 with an unparseable body)
# --------------------------------------------------------------------------- #


class _BadJsonResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        raise ValueError(
            f"Expecting value: line 1 column 1 — url {POLY_URL}?apiKey={DUMMY_KEY}"
        )


def test_company_feed_bad_json_does_not_log_the_key(caplog) -> None:
    session = _Session(response=_BadJsonResponse())
    with caplog.at_level(logging.WARNING):
        assert fetch_raw_company("SEE", api_key=DUMMY_KEY, session=session) is None
    assert DUMMY_KEY not in caplog.text


def test_financials_feed_bad_json_does_not_log_the_key(caplog) -> None:
    session = _Session(response=_BadJsonResponse())
    with caplog.at_level(logging.WARNING):
        assert (
            fetch_financials_vX("SEE", "2026-07-16", api_key=DUMMY_KEY, session=session)
            is None
        )
    assert DUMMY_KEY not in caplog.text


# --------------------------------------------------------------------------- #
# The redaction helper itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "param",
    [
        "apiKey",
        "apikey",
        "api_key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "signature",
    ],
)
def test_redacts_every_known_secret_parameter(param: str) -> None:
    out = redact_secrets(f"https://x.io/v1?{param}={DUMMY_KEY}&ticker=SEE")
    assert DUMMY_KEY not in out
    assert "<redacted>" in out
    # Non-secret parameters survive — they are the diagnosis.
    assert "ticker=SEE" in out


def test_redaction_is_case_insensitive_on_the_parameter_name() -> None:
    assert DUMMY_KEY not in redact_secrets(f"?APIKEY={DUMMY_KEY}")
    assert DUMMY_KEY not in redact_secrets(f"?ApI_KeY={DUMMY_KEY}")


def test_key_with_regex_metacharacters_is_still_redacted() -> None:
    """A key is untrusted input to the regex. It must never be treated as a pattern."""
    nasty = "a.*+?[](){}|^$\\b"
    out = redact_secrets(f"https://x.io/v1?apiKey={nasty}&ticker=SEE")
    assert "<redacted>" in out
    assert "ticker=SEE" in out


def test_value_terminates_at_url_and_repr_boundaries() -> None:
    """The real HTTPError repr ends in ``')`` — redaction must not eat the boundary."""
    out = redact_secrets(f"HTTPError('404 ... for url: {POLY_URL}?apiKey={DUMMY_KEY}')")
    assert DUMMY_KEY not in out
    assert out.endswith("')")
    assert "404" in out


def test_multiple_secrets_in_one_string_are_all_redacted() -> None:
    out = redact_secrets(f"?apiKey={DUMMY_KEY}&token=OTHER456&ticker=SEE")
    assert DUMMY_KEY not in out
    assert "OTHER456" not in out
    assert "ticker=SEE" in out


def test_plain_text_without_secrets_is_returned_unchanged() -> None:
    text = "company_feed: rate-limited (429) for SEE after 3 retries"
    assert redact_secrets(text) is text


def test_bare_key_parameter_is_not_redacted() -> None:
    """``key=`` is too common in payloads; redacting it would destroy diagnostics."""
    assert redact_secrets("cache key=SEE_2026") == "cache key=SEE_2026"


def test_redact_secrets_never_raises_on_odd_input() -> None:
    assert redact_secrets(None) is None  # type: ignore[arg-type]
    assert redact_secrets(123) == 123  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The logging filter (defence in depth for call sites nobody has found yet)
# --------------------------------------------------------------------------- #


def test_filter_redacts_message_args_and_keeps_the_record_usable(caplog) -> None:
    logger = logging.getLogger("test_filter_args")
    caplog.handler.addFilter(SecretRedactingFilter())
    try:
        with caplog.at_level(logging.WARNING, logger="test_filter_args"):
            logger.warning("HTTP error for %s: %r", "SEE", _http_error())
    finally:
        caplog.handler.filters = [
            f
            for f in caplog.handler.filters
            if not isinstance(f, SecretRedactingFilter)
        ]
    assert DUMMY_KEY not in caplog.text
    assert "SEE" in caplog.text
    assert "404" in caplog.text


def test_filter_redacts_exc_info_tracebacks(caplog) -> None:
    """``exc_info=True`` prints the exception's str() — URL and all — into the log."""
    logger = logging.getLogger("test_filter_exc")
    caplog.handler.addFilter(SecretRedactingFilter())
    try:
        with caplog.at_level(logging.ERROR, logger="test_filter_exc"):
            try:
                raise _http_error()
            except requests.HTTPError:
                logger.error("prefetch failed for %s", "SEE", exc_info=True)
    finally:
        caplog.handler.filters = [
            f
            for f in caplog.handler.filters
            if not isinstance(f, SecretRedactingFilter)
        ]
    assert DUMMY_KEY not in caplog.text
    assert "SEE" in caplog.text


def test_filter_never_swallows_a_record_when_redaction_explodes(caplog) -> None:
    """A bug in the filter must never cost a log line, let alone the trading path."""

    class _Exploding(SecretRedactingFilter):
        @staticmethod
        def _redact(_text):  # type: ignore[override]
            raise RuntimeError("boom")

    logger = logging.getLogger("test_filter_failsafe")
    caplog.handler.addFilter(_Exploding())
    try:
        with caplog.at_level(logging.WARNING, logger="test_filter_failsafe"):
            logger.warning("still logged for %s", "SEE")
    finally:
        caplog.handler.filters = [
            f for f in caplog.handler.filters if not isinstance(f, _Exploding)
        ]
    assert "SEE" in caplog.text


def test_filter_redacts_exc_info_under_the_production_json_formatter() -> None:
    """The stdlib-formatter test above CANNOT catch a regression here.

    Production formats through ``GcpJsonFormatter`` (``core/structured_logging.py:15``,
    active whenever ``K_SERVICE`` / ``LOG_FORMAT=json`` is set). pythonjsonlogger checks
    ``record.exc_info`` first and re-renders the traceback, discarding a redacted
    ``exc_text``. Only clearing ``exc_info`` makes redaction stick — and only this test
    fails when that line is removed.
    """
    from core.structured_logging import GcpJsonFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(GcpJsonFormatter("%(message)s"))
    handler.addFilter(SecretRedactingFilter())

    logger = logging.getLogger("test_prod_json_formatter")
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        try:
            raise _http_error()
        except requests.HTTPError:
            logger.error("prefetch failed for %s", "SEE", exc_info=True)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert output, "formatter produced no output — test would be vacuous"
    assert DUMMY_KEY not in output
    assert "SEE" in output


def test_signature_of_a_python_callable_is_not_mangled_into_nonsense() -> None:
    """False positives are how a redactor loses its licence. Bound the damage."""
    out = redact_secrets("signature=(symbol, qty) mismatch")
    assert "qty" in out
    assert out.endswith("mismatch")


@pytest.mark.parametrize(
    "text",
    [
        "cache key=SEE_2026",
        "sig=0.032",  # sigma / significance in a quant codebase, not a secret
        "auth=local mode",
    ],
)
def test_parameters_too_common_to_redact_are_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_alternation_order_never_lets_a_secret_through() -> None:
    """Python's re backtracks across alternatives — assert that, don't assume it."""
    for param in [
        "apiKey",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        "client_secret",
        "api_secret",
        "apisecret",
        "api_token",
        "auth_token",
        "password",
        "passwd",
        "pwd",
        "signature",
    ]:
        for variant in (param, param.upper(), param.lower()):
            out = redact_secrets(f"?{variant}={DUMMY_KEY}&t=SEE")
            assert DUMMY_KEY not in out, f"{variant} leaked"


def test_filter_returns_true_so_no_record_is_dropped() -> None:
    record = logging.LogRecord(
        "n", logging.WARNING, __file__, 1, "?apiKey=%s", (DUMMY_KEY,), None
    )
    assert SecretRedactingFilter().filter(record) is True
    assert DUMMY_KEY not in record.getMessage()
