"""#3388 (ARC-E2.5) — Redis-Adapter des ``StatePort`` (Enterprise).

Duenn: Redis kann fast alles selbst. Die einzige Stelle, an der der Adapter
wirklich etwas hinzufuegt, ist die **Freigabe der Sperre**.

``RedisClient.release_lock`` loescht den Schluessel bedingungslos
(``redis_client.py:262-269``). Laeuft die Frist des ersten Halters ab und
uebernimmt ein zweiter, dann loescht die verspaetete Freigabe des ersten die
Sperre des zweiten — und beide halten sich fuer den einzigen Schreiber. Genau das
darf unter #3390 nicht passieren, also vergleicht dieser Adapter vor dem Loeschen
die Besitzer-Kennung.

Der Vergleich laeuft ueber ``WATCH``/``MULTI`` und nicht ueber ein Lua-Skript:
``lupa`` ist im Bestand nicht installiert, ``EVAL`` waere unter ``fakeredis``
also nicht lauffaehig — und ein Vertragstest, der den Redis-Lauf ueberspringt,
waere die Attrappe, vor der der Plan warnt.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from .port import StateLock, StatePort

logger = logging.getLogger(__name__)


class _RedisLock(StateLock):
    """``SET NX PX`` zum Erwerb, Vergleichen-und-Loeschen zur Freigabe."""

    def __init__(self, client, name: str, ttl_seconds: float):
        self._r = client
        self._key = f"__lock__:{name}"
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._token = uuid.uuid4().hex

    async def acquire(self) -> bool:
        return (
            await self._r.set(self._key, self._token, px=self._ttl_ms, nx=True) is True
        )

    async def _wenn_uns_gehoert(self, was, *, token: Optional[str] = None) -> bool:
        """Vergleichen-und-Handeln unter ``WATCH``/``MULTI``.

        Gemeinsamer Rumpf von ``renew`` und ``release``: Beide duerfen nur wirken,
        solange der Schluessel **unseren** Token traegt. Ohne diese Bedingung loescht
        oder verlaengert eine abgeloeste Instanz die Sperre ihres Nachfolgers.

        Kein Lua: ``lupa`` ist im Bestand nicht installiert, ``EVAL`` waere unter
        ``fakeredis`` also nicht lauffaehig — und ein Vertragstest, der den Redis-Lauf
        ueberspringt, waere eine Attrappe.
        """
        from redis.exceptions import WatchError

        async with self._r.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(self._key)
                    if await pipe.get(self._key) != (token or self._token):
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    was(pipe)
                    await pipe.execute()
                    return True
                except WatchError:
                    # Zwischen Lesen und Handeln hat jemand den Schluessel
                    # angefasst — erneut pruefen statt blind zu wirken.
                    continue

    async def renew(self) -> bool:
        return await self._wenn_uns_gehoert(
            lambda pipe: pipe.pexpire(self._key, self._ttl_ms)
        )

    async def release(self) -> bool:
        return await self._wenn_uns_gehoert(lambda pipe: pipe.delete(self._key))

    async def held(self) -> bool:
        return await self._r.get(self._key) == self._token

    @property
    def besitz_token(self) -> str:
        return self._token

    async def brechen(self, fremdes_token: str) -> bool:
        # Dasselbe Vergleichen-und-Loeschen wie ``release`` — nur gegen das Token des toten
        # Halters. Hat ein anderer uebernommen, traegt der Schluessel dessen Token: kein Effekt.
        if not fremdes_token or fremdes_token == self._token:
            return False
        return await self._wenn_uns_gehoert(
            lambda pipe: pipe.delete(self._key), token=fremdes_token
        )


class RedisStateAdapter(StatePort):
    """Betriebszustand in Redis.

    Der Client wird hereingereicht statt hier beschafft: Der Vertragstest faehrt
    denselben Adapter gegen ``fakeredis``, und der Order-Pfad reicht den Client
    aus ``RedisClient.get_redis()`` herein.
    """

    def __init__(self, client):
        self._r = client

    # ── Schluessel und Werte ────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[str]:
        return await self._r.get(key)

    async def set(
        self, key: str, value: str, *, ttl_seconds: Optional[float] = None
    ) -> None:
        if ttl_seconds is None:
            # Ohne KEEPTTL loescht SET eine bestehende Frist — genau die Zusage
            # des Ports: ein frisch gesetzter Halt erbt keine alte Frist.
            await self._r.set(key, value)
        else:
            await self._r.set(key, value, px=max(1, int(ttl_seconds * 1000)))

    async def delete(self, key: str) -> bool:
        return await self._r.delete(key) > 0

    async def increment(
        self, key: str, amount: float = 1.0, *, ttl_seconds: Optional[float] = None
    ) -> float:
        neu = float(await self._r.incrbyfloat(key, amount))
        if ttl_seconds is not None:
            await self._r.pexpire(key, max(1, int(ttl_seconds * 1000)))
        return neu

    # ── Warteschlange ───────────────────────────────────────────────────────

    async def append(
        self, key: str, value: str, *, max_length: Optional[int] = None
    ) -> int:
        laenge = await self._r.rpush(key, value)
        if max_length is not None and laenge > max_length:
            await self._r.ltrim(key, -max_length, -1)
            return max_length
        return int(laenge)

    async def read(self, key: str) -> List[str]:
        return list(await self._r.lrange(key, 0, -1))

    async def pop(self, key: str) -> Optional[str]:
        return await self._r.lpop(key)

    # ── Sperre ──────────────────────────────────────────────────────────────

    def lock(self, name: str, *, ttl_seconds: float) -> StateLock:
        return _RedisLock(self._r, name, ttl_seconds)

    # ── Betrieb ─────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception as exc:
            logger.warning(
                "RedisStateAdapter: Ablage nicht erreichbar (%s)", exc, exc_info=True
            )
            return False

    async def aclose(self) -> None:
        schliessen = getattr(self._r, "aclose", None) or getattr(self._r, "close", None)
        if schliessen is not None:
            await schliessen()
