import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Guarded imports: Redis may not be installed in local-only desktop mode
try:
    import redis
    from redis import asyncio as aioredis
    from redis.exceptions import RedisError

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    redis = None  # type: ignore[assignment]
    aioredis = None  # type: ignore[assignment]

    class RedisError(Exception):  # type: ignore[no-redef]
        """Stub for when redis is not installed."""

        pass


# Local state client for desktop mode (lazy import to avoid circular)
_local_state_instance = None


def _get_local_state_client():
    """Singleton factory for LocalStateClient (desktop mode)."""
    global _local_state_instance
    if _local_state_instance is None:
        from core.local_state_client import LocalStateClient

        _local_state_instance = LocalStateClient()
    return _local_state_instance


def _is_local_mode() -> bool:
    """Check if we're in local desktop mode (no Redis)."""
    return not os.environ.get("REDIS_URL", "").strip()


class _SyncLocalStateFacade:
    """Synchronous, redis-py-compatible facade over LocalStateClient (local/desktop mode).

    BORA: get_sync_redis() must hand every consumer a *synchronous* handle. In cloud
    mode that is a real redis-py client (sync). In local/desktop mode the backend is
    LocalStateClient, whose get/set/delete are coroutines — so a sync caller doing
    ``r.get(k)`` would receive an un-awaited coroutine and silently break: the
    /benchmark-equity route returned ``internal_error`` and the portfolio-snapshot
    writer (core/engine/base.py) threw on ``json.loads(<coroutine>)`` and was swallowed,
    so ``portfolio_snapshots`` was NEVER written and P&L/Drawdown/Sharpe stayed empty
    forever on desktop. This facade routes the redis-py method names to LocalStateClient's
    existing ``*_sync`` variants and delegates everything else unchanged. Cloud is
    unaffected (that branch returns a real sync redis client). Only get_sync_redis() is
    wrapped — the async get_redis() keeps returning the raw client for ``await`` callers.
    """

    def __init__(self, client):
        self._client = client

    def get(self, key):
        return self._client.get_sync(key)

    def set(self, key, value, ex=None, px=None, nx=False, **_ignored):
        # redis-py callers use ex=<seconds>; LocalStateClient.set_sync uses px=<ms>.
        if ex is not None and px is None:
            px = int(ex) * 1000
        return self._client.set_sync(key, value, px=px, nx=nx)

    def delete(self, *keys):
        return self._client.delete_sync(*keys)

    def ping(self):
        # kill_switch.py and other sync consumers call .ping() on the sync handle.
        return self._client.ping_sync()

    # #1353 — the remaining methods sync consumers call on get_sync_redis():
    # hget/hset (agent weights: round_table/base_agent.py, learning/engine.py),
    # keys (scripts/analyze_bot.py), xadd (round_table/senate_log.py). LocalStateClient
    # has these only as coroutines (and had no hget/hset at all), so without this they
    # silently returned un-awaited coroutines / raised AttributeError.
    def hget(self, name, field):
        return self._client.hget_sync(name, field)

    def hset(self, name, field=None, value=None, mapping=None):
        # sync consumers use the positional hset(name, field, value) form.
        return self._client.hset_sync(name, field, value)

    def keys(self, pattern="*"):
        return self._client.keys_sync(pattern)

    def xadd(self, stream, fields, *args, **kwargs):
        return self._client.xadd_sync(stream, fields)

    def __getattr__(self, name):
        # lock(), publish(), incrbyfloat(), context managers, ... stay as-is.
        if name == "_client":
            raise AttributeError(name)
        return getattr(self._client, name)


class RedisClient:
    """
    Singleton-wrapper for async Redis connection.

    BORA dual-mode (OSS-4 / #1085):
      - REDIS_URL set → real Redis connection (Enterprise/Cloud)
      - REDIS_URL empty → LocalStateClient in-memory (Desktop/Local)

    All 15+ consumer files call RedisClient.get_redis() / get_sync_redis()
    and receive a duck-typed compatible object transparently.

    Epic 2.3-Pre / PR-B: Erweiterung um Distributed Locks, Streams und
    Rolling OHLCV Buffer für Hot-Swap-Infrastruktur.
    """

    _instance = None
    _async_redis_pools = {}
    _sync_redis = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(RedisClient, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    @classmethod
    async def get_redis(cls):
        # ── OSS-4: Local desktop mode → LocalStateClient ───────────────
        if _is_local_mode():
            return _get_local_state_client()

        if not _REDIS_AVAILABLE:
            logger.warning(
                "redis package not installed and REDIS_URL is set — returning None"
            )
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        loop_id = id(loop)

        client = cls._async_redis_pools.get(loop_id)

        if client is not None:
            try:
                await client.ping()
            except RuntimeError as e:
                if "Event loop is closed" in str(
                    e
                ) or "attached to a different loop" in str(e):
                    logger.warning(
                        "RedisClient: Event loop is closed or detached. Recreating async client..."
                    )
                    client = None
            except Exception:
                pass

        if client is None:
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            try:
                # ADR: Memorystore TLS uses Google internal CA (not in standard bundle).
                # ssl_cert_reqs=None disables cert verification safe for VPC-peered connections.
                use_tls = redis_url.startswith("rediss://")
                kwargs = {"decode_responses": True}
                if use_tls:
                    kwargs["ssl_cert_reqs"] = None

                client = aioredis.from_url(redis_url, **kwargs)
                cls._async_redis_pools[loop_id] = client
                logger.info(
                    "Initialized async Redis client at %s for loop %s",
                    redis_url,
                    loop_id,
                )
            except Exception as e:
                logger.error("Failed to initialize async Redis client: %s", e)
        return client

    @classmethod
    def get_sync_redis(cls):
        # ── OSS-4: Local desktop mode → LocalStateClient (sync facade) ──
        # Wrap in the sync facade so .get/.set/.delete are synchronous (the backend's
        # bare get/set/delete are coroutines). Without this, sync consumers silently
        # received un-awaited coroutines (broke the benchmark route + snapshot writer).
        if _is_local_mode():
            return _SyncLocalStateFacade(_get_local_state_client())

        if not _REDIS_AVAILABLE:
            logger.warning(
                "redis package not installed and REDIS_URL is set — returning None"
            )
            return None

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
        for client in cls._async_redis_pools.values():
            if client is not None:
                await client.aclose()
        cls._async_redis_pools.clear()
        logger.info("Closed async Redis connections")
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
