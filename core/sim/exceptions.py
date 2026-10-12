"""#2548 H4 — sim broker exceptions.

The live execution path keys off Alpaca's exact error contract for a FLAT-position query:
``isinstance(e, APIError)`` AND (``.status_code == 404`` OR ``.code == 40410000``) → "already flat /
SELL landed" (rl_signal.py:688, order_executor.py:651/1701, news_poller.py:88).

**#2630 fix:** the prototype only *duck-typed* ``APIError`` (a bare ``Exception`` with a ``.code``
property) and did NOT subclass it — so every live ``isinstance(e, APIError)`` check returned False and
the state/SELL paths mis-read a flat position as an "indeterminate API failure" and aborted (no BUY
evaluation, no SELL dispatch). ``SimAPIError`` now SUBCLASSES the real ``APIError``, so ``isinstance``
passes and ``.code``/``.status_code`` come straight from the parent's contract.
"""

import json

try:
    from alpaca.common.exceptions import APIError
except ImportError:  # pragma: no cover - alpaca always present in the engine env

    class APIError(Exception):  # minimal fallback so imports never hard-fail
        def __init__(self, error, http_error=None):
            super().__init__(error)
            self._error = error
            self._http_error = http_error

        @property
        def code(self):
            return json.loads(self._error)["code"]

        @property
        def status_code(self):
            he = self._http_error
            return he.response.status_code if he is not None else None


class _FakeResponse:
    __slots__ = ("status_code",)

    def __init__(self, status_code):
        self.status_code = status_code


class _FakeHttpError:
    """Minimal http-error shim so ``APIError.status_code`` (reads ``http_error.response.status_code``)
    returns a real 4xx for the rejection case."""

    __slots__ = ("response",)

    def __init__(self, status_code):
        self.response = _FakeResponse(status_code)


class SimAPIError(APIError):
    """A genuine ``alpaca.common.exceptions.APIError`` (so ``isinstance(e, APIError)`` on the live
    path passes), carrying the sim's JSON body → ``.code`` and, for rejections, ``.status_code``.
    """


def flat_position_error() -> SimAPIError:
    """The "position does not exist" error the live SELL / hard-sync / state paths expect for a flat
    symbol. status_code is None (no http_error) — callers key off ``.code == 40410000``.
    """
    return SimAPIError(
        json.dumps({"code": 40410000, "message": "position does not exist"})
    )


def rejected_order_error(status_code: int = 403) -> SimAPIError:
    """A 4xx rejection (e.g. insufficient buying power) so the compliance daily-slot refund fires."""
    return SimAPIError(
        json.dumps({"code": 40010001, "message": "order rejected"}),
        http_error=_FakeHttpError(status_code),
    )
