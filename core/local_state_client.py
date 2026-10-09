"""
core/local_state_client.py — In-Memory State Client (OSS-4 / #1085).

Drop-in replacement for Redis in desktop/local mode. Provides the same
interface as redis-py for the operations used by the AAAgents codebase:

  - get/set/delete (key-value)
  - setnx (distributed locks)
  - rpush/ltrim/lrange (lists / rolling buffers)
  - xadd/xread (streams — simplified in-memory FIFO)
  - ping (health check)
  - pipeline (batched operations)

All data is ephemeral (in-memory, lost on process restart). This is
acceptable for desktop mode where persistence is handled by SQLite.

Thread-safe via threading.Lock.
"""

import fnmatch
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LocalPipeline:
    """Batch command executor for LocalStateClient (mimics redis.Pipeline)."""

    def __init__(self, client: "LocalStateClient"):
        self._client = client
        self._commands: list = []

    def rpush(self, key: str, *values):
        self._commands.append(("rpush", key, values))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self._commands.append(("ltrim", key, start, end))
        return self

    async def execute(self):
        # BORA-02: Acquire the parent client's lock to prevent race conditions
        # when concurrent threads call rpush/ltrim/lrange while a pipeline runs.
        with self._client._lock:
            results = []
            for cmd in self._commands:
                if cmd[0] == "rpush":
                    for v in cmd[2]:
                        self._client._lists[cmd[1]].append(v)
                    results.append(len(self._client._lists[cmd[1]]))
                elif cmd[0] == "ltrim":
                    key, start, end = cmd[1], cmd[2], cmd[3]
                    lst = self._client._lists[key]
                    # Redis LTRIM semantics: keep elements [start, end] inclusive
                    trimmed = (
                        list(lst)[start:] if end == -1 else list(lst)[start : end + 1]
                    )
                    self._client._lists[key] = deque(trimmed)
                    results.append(True)
            self._commands.clear()
            return results


class LocalStateClient:
    """In-memory state client replacing Redis for desktop mode.

    Implements the subset of the redis-py API used across the AAAgents
    codebase. Thread-safe, ephemeral (no disk persistence).

    Used by RedisClient as a transparent fallback when REDIS_URL is empty.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._store: Dict[str, str] = {}
        self._lists: Dict[str, deque] = defaultdict(deque)
        self._streams: Dict[str, list] = defaultdict(list)
        self._hash: Dict[str, Dict[str, str]] = {}
        self._expiries: Dict[str, float] = {}
        logger.info("LocalStateClient initialized (in-memory, no Redis)")

    # ── Distributed Lock Stub ────────────────────────────────────────────────
    # Fixes pre-existing AttributeError: redis_client.lock() is called at
    # order_executor.py:202 — LocalStateClient had no lock() method (F-3 / NB-3).
    # Single-process desktop mode has no real contention — always acquires.

    class _LocalLock:
        """No-op async lock for LocalStateClient desktop mode."""

        async def acquire(self, blocking: bool = True) -> bool:
            return True

        async def release(self) -> None:
            pass

        async def __aenter__(self) -> "LocalStateClient._LocalLock":
            return self

        async def __aexit__(self, *args) -> None:
            pass

    def lock(self, name: str, timeout: float | None = None, **kwargs) -> "_LocalLock":
        """Stub for redis-py lock() — always acquires in local/desktop mode."""
        return self._LocalLock()

    # ── Pub/Sub ──────────────────────────────────────────────────────────────

    async def publish(self, channel: str, message: str) -> int:
        """Stub for pub/sub publish to prevent crashes and warnings in local mode."""
        logger.debug("LocalStateClient: publish to %s skipped in local mode", channel)
        return 0

    # ── Key-Value ────────────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._evict_expired(key)
            return self._store.get(key)

    def get_sync(self, key: str) -> Optional[str]:
        """Synchronous get (for get_sync_redis consumers)."""
        with self._lock:
            self._evict_expired(key)
            return self._store.get(key)

    async def set(
        self, key: str, value: str, px: int | None = None, nx: bool = False
    ) -> Optional[bool]:
        with self._lock:
            self._evict_expired(key)
            if nx and key in self._store:
                return None  # SETNX semantics: key exists → no-op
            self._store[key] = str(value)
            if px:
                self._expiries[key] = time.time() + (px / 1000.0)
            return True

    def set_sync(
        self, key: str, value: str, px: int | None = None, nx: bool = False
    ) -> Optional[bool]:
        """Synchronous set."""
        with self._lock:
            self._evict_expired(key)
            if nx and key in self._store:
                return None
            self._store[key] = str(value)
            if px:
                self._expiries[key] = time.time() + (px / 1000.0)
            return True

    async def delete(self, *keys: str) -> int:
        with self._lock:
            count = 0
            for key in keys:
                if key in self._store:
                    del self._store[key]
                    self._expiries.pop(key, None)
                    count += 1
            return count

    def delete_sync(self, *keys: str) -> int:
        """Synchronous delete."""
        with self._lock:
            count = 0
            for key in keys:
                if key in self._store:
                    del self._store[key]
                    self._expiries.pop(key, None)
                    count += 1
            return count

    # ── Hash (HGET/HSET — e.g. agent_weights_v2, #1353) ──────────────────────
    async def hget(self, name: str, field: str) -> Optional[str]:
        with self._lock:
            return self._hash.get(name, {}).get(field)

    def hget_sync(self, name: str, field: str) -> Optional[str]:
        """Synchronous hget (for get_sync_redis consumers)."""
        with self._lock:
            return self._hash.get(name, {}).get(field)

    async def hset(self, name: str, field: str, value: str) -> int:
        with self._lock:
            h = self._hash.setdefault(name, {})
            is_new = field not in h
            h[field] = str(value)
            return 1 if is_new else 0

    def hset_sync(self, name: str, field: str, value: str) -> int:
        """Synchronous hset. Returns 1 if the field is new, else 0 (redis HSET semantics)."""
        with self._lock:
            h = self._hash.setdefault(name, {})
            is_new = field not in h
            h[field] = str(value)
            return 1 if is_new else 0

    async def keys(self, pattern: str = "*") -> List[str]:
        """Glob key lookup (mimics redis KEYS), evicting expired keys first.

        Added (HITL ii-1) so the HITL queue's enumeration paths (get_pending /
        claim_approved / recover_orphaned_inflight) work on the desktop in-memory
        backend. The hot per-symbol path (has_pending) uses an O(1) secondary index
        instead of scanning.

        Uses ``fnmatchcase`` (always case-sensitive) — NOT ``fnmatch`` (which is
        case-insensitive on Windows) — so behaviour matches Redis KEYS exactly on
        every OS (no "works on a Windows dev box, finds nothing on Linux/Redis" trap).
        """
        with self._lock:
            now = time.time()
            for k in [k for k, exp in self._expiries.items() if now > exp]:
                self._store.pop(k, None)
                self._expiries.pop(k, None)
            return [k for k in self._store if fnmatch.fnmatchcase(k, pattern)]

    def keys_sync(self, pattern: str = "*") -> List[str]:
        """Synchronous keys (for get_sync_redis consumers, #1353)."""
        with self._lock:
            now = time.time()
            for k in [k for k, exp in self._expiries.items() if now > exp]:
                self._store.pop(k, None)
                self._expiries.pop(k, None)
            return [k for k in self._store if fnmatch.fnmatchcase(k, pattern)]

    async def incrbyfloat(self, key: str, amount: float) -> float:
        """Atomic INCRBYFLOAT — mirrors ``redis.asyncio.Redis.incrbyfloat(name, amount)``
        EXACTLY (no extra kwargs; real Redis offers no per-op TTL here). The whole
        read-modify-write runs inside one lock block with no ``await``, so concurrent
        coroutines cannot lose updates — a get()+set() pair would interleave across its
        awaits (HITL day-notional, N5). A caller that also needs a TTL sets it separately
        (``expire``/``pexpire``), exactly as it must on real Redis.
        """
        with self._lock:
            self._evict_expired(key)
            new_value = float(self._store.get(key) or 0.0) + float(amount)
            self._store[key] = str(new_value)
            return new_value

    async def pexpire(self, key: str, time_ms: int) -> bool:
        """Set a millisecond TTL on an existing key — mirrors
        ``redis.asyncio.Redis.pexpire(name, time)``. Returns True if the key exists (TTL
        set), False otherwise. Lets a caller add a TTL after an atomic ``incrbyfloat``
        (which, like real Redis, takes no per-op TTL) — e.g. the HITL day-notional counter.
        """
        with self._lock:
            self._evict_expired(key)
            if key not in self._store:
                return False
            self._expiries[key] = time.time() + (time_ms / 1000.0)
            return True

    # ── Health ───────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        return True

    def ping_sync(self) -> bool:
        return True

    # ── Lists (OHLCV Rolling Buffer) ─────────────────────────────────────────

    async def rpush(self, key: str, *values) -> int:
        with self._lock:
            for v in values:
                self._lists[key].append(v)
            return len(self._lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        with self._lock:
            lst = self._lists[key]
            trimmed = list(lst)[start:] if end == -1 else list(lst)[start : end + 1]
            self._lists[key] = deque(trimmed)
            return True

    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        with self._lock:
            lst = list(self._lists[key])
            if end == -1:
                return lst[start:]
            return lst[start : end + 1]

    def pipeline(self) -> LocalPipeline:
        return LocalPipeline(self)

    # ── Streams (Inter-Agent Messaging) ──────────────────────────────────────

    async def xadd(self, stream: str, fields: Dict[str, str]) -> str:
        with self._lock:
            ts = int(time.time() * 1000)
            msg_id = f"{ts}-{len(self._streams[stream])}"
            self._streams[stream].append((msg_id, fields))
            return msg_id

    def xadd_sync(self, stream: str, fields: Dict[str, str]) -> str:
        """Synchronous xadd (for get_sync_redis consumers, #1353)."""
        with self._lock:
            ts = int(time.time() * 1000)
            msg_id = f"{ts}-{len(self._streams[stream])}"
            self._streams[stream].append((msg_id, fields))
            return msg_id

    async def xread(self, streams: Dict[str, str], count: int = 100) -> list:
        with self._lock:
            result = []
            for stream_name, last_id in streams.items():
                entries = self._streams.get(stream_name, [])
                if last_id == "$":
                    # Only new messages (none in-memory)
                    filtered = []
                elif last_id == "0":
                    filtered = entries[:count]
                else:
                    # Find entries after last_id
                    filtered = []
                    found = False
                    for entry in entries:
                        if found:
                            filtered.append(entry)
                            if len(filtered) >= count:
                                break
                        if entry[0] == last_id:
                            found = True
                if filtered:
                    result.append((stream_name, filtered))
            return result

    # ── Cleanup ──────────────────────────────────────────────────────────────

    async def aclose(self):
        """Mimics redis.aclose() — clears in-memory state."""
        with self._lock:
            self._store.clear()
            self._lists.clear()
            self._streams.clear()
            self._expiries.clear()

    def close(self):
        """Synchronous close."""
        with self._lock:
            self._store.clear()
            self._lists.clear()
            self._streams.clear()
            self._expiries.clear()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _evict_expired(self, key: str):
        """Remove key if its TTL has expired (called under lock)."""
        if key in self._expiries and time.time() > self._expiries[key]:
            self._store.pop(key, None)
            del self._expiries[key]
