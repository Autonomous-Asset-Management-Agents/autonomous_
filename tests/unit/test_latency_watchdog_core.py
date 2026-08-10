"""#2142: the LIVE latency watchdog (core/latency_watchdog.py) must NOT trip the
global kill-switch on a SINGLE transient Alpaca ping — it debounces N consecutive
active-ping failures. Passive real-order latency stays immediate (tested elsewhere).

Archon conditions covered here:
  * only threshold-breaching pings (ReadTimeout / elapsed>threshold) increment the
    counter — an INSTANT connection error (elapsed<threshold) must NOT increment
    (else the debounce would be MORE sensitive than today's main).
  * a healthy ping resets the counter.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.latency_watchdog import LatencyWatchdog


def _wd(limit=3):
    wd = LatencyWatchdog(threshold_ms=15000)
    wd.consecutive_limit = limit
    wd._consecutive_ping_failures = 0
    return wd


def _mock_get(client_cls, *, side_effect=None, return_value=None):
    client = client_cls.return_value.__enter__.return_value
    if side_effect is not None:
        client.get.side_effect = side_effect
    else:
        client.get.return_value = return_value or MagicMock()


@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_single_and_double_timeout_do_not_trip(mock_client, mock_ks):
    mock_ks.is_halted.return_value = False
    _mock_get(mock_client, side_effect=httpx.ReadTimeout("timeout"))
    wd = _wd(limit=3)
    wd._do_active_ping()
    wd._do_active_ping()
    mock_ks.trip.assert_not_called()
    assert wd._consecutive_ping_failures == 2


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timeout")],
)
@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_failing_ping_advances_last_activity_time_no_tight_loop(
    mock_client, mock_ks, exc
):
    # P1 (#2144): a FAILED active ping must still advance last_activity_time. Otherwise _ping_loop
    # (1s cadence) keeps seeing `now - last_activity_time >= ping_interval_sec` and re-pings every
    # second until the error clears — an accidental DDoS on Alpaca / rate-limit exhaustion.
    mock_ks.is_halted.return_value = False
    _mock_get(mock_client, side_effect=exc)
    wd = _wd(limit=3)
    wd.ping_interval_sec = 10
    wd.last_activity_time = (
        time.time() - 3600
    )  # stale → the loop would re-ping immediately
    wd._do_active_ping()
    # the ping failed, but the timestamp advanced → the loop now waits the full interval again
    assert time.time() - wd.last_activity_time < wd.ping_interval_sec


@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_third_consecutive_timeout_trips_once(mock_client, mock_ks):
    mock_ks.is_halted.return_value = False
    _mock_get(mock_client, side_effect=httpx.ReadTimeout("timeout"))
    wd = _wd(limit=3)
    for _ in range(3):
        wd._do_active_ping()
    assert mock_ks.trip.call_count == 1


@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_healthy_ping_resets_the_counter(mock_client, mock_ks):
    mock_ks.is_halted.return_value = False
    wd = _wd(limit=3)
    # 2 timeouts, then a healthy ping, then a timeout -> must NOT trip
    seq = [
        httpx.ReadTimeout("t"),
        httpx.ReadTimeout("t"),
        MagicMock(),
        httpx.ReadTimeout("t"),
    ]
    mock_client.return_value.__enter__.return_value.get.side_effect = seq
    for _ in range(4):
        wd._do_active_ping()
    mock_ks.trip.assert_not_called()
    assert wd._consecutive_ping_failures == 1  # only the trailing timeout counts


@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_instant_connection_error_does_not_increment(mock_client, mock_ks):
    # Archon condition 1: a fast-failing connection error (elapsed << threshold)
    # must NOT increment the strike count — matches today's main behaviour.
    mock_ks.is_halted.return_value = False
    _mock_get(mock_client, side_effect=httpx.ConnectError("connection refused"))
    wd = _wd(limit=3)
    for _ in range(5):
        wd._do_active_ping()
    mock_ks.trip.assert_not_called()
    assert wd._consecutive_ping_failures == 0


@patch("core.latency_watchdog.kill_switch")
@patch("core.latency_watchdog.httpx.Client")
def test_status_exposes_consecutive_failures(mock_client, mock_ks):
    mock_ks.is_halted.return_value = False
    _mock_get(mock_client, side_effect=httpx.ReadTimeout("t"))
    wd = _wd(limit=3)
    wd._do_active_ping()
    assert wd.status().get("consecutive_ping_failures") == 1


def test_consecutive_limit_defaults_safe_without_config():
    # Archon condition 2: construction must not crash if config is unavailable.
    wd = LatencyWatchdog(threshold_ms=15000)
    assert isinstance(wd.consecutive_limit, int) and wd.consecutive_limit >= 1
