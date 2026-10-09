# tests/unit/test_interruptible_pause.py
# BUG-AI-003/006/S01 (#1232): the engine's blocking sleeps in the monitor-loop
# liquidation path, the specialist warm-up check (60s) and the news poller throttle now
# go through BotEngine._interruptible_pause(), which returns IMMEDIATELY on shutdown
# instead of blocking teardown for the full duration.
import threading
import time

from core.engine.base import BotEngine


def _engine_shell() -> BotEngine:
    # Bypass the heavy __init__ — we only exercise the pause helper + its shutdown event.
    engine = object.__new__(BotEngine)
    engine._shutdown_event = threading.Event()
    return engine


def test_pause_returns_immediately_when_shutdown_is_set():
    engine = _engine_shell()
    engine._shutdown_event.set()
    t0 = time.monotonic()
    fired = engine._interruptible_pause(60)  # would block 60s with bare time.sleep
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"shutdown must short-circuit the pause (took {elapsed:.2f}s)"
    assert fired is True


def test_pause_waits_the_timeout_when_not_shutting_down():
    engine = _engine_shell()
    t0 = time.monotonic()
    fired = engine._interruptible_pause(0.15)
    elapsed = time.monotonic() - t0
    assert (
        0.1 <= elapsed < 1.0
    ), f"must wait ~0.15s when not shutting down (was {elapsed:.2f}s)"
    assert fired is False
