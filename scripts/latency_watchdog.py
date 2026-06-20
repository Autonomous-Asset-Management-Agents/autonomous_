import argparse
import logging
import time

import requests

from core.cloud_logger import setup_logging


def run_watchdog(
    url: str = "http://localhost:8001/system-health",
    interval: int = 5,
    timeout_ms: int = 2000,
):
    setup_logging()
    logger = logging.getLogger("latency_watchdog")
    logger.info(
        f"Starting Latency Watchdog on {url} (Interval: {interval}s, SLA Threshold: {timeout_ms}ms)"
    )

    consecutive_errors = 0
    max_errors = 3

    while True:
        try:
            start_time = time.perf_counter()
            response = requests.get(url, timeout=5.0)
            latency_ms = (time.perf_counter() - start_time) * 1000

            if response.status_code >= 500:
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    logger.critical(
                        f"API Unresponsive: 500+ Error after {max_errors} retries. (Status: {response.status_code})"
                    )
                    consecutive_errors = 0  # Reset after alarm
            else:
                consecutive_errors = 0  # Reset on successful ping
                if latency_ms > timeout_ms:
                    logger.critical(
                        f"Latency SLA breached: {latency_ms:.1f}ms (Threshold: {timeout_ms}ms)"
                    )
                else:
                    logger.info(f"Ping OK - Latency: {latency_ms:.1f}ms")

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                logger.critical(
                    f"API Unresponsive: Connection failed after {max_errors} retries. ({type(e).__name__})"
                )
                consecutive_errors = 0
        except Exception as e:
            logger.error(f"Unexpected error in watchdog: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Latency Watchdog")
    parser.add_argument(
        "--url",
        default="http://localhost:8001/system-health",
        help="API Endpoint to ping",
    )
    parser.add_argument(
        "--interval", type=int, default=5, help="Polling interval in seconds"
    )
    parser.add_argument(
        "--timeout-ms", type=int, default=2000, help="SLA Latency threshold in ms"
    )
    args = parser.parse_args()

    try:
        run_watchdog(url=args.url, interval=args.interval, timeout_ms=args.timeout_ms)
    except KeyboardInterrupt:
        print("Watchdog stopped.")
