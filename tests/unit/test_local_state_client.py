"""
Unit tests for LocalStateClient (OSS-4 / #1085).

Tests the in-memory Redis replacement for desktop mode.
"""

import pytest

from core.local_state_client import LocalStateClient


@pytest.fixture
def client():
    return LocalStateClient()


class TestLocalStateClientKV:
    """Key-value operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, client):
        await client.set("key1", "value1")
        result = await client.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, client):
        result = await client.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, client):
        await client.set("key1", "value1")
        count = await client.delete("key1")
        assert count == 1
        assert await client.get("key1") is None

    @pytest.mark.asyncio
    async def test_setnx_respects_existing_key(self, client):
        await client.set("key1", "original")
        result = await client.set("key1", "overwrite", nx=True)
        assert result is None  # Key exists → no-op
        assert await client.get("key1") == "original"

    @pytest.mark.asyncio
    async def test_setnx_on_new_key(self, client):
        result = await client.set("new_key", "value", nx=True)
        assert result is True
        assert await client.get("new_key") == "value"


class TestLocalStateClientHealth:
    """Health check."""

    @pytest.mark.asyncio
    async def test_ping(self, client):
        assert await client.ping() is True

    def test_ping_sync(self, client):
        assert client.ping_sync() is True


class TestLocalStateClientLists:
    """List operations (OHLCV rolling buffer pattern)."""

    @pytest.mark.asyncio
    async def test_rpush_and_lrange(self, client):
        await client.rpush("list1", "a", "b", "c")
        result = await client.lrange("list1", 0, -1)
        assert result == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_ltrim_keeps_last_n(self, client):
        for i in range(10):
            await client.rpush("buffer", str(i))
        await client.ltrim("buffer", -5, -1)
        result = await client.lrange("buffer", 0, -1)
        assert result == ["5", "6", "7", "8", "9"]

    @pytest.mark.asyncio
    async def test_pipeline_rpush_ltrim(self, client):
        """Pipeline mimics the OHLCV rolling buffer pattern."""
        pipe = client.pipeline()
        pipe.rpush("ohlcv:AAPL", '{"close": 150.0}')
        pipe.ltrim("ohlcv:AAPL", -60, -1)
        await pipe.execute()

        result = await client.lrange("ohlcv:AAPL", 0, -1)
        assert len(result) == 1
        assert "150.0" in result[0]


class TestLocalStateClientStreams:
    """Stream operations (inter-agent messaging)."""

    @pytest.mark.asyncio
    async def test_xadd_and_xread(self, client):
        msg_id = await client.xadd("events", {"type": "signal", "symbol": "AAPL"})
        assert msg_id is not None

        # Read all from beginning
        result = await client.xread({"events": "0"}, count=10)
        assert len(result) == 1
        stream_name, entries = result[0]
        assert stream_name == "events"
        assert len(entries) == 1
        assert entries[0][1]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_xread_dollar_returns_empty(self, client):
        """$ means only new messages — nothing in memory yet."""
        await client.xadd("events", {"type": "test"})
        result = await client.xread({"events": "$"}, count=10)
        assert result == []


class TestLocalStateClientCleanup:
    """Cleanup operations."""

    @pytest.mark.asyncio
    async def test_aclose_clears_state(self, client):
        await client.set("key1", "value1")
        await client.rpush("list1", "item")
        await client.xadd("stream1", {"k": "v"})

        await client.aclose()

        assert await client.get("key1") is None
        assert await client.lrange("list1", 0, -1) == []


class TestRedisClientFactory:
    """Test that RedisClient.get_redis() returns LocalStateClient in local mode."""

    @pytest.mark.asyncio
    async def test_local_mode_returns_local_state_client(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            from core.redis_client import RedisClient

            result = await RedisClient.get_redis()
            assert result is not None
            assert await result.ping() is True


class TestLocalStateClientLockStub:
    """Tests for lock() stub — prevents AttributeError when redis_client.lock() is called."""

    @pytest.mark.asyncio
    async def test_lock_always_acquires(self, client):
        """lock().acquire() returns True — no contention in single-process desktop mode."""
        lock = client.lock("order_lock:user:AAPL", timeout=12.0)
        result = await lock.acquire(blocking=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_lock_as_async_context_manager(self, client):
        """lock() supports 'async with' syntax without AttributeError."""
        async with client.lock("test-key"):
            pass  # No crash, no exception
