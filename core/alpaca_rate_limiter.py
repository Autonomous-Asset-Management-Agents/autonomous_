# alpaca_rate_limiter.py
# --- Throttle Alpaca REST API calls to stay under 200 req/min (paper: 200, live: higher) ---
# Prevents "rate limit exceeded" and "sleep 3 seconds and retrying" cascades.

import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Max requests per second (Alpaca paper: 200/min = ~3.3/sec; use 2.5 to be safe)
DEFAULT_MAX_REQUESTS_PER_SECOND = 2.5
_min_interval = 1.0 / DEFAULT_MAX_REQUESTS_PER_SECOND

_lock = threading.Lock()
_last_call_time: float = 0.0


def _wait_if_needed():
    global _last_call_time
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _min_interval:
            sleep_time = _min_interval - elapsed
            time.sleep(sleep_time)
            _last_call_time = time.monotonic()
        else:
            _last_call_time = now


def rate_limited(f: Callable[..., T]) -> Callable[..., T]:
    """Decorator: run f only after throttling to respect Alpaca rate limit."""

    def wrapped(*args, **kwargs):
        _wait_if_needed()
        return f(*args, **kwargs)

    return wrapped


class RateLimitedREST:
    """
    Wrapper around Alpaca REST client that throttles all API calls.
    Use this instead of raw REST() when creating the engine's api to avoid rate limiting.
    """

    def __init__(
        self,
        rest_client: Any,
        max_requests_per_second: float = DEFAULT_MAX_REQUESTS_PER_SECOND,
    ):
        self._client = rest_client
        self._min_interval = 1.0 / max_requests_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _throttle(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()

    def _call(self, method: str, *args, **kwargs):
        self._throttle()
        return getattr(self._client, method)(*args, **kwargs)

    # Proxy high-traffic methods used by engine and strategy
    def get_account(self):
        return self._call("get_account")

    def list_positions(self):
        return self._call("list_positions")

    def list_orders(self, status: str = "open", **kwargs):
        return self._call("list_orders", status=status, **kwargs)

    def get_position(self, symbol: str):
        return self._call("get_position", symbol)

    def get_snapshot(self, symbol: str):
        return self._call("get_snapshot", symbol)

    def get_snapshots(self, symbols):
        return self._call("get_snapshots", symbols)

    def get_clock(self):
        return self._call("get_clock")

    def get_portfolio_history(self, *args, **kwargs):
        # Explicit proxy (not the __getattr__ fallback, which releases the throttle before the
        # actual call). Used for the "since inception" full-history fetch (#1782).
        return self._call("get_portfolio_history", *args, **kwargs)

    def submit_order(self, *args, **kwargs):
        return self._call("submit_order", *args, **kwargs)

    def cancel_order(self, order_id: str):
        return self._call("cancel_order", order_id)

    def cancel_all_orders(self):
        return self._call("cancel_all_orders")

    def close_position(self, symbol: str):
        return self._call("close_position", symbol)

    def close_all_positions(self):
        return self._call("close_all_positions")

    def __getattr__(self, name: str):
        # Any other method: throttle then delegate
        self._throttle()
        return getattr(self._client, name)
