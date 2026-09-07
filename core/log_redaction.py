r"""Strip secrets out of anything on its way into a log line.

Why this exists: ``requests`` builds the full request URL into the text of every
``HTTPError`` it raises — query string included. A Polygon 404 for a delisted ticker is
enough to put ``?apiKey=<the customer's key>`` into a log file the customer may later
attach to a support mail. No crash required; this is the normal path.

``requests.get(url, params=...)`` does not help — requests assembles the same URL and the
same exception. The key is in the *rendered text*, so redaction has to happen where text
is produced: at the log call.

Two layers, deliberately:

* :func:`redact_secrets` at the known call sites. Deterministic, no wiring to forget.
  This is the actual fix.
* :class:`SecretRedactingFilter` as defence in depth against *the same leak shape* at
  sites not yet found, and against third-party loggers (``urllib3`` logs URLs at DEBUG).
  Opt-in via :func:`install_secret_redaction` — this codebase has no central logging
  setup, so there is no single place that could install it honestly.

Known gaps — this redacts the ``param=value`` shape only. It does NOT cover:

* header-style names with dashes (``x-api-key: …``, ``APCA-API-KEY-ID: …``) — the
  ``(?<![\w-])`` boundary deliberately refuses them,
* ``Authorization: Bearer <token>``,
* JSON bodies (``{"apiKey": "…"}`` — colon, not ``=``).

None of those are the observed leak, and the two report feeds cannot produce them. Do not
mistake this module for blanket protection.

Design constraints:

* Fail-safe. A bug in here must never cost a log line, and must never propagate into the
  trading path. Every entry point swallows its own exceptions and degrades to "log the
  original text".
* Diagnosis-preserving. Redaction that eats the symbol or the status code gets ripped out
  at the first incident and the leak comes back. Only the secret's *value* is replaced.
* Conservative parameter list. ``key=`` is deliberately NOT redacted — it is far too
  common in ordinary payloads, and a redactor that mangles real data loses its licence.
"""

import logging
import re
from typing import Any, Optional

__all__ = [
    "REDACTED",
    "SecretRedactingFilter",
    "install_secret_redaction",
    "redact_secrets",
]

REDACTED = "<redacted>"

# Parameter names whose value is a credential.
#
# Excluded on purpose, because the name is far too common in ordinary payloads and a
# redactor that mangles real data loses its licence:
#   ``key``  — ``cache key=SEE_2026``
#   ``sig``  — in a quant codebase this reads as sigma / significance (``sig=0.032``)
#   ``auth`` — ``auth=local mode``
# The same test that guards the leak also guards these (see test_log_redaction.py).
_SECRET_PARAMS = (
    "client_secret",
    "refresh_token",
    "access_token",
    "secret_key",
    "api_secret",
    "apisecret",
    "api_token",
    "auth_token",
    "signature",
    "password",
    "apikey",
    "api_key",
    "passwd",
    "secret",
    "token",
    "pwd",
)

# Matches ``<param>=<value>`` in URLs, query strings, reprs and free text.
#
# The value stops at anything that cannot be part of a query-string value: ``&``, quotes,
# whitespace, and bracket/paren delimiters. That boundary matters twice over — the real
# HTTPError repr ends in ``')`` and eating it would corrupt the message, and an opening
# ``(`` bounds the damage if a non-secret ``signature=(a, b)`` ever shows up in a log.
#
# ``(?<![\w-])`` stops ``token`` from matching inside ``csrf_token`` while still allowing
# a bare ``?token=``. Note it also means header-style ``x-api-key=`` is NOT matched — see
# the module docstring's "known gaps".
#
# Ordering within the alternation is cosmetic, not load-bearing: Python's ``re``
# backtracks across alternatives, so a short alternative that matches but then fails the
# ``(=|%3D)`` step retries the longer one at the same position. Verified by test.
_SECRET_RE = re.compile(
    r"(?<![\w-])(" + "|".join(_SECRET_PARAMS) + r")(=|%3D)([^&\s\"'<>()\[\]{}#]*)",
    re.IGNORECASE,
)


def redact_secrets(text: Any) -> Any:
    """Replace secret parameter *values* in ``text`` with ``<redacted>``.

    Returns non-str input untouched, and returns the original object (identity) when
    nothing matched, so the common no-secret path allocates nothing. Never raises.
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    except Exception:  # pragma: no cover - defensive: never break a log call
        return text


class SecretRedactingFilter(logging.Filter):
    """Redacts secrets from every record that reaches the handler it is attached to.

    Attach to *handlers*, not loggers: a filter on a logger only sees records logged
    directly to it, while a filter on the root handler sees everything that propagates —
    including third-party libraries.

    Always returns ``True``: this filter redacts, it never drops.
    """

    @staticmethod
    def _redact(text: Any) -> Any:
        return redact_secrets(text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Render once, then redact. The secret usually lives in record.args (the
            # exception repr), not in the format string, so redacting msg alone misses
            # it. Collapsing to a plain msg is the only way to catch both.
            message = record.getMessage()
            cleaned = self._redact(message)
            if cleaned != message:
                record.msg = cleaned
                record.args = ()

            # exc_info=True renders the exception's str() into the traceback's last
            # line — URL and all. exc_text may not be populated yet, so force it.
            if record.exc_info and not record.exc_text:
                try:
                    record.exc_text = logging.Formatter().formatException(
                        record.exc_info
                    )
                except Exception:  # pragma: no cover - defensive
                    record.exc_text = None
            if record.exc_text:
                cleaned_tb = self._redact(record.exc_text)
                if cleaned_tb != record.exc_text:
                    record.exc_text = cleaned_tb
                    # ⚠ LOAD-BEARING IN PRODUCTION — do not "simplify" this away.
                    # The stdlib Formatter reuses exc_text when set, so dropping this
                    # line looks harmless and keeps every stdlib/caplog test green. But
                    # production runs GcpJsonFormatter (core/structured_logging.py:15,
                    # active whenever K_SERVICE or LOG_FORMAT=json is set), and
                    # pythonjsonlogger checks exc_info FIRST and re-renders the
                    # traceback from scratch — discarding the redacted exc_text and
                    # leaking the key again. Clearing exc_info is what forces the
                    # redacted text to win. Covered by
                    # test_filter_redacts_exc_info_under_the_production_json_formatter.
                    record.exc_info = None
        except Exception:  # pragma: no cover - defensive
            # A redaction bug must never cost a log line.
            pass
        return True


def install_secret_redaction(
    logger: Optional[logging.Logger] = None,
) -> SecretRedactingFilter:
    """Attach a :class:`SecretRedactingFilter` to every handler of ``logger``.

    Defaults to the root logger, which is where ``logging.basicConfig`` puts its handler.
    Idempotent: calling it twice does not stack filters. Handlers added *after* this call
    are not covered — call it after logging is configured.
    """
    target = logger if logger is not None else logging.getLogger()
    installed = SecretRedactingFilter()
    for handler in target.handlers:
        if any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            continue
        handler.addFilter(installed)
    return installed
