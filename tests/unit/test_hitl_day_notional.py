# tests/unit/test_hitl_day_notional.py
# ii-4a (PR-0a-ii, GAP2): the per-NY-trading-day autonomous-notional counter.
#
# Redis-persisted so a process restart cannot reset the day's autonomous budget (a
# process-local counter would re-open the full budget on every restart = an Art-14
# bypass, the P2/N3 finding). Keyed by NY date; rollover deletes YESTERDAY's key and
# never zeroes today's (N3). Dormant: the threshold gate (PR-0a-ii-4b) wires it.
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.local_state_client import LocalStateClient  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _with_backend(client):
    return patch(
        "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=client)
    )


def test_add_then_current_accumulates():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_day_notional import HitlDayNotional

        assert _run(HitlDayNotional.current("2026-06-14")) == 0.0
        _run(HitlDayNotional.add("2026-06-14", 3000.0))
        _run(HitlDayNotional.add("2026-06-14", 2000.0))
        assert _run(HitlDayNotional.current("2026-06-14")) == 5000.0


def test_add_sets_ttl():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_day_notional import HitlDayNotional

        _run(HitlDayNotional.add("2026-06-14", 100.0))
        for k in list(client._expiries):  # simulate the TTL elapsing
            client._expiries[k] = time.time() - 1.0
        assert _run(HitlDayNotional.current("2026-06-14")) == 0.0


def test_rollover_deletes_previous_not_today():
    # N3: on an NY-date change, delete YESTERDAY's key; never zero today's.
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_day_notional import HitlDayNotional

        _run(HitlDayNotional.add("2026-06-13", 5000.0))  # yesterday
        _run(HitlDayNotional.add("2026-06-14", 1000.0))  # today
        _run(HitlDayNotional.rollover("2026-06-13"))
        assert _run(HitlDayNotional.current("2026-06-13")) == 0.0  # yesterday cleaned
        assert _run(HitlDayNotional.current("2026-06-14")) == 1000.0  # today untouched


def test_no_redis_is_noop():
    with patch("core.redis_client.RedisClient.get_redis", AsyncMock(return_value=None)):
        from core.hitl_day_notional import HitlDayNotional

        assert _run(HitlDayNotional.current("2026-06-14")) == 0.0
        assert _run(HitlDayNotional.add("2026-06-14", 100.0)) == 0.0
        _run(HitlDayNotional.rollover("2026-06-13"))  # must not raise


def test_localstate_pexpire():
    client = LocalStateClient()
    assert _run(client.pexpire("missing", 1000)) is False  # no such key
    _run(client.set("k", "v"))
    assert _run(client.pexpire("k", 50_000)) is True
    assert _run(client.get("k")) == "v"
    for kk in list(client._expiries):  # simulate the TTL elapsing
        client._expiries[kk] = time.time() - 1.0
    assert _run(client.get("k")) is None
