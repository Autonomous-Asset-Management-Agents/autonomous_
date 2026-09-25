"""TDD (ADR-OBS-01 / PR B): watchdogs + loops liveness instrumentation.

Covers the "is the engine actually evaluating / cycling" liveness that was
missing during the ``system_halted`` halt incident:

  * The three watchdog ``status()`` getters (cycle / latency / ml) are read-only,
    null-safe, and return the expected keys with sane values.
  * SAFETY — the loop counters (cycles_completed / scans_completed /
    high_latency_cycles) are PURE OBSERVATION: if a counter increment raises,
    the loop body still completes and control flow is unchanged.
  * The ``watchdogs`` subsystem + the new ``loops`` fields appear in
    ``/engine-diagnostics`` and are fail-soft (a raising collector →
    ``{"_error": ...}`` while the endpoint stays 200).
  * HITL exposes a read-only ``count_approved`` accessor (must NOT mutate).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app

# --------------------------------------------------------------------------- #
# 1. Watchdog status() getters — read-only, null-safe, expected keys
# --------------------------------------------------------------------------- #


def test_cycle_watchdog_status_keys_and_values():
    from core.cycle_watchdog import CycleWatchdog

    wd = CycleWatchdog(alert_threshold=3, kill_threshold=5)
    st = wd.status()
    for k in (
        "consecutive_empty",
        "alert_threshold",
        "kill_threshold",
        "seconds_since_last_successful_cycle",
    ):
        assert k in st, f"cycle status missing {k}"
    assert st["consecutive_empty"] == 0
    assert st["alert_threshold"] == 3
    assert st["kill_threshold"] == 5
    assert st["seconds_since_last_successful_cycle"] >= 0

    # It reflects state and NEVER mutates it (read-only).
    wd.record_empty_cycle(0)
    before = wd._consecutive_empty
    st2 = wd.status()
    assert st2["consecutive_empty"] == 1
    assert wd._consecutive_empty == before  # status() did not change state


def test_latency_watchdog_status_keys_and_values():
    from core.latency_watchdog import LatencyWatchdog

    wd = LatencyWatchdog(threshold_ms=15000)
    st = wd.status()
    for k in (
        "last_latency_ms",
        "threshold_ms",
        "seconds_since_last_activity",
        "running",
        "thread_alive",
    ):
        assert k in st, f"latency status missing {k}"
    assert st["threshold_ms"] == 15000
    assert st["running"] is False
    assert st["thread_alive"] is False  # no thread started
    assert st["seconds_since_last_activity"] >= 0


def test_ml_watchdog_status_keys_and_values():
    from core.ml_watchdog import MLWatchdog

    wd = MLWatchdog(alert_threshold_sec=60, kill_threshold_sec=300)
    st = wd.status()
    for k in (
        "ml_failing",
        "ml_failure_elapsed_seconds",
        "alert_threshold_sec",
        "kill_threshold_sec",
        "escalated",
    ):
        assert k in st, f"ml status missing {k}"
    # No failure yet.
    assert st["ml_failing"] is False
    assert st["ml_failure_elapsed_seconds"] is None
    assert st["alert_threshold_sec"] == 60
    assert st["kill_threshold_sec"] == 300
    assert st["escalated"] is False

    # After a first error the getter reflects ml_failing without mutating trackers.
    wd.first_error_time = wd.first_error_time or __import__("time").time()
    st2 = wd.status()
    assert st2["ml_failing"] is True
    assert st2["ml_failure_elapsed_seconds"] is not None
    assert st2["ml_failure_elapsed_seconds"] >= 0


# --------------------------------------------------------------------------- #
# 2. SAFETY — loop counters are pure observation
# --------------------------------------------------------------------------- #


def test_bump_loop_counter_is_fail_safe():
    """The raw counter bump swallows every error (never raises into the loop)."""
    from core.engine.loop_counters import _bump_loop_counter

    class _Boom:
        def __add__(self, other):
            raise RuntimeError("counter exploded")

    engine = MagicMock()
    # Poison the attribute so the += would raise; the bump must swallow it.
    engine._scans_completed = _Boom()
    _bump_loop_counter(engine, "_scans_completed")  # must NOT raise


def test_monitor_scan_counter_failure_never_breaks_scan(monkeypatch):
    """SAFETY: if the scans_completed increment raises, the REAL fail-safe bump
    swallows it and the monitor scan body still completes one cycle and exits
    cleanly (control flow unchanged) — the counter is pure observation."""
    from core.engine.monitor_loop import MonitorLoopMixin

    # Silence the slack notifier so the test never touches the network.
    monkeypatch.setattr(
        "core.engine.monitor_loop.send_slack_alert", MagicMock(), raising=True
    )

    class _Boom:
        def __add__(self, other):
            raise RuntimeError("counter exploded")

    eng = MagicMock(spec=MonitorLoopMixin)
    # Poison the counter attribute so the += inside the real _bump_loop_counter would
    # raise — the fail-safe wrapper MUST swallow it and the scan must still complete.
    eng._scans_completed = _Boom()
    # monitor_running: True for one pass, then False so the while-loop exits.
    eng.monitor_running = MagicMock()
    eng.monitor_running.is_set.side_effect = [True, False]
    eng._shutdown_event = MagicMock()
    eng._shutdown_event.is_set.return_value = False
    eng._shutdown_event.wait = MagicMock()
    eng._skipped_symbols = set()
    eng._log_strategy_thought = MagicMock()
    eng.current_market_data = {}
    eng.regime_model = MagicMock()
    eng.regime_model.get_market_regime.return_value = {
        "regime": "Ranging",
        "value": 12.0,
    }
    eng.live_universe = ["AAPL"]
    eng.market_scanner = MagicMock()
    eng.market_scanner.scan_market = AsyncMock(
        return_value={
            "top_stocks": [{"symbol": "AAPL"}],
            "recommendation_confidence": "medium",
        }
    )
    eng.api = None
    eng.is_simulation = True
    eng.strategy_lock = MagicMock()
    eng.strategy_lock.__enter__ = MagicMock(return_value=None)
    eng.strategy_lock.__exit__ = MagicMock(return_value=False)
    eng.active_strategy = None
    eng._last_live_equity_write_date = None
    eng.strategy_thread = None
    eng.strategy_running = MagicMock()

    # Must complete the cycle without the poisoned counter escaping.
    MonitorLoopMixin.run_strategy_monitor_loop(eng)

    # Control flow unchanged: the scan actually ran despite the poisoned counter.
    eng.market_scanner.scan_market.assert_called()


# --------------------------------------------------------------------------- #
# 3. HITL count_approved — read-only accessor (must NOT mutate)
# --------------------------------------------------------------------------- #


def test_hitl_count_approved_is_read_only():
    """count_approved() counts hitl:approved:* keys WITHOUT mutating them (unlike
    claim_approved which moves approved→inflight)."""
    from core.hitl_queue import _APPROVED_PREFIX, HitlQueue

    redis = AsyncMock()
    redis.keys.return_value = [
        f"{_APPROVED_PREFIX}a",
        f"{_APPROVED_PREFIX}b",
    ]

    async def _fake_get_redis():
        return redis

    with patch("core.hitl_queue.RedisClient.get_redis", new=_fake_get_redis):
        n = asyncio.run(HitlQueue.count_approved())

    assert n == 2
    # Read-only: no delete / set on the approved keys.
    redis.delete.assert_not_called()
    redis.set.assert_not_called()


def test_hitl_count_approved_no_redis_is_zero():
    from core.hitl_queue import HitlQueue

    async def _no_redis():
        return None

    with patch("core.hitl_queue.RedisClient.get_redis", new=_no_redis):
        assert asyncio.run(HitlQueue.count_approved()) == 0


# --------------------------------------------------------------------------- #
# 4. /engine-diagnostics wiring — watchdogs subsystem + new loops fields
# --------------------------------------------------------------------------- #


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_watchdogs_subsystem_present_and_shaped(client_authed):
    body = client_authed.get("/engine-diagnostics").json()

    assert "watchdogs" in body
    wd = body["watchdogs"]
    assert "cycle" in wd
    assert "latency" in wd
    assert "ml" in wd
    for k in ("consecutive_empty", "alert_threshold", "kill_threshold"):
        assert k in wd["cycle"], f"watchdogs.cycle missing {k}"
    for k in ("last_latency_ms", "threshold_ms", "running", "thread_alive"):
        assert k in wd["latency"], f"watchdogs.latency missing {k}"
    for k in ("ml_failing", "alert_threshold_sec", "kill_threshold_sec", "escalated"):
        assert k in wd["ml"], f"watchdogs.ml missing {k}"


def test_loops_new_liveness_fields_present(client_authed):
    body = client_authed.get("/engine-diagnostics").json()
    loops = body["loops"]
    for k in (
        "cycles_completed",
        "scans_completed",
        "cycle_sample_count",
        "high_latency_cycles",
        "last_cycle_age_seconds",
        "is_market_open",
    ):
        assert k in loops, f"loops missing {k}"


def test_watchdogs_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_watchdogs", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["watchdogs"] == {"_error": "RuntimeError"}
    # Siblings unaffected.
    assert "_error" not in body["loops"]


def test_loops_collector_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_loops", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    assert r.json()["loops"] == {"_error": "RuntimeError"}
