import pytest
from unittest.mock import patch, MagicMock
from scripts.latency_watchdog import run_watchdog
import requests


@pytest.fixture
def mock_logger():
    with patch("scripts.latency_watchdog.logging.getLogger") as mock_get_logger:
        logger_mock = MagicMock()
        mock_get_logger.return_value = logger_mock
        with patch("scripts.latency_watchdog.setup_logging"):
            yield logger_mock


@pytest.fixture
def run_loop_once():
    # Helper to only run one iteration of the while True loop
    # We patch time.sleep to raise an Exception to break the loop
    class BreakLoop(Exception):
        pass

    with patch("scripts.latency_watchdog.time.sleep", side_effect=BreakLoop):
        yield BreakLoop


def test_watchdog_healthy(requests_mock, mock_logger, run_loop_once):
    requests_mock.get("http://localhost:8001/system-health", status_code=200, text="OK")

    with patch(
        "scripts.latency_watchdog.time.perf_counter", side_effect=[0.0, 0.5]
    ):  # 500ms latency
        try:
            run_watchdog(timeout_ms=2000)
        except run_loop_once:
            pass

    mock_logger.info.assert_called_with("Ping OK - Latency: 500.0ms")
    mock_logger.critical.assert_not_called()


def test_watchdog_latency_breach(requests_mock, mock_logger, run_loop_once):
    requests_mock.get("http://localhost:8001/system-health", status_code=200, text="OK")

    with patch(
        "scripts.latency_watchdog.time.perf_counter", side_effect=[0.0, 3.5]
    ):  # 3500ms latency
        try:
            run_watchdog(timeout_ms=2000)
        except run_loop_once:
            pass

    mock_logger.critical.assert_called_with(
        "Latency SLA breached: 3500.0ms (Threshold: 2000ms)"
    )


@patch("scripts.latency_watchdog.requests.get")
def test_watchdog_connection_error(mock_get, mock_logger):
    # Simulate connection error for 3 iterations
    mock_get.side_effect = requests.exceptions.ConnectionError("Failed to connect")

    class BreakLoop(Exception):
        pass

    # We want it to run exactly 3 times, then break
    call_count = 0

    def side_effect_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise BreakLoop()

    with patch("scripts.latency_watchdog.time.sleep", side_effect=side_effect_sleep):
        try:
            run_watchdog()
        except BreakLoop:
            pass

    # It should have triggered a critical alert exactly once after 3 consecutive failures
    mock_logger.critical.assert_called_once_with(
        "API Unresponsive: Connection failed after 3 retries. (ConnectionError)"
    )
