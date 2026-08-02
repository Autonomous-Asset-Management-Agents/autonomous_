"""#2548 H4 — sim broker exceptions duck-typing ``alpaca.common.exceptions.APIError``.

The live execution path keys off Alpaca's exact error contract: a query for a FLAT
position must RAISE an APIError exposing ``.status_code == 404`` OR ``.code == 40410000``
(order_executor.py:651/1701, news_poller.py:88 treat that as "already flat / SELL landed").
The prototype's ``get_open_position`` returned ``None`` → the SELL path aborted as an error.
``.code`` is read via ``json.loads(self._error)['code']`` inside a try/except, so the body
MUST be valid JSON.
"""

import json


class SimAPIError(Exception):
    """Duck-types alpaca ``APIError``: exposes ``.status_code`` and ``.code`` (JSON body).

    No custom ``__init__`` (bugbear B042 / pickle-safe): the JSON body + status code are set by
    the factory helpers below after construction.
    """

    status_code = None
    _error = "{}"

    @property
    def code(self):
        # Mirrors APIError.code: parse the JSON body; callers wrap this in try/except.
        return json.loads(self._error)["code"]


def _make(error_body: str, status_code=None) -> SimAPIError:
    err = SimAPIError(error_body)
    err._error = error_body
    err.status_code = status_code
    return err


def flat_position_error() -> SimAPIError:
    """The "position does not exist" error the live SELL / hard-sync paths expect for a flat symbol."""
    return _make(
        json.dumps({"code": 40410000, "message": "position does not exist"}),
        status_code=None,
    )


def rejected_order_error(status_code: int = 403) -> SimAPIError:
    """A 4xx rejection (e.g. insufficient buying power) so the compliance daily-slot refund fires."""
    return _make(
        json.dumps({"code": 40010001, "message": "order rejected"}),
        status_code=status_code,
    )
