# tests/unit/test_hitl_queue.py
# ii-1 (PR-0a-ii, GAP2): the HitlQueue storage layer + LocalStateClient.keys/incrbyfloat.
#
# Dormant foundation: the queue is UNWIRED (nothing calls it yet). These tests exercise it
# directly against a real in-memory LocalStateClient (deterministic, no external Redis) — the
# same object RedisClient.get_redis() returns on the desktop path, so we test the real
# cross-backend code path. Covers: push/get_pending/approve/reject + claim/ack/recover (the
# crash-safe drain) happy paths,
# G dedup on (symbol,user_id), has_pending point-lookup, N6 index-expires-with-pending (no
# stale-True permanent block), redis-None no-op, and N5 atomic incrbyfloat.
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
    """Patch RedisClient.get_redis() to return our shared in-memory client."""
    return patch(
        "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=client)
    )


_PUSH = {
    "user_id": "u1",
    "symbol": "AAPL",
    "action": "BUY",
    "qty": 10.0,
    "price": 100.0,
    "conviction": 0.8,
    "target_weight": 0.1,
    "event_json": "{}",
}


def test_push_get_pending_approve_drains():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        aid = _run(HitlQueue.push(**_PUSH))
        assert aid
        pending = _run(HitlQueue.get_pending())
        assert len(pending) == 1 and pending[0]["symbol"] == "AAPL"

        payload = _run(HitlQueue.approve(aid))
        assert payload and payload["action"] == "BUY"
        assert _run(HitlQueue.get_pending()) == []  # moved out of pending

        claimed = _run(HitlQueue.claim_approved())
        assert len(claimed) == 1 and claimed[0]["symbol"] == "AAPL"
        assert _run(HitlQueue.claim_approved()) == []  # approved consumed exactly once
        assert (
            _run(HitlQueue.ack_inflight(aid)) is True
        )  # marker cleared after execution


def test_claim_moves_to_inflight_then_ack_clears():
    # The drain must not delete-on-read: a claimed approval lives as an inflight marker until
    # the caller acks a definitive outcome, so a crash in between cannot lose it.
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        aid = _run(HitlQueue.push(**_PUSH))
        _run(HitlQueue.approve(aid))
        claimed = _run(HitlQueue.claim_approved())
        assert len(claimed) == 1 and claimed[0]["approval_id"] == aid
        assert _run(client.keys("hitl:inflight:*"))  # marker present until acked
        assert _run(
            HitlQueue.recover_orphaned_inflight()
        )  # visible as in-flight meanwhile
        assert _run(HitlQueue.ack_inflight(aid)) is True
        assert _run(client.keys("hitl:inflight:*")) == []  # cleared
        assert _run(HitlQueue.recover_orphaned_inflight()) == []


def test_crash_mid_drain_orphan_surfaced_not_reexecuted():
    # Claim WITHOUT ack = the engine crashed mid-execution. The approval must NOT be silently
    # lost (recover surfaces it) and must NOT be auto-re-claimed/re-executed (no double order).
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        aid = _run(HitlQueue.push(**_PUSH))
        _run(HitlQueue.approve(aid))
        _run(HitlQueue.claim_approved())  # claimed; we never ack (simulated crash)

        assert (
            _run(HitlQueue.claim_approved()) == []
        )  # not re-claimed for auto re-execution
        orphans = _run(
            HitlQueue.recover_orphaned_inflight()
        )  # but surfaced for the operator
        assert len(orphans) == 1 and orphans[0]["approval_id"] == aid


def test_reject_removes_pending_and_index():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        aid = _run(HitlQueue.push(**_PUSH))
        assert _run(HitlQueue.reject(aid, "nope")) is True
        assert _run(HitlQueue.get_pending()) == []
        assert _run(HitlQueue.has_pending("AAPL", "u1")) is False


def test_push_dedups_same_symbol_user():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        a1 = _run(HitlQueue.push(**_PUSH))
        a2 = _run(HitlQueue.push(**{**_PUSH, "action": "SELL"}))  # supersedes a1
        pending = _run(HitlQueue.get_pending())
        assert len(pending) == 1  # only one live approval for (u1, AAPL)
        assert pending[0]["action"] == "SELL"
        assert a1 != a2


def test_has_pending_true_after_push():
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        _run(HitlQueue.push(**_PUSH))
        assert _run(HitlQueue.has_pending("AAPL", "u1")) is True
        assert _run(HitlQueue.has_pending("MSFT", "u1")) is False


def test_has_pending_false_after_index_expiry():
    # N6: the index must expire WITH the pending key — else has_pending returns stale-True
    # and the symbol is permanently blocked from new autonomous signals.
    client = LocalStateClient()
    with _with_backend(client):
        from core.hitl_queue import HitlQueue

        _run(HitlQueue.push(**_PUSH))
        for k in list(client._expiries.keys()):  # simulate TTL elapsed
            client._expiries[k] = time.time() - 1.0
        assert _run(HitlQueue.has_pending("AAPL", "u1")) is False
        assert _run(HitlQueue.get_pending()) == []


def test_no_redis_is_noop():
    with patch("core.redis_client.RedisClient.get_redis", AsyncMock(return_value=None)):
        from core.hitl_queue import HitlQueue

        assert _run(HitlQueue.push(**_PUSH)) is None
        assert _run(HitlQueue.get_pending()) == []
        assert _run(HitlQueue.approve("x")) is None
        assert _run(HitlQueue.reject("x")) is False
        assert _run(HitlQueue.claim_approved()) == []
        assert _run(HitlQueue.ack_inflight("x")) is False
        assert _run(HitlQueue.recover_orphaned_inflight()) == []
        assert _run(HitlQueue.has_pending("AAPL", "u1")) is False


def test_incrbyfloat_atomic_concurrent():
    # N5: the whole read-modify-write is inside one `with self._lock` block (no await),
    # so 1000 concurrent coroutines cannot lose updates. A get()+set() impl would.
    client = LocalStateClient()

    async def _race():
        await asyncio.gather(*[client.incrbyfloat("k", 1.0) for _ in range(1000)])
        return await client.get("k")

    assert float(_run(_race())) == 1000.0


def test_localstate_keys_glob_is_case_sensitive():
    # Redis KEYS is always case-sensitive; keys() must match it on every OS (fnmatchcase,
    # not fnmatch — the latter is case-insensitive on Windows = a watermelon trap).
    client = LocalStateClient()
    _run(client.set("hitl:pending:a", "1"))
    _run(client.set("hitl:pending:b", "2"))
    _run(client.set("other", "3"))
    assert set(_run(client.keys("hitl:pending:*"))) == {
        "hitl:pending:a",
        "hitl:pending:b",
    }
    assert _run(client.keys("HITL:PENDING:*")) == []  # case-sensitive, like real Redis
