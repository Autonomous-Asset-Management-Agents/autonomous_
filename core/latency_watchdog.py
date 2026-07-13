import logging
import os
import threading
import time
from typing import Optional

import httpx

from core.kill_switch import kill_switch


class LatencyWatchdog:
    """
    Hybrid Latency Watchdog.
    Passively monitors recorded latency of real orders and actively pings the Alpaca API when idle.
    Trips the KillSwitch if latency exceeds the specified threshold.
    """

    def __init__(self, threshold_ms: Optional[int] = None, ping_interval_sec: int = 10):
        self.logger = logging.getLogger("latency_watchdog")
        self.threshold_ms = threshold_ms or int(
            os.environ.get("LATENCY_THRESHOLD_MS", 15000)
        )
        self.ping_interval_sec = ping_interval_sec
        self.last_activity_time = time.time()
        self.last_latency_ms = 0.0

        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.alpaca_api_key = kill_switch.alpaca_api_key
        self.alpaca_secret_key = kill_switch.alpaca_secret_key
        self.alpaca_base_url = kill_switch.alpaca_base_url

    def start(self):
        """Start the active background monitoring thread."""
        if "PYTEST_CURRENT_TEST" in os.environ:
            self.logger.info("Skipping LatencyWatchdog start under pytest environment.")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._ping_loop, daemon=True, name="LatencyWatchdog"
        )
        self._thread.start()
        self.logger.info(
            f"Started LatencyWatchdog (Threshold: {self.threshold_ms}ms, Ping interval: {self.ping_interval_sec}s)"
        )

    def stop(self):
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def record_passive_latency(self, latency_ms: float, endpoint: str = "unknown"):
        """Record latency from a real API request."""
        self.last_activity_time = time.time()
        self.last_latency_ms = latency_ms

        if latency_ms > self.threshold_ms:
            self.logger.warning(
                f"High latency detected on {endpoint}: {latency_ms:.1f}ms (Threshold: {self.threshold_ms}ms)"
            )
            kill_switch.trip(
                f"Passive Latency anomaly ({latency_ms:.1f}ms > {self.threshold_ms}ms) on {endpoint}"
            )

    def status(self) -> dict:
        """Read-only liveness snapshot (ADR-OBS-01 / PR B) — NEVER mutates state.

        Exposed via /engine-diagnostics ``watchdogs.latency``: last observed
        latency, the trip threshold, idle time since the last activity, and
        whether the background ping thread is running/alive. Null-safe."""
        last_activity = getattr(self, "last_activity_time", None)
        idle = (time.time() - last_activity) if last_activity is not None else None
        thread = getattr(self, "_thread", None)
        return {
            "last_latency_ms": getattr(self, "last_latency_ms", None),
            "threshold_ms": getattr(self, "threshold_ms", None),
            "seconds_since_last_activity": (
                round(idle, 1) if idle is not None else None
            ),
            "running": bool(getattr(self, "_running", False)),
            "thread_alive": bool(thread.is_alive()) if thread is not None else False,
        }

    def _ping_loop(self):
        """Background thread that actively pings the API if idle."""
        # Using synchronous httpx client for the background thread ping
        # with a timeout equal to the threshold
        timeout_sec = self.threshold_ms / 1000.0

        headers = {
            "APCA-API-KEY-ID": self.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.alpaca_secret_key,
        }
        ping_url = f"{self.alpaca_base_url}/v2/clock"

        while self._running:
            time.sleep(1)  # Check interval (sleep 1s to be responsive to stop)

            # If kill switch is already tripped, no need to keep pinging to trip it again
            if kill_switch.is_halted():
                continue

            now = time.time()
            if now - self.last_activity_time >= self.ping_interval_sec:
                # Time for an active ping
                start_time = time.perf_counter()
                try:
                    with httpx.Client(timeout=timeout_sec) as client:
                        response = client.get(ping_url, headers=headers)
                        response.raise_for_status()

                    latency_ms = (time.perf_counter() - start_time) * 1000.0
                    self.last_activity_time = time.time()
                    self.last_latency_ms = latency_ms

                    if latency_ms > self.threshold_ms:
                        self.logger.warning(
                            f"High latency on active ping: {latency_ms:.1f}ms"
                        )
                        kill_switch.trip(
                            f"Active Ping Latency anomaly ({latency_ms:.1f}ms > {self.threshold_ms}ms)"
                        )

                except httpx.ReadTimeout:
                    self.logger.error(
                        f"Ping timeout! Latency exceeded {self.threshold_ms}ms."
                    )
                    kill_switch.trip(f"Active Ping Timeout (>{self.threshold_ms}ms)")
                except Exception as e:
                    self.logger.warning("Ping error: %s", e)
                    # Only trip on latency, generic request errors might be Alpaca 500s or temporary issues
                    # If it's a connect timeout it might also be > 2000ms
                    elapsed = (time.perf_counter() - start_time) * 1000.0
                    if elapsed > self.threshold_ms:
                        kill_switch.trip(
                            f"Ping Connection Error / Timeout ({elapsed:.1f}ms > {self.threshold_ms}ms): {e}"
                        )


# Global instance
latency_watchdog = LatencyWatchdog()
