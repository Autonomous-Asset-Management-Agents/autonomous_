# tests/unit/test_redis_locks.py
# Epic 2.3-Pre / PR-B — TDD Red-Phase
# Redis Distributed Locks + Streams + Rolling OHLCV Buffer
#
# Nutzt fakeredis für In-Memory-Redis ohne echten Server.
# Alle Tests ROT bis redis_client.py erweitert ist.
# Policy: docs/CODING_POLICY.md §11.5 TDD

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_async_redis():
    """Gibt einen fakeredis async Client zurück."""
    try:
        import fakeredis.aioredis as fakeredis_async

        return fakeredis_async.FakeRedis(decode_responses=True)
    except ImportError:
        pytest.skip("fakeredis nicht installiert — pip install fakeredis")


# ---------------------------------------------------------------------------
# 1. Distributed Locks
# ---------------------------------------------------------------------------


class TestRedisDistributedLock:
    @pytest.mark.anyio
    async def test_acquire_lock_returns_true_first_time(self):
        """Erster Lock-Acquire gibt True zurück."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        result = await RedisClient.acquire_lock(r, "test:lock:aapl", ttl_ms=5000)
        assert result is True

    @pytest.mark.anyio
    async def test_acquire_lock_returns_false_when_held(self):
        """Zweiter Acquire auf selben Key gibt False zurück (Lock bereits belegt)."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        await RedisClient.acquire_lock(r, "test:lock:msft", ttl_ms=5000)
        result = await RedisClient.acquire_lock(r, "test:lock:msft", ttl_ms=5000)
        assert result is False

    @pytest.mark.anyio
    async def test_release_lock_allows_reacquire(self):
        """Nach release_lock() kann derselbe Key erneut acquired werden."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        await RedisClient.acquire_lock(r, "test:lock:tsla", ttl_ms=5000)
        await RedisClient.release_lock(r, "test:lock:tsla")
        result = await RedisClient.acquire_lock(r, "test:lock:tsla", ttl_ms=5000)
        assert result is True

    @pytest.mark.anyio
    async def test_release_lock_is_idempotent(self):
        """release_lock() auf nicht-existent Key wirft keine Exception."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        # Sollte keinen Fehler werfen
        await RedisClient.release_lock(r, "test:lock:nonexistent")


# ---------------------------------------------------------------------------
# 2. Redis Streams
# ---------------------------------------------------------------------------


class TestRedisStreams:
    @pytest.mark.anyio
    async def test_publish_stream_adds_message(self):
        """publish_stream() fügt Message zum Stream hinzu."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        msg_id = await RedisClient.publish_stream(
            r, "test:stream:swap", {"event": "swap_initiated", "target": "LSTMDynamic"}
        )
        assert msg_id is not None  # XADD gibt Message-ID zurück

    @pytest.mark.anyio
    async def test_read_stream_returns_messages(self):
        """read_stream() gibt publizierte Nachrichten zurück."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        await RedisClient.publish_stream(
            r, "test:stream:events", {"event": "handover_complete", "from": "RLAgent"}
        )
        messages = await RedisClient.read_stream(r, "test:stream:events", last_id="0")
        assert len(messages) >= 1
        assert any(m.get("event") == "handover_complete" for m in messages)


# ---------------------------------------------------------------------------
# 3. Rolling OHLCV Buffer
# ---------------------------------------------------------------------------


class TestRollingOHLCVBuffer:
    @pytest.mark.anyio
    async def test_set_ohlcv_rolling_stores_tick(self):
        """set_ohlcv_rolling() speichert OHLCV-Tick im Buffer."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        ohlcv = {
            "open": 150.0,
            "high": 151.0,
            "low": 149.5,
            "close": 150.5,
            "volume": 1000,
        }
        await RedisClient.set_ohlcv_rolling(r, "AAPL", ohlcv)
        ticks = await RedisClient.get_ohlcv_rolling(r, "AAPL", count=1)
        assert len(ticks) == 1
        assert ticks[0]["close"] == 150.5

    @pytest.mark.anyio
    async def test_rolling_buffer_trims_to_max_60_ticks(self):
        """Nach 70 pushes sind nur noch 60 Ticks im Buffer (LTRIM)."""
        from core.redis_client import RedisClient

        r = _get_async_redis()
        for i in range(70):
            ohlcv = {
                "open": float(i),
                "high": float(i),
                "low": float(i),
                "close": float(i),
                "volume": i,
            }
            await RedisClient.set_ohlcv_rolling(r, "NVDA", ohlcv)

        ticks = await RedisClient.get_ohlcv_rolling(r, "NVDA", count=100)
        assert len(ticks) == 60, f"Expected 60 ticks, got {len(ticks)}"
