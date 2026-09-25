"""#1230 (BUG-AI-001) — every ``redis.publish()`` in the order-execution path must go
through the single guarded ``_safe_publish`` route.

``RedisClient.get_redis()`` may legitimately return ``None`` (Enterprise-degraded /
no running loop). An unguarded ``await redis.publish(...)`` then raises
``AttributeError: 'NoneType' object has no attribute 'publish'`` — historically only
caught by the surrounding ``except`` and logged as a misleading ``WARNING "PubSub
error"``. These tests pin the guard (no-op on None, forward otherwise) and the
regression invariant (no unguarded publish remains in the source).
"""

import inspect
from unittest.mock import AsyncMock

import pytest

from core.engine import order_executor
from core.engine.order_executor import _safe_publish


@pytest.mark.asyncio
async def test_safe_publish_none_is_noop():
    """get_redis() -> None must NOT raise and must NOT attempt a publish."""
    # No exception = pass; the whole point is that None is tolerated.
    await _safe_publish(None, "explainability:u1", "{}")


@pytest.mark.asyncio
async def test_safe_publish_forwards_to_client():
    """A real client (Redis or LocalStateClient) receives the publish verbatim."""
    client = AsyncMock()
    await _safe_publish(client, "explainability:u1", '{"type":"x"}')
    client.publish.assert_awaited_once_with("explainability:u1", '{"type":"x"}')


def test_no_unguarded_publish_remains():
    """Regression invariant: no direct ``redis.publish(`` / ``_redis.publish(`` call
    survives in order_executor — every publish must route through ``_safe_publish``.

    ``_safe_publish`` itself uses the param name ``redis_conn`` so this assertion does
    not false-match the guarded helper body.
    """
    src = inspect.getsource(order_executor)
    assert (
        "redis.publish(" not in src
    ), "unguarded 'redis.publish(' found — route it through _safe_publish (#1230)"
    assert (
        "_redis.publish(" not in src
    ), "unguarded '_redis.publish(' found — route it through _safe_publish (#1230)"
