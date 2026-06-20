from unittest.mock import patch

import allure
import pytest


@pytest.fixture
def mock_kill_switch():
    with patch("core.kill_switch.KillSwitch", autospec=True) as mock:
        yield mock


@pytest.fixture(autouse=True)
def stop_global_latency_watchdog():
    from core.latency_watchdog import latency_watchdog

    latency_watchdog.stop()
    yield


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_watchdog_passive_recording():
    # Import inside to ensure fresh mocks if necessary
    from core.latency_watchdog import LatencyWatchdog

    with patch("core.latency_watchdog.kill_switch") as mock_ks:
        wd = LatencyWatchdog(threshold_ms=2000, ping_interval_sec=10)

        # Test healthy latency
        wd.record_passive_latency(500.0, "order")
        mock_ks.trip.assert_not_called()

        # Test anomaly latency
        wd.record_passive_latency(2050.0, "order")
        mock_ks.trip.assert_called_once()
        assert "Passive Latency" in mock_ks.trip.call_args[0][0]


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
def test_watchdog_active_ping_timeout():
    import httpx

    from core.latency_watchdog import LatencyWatchdog

    with patch("core.latency_watchdog.kill_switch") as mock_ks, patch(
        "core.latency_watchdog.httpx.Client"
    ) as mock_client:

        mock_client_instance = mock_client.return_value.__enter__.return_value
        mock_client_instance.get.side_effect = httpx.ReadTimeout("Timeout")

        # ping immediately
        wd = LatencyWatchdog(threshold_ms=2000, ping_interval_sec=0)

        # Run one iteration of the ping loop logic manually
        wd._running = True
        mock_ks.is_halted.return_value = False

        # Force the condition for ping
        wd.last_activity_time = 0

        # Mock time.sleep to set running = False immediately to run just 1 loop
        def mock_sleep(secs):
            wd._running = False

        with patch("time.sleep", side_effect=mock_sleep):
            wd._ping_loop()

        mock_ks.trip.assert_called_once()
        assert "Active Ping Timeout" in mock_ks.trip.call_args[0][0]
