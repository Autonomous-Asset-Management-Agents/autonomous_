import json
import os
import logging
from typing import Any, Dict, List, Optional

import redis
from redis import asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Singleton-wrapper for async Redis connection.
    Connects to REDIS_URL environment variable.

    Epic 2.3-Pre / PR-B: Erweiterung um Distributed Locks, Streams und
    Rolling OHLCV Buffer für Hot-Swap-Infrastruktur.
    """

    _instance = None
    _async_redis = None
    _sync_redis = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(RedisClient, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    @classmethod
    async def get_redis(cls) -> Optional[aioredis.Redis]:
        if cls._async_redis is not None:
            try:
                await cls._async_redis.ping()
            except RuntimeError as e:
                if "Event loop is closed" in str(
                    e
                ) or "attached to a different loop" in str(e):
                    logger.warning(
                        "RedisClient: Event loop is closed or detached. Recreating async client..."
                    )
                    cls._async_redis = None
            except Exception:
                pass

        if cls._async_redis is None:
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            try:
                # ADR: Memorystore TLS uses Google internal CA (not in standard bundle).
                # ssl_cert_reqs=None disables cert verification safe for VPC-peered connections.
                use_tls = redis_url.startswith("rediss://")
                kwargs = {"decode_responses": True}
                if use_tls:
                    kwargs["ssl_cert_reqs"] = None

                cls._async_redis = aioredis.from_url(redis_url, **kwargs)
                logger.info("Initialized async Redis client at %s", redis_url)
            except Exception as e:
                logger.error("Failed to initialize async Redis client: %s", e)
        return cls._async_redis

    @classmethod
    def get_sync_redis(cls) -> Optional[redis.Redis]:
        if cls._sync_redis is None:
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            try:
                # ADR: same ssl_cert_reqs=None for Memorystore TLS (internal CA)
                use_tls = redis_url.startswith("rediss://")
                kwargs = {"decode_responses": True}
                if use_tls:
                    kwargs["ssl_cert_reqs"] = None

                cls._sync_redis = redis.from_url(redis_url, **kwargs)
                logger.info("Initialized sync Redis client at %s", redis_url)
            except Exception as e:
                logger.error("Failed to initialize sync Redis client: %s", e)
        return cls._sync_redis

    @classmethod
    async def check_health(cls) -> bool:
        """Check if Redis is reachable."""
        try:
            r = await cls.get_redis()
            return await r.ping()
        except RedisError as e:
            logger.error("Redis health check failed: %s", e)
            return False

    @classmethod
    async def close(cls):
        if cls._async_redis is not None:
            await cls._async_redis.aclose()
            cls._async_redis = None
            logger.info("Closed async Redis connection")
        if cls._sync_redis is not None:
            cls._sync_redis.close()
            cls._sync_redis = None
            logger.info("Closed sync Redis connection")

    # ------------------------------------------------------------------
    # Distributed Locks (Redlock-Pattern — Epic 2.3-Pre / PR-B)
    # ------------------------------------------------------------------

    @classmethod
    async def acquire_lock(
        cls,
        r: aioredis.Redis,
        key: str,
        ttl_ms: int = 5000,
    ) -> bool:
        """Atomarer Distributed Lock via SETNX + PEXPIRE (Redlock-Pattern).

        Args:
            r:       Async Redis-Instanz (erlaubt Dependency Injection für Tests).
            key:     Lock-Key (z.B. "lock:position:AAPL").
            ttl_ms:  TTL in Millisekunden (default: 5000ms = 5s).

        Returns:
            True wenn Lock erworben, False wenn bereits belegt.
        """
        result = await r.set(key, "1", px=ttl_ms, nx=True)
        return result is True

    @classmethod
    async def release_lock(cls, r: aioredis.Redis, key: str) -> None:
        """Gibt einen Distributed Lock frei (DEL — idempotent).

        Args:
            r:   Async Redis-Instanz.
            key: Lock-Key der gelöscht werden soll.
        """
        await r.delete(key)

    # ------------------------------------------------------------------
    # Redis Streams (Inter-Agent-Messaging — Epic 2.3-Pre / PR-B)
    # ------------------------------------------------------------------

    @classmethod
    async def publish_stream(
        cls,
        r: aioredis.Redis,
        stream: str,
        message: Dict[str, Any],
    ) -> Optional[str]:
        """Publiziert eine Nachricht in einen Redis Stream via XADD.

        Args:
            r:       Async Redis-Instanz.
            stream:  Stream-Name (z.B. "stream:agent:events").
            message: Nachricht als Dict (Werte müssen str-kompatibel sein).

        Returns:
            Message-ID (z.B. "1699999999999-0") oder None bei Fehler.
        """
        try:
            # Alle Werte zu str konvertieren (Redis Streams-Anforderung)
            str_message = {k: str(v) for k, v in message.items()}
            msg_id = await r.xadd(stream, str_message)
            return msg_id
        except RedisError as e:
            logger.error("publish_stream(%s) failed: %s", stream, e)
            return None

    @classmethod
    async def read_stream(
        cls,
        r: aioredis.Redis,
        stream: str,
        last_id: str = "$",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """Liest Nachrichten aus einem Redis Stream via XREAD.

        Args:
            r:       Async Redis-Instanz.
            stream:  Stream-Name.
            last_id: Ab welcher ID gelesen wird ("0" = alles, "$" = nur neue).
            count:   Maximale Anzahl Nachrichten.

        Returns:
            Liste von Nachrichten als Dicts.
        """
        try:
            result = await r.xread({stream: last_id}, count=count)
            messages = []
            if result:
                for _stream_name, entries in result:
                    for _msg_id, fields in entries:
                        messages.append(dict(fields))
            return messages
        except RedisError as e:
            logger.error("read_stream(%s) failed: %s", stream, e)
            return []

    # ------------------------------------------------------------------
    # Rolling OHLCV Buffer (LSTM-Inferenz nach Swap — Epic 2.3-Pre / PR-B)
    # ------------------------------------------------------------------

    _OHLCV_BUFFER_KEY = "ohlcv:rolling:{symbol}"
    _OHLCV_MAX_TICKS = 60

    @classmethod
    async def set_ohlcv_rolling(
        cls,
        r: aioredis.Redis,
        symbol: str,
        ohlcv: Dict[str, Any],
    ) -> None:
        """Fügt einen OHLCV-Tick zum Rolling Buffer hinzu (max 60 Ticks via LTRIM).

        Neue Ticks werden rechts angefügt (RPUSH). LTRIM hält die letzten
        _OHLCV_MAX_TICKS Einträge — älteste werden automatisch verworfen.

        Args:
            r:      Async Redis-Instanz.
            symbol: Ticker-Symbol (z.B. "AAPL").
            ohlcv:  OHLCV-Dict mit open, high, low, close, volume.
        """
        key = cls._OHLCV_BUFFER_KEY.format(symbol=symbol)
        serialized = json.dumps(ohlcv)
        pipe = r.pipeline()
        pipe.rpush(key, serialized)
        pipe.ltrim(key, -cls._OHLCV_MAX_TICKS, -1)
        await pipe.execute()

    @classmethod
    async def get_ohlcv_rolling(
        cls,
        r: aioredis.Redis,
        symbol: str,
        count: int = 60,
    ) -> List[Dict[str, Any]]:
        """Liest die letzten `count` OHLCV-Ticks aus dem Rolling Buffer.

        Args:
            r:      Async Redis-Instanz.
            symbol: Ticker-Symbol.
            count:  Anzahl Ticks (max 60).

        Returns:
            Liste von OHLCV-Dicts, älteste zuerst.
        """
        key = cls._OHLCV_BUFFER_KEY.format(symbol=symbol)
        actual_count = min(count, cls._OHLCV_MAX_TICKS)
        try:
            raw = await r.lrange(key, -actual_count, -1)
            return [json.loads(item) for item in raw]
        except RedisError as e:
            logger.error("get_ohlcv_rolling(%s) failed: %s", symbol, e)
            return []
